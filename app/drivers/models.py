from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(120))
    first_name: Mapped[str | None] = mapped_column(String(120))
    middle_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(255))
    iin: Mapped[str | None] = mapped_column(String(12))
    birth_date: Mapped[str | None] = mapped_column(String(32))
    driving_experience_since: Mapped[str | None] = mapped_column(String(32))
    driver_license_number: Mapped[str | None] = mapped_column(String(64))
    driver_license_issue_date: Mapped[str | None] = mapped_column(String(32))
    driver_license_expires_at: Mapped[str | None] = mapped_column(String(32))
    executor_type: Mapped[str | None] = mapped_column(String(64))
    employment_type: Mapped[str | None] = mapped_column(String(64))
    hired_at: Mapped[str | None] = mapped_column(String(32))
    has_personal_car: Mapped[str | None] = mapped_column(String(8))
    existing_vehicle_lookup: Mapped[str | None] = mapped_column(String(120))
    is_hearing_impaired: Mapped[str | None] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(64), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)

    vehicle = relationship("Vehicle", back_populates="driver", uselist=False, cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="driver", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="driver", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="driver", cascade="all, delete-orphan")
