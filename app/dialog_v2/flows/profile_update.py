from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply


PROFILE_UPDATE_MENU = (
    "Что нужно изменить?\n"
    "1. ФИО\n"
    "2. Телефон\n"
    "3. Город/адрес\n"
    "4. Автомобиль\n"
    "5. Госномер\n"
    "6. СТС/техпаспорт\n"
    "7. Водительское удостоверение\n"
    "8. СМЗ/тип сотрудничества\n"
    "9. Менеджер"
)

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

    def handle(self, db, driver, application, message, reason: str = "profile_update") -> StructuredReply:
        text = (message.text or "").strip()
        context = self._context(driver)
        selected_field = FIELD_MAP.get(text)

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
            ticket = self._ticket_payload(selected_field or "manager", current_value)

        if selected_field is None and text:
            ticket.setdefault("reason", "profile_update")
            ticket.setdefault("status", "collecting")

        context["profile_update_requested"] = True
        context["profile_update_reason"] = reason
        context["manager_ticket"] = ticket
        driver.support_context_json = context
        driver.requires_attention = True

        if selected_field == "manager":
            self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": "manager"})
            return self.manager_flow.handle(db, driver, application, message, reason="profile_update")

        reply = StructuredReply(
            text=PROFILE_UPDATE_MENU,
            flow="profile_update",
            state="profile_update",
            metadata={"intent": "profile_update", "ticket": ticket},
        )
        self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": ticket.get("field")}, reply=reply)
        self.bus.emit(db, driver, "support_ticket_created", {"kind": "profile_update", "field": ticket.get("field")}, reply=reply)

        return reply
