from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified

from app.applications.service import get_or_create_application
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.flows.profile_update import ProfileUpdateFlow
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import EXISTING_DRIVER_MENU_LIST, list_reply
from app.drivers.service import find_driver_by_iin, find_driver_by_phone, find_driver_by_whatsapp_phone
from app.integrations.yandex.service import YandexSubmissionService
from app.utils.validators import normalize_phone


MENU_TEXT = (
    "Понял, вы уже подключены. Что нужно?\n"
    "Выберите пункт меню ниже."
)

LOOKUP_PROMPT = (
    "Не нашёл полный профиль в боте.\n"
    "Напишите ИИН (12 цифр) или номер телефона из Яндекс Про — подтяну данные из парка."
)

PENDING_LOOKUP_MENU = "existing_driver_lookup"


class ExistingDriverFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.manager_flow = ManagerHandoffFlow()
        self.profile_update_flow = ProfileUpdateFlow()
        self.yandex = YandexSubmissionService()

    def _context(self, driver) -> dict:
        return dict(driver.support_context_json or {})

    def _save_context(self, driver, context: dict) -> None:
        driver.support_context_json = context
        try:
            flag_modified(driver, "support_context_json")
        except Exception:
            pass

    def _has_known_profile(self, driver, application=None) -> bool:
        if driver.full_name or driver.iin:
            return True
        if driver.state in {"completed", "ready_to_send_yandex"}:
            return True
        if application and application.yandex_driver_id:
            return True
        return False

    def _profile_text(self, driver) -> str:
        vehicle = getattr(driver, "vehicle", None)
        vehicle_text = "—"
        if vehicle:
            vehicle_text = " ".join(part for part in [vehicle.brand, vehicle.model] if part).strip() or "—"
        return (
            "Профиль найден:\n"
            f"ФИО: {driver.full_name or '—'}\n"
            f"Телефон: {driver.phone or driver.whatsapp_phone or '—'}\n"
            f"Город: {driver.city or '—'}\n"
            f"Авто: {vehicle_text}"
        )

    def _menu_reply(self, matched_driver) -> StructuredReply:
        return list_reply(
            f"{self._profile_text(matched_driver)}\n\n{MENU_TEXT}",
            EXISTING_DRIVER_MENU_LIST,
            flow="existing_driver",
            state="existing_driver",
            metadata={"intent": "existing_driver_menu", "driver_id": matched_driver.id},
        )

    def _store_menu(self, driver, matched_driver_id: int) -> None:
        context = self._context(driver)
        context["existing_driver_target_id"] = matched_driver_id
        context["pending_menu"] = "existing_driver_main"
        context["menu"] = "existing_driver_main"
        context["mode"] = "existing_driver_support"
        context.pop("pending_action", None)
        self._save_context(driver, context)

    def _ask_lookup(self, driver) -> StructuredReply:
        context = self._context(driver)
        context["pending_action"] = "existing_driver_lookup"
        context["pending_menu"] = PENDING_LOOKUP_MENU
        self._save_context(driver, context)
        return StructuredReply(
            text=LOOKUP_PROMPT,
            flow="existing_driver",
            state="existing_driver_lookup",
            metadata={"intent": "existing_driver_lookup"},
        )

    def _lookup_value(self, text: str) -> str | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if len(digits) == 12:
            return digits
        if len(digits) >= 10:
            return normalize_phone(cleaned)
        return None

    def _resolve_by_lookup(self, db, chat_driver, lookup: str):
        # Prefer another local profile if IIN/phone belongs to a different row.
        matched = None
        digits = "".join(ch for ch in lookup if ch.isdigit())
        if len(digits) == 12:
            matched = find_driver_by_iin(db, digits)
        if not matched:
            matched = find_driver_by_phone(db, lookup)
        if matched and matched.id != chat_driver.id and self._has_known_profile(matched):
            return matched, "local"

        # Sync from Yandex into the current WhatsApp chat driver.
        try:
            synced = self.yandex.find_and_sync_existing_driver(db, chat_driver, lookup)
        except Exception:
            synced = None
        if synced:
            return synced, "yandex"
        return None, None

    def _handle_menu_choice(self, db, driver, application, message, matched_driver) -> StructuredReply:
        choice = (message.text or "").strip()
        self._store_menu(driver, matched_driver.id)

        if choice == "1":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="payout_issue", skip_triage=True)
        if choice == "2":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="tariff_issue", skip_triage=True)
        if choice == "3":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="yandex_login_issue", skip_triage=True)
        if choice == "4":
            return self.profile_update_flow.handle(
                db, matched_driver, application, message, reason="profile_update", show_menu=True
            )
        if choice == "5":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="blocking_or_orders", skip_triage=True)
        if choice == "6":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="human_requested", skip_triage=True)
        return self._menu_reply(matched_driver)

    def handle(self, db, driver, application, message) -> StructuredReply:
        application = application or get_or_create_application(db, driver)
        context = self._context(driver)
        pending_menu = context.get("pending_menu") or context.get("menu")
        pending_action = context.get("pending_action")
        text = (message.text or "").strip()
        target_id = context.get("existing_driver_target_id")

        if pending_menu == "existing_driver_main" and target_id:
            matched_driver = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
            if matched_driver and matched_driver.id == target_id and self._has_known_profile(matched_driver, application):
                return self._handle_menu_choice(db, driver, application, message, matched_driver)
            # Target may be a different local driver id.
            if matched_driver is None or matched_driver.id != target_id:
                # Fall through to normal resolution using the menu choice only if profile known.
                pass

        if pending_action == "existing_driver_lookup" or (
            not self._has_known_profile(driver, application) and self._lookup_value(text)
        ):
            lookup = self._lookup_value(text)
            if not lookup:
                return self._ask_lookup(driver)
            matched, source = self._resolve_by_lookup(db, driver, lookup)
            if matched:
                reply = self._menu_reply(matched)
                reply.metadata["intent"] = "existing_driver"
                reply.metadata["lookup_source"] = source
                self._store_menu(driver, matched.id)
                self.bus.emit(db, matched, "existing_driver_found", {"by": source or "lookup"}, reply=reply)
                return reply
            return StructuredReply(
                text=(
                    "В парке по этим данным профиль не найден.\n"
                    "Проверьте ИИН/телефон или напишите «менеджер»."
                ),
                flow="existing_driver",
                state="existing_driver_lookup",
                metadata={"intent": "existing_driver_lookup_miss"},
            )

        if self._has_known_profile(driver, application):
            reply = self._menu_reply(driver)
            reply.metadata["intent"] = "existing_driver"
            self._store_menu(driver, driver.id)
            self.bus.emit(db, driver, "existing_driver_found", {"by": "whatsapp_phone"}, reply=reply)
            return reply

        # Empty local shell (WhatsApp first contact) — ask for IIN/phone and pull from Yandex.
        return self._ask_lookup(driver)
