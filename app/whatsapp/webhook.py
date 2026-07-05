from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime
from time import perf_counter

from app.applications.models import Application
from app.conversation_events.service import create_conversation_event
from app.database.session import get_db
from app.dialog.engine import DialogueEngine
from app.dialog_v2 import handle_message_v2
from app.dialog_v2.hooks import notify_manager_stub, save_reply_events
from app.dialog_v2.serializer import build_text_fallback, serialize_reply
from app.dialog_v2.trace import build_v2_trace, trace_duration_ms
from app.drivers.models import Driver
from app.drivers.service import get_or_create_driver
from app.integration_jobs.service import create_integration_job, finish_integration_job
from app.messages.models import Message
from app.messages.service import create_message
from app.utils.logger import get_logger
from app.whatsapp.parser import parse_whatsapp_payload
from app.whatsapp.sender import WhatsAppSender
from app.config import get_settings

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
engine = DialogueEngine()
sender = WhatsAppSender()
logger = get_logger(__name__)


def _dialog_v2_enabled_for_sender(settings, sender_phone: str) -> bool:
    if not settings.use_dialog_v2:
        return False
    allowlist = [item.strip().lstrip("+") for item in (settings.use_dialog_v2_phone_allowlist or "").split(",") if item.strip()]
    if not allowlist:
        return True
    normalized = sender_phone.lstrip("+")
    return normalized in allowlist


def safe_send_v2(sender_client: WhatsAppSender, primary_payload: dict[str, object], fallback_payload: dict[str, object], phone: str) -> dict[str, object]:
    try:
        return sender_client.send_payload(primary_payload)
    except Exception as primary_exc:
        logger.exception("Failed to send interactive WhatsApp payload to %s: %s", phone, primary_exc)
        if fallback_payload == primary_payload:
            raise
        try:
            return sender_client.send_payload(fallback_payload)
        except Exception as fallback_exc:
            logger.exception("Failed to send text fallback to %s: %s", phone, fallback_exc)
            raise fallback_exc from primary_exc


def _log_v2_trace(trace: dict[str, object]) -> None:
    logger.info("[V2] %s", trace)


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
    settings = get_settings()

    for parsed in parsed_messages:
        if parsed.provider_message_id:
            existing_message = db.scalar(
                select(Message).where(
                    Message.provider_message_id == parsed.provider_message_id,
                    Message.direction == "incoming",
                )
            )
            if existing_message:
                logger.info("Skipping duplicate WhatsApp message %s", parsed.provider_message_id)
                replies.append({"phone": parsed.sender_phone, "reply": ""})
                continue

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

        use_dialog_v2 = _dialog_v2_enabled_for_sender(settings, parsed.sender_phone)
        state_before = driver.state
        pending_menu_before = (driver.support_context_json or {}).get("pending_menu")
        started_at = perf_counter()
        structured_reply = None
        outbound_payload = None
        fallback_payload = None
        reply = ""
        v2_trace = None
        error_text = None
        try:
            if use_dialog_v2:
                structured_reply = handle_message_v2(db, driver, parsed)
                outbound_payload = serialize_reply(structured_reply, driver.whatsapp_phone)
                fallback_payload = {
                    "messaging_product": "whatsapp",
                    "to": driver.whatsapp_phone.lstrip("+"),
                    "type": "text",
                    "text": {"body": build_text_fallback(structured_reply)},
                }
                reply = structured_reply.to_text()
            else:
                reply = engine.handle_message(db, driver, parsed)
            if use_dialog_v2:
                state_after = driver.state
                pending_menu_after = (driver.support_context_json or {}).get("pending_menu")
                v2_trace = build_v2_trace(
                    phone=driver.whatsapp_phone,
                    message_type=parsed.message_type,
                    text=parsed.text,
                    flow=structured_reply.flow if structured_reply else None,
                    intent=(structured_reply.metadata or {}).get("intent") if structured_reply else None,
                    state_before=state_before,
                    state_after=state_after,
                    pending_menu_before=pending_menu_before,
                    pending_menu_after=pending_menu_after,
                    reply=structured_reply,
                    duration_ms=trace_duration_ms(started_at),
                )
                _log_v2_trace(v2_trace)
        except Exception as exc:
            if not use_dialog_v2:
                raise
            error_text = str(exc)
            v2_trace = build_v2_trace(
                phone=driver.whatsapp_phone,
                message_type=parsed.message_type,
                text=parsed.text,
                flow=structured_reply.flow if structured_reply else None,
                intent=(structured_reply.metadata or {}).get("intent") if structured_reply else None,
                state_before=state_before,
                state_after=driver.state,
                pending_menu_before=pending_menu_before,
                pending_menu_after=(driver.support_context_json or {}).get("pending_menu"),
                reply=structured_reply,
                duration_ms=trace_duration_ms(started_at),
                error=error_text,
            )
            _log_v2_trace(v2_trace)
            if use_dialog_v2:
                create_message(
                    db,
                    driver=driver,
                    direction="outgoing",
                    sender_type="bot",
                    message_type="debug_trace",
                    text="v2 trace error",
                    provider_message_id=None,
                    delivery_status="error",
                    error_text=error_text,
                    raw_payload={"v2_trace": v2_trace},
                )
                db.commit()
            replies.append({"phone": driver.whatsapp_phone, "reply": ""})
            continue
        application = db.scalar(select(Application).where(Application.driver_id == driver.id))
        job = create_integration_job(
            db,
            provider="whatsapp",
            action="auto_reply",
            status="pending",
            request_payload={"phone": driver.whatsapp_phone, "payload": outbound_payload if use_dialog_v2 else {"type": "text", "text": reply}},
            application=application,
            driver=driver,
        )
        if use_dialog_v2 and structured_reply is not None:
            create_message(
                db,
                driver=driver,
                direction="outgoing",
                sender_type="bot",
                message_type=structured_reply.type or "text",
                text=structured_reply.text,
                provider_message_id=None,
                delivery_status="pending",
                raw_payload={
                    "outbound_payload": outbound_payload,
                    "reply": structured_reply.to_dict(),
                    "v2_trace": v2_trace,
                },
            )
            if structured_reply and structured_reply.manager_alert:
                driver.support_context_json = dict(driver.support_context_json or {})
                driver.support_context_json["v2_trace"] = v2_trace
        db.commit()
        try:
            if use_dialog_v2:
                send_result = safe_send_v2(sender, outbound_payload, fallback_payload, driver.whatsapp_phone)
            else:
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
                pending_message.raw_payload = {
                    **(pending_message.raw_payload or {}),
                    "send_result": send_result,
                    "v2_trace": v2_trace,
                }
                db.add(pending_message)
            if use_dialog_v2:
                save_reply_events(db, driver, structured_reply)
                if structured_reply.requires_manager and structured_reply.manager_alert:
                    notify_manager_stub(db, driver, structured_reply.manager_alert)
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
                failed_message.raw_payload = {
                    **(failed_message.raw_payload or {}),
                    "send_error": str(exc),
                    "v2_trace": v2_trace,
                }
                db.add(failed_message)
            if use_dialog_v2 and structured_reply is not None:
                save_reply_events(db, driver, structured_reply)
                if structured_reply.requires_manager and structured_reply.manager_alert:
                    notify_manager_stub(db, driver, structured_reply.manager_alert)
        db.commit()
        replies.append({"phone": driver.whatsapp_phone, "reply": reply})

    return {"status": "ok", "processed": len(replies), "replies": replies}
