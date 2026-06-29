from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class UnknownIntent(Base):
    __tablename__ = "unknown_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), index=True, nullable=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), index=True, nullable=True)
    state_before: Mapped[str | None] = mapped_column(String(64))
    message_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    message_type: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver")
    message = relationship("Message")
