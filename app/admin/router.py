from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.admin.auth import (
    admin_template_context,
    check_login_rate_limit,
    clear_failed_logins,
    ensure_default_admin_account,
    get_current_admin,
    get_csrf_token,
    login_admin,
    register_failed_login,
    require_admin_api,
    verify_csrf,
    verify_password,
)
from app.config import get_settings
from app.admin.service import (
    ChatFilters,
    assign_manager_name,
    dashboard_stats,
    distinct_values,
    get_application_or_404,
    get_driver_application,
    get_driver_or_404,
    list_applications,
    list_audit_logs,
    list_drivers,
    list_events,
    list_integration_jobs,
    mark_messages_read,
    request_deletion,
    restart_application,
    send_manual_reply,
    serialize_driver_summary,
    serialize_message,
    set_driver_dialog_mode,
    set_duplicate_flag,
    submit_to_yandex,
    sync_google,
    update_application_snapshot,
)
from app.applications.models import Application
from app.database.session import get_db
from app.documents.models import Document
from app.drivers.models import Driver
from app.integration_jobs.models import IntegrationJob

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class ManualReplyRequest(BaseModel):
    text: str = Field(min_length=1)


class SwitchModeRequest(BaseModel):
    mode: str


class AssignManagerRequest(BaseModel):
    name: str


class RequestDeletionRequest(BaseModel):
    reason: str | None = None


class ApplicationPatchRequest(BaseModel):
    driver: dict[str, Any] = Field(default_factory=dict)
    vehicle: dict[str, Any] = Field(default_factory=dict)
    application: dict[str, Any] = Field(default_factory=dict)


def _dashboard_context(db: Session, request: Request) -> dict[str, Any]:
    stats = dashboard_stats(db)
    return admin_template_context(request, nav="dashboard", stats=stats)


def _chat_filters_from_request(request: Request) -> ChatFilters:
    params = request.query_params
    return ChatFilters(
        search=params.get("search", ""),
        status=params.get("status", ""),
        state=params.get("state", ""),
        dialog_mode=params.get("dialog_mode", ""),
        requires_attention=params.get("requires_attention", ""),
        duplicate=params.get("duplicate", ""),
        yandex_status=params.get("yandex_status", ""),
        has_documents=params.get("has_documents", ""),
    )


def _chat_page_context(db: Session, request: Request, selected_driver: Driver | None = None) -> dict[str, Any]:
    filters = _chat_filters_from_request(request)
    drivers = list_drivers(db, filters)
    selected_application = get_driver_application(selected_driver) if selected_driver else None
    statuses = distinct_values(
        app.status for driver in drivers for app in driver.applications
    )
    yandex_statuses = distinct_values(
        app.yandex_status for driver in drivers for app in driver.applications
    )
    states = distinct_values(driver.state for driver in drivers)
    return admin_template_context(
        request,
        nav="chats",
        filters=filters,
        drivers=drivers,
        selected_driver=selected_driver,
        selected_application=selected_application,
        statuses=statuses,
        states=states,
        yandex_statuses=yandex_statuses,
    )


def _application_page_context(db: Session, request: Request, selected_application: Application | None = None) -> dict[str, Any]:
    status_filter = request.query_params.get("status", "")
    applications = list_applications(db, status_filter=status_filter)
    statuses = distinct_values(application.status for application in applications)
    return admin_template_context(
        request,
        nav="applications",
        applications=applications,
        selected_application=selected_application,
        status_filter=status_filter,
        statuses=statuses,
    )


@router.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ensure_default_admin_account(db)
    if request.session.get("admin_authenticated"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("login.html", admin_template_context(request, error=None))


@router.post("/login")
def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ensure_default_admin_account(db)
    check_login_rate_limit(request)
    from app.admin.models import AdminAccount

    candidate = db.scalar(select(AdminAccount).where(AdminAccount.username == username))
    if not candidate or not verify_password(password, candidate.password_hash):
        register_failed_login(request)
        return templates.TemplateResponse(
            "login.html",
            admin_template_context(request, error="Неверный логин или пароль."),
            status_code=400,
        )
    clear_failed_logins(request)
    login_admin(request, candidate)
    db.add(candidate)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def admin_logout(request: Request, csrf_token: str = Form(...), _admin=Depends(get_current_admin)) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    request.session.clear()
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(get_settings().admin_session_cookie_name)
    return response


@router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", _dashboard_context(db, request))


@router.get("/chats", response_class=HTMLResponse)
def admin_chats(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("chats.html", _chat_page_context(db, request))


@router.get("/chats/{driver_id}", response_class=HTMLResponse)
def admin_chat_detail(
    driver_id: int,
    request: Request,
    _admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        driver = get_driver_or_404(db, driver_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse("chats.html", _chat_page_context(db, request, selected_driver=driver))


@router.get("/applications", response_class=HTMLResponse)
def admin_applications(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("applications.html", _application_page_context(db, request))


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def admin_application_detail(
    application_id: int,
    request: Request,
    _admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        application = db.get(
            Application,
            application_id,
            options=[selectinload(Application.driver).selectinload(Driver.vehicle), selectinload(Application.driver).selectinload(Driver.documents)],
        )
        if not application:
            raise ValueError("Application not found")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse("applications.html", _application_page_context(db, request, selected_application=application))


@router.get("/documents", response_class=HTMLResponse)
def admin_documents(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    documents = list(db.scalars(select(Document).order_by(Document.created_at.desc()).limit(100)).all())
    return templates.TemplateResponse(
        "documents.html",
        admin_template_context(request, nav="documents", documents=documents),
    )


@router.get("/integrations", response_class=HTMLResponse)
def admin_integrations(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    jobs = list_integration_jobs(db)
    return templates.TemplateResponse(
        "integrations.html",
        admin_template_context(request, nav="integrations", jobs=jobs),
    )


@router.get("/audit", response_class=HTMLResponse)
def admin_audit(request: Request, _admin=Depends(get_current_admin), db: Session = Depends(get_db)) -> HTMLResponse:
    logs = list_audit_logs(db)
    events = list_events(db, limit=50)
    return templates.TemplateResponse(
        "audit.html",
        admin_template_context(request, nav="audit", logs=logs, events=events),
    )


@router.get("/api/dashboard")
def api_dashboard(_admin=Depends(require_admin_api), db: Session = Depends(get_db)) -> dict[str, Any]:
    stats = dashboard_stats(db)
    return {
        key: value
        for key, value in stats.items()
        if key not in {"recent_drivers", "recent_events", "recent_jobs"}
    }


@router.get("/api/chats")
def api_chats(
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    drivers = list_drivers(db, _chat_filters_from_request(request))
    return {"items": [serialize_driver_summary(driver) for driver in drivers]}


@router.get("/api/chats/{driver_id}/messages")
def api_chat_messages(driver_id: int, _admin=Depends(require_admin_api), db: Session = Depends(get_db)) -> dict[str, Any]:
    driver = get_driver_or_404(db, driver_id)
    mark_messages_read(db, driver)
    db.commit()
    return {
        "driver": serialize_driver_summary(driver),
        "messages": [serialize_message(message) for message in sorted(driver.messages, key=lambda item: item.created_at)],
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "event_payload": event.event_payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in sorted(driver.conversation_events, key=lambda item: item.created_at)
        ],
    }


@router.post("/api/chats/{driver_id}/reply")
async def api_chat_reply(
    driver_id: int,
    request: Request,
    payload: ManualReplyRequest,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    message = send_manual_reply(db, driver, payload.text)
    db.commit()
    return {"status": "ok", "message": serialize_message(message)}


@router.post("/api/chats/{driver_id}/pause")
async def api_chat_pause(
    driver_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    set_driver_dialog_mode(db, driver, "paused")
    application = get_driver_application(driver)
    if application:
        application.status = "manually_paused"
        db.add(application)
    db.commit()
    return {"status": "ok"}


@router.post("/api/chats/{driver_id}/resume")
async def api_chat_resume(
    driver_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    set_driver_dialog_mode(db, driver, "bot_active")
    application = get_driver_application(driver)
    if application and application.status == "manually_paused":
        application.status = "collecting_data"
        db.add(application)
    db.commit()
    return {"status": "ok"}


@router.post("/api/chats/{driver_id}/switch-mode")
async def api_chat_switch_mode(
    driver_id: int,
    request: Request,
    payload: SwitchModeRequest,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    set_driver_dialog_mode(db, driver, payload.mode)
    db.commit()
    return {"status": "ok"}


@router.post("/api/chats/{driver_id}/mark-read")
async def api_chat_mark_read(
    driver_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    mark_messages_read(db, driver)
    db.commit()
    return {"status": "ok"}


@router.post("/api/chats/{driver_id}/assign-manager-name")
async def api_chat_assign_manager(
    driver_id: int,
    request: Request,
    payload: AssignManagerRequest,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    driver = get_driver_or_404(db, driver_id)
    assign_manager_name(db, driver, payload.name)
    db.commit()
    return {"status": "ok"}


@router.patch("/api/applications/{application_id}")
async def api_patch_application(
    application_id: int,
    request: Request,
    payload: ApplicationPatchRequest,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    application = get_application_or_404(db, application_id)
    update_application_snapshot(db, application, payload.model_dump())
    db.commit()
    return {"status": "ok"}


@router.post("/api/applications/{application_id}/restart")
async def api_restart_application(
    application_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    application = get_application_or_404(db, application_id)
    restart_application(db, application)
    db.commit()
    return {"status": "ok"}


@router.post("/api/applications/{application_id}/mark-duplicate")
async def api_mark_duplicate(
    application_id: int,
    request: Request,
    flag: bool = Form(True),
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token") or (await request.form()).get("csrf_token"))
    application = get_application_or_404(db, application_id)
    set_duplicate_flag(db, application, flag)
    db.commit()
    return {"status": "ok"}


@router.post("/api/applications/{application_id}/request-deletion")
async def api_request_deletion(
    application_id: int,
    request: Request,
    payload: RequestDeletionRequest,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    application = get_application_or_404(db, application_id)
    request_deletion(db, application, payload.reason)
    db.commit()
    return {"status": "ok"}


@router.post("/api/applications/{application_id}/submit-yandex")
async def api_submit_yandex(
    application_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    application = get_application_or_404(db, application_id)
    submit_to_yandex(db, application)
    db.commit()
    return {"status": "ok"}


@router.post("/api/applications/{application_id}/sync-google")
async def api_sync_google(
    application_id: int,
    request: Request,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    application = get_application_or_404(db, application_id)
    result = sync_google(db, application)
    db.commit()
    return {"status": "ok", "result": result}


@router.get("/api/documents/{document_id}")
def api_document_view(
    document_id: int,
    _admin=Depends(require_admin_api),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    document = db.get(Document, document_id)
    if not document or not document.file_url:
        raise HTTPException(status_code=404, detail="Document not found")
    return RedirectResponse(document.file_url, status_code=307)


@router.get("/api/events")
def api_events(_admin=Depends(require_admin_api), db: Session = Depends(get_db)) -> dict[str, Any]:
    events = list_events(db)
    return {
        "items": [
            {
                "id": event.id,
                "driver_id": event.driver_id,
                "event_type": event.event_type,
                "event_payload": event.event_payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    }


@router.get("/api/audit")
def api_audit(_admin=Depends(require_admin_api), db: Session = Depends(get_db)) -> dict[str, Any]:
    logs = list_audit_logs(db)
    return {
        "items": [
            {
                "id": log.id,
                "application_id": log.application_id,
                "driver_id": log.driver_id,
                "actor_type": log.actor_type,
                "field_name": log.field_name,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "action_type": log.action_type,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    }


@router.get("/api/integrations")
def api_integrations(_admin=Depends(require_admin_api), db: Session = Depends(get_db)) -> dict[str, Any]:
    jobs = list_integration_jobs(db)
    return {
        "items": [
            {
                "id": job.id,
                "application_id": job.application_id,
                "driver_id": job.driver_id,
                "provider": job.provider,
                "action": job.action,
                "status": job.status,
                "error_text": job.error_text,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
            for job in jobs
        ]
    }
