from sqlalchemy.orm import Session

from app.applications.models import Application
from app.audit.models import ApplicationAuditLog
from app.drivers.models import Driver


def create_audit_log(
    db: Session,
    driver: Driver,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    action_type: str,
    application: Application | None = None,
    actor_type: str = "shared_admin",
) -> ApplicationAuditLog:
    log = ApplicationAuditLog(
        application_id=application.id if application else None,
        driver_id=driver.id,
        actor_type=actor_type,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        action_type=action_type,
    )
    db.add(log)
    db.flush()
    return log
