from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="collecting_data")
    yandex_status: Mapped[str | None] = mapped_column(String(64))
    yandex_driver_id: Mapped[str | None] = mapped_column(String(128))
    yandex_vehicle_id: Mapped[str | None] = mapped_column(String(128))
    yandex_error: Mapped[str | None] = mapped_column(String(1024))
    sent_to_yandex_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    driver = relationship("Driver", back_populates="applications")
    audit_logs = relationship("ApplicationAuditLog", back_populates="application", cascade="all, delete-orphan")
    integration_jobs = relationship("IntegrationJob", back_populates="application", cascade="all, delete-orphan")
