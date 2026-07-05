from __future__ import annotations

from app.utils.logger import get_logger
from app.messages.service import create_message

logger = get_logger(__name__)


def notify_manager_stub(db, driver, manager_alert: dict[str, object] | None) -> None:
    if db is None or driver is None or not manager_alert:
        return
    logger.info("manager_alert=%s", manager_alert)
    context = dict(driver.support_context_json or {})
    context["manager_notification_pending"] = True
    context["manager_alert"] = manager_alert
    driver.support_context_json = context


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
