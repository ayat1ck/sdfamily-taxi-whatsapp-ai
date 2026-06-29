from sqlalchemy import select
from sqlalchemy.orm import Session

from app.unknown_intents.models import UnknownIntent


def create_unknown_intent(
    db: Session,
    *,
    driver_id: int | None,
    message_id: int | None,
    state_before: str | None,
    message_text: str,
    normalized_text: str | None,
    message_type: str | None,
    reason: str | None,
) -> UnknownIntent | None:
    if not (message_text or "").strip():
        return None
    record = UnknownIntent(
        driver_id=driver_id,
        message_id=message_id,
        state_before=state_before,
        message_text=message_text,
        normalized_text=normalized_text,
        message_type=message_type,
        reason=reason,
    )
    db.add(record)
    db.flush()
    return record


def list_unknown_intents(db: Session, *, state: str = "", limit: int = 100) -> list[UnknownIntent]:
    query = select(UnknownIntent).order_by(UnknownIntent.created_at.desc())
    if state:
        query = query.where(UnknownIntent.state_before == state)
    return list(db.scalars(query.limit(limit)).all())
