from sqlalchemy.orm import Session

from app.conversation_events.models import ConversationEvent
from app.drivers.models import Driver


def create_conversation_event(
    db: Session,
    driver: Driver,
    event_type: str,
    event_payload: dict | None = None,
) -> ConversationEvent:
    event = ConversationEvent(
        driver_id=driver.id,
        event_type=event_type,
        event_payload=event_payload,
    )
    db.add(event)
    db.flush()
    return event
