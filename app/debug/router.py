import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.database.session import get_db
from app.dialog.ai import get_ai_service
from app.dialog.engine import DialogueEngine
from app.dialog.states import DialogueState
from app.documents.models import Document
from app.drivers.models import Driver
from app.drivers.service import get_or_create_driver
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.yandex.service import YandexSubmissionService
from app.messages.models import Message
from app.whatsapp.parser import ParsedWhatsAppMessage

router = APIRouter(prefix="/debug", tags=["debug"])
engine = DialogueEngine()
google_drive = GoogleDriveClient()
google_sheets = GoogleSheetsClient()
yandex = YandexSubmissionService()
ai_service = get_ai_service()


class DebugMessageRequest(BaseModel):
    phone: str
    text: str = Field(min_length=1)


class DebugDocumentRequest(BaseModel):
    phone: str
    filename: str
    content_base64: str
    upload_to_drive: bool = True


class DebugAIInspectRequest(BaseModel):
    phone: str
    text: str = Field(min_length=1)
    state: str | None = None


def _get_driver_or_404(db: Session, phone: str) -> Driver:
    driver = get_or_create_driver(db, phone)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


def _get_application_or_404(db: Session, driver: Driver) -> Application:
    application = db.scalar(select(Application).where(Application.driver_id == driver.id))
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/messages")
def debug_message(payload: DebugMessageRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = get_or_create_driver(db, payload.phone)
    reply = engine.handle_message(
        db,
        driver,
        ParsedWhatsAppMessage(
            sender_phone=payload.phone,
            message_type="text",
            text=payload.text,
            raw_payload={"source": "debug", "text": payload.text},
        ),
    )
    db.commit()
    application = db.scalar(select(Application).where(Application.driver_id == driver.id))
    return {
        "status": "ok",
        "phone": driver.whatsapp_phone,
        "driver_state": driver.state,
        "application_status": application.status if application else None,
        "reply": reply,
    }


@router.get("/messages/{phone}")
def debug_messages(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    messages = db.scalars(select(Message).where(Message.driver_id == driver.id).order_by(Message.created_at)).all()
    return {
        "phone": driver.whatsapp_phone,
        "driver_state": driver.state,
        "messages": [
            {
                "id": message.id,
                "direction": message.direction,
                "sender_type": message.sender_type,
                "message_type": message.message_type,
                "text": message.text,
                "delivery_status": message.delivery_status,
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in messages
        ],
    }


@router.post("/ai/inspect")
def debug_ai_inspect(payload: DebugAIInspectRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = get_or_create_driver(db, payload.phone)
    state = payload.state or driver.state or DialogueState.NEW.value
    result = ai_service.respond(state, payload.text, driver)
    return {
        "status": "ok",
        "phone": driver.whatsapp_phone,
        "state": state,
        "text": payload.text,
        "ai_result": {
            "reply": result.reply,
            "intent": result.intent,
            "next_state": result.next_state,
            "confidence": result.confidence,
            "extracted_fields": result.extracted_fields,
        },
    }


@router.post("/documents")
def debug_document(payload: DebugDocumentRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = get_or_create_driver(db, payload.phone)
    try:
        content = base64.b64decode(payload.content_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 document content: {exc}") from exc

    try:
        result = engine.handle_debug_document(
            db,
            driver,
            filename=payload.filename,
            content=content,
            upload_to_drive=payload.upload_to_drive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return {"status": "ok", "phone": driver.whatsapp_phone, **result}


@router.get("/documents/{phone}")
def debug_documents(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    documents = db.scalars(select(Document).where(Document.driver_id == driver.id).order_by(Document.created_at)).all()
    return {
        "phone": driver.whatsapp_phone,
        "driver_state": driver.state,
        "documents": [
            {
                "id": document.id,
                "document_type": document.document_type,
                "status": document.status,
                "file_url": document.file_url,
                "google_drive_file_id": document.google_drive_file_id,
                "created_at": document.created_at.isoformat() if document.created_at else None,
            }
            for document in documents
        ],
    }


@router.post("/google/sheets-sync/{phone}")
def debug_google_sheets_sync(phone: str, db: Session = Depends(get_db)) -> dict[str, str]:
    driver = _get_driver_or_404(db, phone)
    application = _get_application_or_404(db, driver)
    google_sheets.sync_application(driver, application)
    return {"status": "ok", "phone": driver.whatsapp_phone, "synced": "true"}


@router.post("/google/export/{phone}")
def debug_google_export(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    application = _get_application_or_404(db, driver)
    google_sheets.sync_application(driver, application)
    drive_result = google_drive.upload_application_snapshot(driver, application)
    return {
        "status": "ok",
        "phone": driver.whatsapp_phone,
        "sheets_synced": True,
        "drive_export": drive_result,
    }


@router.get("/yandex/preview/{phone}")
def debug_yandex_preview(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    return {
        "status": "ok",
        "phone": driver.whatsapp_phone,
        "preview": yandex.preview(driver),
    }


@router.post("/yandex/submit/{phone}")
def debug_yandex_submit(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    application = _get_application_or_404(db, driver)
    try:
        yandex.submit(db, driver, application)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "ok",
        "phone": driver.whatsapp_phone,
        "application_status": application.status,
        "yandex_status": application.yandex_status,
        "yandex_driver_id": application.yandex_driver_id,
        "yandex_vehicle_id": application.yandex_vehicle_id,
    }
