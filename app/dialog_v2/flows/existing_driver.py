from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.flows.profile_update import ProfileUpdateFlow
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import EXISTING_DRIVER_MENU_LIST, list_reply
from app.drivers.service import find_driver_by_iin, find_driver_by_phone, find_driver_by_whatsapp_phone


MENU_TEXT = (
    "Понял, вы уже подключены. Что нужно?\n"
    "Выберите пункт меню ниже."
)


class ExistingDriverFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.manager_flow = ManagerHandoffFlow()
        self.profile_update_flow = ProfileUpdateFlow()

    def _context(self, driver) -> dict:
        return dict(driver.support_context_json or {})

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
        driver.support_context_json = context

    def _handle_menu_choice(self, db, driver, application, message, matched_driver) -> StructuredReply:
        choice = (message.text or "").strip()
        self._store_menu(driver, matched_driver.id)

        if choice == "1":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="payout_issue")
        if choice == "2":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="tariff_issue")
        if choice == "3":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="yandex_login_issue")
        if choice == "4":
            return self.profile_update_flow.handle(
                db, matched_driver, application, message, reason="profile_update", show_menu=True
            )
        if choice == "5":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="blocking_or_orders")
        if choice == "6":
            return self.manager_flow.handle(db, matched_driver, application, message, reason="human_requested")
        return self._menu_reply(matched_driver)

    def handle(self, db, driver, application, message) -> StructuredReply:
        context = self._context(driver)
        pending_menu = context.get("pending_menu") or context.get("menu")
        target_id = context.get("existing_driver_target_id")
        if pending_menu == "existing_driver_main" and target_id:
            matched_driver = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
            if not matched_driver or matched_driver.id != target_id:
                text = message.text or ""
                if text and len(text.replace(" ", "")) == 12 and text.isdigit():
                    matched_driver = find_driver_by_iin(db, text)
                if not matched_driver and text:
                    matched_driver = find_driver_by_phone(db, text)
            if matched_driver and matched_driver.id == target_id:
                return self._handle_menu_choice(db, driver, application, message, matched_driver)

        matched = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
        if not matched:
            text = message.text or ""
            if text and len(text.replace(" ", "")) == 12 and text.isdigit():
                matched = find_driver_by_iin(db, text)
            if not matched and text:
                matched = find_driver_by_phone(db, text)

        if matched:
            reply = self._menu_reply(matched)
            reply.metadata["intent"] = "existing_driver"
            self._store_menu(driver, matched.id)
            self.bus.emit(db, matched, "existing_driver_found", {"by": "whatsapp_phone"}, reply=reply)
            return reply

        context["pending_action"] = "existing_driver_lookup"
        driver.support_context_json = context
        return StructuredReply(
            text="Не нашёл профиль. Напишите ИИН или номер телефона.",
            flow="existing_driver",
            state="existing_driver",
            metadata={"intent": "existing_driver_lookup"},
        )
