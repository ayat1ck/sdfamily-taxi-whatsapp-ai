from sqlalchemy.orm import Session

from app.drivers.models import Driver
from app.messages.models import Message


def create_message(
    db: Session,
    driver: Driver,
    direction: str,
    message_type: str,
    text: str | None = None,
    raw_payload: dict | None = None,
    sender_type: str = "customer",
    provider_message_id: str | None = None,
    media_url: str | None = None,
    mime_type: str | None = None,
    delivery_status: str | None = None,
    error_text: str | None = None,
) -> Message:
    normalized_sender_type = sender_type
    if direction == "outgoing" and sender_type == "customer":
        normalized_sender_type = "bot"
    elif direction == "incoming" and sender_type in {"bot", "manager"}:
        normalized_sender_type = "customer"

    message = Message(
        driver_id=driver.id,
        direction=direction,
        sender_type=normalized_sender_type,
        message_type=message_type,
        text=text,
        provider_message_id=provider_message_id,
        media_url=media_url,
        mime_type=mime_type,
        delivery_status=delivery_status,
        error_text=error_text,
        raw_payload=raw_payload,
    )
    db.add(message)
    db.flush()
    return message
