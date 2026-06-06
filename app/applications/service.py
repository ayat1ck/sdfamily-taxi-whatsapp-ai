from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver


def get_or_create_application(db: Session, driver: Driver) -> Application:
    application = db.scalar(select(Application).where(Application.driver_id == driver.id))
    if application:
        return application
    application = Application(driver_id=driver.id)
    db.add(application)
    db.flush()
    return application


def set_application_status(
    db: Session,
    application: Application,
    status: str,
    yandex_status: str | None = None,
    yandex_error: str | None = None,
) -> Application:
    application.status = status
    application.updated_at = datetime.utcnow()
    if yandex_status is not None:
        application.yandex_status = yandex_status
    if yandex_error is not None:
        application.yandex_error = yandex_error
    db.add(application)
    db.flush()
    return application
