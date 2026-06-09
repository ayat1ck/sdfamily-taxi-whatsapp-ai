from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class MessageAITrace(Base):
    __tablename__ = "message_ai_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True, unique=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), index=True)
    state_before: Mapped[str] = mapped_column(String(64))
    input_text: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    intent: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(default=0.0)
    next_state: Mapped[str | None] = mapped_column(String(64))
    reply_preview: Mapped[str | None] = mapped_column(Text)
    extracted_fields_json: Mapped[dict | None] = mapped_column(JSON)
    normalized_fields_json: Mapped[dict | None] = mapped_column(JSON)
    reasoning_summary: Mapped[str | None] = mapped_column(String(255))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(255))
    validation_errors_json: Mapped[list | dict | None] = mapped_column(JSON)
    suggested_next_action: Mapped[str | None] = mapped_column(String(128))
    raw_decision_json: Mapped[dict | None] = mapped_column(JSON)
    final_decision_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message = relationship("Message", back_populates="ai_trace")
    driver = relationship("Driver", back_populates="ai_traces")
