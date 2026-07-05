from __future__ import annotations

from app.config import settings
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.response import StructuredReply
from app.messages.models import Message


class ManagerHandoffFlow:
    def __init__(self) -> None:
        self.bus = EventBus()

    def should_handoff(self, driver, message) -> bool:
        text = (message.text or "").lower()
        return any(token in text for token in ("оператор", "менеджер", "деньги", "тариф", "блокировк", "жалоб", "смз", "яндекс"))

    def _last_messages(self, db, driver, limit: int = 5) -> list[str]:
        if db is None:
            return []
        try:
            rows = (
                db.query(Message)
                .filter(Message.driver_id == driver.id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            return [row.text or row.message_type or "" for row in reversed(rows)]
        except Exception:
            return []

    def _admin_url(self, driver) -> str:
        base = (settings.admin_base_url or settings.app_host or "").rstrip("/")
        return f"{base}/admin/chats/{driver.id}"

    def handle(self, db, driver, application, message, reason: str | None = None) -> StructuredReply:
        context = dict(driver.support_context_json or {})
        payload_reason = reason or (message.text or "").strip() or "manager_requested"
        ticket = {
            "reason": payload_reason,
            "status": "open",
            "source": "dialog_v2",
        }
        alert = {
            "phone": driver.phone or driver.whatsapp_phone,
            "name": driver.full_name,
            "reason": payload_reason,
            "last_messages": self._last_messages(db, driver),
            "admin_url": self._admin_url(driver),
        }
        context["dialog_mode"] = "manual"
        context["manager_ticket"] = ticket
        context["manager_alert"] = alert
        driver.support_context_json = context
        driver.dialog_mode = "manual"
        driver.requires_attention = True

        reply = StructuredReply(
            text="Понял, передал менеджеру.",
            requires_manager=True,
            flow="manager",
            state="manager",
            manager_alert=alert,
            metadata={"intent": "manager", "ticket": ticket, "alert": alert},
        )
        self.bus.emit(db, driver, "manager_handoff", {"reason": payload_reason, "alert": alert}, reply=reply)
        self.bus.emit(db, driver, "support_ticket_created", {"reason": payload_reason, "ticket": ticket}, reply=reply)

        return reply
