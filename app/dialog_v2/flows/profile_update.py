from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import PROFILE_UPDATE_LIST, list_reply


PROFILE_UPDATE_MENU = "Что нужно изменить?\nВыберите пункт меню ниже."

FIELD_MAP = {
    "1": "full_name",
    "2": "phone",
    "3": "city_address",
    "4": "vehicle",
    "5": "plate_number",
    "6": "registration_certificate",
    "7": "driver_license",
    "8": "employment_type",
    "9": "manager",
}


class ProfileUpdateFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.manager_flow = ManagerHandoffFlow()

    def _context(self, driver) -> dict:
        return dict(driver.support_context_json or {})

    def _ticket_payload(self, field: str, current_value: str | None) -> dict:
        return {
            "reason": "profile_update",
            "field": field,
            "current_value": current_value,
            "new_value": None,
            "files": [],
            "status": "collecting",
        }

    def _menu_reply(self, ticket: dict) -> StructuredReply:
        return list_reply(
            PROFILE_UPDATE_MENU,
            PROFILE_UPDATE_LIST,
            flow="profile_update",
            state="profile_update",
            metadata={"intent": "profile_update", "ticket": ticket},
        )

    def handle(self, db, driver, application, message, reason: str = "profile_update", *, show_menu: bool = False) -> StructuredReply:
        text = "" if show_menu else (message.text or "").strip()
        context = self._context(driver)
        selected_field = None if show_menu else FIELD_MAP.get(text)

        ticket = dict(context.get("manager_ticket") or {})
        if not ticket or ticket.get("reason") != "profile_update":
            current_value = None
            if selected_field == "full_name":
                current_value = driver.full_name
            elif selected_field == "phone":
                current_value = driver.phone or driver.whatsapp_phone
            elif selected_field == "city_address":
                current_value = ", ".join(part for part in [driver.city, driver.address] if part) or None
            elif selected_field == "vehicle":
                vehicle = getattr(driver, "vehicle", None)
                if vehicle:
                    current_value = " ".join(part for part in [vehicle.brand, vehicle.model] if part).strip() or None
            elif selected_field == "plate_number":
                current_value = getattr(getattr(driver, "vehicle", None), "plate_number", None)
            elif selected_field == "registration_certificate":
                current_value = getattr(getattr(driver, "vehicle", None), "registration_certificate", None)
            elif selected_field == "driver_license":
                current_value = driver.driver_license_number
            elif selected_field == "employment_type":
                current_value = driver.employment_type
            ticket = self._ticket_payload(selected_field or "full_name", current_value)

        if selected_field is None and text:
            ticket.setdefault("reason", "profile_update")
            ticket.setdefault("status", "collecting")

        context["profile_update_requested"] = True
        context["profile_update_reason"] = reason
        context["manager_ticket"] = ticket
        context["pending_menu"] = "profile_update_menu"
        driver.support_context_json = context
        driver.requires_attention = True

        if selected_field == "manager":
            self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": "manager"})
            return self.manager_flow.handle(db, driver, application, message, reason="profile_update")

        if selected_field and selected_field != "manager":
            ticket["field"] = selected_field
            context["manager_ticket"] = ticket
            driver.support_context_json = context
            reply = StructuredReply(
                text=(
                    f"Принял: нужно изменить «{selected_field}».\n"
                    "Напишите новое значение или отправьте фото документа.\n"
                    "Менеджер проверит и обновит данные."
                ),
                flow="profile_update",
                state="profile_update",
                requires_manager=True,
                metadata={"intent": "profile_update", "ticket": ticket},
            )
            self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": selected_field}, reply=reply)
            self.bus.emit(db, driver, "support_ticket_created", {"kind": "profile_update", "field": selected_field}, reply=reply)
            return reply

        reply = self._menu_reply(ticket)
        self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": ticket.get("field")}, reply=reply)
        self.bus.emit(db, driver, "support_ticket_created", {"kind": "profile_update", "field": ticket.get("field")}, reply=reply)
        return reply
