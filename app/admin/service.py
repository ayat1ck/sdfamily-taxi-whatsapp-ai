from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.applications.models import Application
from app.applications.service import set_application_status
from app.audit.service import create_audit_log
from app.conversation_events.models import ConversationEvent
from app.conversation_events.service import create_conversation_event
from app.dialog.engine import DialogueEngine
from app.documents.models import Document
from app.drivers.models import Driver
from app.integration_jobs.models import IntegrationJob
from app.integration_jobs.service import create_integration_job, finish_integration_job
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.yandex.service import YandexSubmissionService
from app.messages.models import Message
from app.messages.service import create_message
from app.utils.validators import normalize_plate_number
from app.vehicles.models import Vehicle
from app.whatsapp.sender import WhatsAppSender


VALID_DIALOG_MODES = {"bot_active", "manual", "paused", "closed"}
APPLICATION_FIELDS = {
    "status",
    "yandex_status",
    "yandex_error",
}
DRIVER_FIELDS = {
    "full_name",
    "last_name",
    "first_name",
    "middle_name",
    "phone",
    "city",
    "address",
    "iin",
    "birth_date",
    "driving_experience_since",
    "driver_license_number",
    "driver_license_issue_date",
    "driver_license_expires_at",
    "executor_type",
    "employment_type",
    "hired_at",
    "has_personal_car",
    "existing_vehicle_lookup",
    "is_hearing_impaired",
    "assigned_manager_name",
    "admin_notes",
    "admin_tags",
}
VEHICLE_FIELDS = {
    "brand",
    "model",
    "year",
    "plate_number",
    "color",
    "vin",
}


@dataclass
class ChatFilters:
    search: str = ""
    status: str = ""
    state: str = ""
    dialog_mode: str = ""
    requires_attention: str = ""
    duplicate: str = ""
    yandex_status: str = ""
    has_documents: str = ""


def get_driver_application(driver: Driver) -> Application | None:
    return driver.applications[0] if driver.applications else None


def get_driver_vehicle(driver: Driver) -> Vehicle | None:
    return driver.vehicle


def repair_driver_message_roles(db: Session, driver: Driver) -> None:
    changed = False
    for message in driver.messages:
        if message.direction == "outgoing" and message.sender_type == "customer":
            message.sender_type = "bot"
            db.add(message)
            changed = True
        elif message.direction == "incoming" and message.sender_type in {"bot", "manager"}:
            message.sender_type = "customer"
            db.add(message)
            changed = True
    if changed:
        db.flush()


def get_driver_or_404(db: Session, driver_id: int) -> Driver:
    driver = db.get(
        Driver,
        driver_id,
        options=[
            selectinload(Driver.messages),
            selectinload(Driver.documents),
            selectinload(Driver.vehicle),
            selectinload(Driver.applications),
            selectinload(Driver.conversation_events),
        ],
    )
    if not driver:
        raise ValueError("Driver not found")
    repair_driver_message_roles(db, driver)
    return driver


def get_application_or_404(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if not application:
        raise ValueError("Application not found")
    return application


def driver_query(filters: ChatFilters) -> Select[tuple[Driver]]:
    query = (
        select(Driver)
        .options(
            selectinload(Driver.messages),
            selectinload(Driver.documents),
            selectinload(Driver.vehicle),
            selectinload(Driver.applications),
            selectinload(Driver.conversation_events),
        )
        .outerjoin(Application, Application.driver_id == Driver.id)
        .outerjoin(Vehicle, Vehicle.driver_id == Driver.id)
    )
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        query = query.where(
            or_(
                Driver.whatsapp_phone.ilike(pattern),
                Driver.full_name.ilike(pattern),
                Driver.iin.ilike(pattern),
                Driver.driver_license_number.ilike(pattern),
                Driver.admin_notes.ilike(pattern),
                Vehicle.plate_number.ilike(pattern),
                Vehicle.brand.ilike(pattern),
                Vehicle.model.ilike(pattern),
            )
        )
    if filters.status:
        query = query.where(Application.status == filters.status)
    if filters.state:
        query = query.where(Driver.state == filters.state)
    if filters.dialog_mode:
        query = query.where(Driver.dialog_mode == filters.dialog_mode)
    if filters.requires_attention == "1":
        query = query.where(Driver.requires_attention.is_(True))
    if filters.duplicate == "1":
        query = query.where(Driver.duplicate_flag.is_(True))
    if filters.yandex_status:
        query = query.where(Application.yandex_status == filters.yandex_status)
    if filters.has_documents == "1":
        query = query.where(
            select(func.count(Document.id)).where(Document.driver_id == Driver.id).scalar_subquery() > 0
        )
    return query.order_by(Driver.last_message_at.desc().nullslast(), Driver.updated_at.desc())


def list_drivers(db: Session, filters: ChatFilters) -> list[Driver]:
    return list(db.scalars(driver_query(filters)).unique().all())


def dashboard_stats(db: Session) -> dict[str, Any]:
    today = datetime.utcnow() - timedelta(days=1)
    recent_drivers = list(
        db.scalars(
            select(Driver)
            .options(selectinload(Driver.messages), selectinload(Driver.applications))
            .order_by(Driver.last_message_at.desc().nullslast())
            .limit(10)
        ).all()
    )
    recent_events = list(
        db.scalars(select(ConversationEvent).order_by(ConversationEvent.created_at.desc()).limit(12)).all()
    )
    integration_jobs = list(
        db.scalars(select(IntegrationJob).order_by(IntegrationJob.created_at.desc()).limit(12)).all()
    )
    return {
        "new_today": db.scalar(select(func.count(Driver.id)).where(Driver.created_at >= today)) or 0,
        "active": db.scalar(select(func.count(Driver.id)).where(Driver.dialog_mode != "closed")) or 0,
        "waiting_documents": db.scalar(select(func.count(Application.id)).where(Application.status == "waiting_documents"))
        or 0,
        "duplicates": db.scalar(select(func.count(Driver.id)).where(Driver.duplicate_flag.is_(True))) or 0,
        "errors": db.scalar(select(func.count(Driver.id)).where(Driver.requires_attention.is_(True))) or 0,
        "deletions": db.scalar(select(func.count(Application.id)).where(Application.status == "deletion_requested")) or 0,
        "sent_to_yandex": db.scalar(select(func.count(Application.id)).where(Application.status == "sent_to_yandex"))
        or 0,
        "completed": db.scalar(select(func.count(Application.id)).where(Application.status == "completed")) or 0,
        "recent_drivers": recent_drivers,
        "recent_events": recent_events,
        "recent_jobs": integration_jobs,
    }


def serialize_message(message: Message) -> dict[str, Any]:
    return {
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
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "is_read_by_admin": message.is_read_by_admin,
    }


def serialize_driver_summary(driver: Driver) -> dict[str, Any]:
    application = get_driver_application(driver)
    messages = sorted(driver.messages, key=lambda item: item.created_at or datetime.min)
    last_message = messages[-1] if messages else None
    return {
        "id": driver.id,
        "phone": driver.whatsapp_phone,
        "full_name": driver.full_name,
        "status": application.status if application else None,
        "state": driver.state,
        "dialog_mode": driver.dialog_mode,
        "last_message_text": last_message.text if last_message else None,
        "last_message_at": driver.last_message_at.isoformat() if driver.last_message_at else None,
        "unread_count": driver.unread_count,
        "requires_attention": driver.requires_attention,
        "duplicate_flag": driver.duplicate_flag,
        "assigned_manager_name": driver.assigned_manager_name,
    }


def mark_messages_read(db: Session, driver: Driver) -> None:
    now = datetime.utcnow()
    for message in driver.messages:
        if message.direction == "incoming" and not message.is_read_by_admin:
            message.is_read_by_admin = True
            message.read_at = now
            db.add(message)
    driver.unread_count = 0
    db.add(driver)
    db.flush()


def set_driver_dialog_mode(db: Session, driver: Driver, mode: str) -> None:
    if mode not in VALID_DIALOG_MODES:
        raise ValueError("Invalid dialog mode")
    old_mode = driver.dialog_mode
    driver.dialog_mode = mode
    driver.requires_attention = mode in {"manual", "paused"}
    now = datetime.utcnow()
    if mode == "paused":
        driver.paused_at = now
    if mode == "closed":
        driver.closed_at = now
    if mode == "bot_active":
        driver.paused_at = None
        driver.closed_at = None
    db.add(driver)
    db.flush()
    create_conversation_event(db, driver, f"switched_to_{mode}", {"old_mode": old_mode, "new_mode": mode})
    application = get_driver_application(driver)
    create_audit_log(
        db,
        driver=driver,
        application=application,
        field_name="dialog_mode",
        old_value=old_mode,
        new_value=mode,
        action_type="dialog_mode_changed",
    )


def assign_manager_name(db: Session, driver: Driver, name: str) -> None:
    old_name = driver.assigned_manager_name
    driver.assigned_manager_name = name.strip() or None
    db.add(driver)
    db.flush()
    create_audit_log(
        db,
        driver=driver,
        application=get_driver_application(driver),
        field_name="assigned_manager_name",
        old_value=old_name,
        new_value=driver.assigned_manager_name,
        action_type="assigned_manager_updated",
    )


def send_manual_reply(db: Session, driver: Driver, text: str) -> Message:
    sender = WhatsAppSender()
    application = get_driver_application(driver)
    request_payload = {"phone": driver.whatsapp_phone, "text": text}
    job = create_integration_job(
        db,
        provider="whatsapp",
        action="manual_reply",
        status="pending",
        request_payload=request_payload,
        application=application,
        driver=driver,
    )
    try:
        response_payload = sender.send_text(driver.whatsapp_phone, text)
        finish_integration_job(db, job, "sent", response_payload=response_payload)
        provider_message_id = None
        messages_payload = response_payload.get("messages")
        if isinstance(messages_payload, list) and messages_payload:
            provider_message_id = messages_payload[0].get("id")
        message = create_message(
            db,
            driver=driver,
            direction="outgoing",
            sender_type="manager",
            message_type="text",
            text=text,
            raw_payload=response_payload if isinstance(response_payload, dict) else None,
            provider_message_id=provider_message_id,
            delivery_status="sent",
        )
        create_conversation_event(db, driver, "manual_reply_sent", {"text": text})
        return message
    except Exception as exc:
        driver.requires_attention = True
        db.add(driver)
        finish_integration_job(db, job, "error", error_text=str(exc))
        create_message(
            db,
            driver=driver,
            direction="outgoing",
            sender_type="manager",
            message_type="text",
            text=text,
            delivery_status="error",
            error_text=str(exc),
            raw_payload={"error": str(exc)},
        )
        raise


def update_application_snapshot(db: Session, application: Application, payload: dict[str, Any]) -> None:
    driver = db.get(Driver, application.driver_id)
    if not driver:
        raise ValueError("Driver not found")
    vehicle_payload = payload.get("vehicle", {})
    vehicle = driver.vehicle
    if vehicle_payload and not vehicle:
        vehicle = Vehicle(driver_id=driver.id)

    for field_name, new_value in payload.get("driver", {}).items():
        if field_name not in DRIVER_FIELDS:
            continue
        old_value = getattr(driver, field_name)
        if old_value == new_value:
            continue
        setattr(driver, field_name, new_value)
        create_audit_log(
            db,
            driver=driver,
            application=application,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            action_type="driver_field_updated",
        )

    for field_name, new_value in vehicle_payload.items():
        if field_name not in VEHICLE_FIELDS:
            continue
        if vehicle is None:
            vehicle = Vehicle(driver_id=driver.id)
        if field_name == "plate_number" and isinstance(new_value, str):
            new_value = normalize_plate_number(new_value)
        old_value = getattr(vehicle, field_name)
        if old_value == new_value:
            continue
        setattr(vehicle, field_name, new_value)
        create_audit_log(
            db,
            driver=driver,
            application=application,
            field_name=f"vehicle.{field_name}",
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            action_type="vehicle_field_updated",
        )

    for field_name, new_value in payload.get("application", {}).items():
        if field_name not in APPLICATION_FIELDS:
            continue
        old_value = getattr(application, field_name)
        if old_value == new_value:
            continue
        setattr(application, field_name, new_value)
        create_audit_log(
            db,
            driver=driver,
            application=application,
            field_name=f"application.{field_name}",
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            action_type="application_field_updated",
        )

    db.add(driver)
    if vehicle is not None:
        db.add(vehicle)
    db.add(application)
    db.flush()


def restart_application(db: Session, application: Application) -> None:
    driver = get_driver_or_404(db, application.driver_id)
    engine = DialogueEngine()
    engine._reset_registration(db, driver, application)
    driver.requires_attention = False
    driver.duplicate_flag = False
    driver.dialog_mode = "bot_active"
    db.add(driver)
    db.flush()
    create_conversation_event(db, driver, "registration_restarted")
    create_audit_log(
        db,
        driver=driver,
        application=application,
        field_name="application.restart",
        old_value=application.status,
        new_value="collecting_data",
        action_type="application_restarted",
    )


def set_duplicate_flag(db: Session, application: Application, flag: bool) -> None:
    driver = get_driver_or_404(db, application.driver_id)
    old_value = driver.duplicate_flag
    driver.duplicate_flag = flag
    driver.requires_attention = flag
    if flag:
        set_application_status(db, application, "duplicate_rejected", yandex_status="duplicate_rejected")
        create_conversation_event(db, driver, "duplicate_marked_manual")
    else:
        create_conversation_event(db, driver, "duplicate_cleared_manual")
    db.add(driver)
    db.flush()
    create_audit_log(
        db,
        driver=driver,
        application=application,
        field_name="duplicate_flag",
        old_value=str(old_value),
        new_value=str(flag),
        action_type="duplicate_flag_updated",
    )


def request_deletion(db: Session, application: Application, reason: str | None = None) -> None:
    driver = get_driver_or_404(db, application.driver_id)
    old_status = application.status
    set_application_status(db, application, "deletion_requested", yandex_status="deletion_requested", yandex_error=reason)
    driver.deletion_requested_at = datetime.utcnow()
    driver.requires_attention = True
    db.add(driver)
    db.flush()
    create_conversation_event(db, driver, "deletion_requested", {"reason": reason})
    create_audit_log(
        db,
        driver=driver,
        application=application,
        field_name="application.status",
        old_value=old_status,
        new_value="deletion_requested",
        action_type="deletion_requested",
    )


def submit_to_yandex(db: Session, application: Application) -> Application:
    driver = get_driver_or_404(db, application.driver_id)
    service = YandexSubmissionService()
    preview = service.preview(driver)
    job = create_integration_job(
        db,
        provider="yandex",
        action="submit_driver",
        status="pending",
        request_payload=preview,
        application=application,
        driver=driver,
    )
    try:
        result = service.submit(db, driver, application)
        finish_integration_job(
            db,
            job,
            "sent",
            response_payload={
                "status": result.yandex_status,
                "driver_id": result.yandex_driver_id,
                "vehicle_id": result.yandex_vehicle_id,
            },
        )
        driver.requires_attention = False
        db.add(driver)
        db.flush()
        create_conversation_event(db, driver, "submitted_to_yandex")
        return result
    except Exception as exc:
        finish_integration_job(db, job, "error", error_text=str(exc))
        driver.requires_attention = True
        db.add(driver)
        db.flush()
        create_conversation_event(db, driver, "yandex_failed", {"error": str(exc)})
        raise


def sync_google(db: Session, application: Application) -> dict[str, Any]:
    driver = get_driver_or_404(db, application.driver_id)
    sheets = GoogleSheetsClient()
    drive = GoogleDriveClient()
    job = create_integration_job(
        db,
        provider="google",
        action="sync_application",
        status="pending",
        request_payload={"driver_id": driver.id, "application_id": application.id},
        application=application,
        driver=driver,
    )
    try:
        sheets.sync_application(driver, application)
        drive_result = drive.upload_application_snapshot(driver, application)
        finish_integration_job(db, job, "sent", response_payload=drive_result)
        create_conversation_event(db, driver, "google_synced", drive_result)
        return drive_result
    except Exception as exc:
        finish_integration_job(db, job, "error", error_text=str(exc))
        driver.requires_attention = True
        db.add(driver)
        db.flush()
        raise


def list_applications(db: Session, status_filter: str = "") -> list[Application]:
    query = select(Application).options(selectinload(Application.driver))
    if status_filter:
        query = query.where(Application.status == status_filter)
    return list(db.scalars(query.order_by(Application.updated_at.desc())).all())


def list_events(db: Session, limit: int = 100) -> list[ConversationEvent]:
    return list(db.scalars(select(ConversationEvent).order_by(ConversationEvent.created_at.desc()).limit(limit)).all())


def list_audit_logs(db: Session, limit: int = 100) -> list[Any]:
    from app.audit.models import ApplicationAuditLog

    return list(db.scalars(select(ApplicationAuditLog).order_by(ApplicationAuditLog.created_at.desc()).limit(limit)).all())


def list_integration_jobs(db: Session, limit: int = 100) -> list[IntegrationJob]:
    return list(db.scalars(select(IntegrationJob).order_by(IntegrationJob.created_at.desc()).limit(limit)).all())


def distinct_values(values: Iterable[str | None]) -> list[str]:
    return sorted({value for value in values if value})
