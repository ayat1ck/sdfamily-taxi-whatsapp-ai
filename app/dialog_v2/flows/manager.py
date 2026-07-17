from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import MANAGER_TRIAGE_BUTTONS, buttons_reply
from app.messages.models import Message
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


TRIAGE_TEXT = (
    "Хорошо. Чем помочь?\n\n"
    "1. Регистрация / подключение в парк\n"
    "2. Уже водитель — нужна помощь\n"
    "3. Сразу связать с менеджером"
)

EXPLICIT_MANAGER_REASONS = {
    "human_requested",
    "manager_requested",
    "manager",
    "оператор",
    "менеджер",
}


def _flag_context(driver) -> None:
    try:
        flag_modified(driver, "support_context_json")
    except Exception:
        pass


class ManagerHandoffFlow:
    def __init__(self) -> None:
        self.bus = EventBus()

    def should_handoff(self, driver, message) -> bool:
        text = normalize_text_token(repair_mojibake(message.text or ""))
        return any(token in text for token in ("оператор", "менеджер", "живой человек", "техподдержка"))

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
        settings = get_settings()
        base = (settings.admin_base_url or settings.app_host or "").rstrip("/")
        return f"{base}/admin/chats/{driver.id}"

    def _handoff_text(self, reason: str | None = None) -> str:
        phone = get_settings().public_site_manager_phone
        reason_hint = ""
        if reason and reason not in {"human_requested", "manager_requested", "support"}:
            reason_hint = " Можете сразу одним сообщением уточнить детали."
        return (
            "Передал ваш запрос менеджеру.\n\n"
            "Обычно отвечают в рабочее время в течение 15–60 минут.\n"
            f"Если нужно срочно — напишите или позвоните: {phone}.{reason_hint}\n\n"
            "Пока ждёте, можно просто описать проблему здесь — менеджер увидит переписку."
        )

    def _wants_triage(self, reason: str | None, message) -> bool:
        payload = normalize_text_token(repair_mojibake(reason or ""))
        text = normalize_text_token(repair_mojibake((message.text if message else None) or ""))
        if payload in EXPLICIT_MANAGER_REASONS or text in EXPLICIT_MANAGER_REASONS:
            return True
        return any(token in text for token in ("оператор", "менеджер", "живой человек", "техподдержка"))

    def offer_triage(self, driver, reason: str | None = None) -> StructuredReply:
        context = dict(driver.support_context_json or {})
        context["pending_menu"] = "manager_triage"
        context["manager_triage_reason"] = reason or "human_requested"
        driver.support_context_json = context
        _flag_context(driver)
        return buttons_reply(
            TRIAGE_TEXT,
            MANAGER_TRIAGE_BUTTONS,
            flow="manager",
            state="manager_triage",
            next_flow="manager",
            flow_state="manager_triage",
            metadata={"intent": "manager_triage", "reason": reason or "human_requested"},
        )

    def handle_triage_choice(self, db, driver, application, message) -> StructuredReply:
        text = normalize_text_token(repair_mojibake(message.text or ""))
        context = dict(driver.support_context_json or {})
        reason = context.get("manager_triage_reason") or "human_requested"
        context.pop("pending_menu", None)
        context.pop("manager_triage_reason", None)
        driver.support_context_json = context
        _flag_context(driver)

        if text in {"mgr_register", "1", "регистрация", "подключение", "подключ"}:
            from app.dialog_v2.flows.registration import RegistrationFlow

            return RegistrationFlow().start(db, driver, application)

        if text in {"mgr_help", "2", "уже водитель", "помощь", "help"}:
            from app.dialog_v2.flows.existing_driver import ExistingDriverFlow

            return ExistingDriverFlow().handle(db, driver, application, message)

        return self.handle(db, driver, application, message, reason=reason, skip_triage=True)

    def handle(
        self,
        db,
        driver,
        application,
        message,
        reason: str | None = None,
        *,
        skip_triage: bool = False,
    ) -> StructuredReply:
        if not skip_triage and self._wants_triage(reason, message):
            return self.offer_triage(driver, reason=reason or "human_requested")

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
        context["dialog_mode"] = "bot_active"
        context["manager_ticket"] = ticket
        context["manager_alert"] = alert
        context.pop("pending_menu", None)
        driver.support_context_json = context
        _flag_context(driver)
        driver.dialog_mode = "bot_active"
        driver.requires_attention = True

        reply = StructuredReply(
            text=self._handoff_text(payload_reason),
            requires_manager=True,
            flow="manager",
            state="manager",
            manager_alert=alert,
            metadata={"intent": "manager", "ticket": ticket, "alert": alert},
        )
        self.bus.emit(db, driver, "manager_handoff", {"reason": payload_reason, "alert": alert}, reply=reply)
        self.bus.emit(db, driver, "support_ticket_created", {"reason": payload_reason, "ticket": ticket}, reply=reply)

        return reply
