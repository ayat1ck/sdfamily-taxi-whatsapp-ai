from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.auth import ensure_default_admin_account
from app.admin.router import router as admin_router
from app.applications.models import Application
from app.audit.models import ApplicationAuditLog
from app.config import get_settings
from app.conversation_events.models import ConversationEvent
from app.database.base import Base
from app.database.bootstrap import ensure_runtime_schema
from app.database.session import engine, get_db
from app.documents.models import Document
from app.debug.router import router as debug_router
from app.drivers.models import Driver
from app.integration_jobs.models import IntegrationJob
from app.messages.models import Message
from app.public_site import router as public_site_router
from app.vehicles.models import Vehicle
from app.utils.logger import get_logger
from app.whatsapp.webhook import router as whatsapp_router

app = FastAPI(title="Taxi WhatsApp AI Manager", version="0.1.0")
logger = get_logger(__name__)
settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.admin_session_secret,
    session_cookie=settings.admin_session_cookie_name,
    same_site="lax",
    https_only=settings.app_env != "development",
)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(engine)
    missing = settings.missing_config()
    if missing:
        logger.warning("Missing external integration config: %s", missing)
        if settings.strict_config:
            raise RuntimeError(f"Missing required configuration: {missing}")
    with Session(engine) as db:
        ensure_default_admin_account(db)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "strict_config": str(settings.strict_config).lower(),
        "config_ready": str(not settings.missing_config()).lower(),
    }


@app.get("/applications/{application_id}")
def get_application(application_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    application = db.get(Application, application_id)
    if not application:
        return {"status": "not_found"}
    driver = db.get(Driver, application.driver_id)
    vehicle = driver.vehicle if driver else None
    documents = db.scalars(select(Document).where(Document.driver_id == application.driver_id)).all()
    messages = db.scalars(select(Message).where(Message.driver_id == application.driver_id)).all()
    return {
        "application": {
            "id": application.id,
            "status": application.status,
            "yandex_status": application.yandex_status,
            "yandex_driver_id": application.yandex_driver_id,
            "yandex_vehicle_id": application.yandex_vehicle_id,
            "yandex_error": application.yandex_error,
        },
        "driver": {
            "id": driver.id if driver else None,
            "whatsapp_phone": driver.whatsapp_phone if driver else None,
            "full_name": driver.full_name if driver else None,
            "last_name": driver.last_name if driver else None,
            "first_name": driver.first_name if driver else None,
            "middle_name": driver.middle_name if driver else None,
            "phone": driver.phone if driver else None,
            "city": driver.city if driver else None,
            "address": driver.address if driver else None,
            "iin": driver.iin if driver else None,
            "birth_date": driver.birth_date if driver else None,
            "driving_experience_since": driver.driving_experience_since if driver else None,
            "driver_license_number": driver.driver_license_number if driver else None,
            "driver_license_issue_date": driver.driver_license_issue_date if driver else None,
            "driver_license_expires_at": driver.driver_license_expires_at if driver else None,
            "executor_type": driver.executor_type if driver else None,
            "employment_type": driver.employment_type if driver else None,
            "hired_at": driver.hired_at if driver else None,
            "is_hearing_impaired": driver.is_hearing_impaired if driver else None,
            "state": driver.state if driver else None,
            "dialog_mode": driver.dialog_mode if driver else None,
            "assigned_manager_name": driver.assigned_manager_name if driver else None,
            "unread_count": driver.unread_count if driver else None,
            "requires_attention": driver.requires_attention if driver else None,
            "duplicate_flag": driver.duplicate_flag if driver else None,
        },
        "vehicle": {
            "brand": vehicle.brand if vehicle else None,
            "model": vehicle.model if vehicle else None,
            "year": vehicle.year if vehicle else None,
            "plate_number": vehicle.plate_number if vehicle else None,
            "color": vehicle.color if vehicle else None,
        },
        "documents": [
            {"document_type": document.document_type, "file_url": document.file_url, "status": document.status}
            for document in documents
        ],
        "messages": [
            {
                "direction": message.direction,
                "sender_type": message.sender_type,
                "message_type": message.message_type,
                "text": message.text,
                "delivery_status": message.delivery_status,
            }
            for message in messages
        ],
    }


app.include_router(whatsapp_router)
app.include_router(debug_router)
app.include_router(admin_router)
app.include_router(public_site_router)
