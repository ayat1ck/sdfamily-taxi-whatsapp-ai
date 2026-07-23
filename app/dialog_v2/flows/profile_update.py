from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import PROFILE_UPDATE_LIST, list_reply
from app.utils.validators import normalize_phone


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

FIELD_LABELS = {
    "full_name": "ФИО",
    "phone": "телефон",
    "city_address": "город/адрес",
    "vehicle": "автомобиль",
    "plate_number": "госномер",
    "registration_certificate": "СТС",
    "driver_license": "водительское удостоверение",
    "employment_type": "условие работы",
}


class ProfileUpdateFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.manager_flow = ManagerHandoffFlow()

    def _context(self, driver) -> dict:
        return dict(driver.support_context_json or {})

    def _save_context(self, driver, context: dict) -> None:
        driver.support_context_json = context
        try:
            flag_modified(driver, "support_context_json")
        except Exception:
            pass

    def _ticket_payload(self, field: str, current_value: str | None) -> dict:
        return {
            "reason": "profile_update",
            "field": field,
            "current_value": current_value,
            "new_value": None,
            "files": [],
            "status": "collecting",
        }

    def _current_value(self, driver, field: str) -> str | None:
        if field == "full_name":
            return driver.full_name
        if field == "phone":
            return driver.phone or driver.whatsapp_phone
        if field == "city_address":
            return ", ".join(part for part in [driver.city, driver.address] if part) or None
        if field == "vehicle":
            vehicle = getattr(driver, "vehicle", None)
            if vehicle:
                return " ".join(part for part in [vehicle.brand, vehicle.model] if part).strip() or None
            return None
        if field == "plate_number":
            return getattr(getattr(driver, "vehicle", None), "plate_number", None)
        if field == "registration_certificate":
            return getattr(getattr(driver, "vehicle", None), "registration_certificate", None)
        if field == "driver_license":
            return driver.driver_license_number
        if field == "employment_type":
            return driver.employment_type
        return None

    def _menu_reply(self, ticket: dict) -> StructuredReply:
        return list_reply(
            PROFILE_UPDATE_MENU,
            PROFILE_UPDATE_LIST,
            flow="profile_update",
            state="profile_update",
            metadata={"intent": "profile_update", "ticket": ticket},
        )

    def _normalize_new_value(self, field: str, text: str) -> str:
        cleaned = (text or "").strip()
        if field == "phone":
            return normalize_phone(cleaned)
        return cleaned

    def handle(self, db, driver, application, message, reason: str = "profile_update", *, show_menu: bool = False) -> StructuredReply:
        context = self._context(driver)
        text = "" if show_menu else (message.text or "").strip()
        ticket = dict(context.get("manager_ticket") or {})
        pending_menu = context.get("pending_menu")
        selected_field = None if show_menu else FIELD_MAP.get(text)

        # Accept free-text / media only while waiting for the new field value.
        collecting = pending_menu == "profile_update_value" and not show_menu and not selected_field
        if collecting:
            return self._collect_value(db, driver, application, message, ticket, context)

        if not ticket or ticket.get("reason") != "profile_update":
            ticket = self._ticket_payload(
                selected_field or "full_name",
                self._current_value(driver, selected_field or "full_name") if selected_field else None,
            )

        if selected_field is None and text:
            ticket.setdefault("reason", "profile_update")
            ticket.setdefault("status", "collecting")

        context["profile_update_requested"] = True
        context["profile_update_reason"] = reason
        context["manager_ticket"] = ticket
        driver.requires_attention = True

        if selected_field == "manager":
            context.pop("pending_menu", None)
            self._save_context(driver, context)
            self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": "manager"})
            return self.manager_flow.handle(db, driver, application, message, reason="profile_update", skip_triage=True)

        if selected_field and selected_field != "manager":
            ticket["field"] = selected_field
            ticket["current_value"] = self._current_value(driver, selected_field)
            ticket["new_value"] = None
            ticket["status"] = "collecting"
            context["manager_ticket"] = ticket
            context["pending_menu"] = "profile_update_value"
            self._save_context(driver, context)
            label = FIELD_LABELS.get(selected_field, selected_field)
            reply = StructuredReply(
                text=(
                    f"Принял: нужно изменить «{label}».\n"
                    "Напишите новое значение или отправьте фото документа.\n"
                    "Менеджер проверит и обновит данные."
                ),
                flow="profile_update",
                state="waiting_value",
                requires_manager=False,
                metadata={"intent": "profile_update", "ticket": ticket},
            )
            self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": selected_field}, reply=reply)
            return reply

        context["pending_menu"] = "profile_update_menu"
        self._save_context(driver, context)
        reply = self._menu_reply(ticket)
        self.bus.emit(db, driver, "profile_update_requested", {"reason": reason, "field": ticket.get("field")}, reply=reply)
        return reply

    def _collect_value(self, db, driver, application, message, ticket: dict, context: dict) -> StructuredReply:
        field = ticket.get("field") or "unknown"
        label = FIELD_LABELS.get(field, field)
        text = (message.text or "").strip()
        files = list(ticket.get("files") or [])

        if message.message_type in {"image", "document", "video"} and message.media_id:
            files.append(
                {
                    "media_id": message.media_id,
                    "mime_type": message.mime_type,
                    "filename": message.filename,
                    "message_type": message.message_type,
                }
            )
            ticket["files"] = files
            if not text:
                text = f"[файл: {message.filename or message.message_type}]"

        if not text and not files:
            context["pending_menu"] = "profile_update_value"
            context["manager_ticket"] = ticket
            self._save_context(driver, context)
            return StructuredReply(
                text=f"Напишите новое значение для «{label}» или отправьте фото документа.",
                flow="profile_update",
                state="waiting_value",
                metadata={"intent": "profile_update", "ticket": ticket},
            )

        new_value = self._normalize_new_value(field, text) if text else None
        ticket["new_value"] = new_value
        ticket["files"] = files
        ticket["status"] = "open"
        context["manager_ticket"] = ticket
        context.pop("pending_menu", None)
        context["dialog_mode"] = "bot_active"
        driver.requires_attention = True
        self._save_context(driver, context)

        display_value = new_value or "файл"
        reply = StructuredReply(
            text=(
                f"Принял новое значение для «{label}»: {display_value}.\n\n"
                "Передал менеджеру — обычно проверяют в рабочее время.\n"
                "Если срочно — напишите «менеджер»."
            ),
            flow="profile_update",
            state="submitted",
            requires_manager=True,
            metadata={"intent": "profile_update", "ticket": ticket},
        )
        self.bus.emit(
            db,
            driver,
            "profile_update_value_received",
            {"field": field, "new_value": new_value, "files": len(files)},
            reply=reply,
        )
        self.bus.emit(db, driver, "support_ticket_created", {"kind": "profile_update", "field": field}, reply=reply)
        return reply
