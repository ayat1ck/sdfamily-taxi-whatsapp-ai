from __future__ import annotations

import html

import httpx
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.utils.logger import get_logger
from app.messages.service import create_message

logger = get_logger(__name__)


def _format_manager_alert(manager_alert: dict[str, object]) -> str:
    phone = html.escape(str(manager_alert.get("phone") or ""))
    name = html.escape(str(manager_alert.get("name") or "Без имени"))
    reason = html.escape(str(manager_alert.get("reason") or "manager_requested"))
    admin_url = html.escape(str(manager_alert.get("admin_url") or ""))
    last_messages = manager_alert.get("last_messages") or []
    if isinstance(last_messages, list):
        rendered_messages = "\n".join(f"- {html.escape(str(item))}" for item in last_messages[-5:] if item)
    else:
        rendered_messages = ""
    parts = [
        "<b>Нужен менеджер</b>",
        f"<b>Телефон:</b> {phone}",
        f"<b>Имя:</b> {name}",
        f"<b>Причина:</b> {reason}",
    ]
    if rendered_messages:
        parts.append(f"<b>Последние сообщения:</b>\n{rendered_messages}")
    if admin_url:
        parts.append(f'<a href="{admin_url}">Открыть чат в админке</a>')
    return "\n\n".join(parts)


def _send_telegram_manager_alert(manager_alert: dict[str, object]) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_manager_chat_id:
        logger.warning("Telegram manager alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_MANAGER_CHAT_ID is missing")
        return False
    url = f"{settings.telegram_api_base_url.rstrip('/')}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_manager_chat_id,
        "text": _format_manager_alert(manager_alert),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.exception("Failed to send Telegram manager alert: %s", exc)
        return False


def notify_manager_stub(db, driver, manager_alert: dict[str, object] | None) -> None:
    if db is None or driver is None or not manager_alert:
        return
    logger.info("manager_alert=%s", manager_alert)
    context = dict(driver.support_context_json or {})
    context["manager_notification_pending"] = True
    context["manager_alert"] = manager_alert
    context["manager_notification_channel"] = "telegram"
    context["manager_notification_sent"] = _send_telegram_manager_alert(manager_alert)
    driver.support_context_json = context
    try:
        flag_modified(driver, "support_context_json")
    except Exception:
        pass


def save_reply_events(db, driver, reply) -> None:
    if db is None or driver is None:
        return
    for event in getattr(reply, "events", []) or []:
        create_message(
            db,
            driver=driver,
            direction="outgoing",
            sender_type="bot",
            message_type="event",
            text=event.get("type"),
            raw_payload=event,
            delivery_status="sent",
        )
