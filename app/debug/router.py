import base64
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.conversation_events.models import ConversationEvent
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


class DebugYandexLookupRequest(BaseModel):
    phone: str
    lookup: str = Field(min_length=1)


class DebugChatsExportQuery(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _serialize_driver(driver: Driver) -> dict[str, object]:
    vehicle = driver.vehicle
    return {
        "id": driver.id,
        "whatsapp_phone": driver.whatsapp_phone,
        "full_name": driver.full_name,
        "last_name": driver.last_name,
        "first_name": driver.first_name,
        "middle_name": driver.middle_name,
        "phone": driver.phone,
        "city": driver.city,
        "address": driver.address,
        "iin": driver.iin,
        "birth_date": driver.birth_date,
        "driving_experience_since": driver.driving_experience_since,
        "driver_license_number": driver.driver_license_number,
        "driver_license_issue_date": driver.driver_license_issue_date,
        "driver_license_expires_at": driver.driver_license_expires_at,
        "executor_type": driver.executor_type,
        "employment_type": driver.employment_type,
        "hired_at": driver.hired_at,
        "has_personal_car": driver.has_personal_car,
        "existing_vehicle_lookup": driver.existing_vehicle_lookup,
        "is_hearing_impaired": driver.is_hearing_impaired,
        "state": driver.state,
        "dialog_mode": driver.dialog_mode,
        "unread_count": driver.unread_count,
        "requires_attention": driver.requires_attention,
        "duplicate_flag": driver.duplicate_flag,
        "active_support_topic": driver.active_support_topic,
        "active_support_step": driver.active_support_step,
        "support_context_json": driver.support_context_json,
        "fallback_count": driver.fallback_count,
        "created_at": _serialize_datetime(driver.created_at),
        "updated_at": _serialize_datetime(driver.updated_at),
        "last_message_at": _serialize_datetime(driver.last_message_at),
        "deletion_requested_at": _serialize_datetime(driver.deletion_requested_at),
        "paused_at": _serialize_datetime(driver.paused_at),
        "closed_at": _serialize_datetime(driver.closed_at),
        "vehicle": None
        if not vehicle
        else {
            "id": vehicle.id,
            "brand": vehicle.brand,
            "model": vehicle.model,
            "year": vehicle.year,
            "plate_number": vehicle.plate_number,
            "color": vehicle.color,
            "registration_certificate": vehicle.registration_certificate,
            "vin": vehicle.vin,
            "service_class": vehicle.service_class,
            "created_at": _serialize_datetime(vehicle.created_at),
            "updated_at": _serialize_datetime(vehicle.updated_at),
        },
    }


def _serialize_messages(messages: list[Message]) -> list[dict[str, object]]:
    return [
        {
            "id": message.id,
            "direction": message.direction,
            "sender_type": message.sender_type,
            "message_type": message.message_type,
            "text": message.text,
            "provider_message_id": message.provider_message_id,
            "media_url": message.media_url,
            "mime_type": message.mime_type,
            "delivery_status": message.delivery_status,
            "error_text": message.error_text,
            "is_read_by_admin": message.is_read_by_admin,
            "read_at": _serialize_datetime(message.read_at),
            "created_at": _serialize_datetime(message.created_at),
            "raw_payload": message.raw_payload,
            "ai_trace": None
            if not message.ai_trace
            else {
                "id": message.ai_trace.id,
                "state_before": message.ai_trace.state_before,
                "input_text": message.ai_trace.input_text,
                "provider": message.ai_trace.provider,
                "intent": message.ai_trace.intent,
                "confidence": message.ai_trace.confidence,
                "next_state": message.ai_trace.next_state,
                "reply_preview": message.ai_trace.reply_preview,
                "extracted_fields_json": message.ai_trace.extracted_fields_json,
                "normalized_fields_json": message.ai_trace.normalized_fields_json,
                "reasoning_summary": message.ai_trace.reasoning_summary,
                "fallback_used": message.ai_trace.fallback_used,
                "fallback_reason": message.ai_trace.fallback_reason,
                "validation_errors_json": message.ai_trace.validation_errors_json,
                "suggested_next_action": message.ai_trace.suggested_next_action,
                "raw_decision_json": message.ai_trace.raw_decision_json,
                "final_decision_json": message.ai_trace.final_decision_json,
                "created_at": _serialize_datetime(message.ai_trace.created_at),
            },
        }
        for message in messages
    ]


def _serialize_documents(documents: list[Document]) -> list[dict[str, object]]:
    return [
        {
            "id": document.id,
            "message_id": document.message_id,
            "document_type": document.document_type,
            "file_url": document.file_url,
            "google_drive_file_id": document.google_drive_file_id,
            "whatsapp_media_id": document.whatsapp_media_id,
            "file_name": document.file_name,
            "mime_type": document.mime_type,
            "storage_provider": document.storage_provider,
            "storage_path": document.storage_path,
            "status": document.status,
            "created_at": _serialize_datetime(document.created_at),
        }
        for document in documents
    ]


def _serialize_events(events) -> list[dict[str, object]]:
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "event_payload": event.event_payload,
            "created_at": _serialize_datetime(event.created_at),
        }
        for event in events
    ]


def _serialize_application(application: Application | None) -> dict[str, object] | None:
    if not application:
        return None
    return {
        "id": application.id,
        "driver_id": application.driver_id,
        "status": application.status,
        "yandex_status": application.yandex_status,
        "yandex_driver_id": application.yandex_driver_id,
        "yandex_vehicle_id": application.yandex_vehicle_id,
        "yandex_error": application.yandex_error,
        "sent_to_yandex_at": _serialize_datetime(application.sent_to_yandex_at),
        "created_at": _serialize_datetime(application.created_at),
        "updated_at": _serialize_datetime(application.updated_at),
    }


def _serialize_chat(driver: Driver, application: Application | None, messages, documents, events) -> dict[str, object]:
    return {
        "driver": _serialize_driver(driver),
        "application": _serialize_application(application),
        "messages": _serialize_messages(messages),
        "documents": _serialize_documents(documents),
        "conversation_events": _serialize_events(events),
    }


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


@router.get("/export/chat/{phone}")
def debug_export_chat(phone: str, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = _get_driver_or_404(db, phone)
    application = db.scalar(select(Application).where(Application.driver_id == driver.id))
    messages = db.scalars(select(Message).where(Message.driver_id == driver.id).order_by(Message.created_at)).all()
    documents = db.scalars(select(Document).where(Document.driver_id == driver.id).order_by(Document.created_at)).all()
    events = db.scalars(select(ConversationEvent).where(ConversationEvent.driver_id == driver.id).order_by(ConversationEvent.created_at)).all()
    return {
        "status": "ok",
        "exported_at": datetime.utcnow().isoformat(),
        "phone": driver.whatsapp_phone,
        **_serialize_chat(driver, application, messages, documents, events),
    }


@router.get("/export/chats")
def debug_export_chats(
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(Driver).order_by(Driver.updated_at.desc(), Driver.id.desc())
    if since:
        query = query.where(Driver.updated_at >= since)
    if until:
        query = query.where(Driver.updated_at <= until)
    drivers = db.scalars(query.limit(max(1, min(limit, 1000)))).all()
    exports: list[dict[str, object]] = []
    for driver in drivers:
        application = db.scalar(select(Application).where(Application.driver_id == driver.id))
        messages = db.scalars(select(Message).where(Message.driver_id == driver.id).order_by(Message.created_at)).all()
        documents = db.scalars(select(Document).where(Document.driver_id == driver.id).order_by(Document.created_at)).all()
        events = db.scalars(select(ConversationEvent).where(ConversationEvent.driver_id == driver.id).order_by(ConversationEvent.created_at)).all()
        exports.append(_serialize_chat(driver, application, messages, documents, events))
    return {
        "status": "ok",
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(exports),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "limit": limit,
        "chats": exports,
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


@router.post("/yandex/lookup")
def debug_yandex_lookup(payload: DebugYandexLookupRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    driver = get_or_create_driver(db, payload.phone)
    try:
        profile = yandex.find_and_sync_existing_driver(db, driver, payload.lookup)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    application = db.scalar(select(Application).where(Application.driver_id == driver.id))
    return {
        "status": "ok",
        "found": bool(profile),
        "phone": driver.whatsapp_phone,
        "driver_id": driver.id,
        "driver_state": driver.state,
        "full_name": driver.full_name,
        "driver_phone": driver.phone,
        "iin": driver.iin,
        "yandex_driver_id": application.yandex_driver_id if application else None,
        "yandex_vehicle_id": application.yandex_vehicle_id if application else None,
    }
