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
) -> Message:
    message = Message(
        driver_id=driver.id,
        direction=direction,
        message_type=message_type,
        text=text,
        raw_payload=raw_payload,
    )
    db.add(message)
    db.flush()
    return message
