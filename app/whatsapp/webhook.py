from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.database.session import get_db
from app.dialog.engine import DialogueEngine
from app.drivers.service import get_or_create_driver
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
        reply = engine.handle_message(db, driver, parsed)
        try:
            sender.send_text(driver.whatsapp_phone, reply)
        except Exception as exc:
            logger.exception("Failed to send WhatsApp response to %s: %s", driver.whatsapp_phone, exc)
        replies.append({"phone": driver.whatsapp_phone, "reply": reply})

    db.commit()
    return {"status": "ok", "processed": len(replies), "replies": replies}
