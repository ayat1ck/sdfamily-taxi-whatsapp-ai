from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime

from app.applications.models import Application
from app.conversation_events.service import create_conversation_event
from app.database.session import get_db
from app.dialog.engine import DialogueEngine
from app.drivers.models import Driver
from app.drivers.service import get_or_create_driver
from app.integration_jobs.service import create_integration_job, finish_integration_job
from app.messages.models import Message
from app.messages.service import create_message
from app.utils.logger import get_logger
from app.whatsapp.parser import parse_whatsapp_payload
from app.whatsapp.sender import WhatsAppSender

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
engine = DialogueEngine()
sender = WhatsAppSender()
logger = get_logger(__name__)


@router.get("", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> str:
    from app.config import get_settings

    settings = get_settings()
    if hub_mode != "subscribe" or hub_verify_token != settings.whatsapp_verify_token:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return hub_challenge


@router.post("")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    payload = await request.json()
    parsed_messages = parse_whatsapp_payload(payload)
    replies: list[dict[str, str]] = []

    for parsed in parsed_messages:
        driver = get_or_create_driver(db, parsed.sender_phone)
        application = db.scalar(select(Application).where(Application.driver_id == driver.id))
        if driver.dialog_mode in {"manual", "paused", "closed"}:
            driver.last_message_at = datetime.utcnow()
            driver.unread_count = (driver.unread_count or 0) + 1
            driver.requires_attention = True
            create_message(
                db,
                driver=driver,
                direction="incoming",
                sender_type="customer",
                message_type=parsed.message_type,
                text=parsed.text,
                provider_message_id=parsed.provider_message_id,
                mime_type=parsed.mime_type,
                delivery_status="received",
                raw_payload=parsed.raw_payload,
            )
            create_conversation_event(db, driver, f"incoming_while_{driver.dialog_mode}")
            replies.append({"phone": driver.whatsapp_phone, "reply": ""})
            continue

        reply = engine.handle_message(db, driver, parsed)
        application = db.scalar(select(Application).where(Application.driver_id == driver.id))
        job = create_integration_job(
            db,
            provider="whatsapp",
            action="auto_reply",
            status="pending",
            request_payload={"phone": driver.whatsapp_phone, "text": reply},
            application=application,
            driver=driver,
        )
        try:
            send_result = sender.send_text(driver.whatsapp_phone, reply)
            finish_integration_job(db, job, "sent", response_payload=send_result)
            provider_message_id = None
            payload_messages = send_result.get("messages")
            if isinstance(payload_messages, list) and payload_messages:
                provider_message_id = payload_messages[0].get("id")
            pending_message = db.scalar(
                select(Message)
                .where(Message.driver_id == driver.id, Message.sender_type == "bot")
                .order_by(Message.created_at.desc())
            )
            if pending_message:
                pending_message.delivery_status = "sent"
                pending_message.provider_message_id = provider_message_id
                pending_message.raw_payload = send_result
                db.add(pending_message)
        except Exception as exc:
            logger.exception("Failed to send WhatsApp response to %s: %s", driver.whatsapp_phone, exc)
            finish_integration_job(db, job, "error", error_text=str(exc))
            driver.requires_attention = True
            failed_message = db.scalar(
                select(Message)
                .where(Message.driver_id == driver.id, Message.sender_type == "bot")
                .order_by(Message.created_at.desc())
            )
            if failed_message:
                failed_message.delivery_status = "error"
                failed_message.error_text = str(exc)
                db.add(failed_message)
        replies.append({"phone": driver.whatsapp_phone, "reply": reply})

    db.commit()
    return {"status": "ok", "processed": len(replies), "replies": replies}
