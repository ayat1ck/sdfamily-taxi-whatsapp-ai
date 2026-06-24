from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.drivers.models import Driver
from app.utils.validators import normalize_phone


def get_or_create_driver(db: Session, whatsapp_phone: str) -> Driver:
    phone = normalize_phone(whatsapp_phone)
    driver = db.scalar(select(Driver).where(Driver.whatsapp_phone == phone))
    if driver:
        return driver
    driver = Driver(whatsapp_phone=phone, phone=phone, last_message_at=datetime.utcnow())
    db.add(driver)
    db.flush()
    return driver


def update_driver_state(db: Session, driver: Driver, state: str) -> Driver:
    driver.state = state
    driver.updated_at = datetime.utcnow()
    db.add(driver)
    db.flush()
    return driver


def find_other_driver_by_iin(db: Session, iin: str, exclude_driver_id: int | None = None) -> Driver | None:
    query = select(Driver).where(Driver.iin == iin)
    if exclude_driver_id is not None:
        query = query.where(Driver.id != exclude_driver_id)
    return db.scalar(query)


def find_driver_by_whatsapp_phone(db: Session, whatsapp_phone: str) -> Driver | None:
    phone = normalize_phone(whatsapp_phone)
    return db.scalar(select(Driver).where(Driver.whatsapp_phone == phone))


def find_driver_by_phone(db: Session, phone: str) -> Driver | None:
    normalized = normalize_phone(phone)
    return db.scalar(select(Driver).where(Driver.phone == normalized))


def find_driver_by_iin(db: Session, iin: str) -> Driver | None:
    digits = "".join(ch for ch in iin if ch.isdigit())
    if len(digits) != 12:
        return None
    return db.scalar(select(Driver).where(Driver.iin == digits))
