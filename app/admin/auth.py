import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.models import AdminAccount
from app.config import get_settings
from app.database.session import get_db

LOGIN_ATTEMPTS: dict[str, deque[datetime]] = defaultdict(deque)


def hash_password(password: str, salt: str | None = None) -> str:
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), resolved_salt.encode("utf-8"), 120000)
    return f"{resolved_salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, stored_digest = password_hash.split("$", 1)
    except ValueError:
        return False
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, f"{salt}${stored_digest}")


def ensure_default_admin_account(db: Session) -> AdminAccount:
    settings = get_settings()
    account = db.scalar(select(AdminAccount).where(AdminAccount.username == settings.admin_username))
    password_hash = hash_password(settings.admin_password)
    if account:
        account.password_hash = password_hash
        account.is_active = True
    else:
        account = AdminAccount(
            username=settings.admin_username,
            password_hash=password_hash,
            is_active=True,
        )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_login_rate_limit(request: Request) -> None:
    settings = get_settings()
    key = _client_key(request)
    now = datetime.utcnow()
    attempts = LOGIN_ATTEMPTS[key]
    while attempts and (now - attempts[0]).total_seconds() > settings.admin_login_rate_limit_window_seconds:
        attempts.popleft()
    if len(attempts) >= settings.admin_login_rate_limit_attempts:
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Повторите позже.")


def register_failed_login(request: Request) -> None:
    LOGIN_ATTEMPTS[_client_key(request)].append(datetime.utcnow())


def clear_failed_logins(request: Request) -> None:
    LOGIN_ATTEMPTS.pop(_client_key(request), None)


def login_admin(request: Request, account: AdminAccount) -> str:
    csrf_token = secrets.token_urlsafe(24)
    request.session["admin_authenticated"] = True
    request.session["admin_username"] = account.username
    request.session["csrf_token"] = csrf_token
    request.session["admin_login_at"] = datetime.utcnow().isoformat()
    account.last_login_at = datetime.utcnow()
    return csrf_token


def logout_admin(request: Request, response: Response | None = None) -> None:
    request.session.clear()
    if response is not None:
        response.delete_cookie(get_settings().admin_session_cookie_name)


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminAccount:
    username = request.session.get("admin_username")
    authenticated = request.session.get("admin_authenticated")
    if not username or not authenticated:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    account = db.scalar(select(AdminAccount).where(AdminAccount.username == username, AdminAccount.is_active.is_(True)))
    if not account:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return account


def require_admin_api(request: Request, db: Session = Depends(get_db)) -> AdminAccount:
    username = request.session.get("admin_username")
    authenticated = request.session.get("admin_authenticated")
    if not username or not authenticated:
        raise HTTPException(status_code=401, detail="Admin login required")
    account = db.scalar(select(AdminAccount).where(AdminAccount.username == username, AdminAccount.is_active.is_(True)))
    if not account:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Admin login required")
    return account


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def admin_template_context(request: Request, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "admin_username": request.session.get("admin_username"),
        **extra,
    }
