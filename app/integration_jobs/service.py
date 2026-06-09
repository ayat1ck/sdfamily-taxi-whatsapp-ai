from datetime import datetime

from sqlalchemy.orm import Session

from app.applications.models import Application
from app.drivers.models import Driver
from app.integration_jobs.models import IntegrationJob


def create_integration_job(
    db: Session,
    provider: str,
    action: str,
    status: str,
    request_payload: dict | None = None,
    response_payload: dict | None = None,
    error_text: str | None = None,
    application: Application | None = None,
    driver: Driver | None = None,
    finished: bool = False,
) -> IntegrationJob:
    job = IntegrationJob(
        application_id=application.id if application else None,
        driver_id=driver.id if driver else None,
        provider=provider,
        action=action,
        status=status,
        request_payload=request_payload,
        response_payload=response_payload,
        error_text=error_text,
        finished_at=datetime.utcnow() if finished else None,
    )
    db.add(job)
    db.flush()
    return job


def finish_integration_job(
    db: Session,
    job: IntegrationJob,
    status: str,
    response_payload: dict | None = None,
    error_text: str | None = None,
) -> IntegrationJob:
    job.status = status
    job.response_payload = response_payload
    job.error_text = error_text
    job.finished_at = datetime.utcnow()
    db.add(job)
    db.flush()
    return job
