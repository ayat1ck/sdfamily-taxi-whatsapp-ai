from __future__ import annotations

import re

from sqlalchemy.orm.attributes import flag_modified

from app.applications.service import get_or_create_application
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import PROFILE_UPDATE_LIST, list_reply
from app.integrations.yandex.messages import format_yandex_error_for_user
from app.integrations.yandex.service import YandexSubmissionService
from app.utils.validators import normalize_phone, normalize_plate_number, normalize_registration_certificate
from app.vehicles.service import get_or_create_vehicle


PROFILE_UPDATE_MENU = (
    "Что нужно изменить?\n"
    "Выберите пункт меню ниже.\n\n"
    "Авто / госномер / СТС / новая машина — бот попробует обновить в Яндексе сам.\n"
    "Телефон, ФИО, ВУ — передаём менеджеру."
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
    "10": "new_vehicle",
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
    "new_vehicle": "новая машина",
}

# Fields the bot can push to Yandex Fleet without a manager.
AUTO_VEHICLE_FIELDS = {"vehicle", "plate_number", "registration_certificate", "new_vehicle"}

MANAGER_ONLY_FIELDS = {"full_name", "phone", "driver_license", "employment_type", "city_address"}

NEW_VEHICLE_PROMPT = (
    "Добавим новую машину в парк и привяжем к вам.\n\n"
    "Напишите данные так:\n"
    "Toyota Camry 2018\n"
    "госномер 123ABC01\n"
    "цвет белый\n"
    "СТС AA12345678\n\n"
    "Или отправьте фото/PDF техпаспорта — менеджер поможет, если автоматом не разберём."
)


class ProfileUpdateFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.manager_flow = ManagerHandoffFlow()
        self.yandex = YandexSubmissionService()

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
        if field in {"vehicle", "new_vehicle"}:
            vehicle = getattr(driver, "vehicle", None)
            if vehicle:
                return " ".join(part for part in [vehicle.brand, vehicle.model, vehicle.year] if part).strip() or None
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
        if field == "plate_number":
            return normalize_plate_number(cleaned)
        if field == "registration_certificate":
            return normalize_registration_certificate(cleaned)
        return cleaned

    def _prompt_for_field(self, field: str) -> str:
        label = FIELD_LABELS.get(field, field)
        if field == "new_vehicle":
            return NEW_VEHICLE_PROMPT
        if field == "vehicle":
            return (
                f"Принял: нужно изменить «{label}».\n"
                "Напишите марку, модель и год, например: Toyota Camry 2018"
            )
        if field in AUTO_VEHICLE_FIELDS:
            return (
                f"Принял: нужно изменить «{label}».\n"
                "Напишите новое значение — попробую обновить в Яндексе сам."
            )
        return (
            f"Принял: нужно изменить «{label}».\n"
            "Напишите новое значение или отправьте фото документа.\n"
            "Менеджер проверит и обновит данные."
        )

    def handle(self, db, driver, application, message, reason: str = "profile_update", *, show_menu: bool = False) -> StructuredReply:
        context = self._context(driver)
        text = "" if show_menu else (message.text or "").strip()
        ticket = dict(context.get("manager_ticket") or {})
        pending_menu = context.get("pending_menu")
        selected_field = None if show_menu else FIELD_MAP.get(text)

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

        if selected_field == "manager":
            context.pop("pending_menu", None)
            self._save_context(driver, context)
            driver.requires_attention = True
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
            # Don't page managers until a value is collected for manager-only fields.
            if selected_field in MANAGER_ONLY_FIELDS:
                driver.requires_attention = False
            reply = StructuredReply(
                text=self._prompt_for_field(selected_field),
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
                # Photos for auto vehicle fields still need a manager if we can't OCR here.
                if field in AUTO_VEHICLE_FIELDS:
                    return self._finish_as_manager_ticket(
                        db,
                        driver,
                        context,
                        ticket,
                        field,
                        new_value=f"[файл: {message.filename or message.message_type}]",
                        files=files,
                        note="Получил файл. Передал менеджеру — обновят авто в Яндексе.",
                    )
                text = f"[файл: {message.filename or message.message_type}]"

        if not text and not files:
            context["pending_menu"] = "profile_update_value"
            context["manager_ticket"] = ticket
            self._save_context(driver, context)
            return StructuredReply(
                text=self._prompt_for_field(field),
                flow="profile_update",
                state="waiting_value",
                metadata={"intent": "profile_update", "ticket": ticket},
            )

        new_value = self._normalize_new_value(field, text) if text else None
        ticket["new_value"] = new_value
        ticket["files"] = files

        if field in AUTO_VEHICLE_FIELDS and new_value and not str(new_value).startswith("[файл:"):
            return self._apply_vehicle_change(db, driver, application, context, ticket, field, new_value)

        return self._finish_as_manager_ticket(db, driver, context, ticket, field, new_value=new_value, files=files)

    def _finish_as_manager_ticket(
        self,
        db,
        driver,
        context: dict,
        ticket: dict,
        field: str,
        *,
        new_value: str | None,
        files: list | None = None,
        note: str | None = None,
    ) -> StructuredReply:
        label = FIELD_LABELS.get(field, field)
        ticket["new_value"] = new_value
        ticket["files"] = files or ticket.get("files") or []
        ticket["status"] = "open"
        context["manager_ticket"] = ticket
        context.pop("pending_menu", None)
        context["dialog_mode"] = "bot_active"
        driver.requires_attention = True
        self._save_context(driver, context)

        display_value = new_value or "файл"
        reply = StructuredReply(
            text=note
            or (
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
            {"field": field, "new_value": new_value, "files": len(ticket.get("files") or [])},
            reply=reply,
        )
        self.bus.emit(db, driver, "support_ticket_created", {"kind": "profile_update", "field": field}, reply=reply)
        return reply

    def _parse_vehicle_text(self, text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        blob = " ".join(lines)

        plate_match = re.search(r"(?:госномер|номер)\s*[:\-]?\s*([A-Za-zА-Яа-я0-9]+)", blob, re.IGNORECASE)
        if plate_match:
            result["plate_number"] = normalize_plate_number(plate_match.group(1))
        color_match = re.search(r"(?:цвет)\s*[:\-]?\s*([^\n,;]+)", blob, re.IGNORECASE)
        if color_match:
            result["color"] = color_match.group(1).strip()
        sts_match = re.search(r"(?:стс|техпаспорт)\s*[:\-]?\s*([A-Za-zА-Яа-я0-9]+)", blob, re.IGNORECASE)
        if sts_match:
            result["registration_certificate"] = normalize_registration_certificate(sts_match.group(1))

        # First line like "Toyota Camry 2018"
        first = lines[0] if lines else blob
        year_match = re.search(r"\b(19|20)\d{2}\b", first)
        if year_match:
            result["year"] = year_match.group(0)
            first = first.replace(year_match.group(0), " ").strip()
        parts = [part for part in first.split() if part]
        if len(parts) >= 2 and "brand" not in result:
            # Skip label words
            if parts[0].lower() not in {"госномер", "цвет", "стс", "техпаспорт"}:
                result["brand"] = parts[0]
                result["model"] = " ".join(parts[1:])
        return result

    def _apply_local_vehicle_fields(self, db, driver, updates: dict[str, str]) -> None:
        vehicle = get_or_create_vehicle(db, driver)
        for key, value in updates.items():
            if value and hasattr(vehicle, key):
                setattr(vehicle, key, value)
        db.add(vehicle)
        db.flush()

    def _apply_vehicle_change(self, db, driver, application, context: dict, ticket: dict, field: str, new_value: str) -> StructuredReply:
        application = application or get_or_create_application(db, driver)
        updates: dict[str, str] = {}

        if field == "plate_number":
            updates["plate_number"] = new_value
        elif field == "registration_certificate":
            updates["registration_certificate"] = new_value
        elif field in {"vehicle", "new_vehicle"}:
            updates = self._parse_vehicle_text(new_value)
            if field == "vehicle" and not updates.get("brand"):
                # Treat as brand model year free text even without newlines.
                updates = self._parse_vehicle_text(new_value.replace(",", " "))
            if field == "new_vehicle" and not (updates.get("brand") and updates.get("plate_number")):
                context["pending_menu"] = "profile_update_value"
                context["manager_ticket"] = ticket
                self._save_context(driver, context)
                return StructuredReply(
                    text=(
                        "Не хватает данных для новой машины.\n"
                        "Нужны минимум марка/модель и госномер.\n\n" + NEW_VEHICLE_PROMPT
                    ),
                    flow="profile_update",
                    state="waiting_value",
                    metadata={"intent": "profile_update", "ticket": ticket},
                )

        if not updates:
            return self._finish_as_manager_ticket(db, driver, context, ticket, field, new_value=new_value)

        self._apply_local_vehicle_fields(db, driver, updates)

        try:
            if field == "new_vehicle":
                if not application.yandex_driver_id:
                    raise ValueError("missing_yandex_driver_id")
                result = self.yandex.add_vehicle_and_bind(db, driver, application)
                action = "created_and_bound"
            else:
                if not application.yandex_vehicle_id:
                    # No existing car id — create+bind if we know the driver in Yandex.
                    if application.yandex_driver_id:
                        result = self.yandex.add_vehicle_and_bind(db, driver, application)
                        action = "created_and_bound"
                    else:
                        raise ValueError("missing_yandex_vehicle_id")
                else:
                    result = self.yandex.update_vehicle_in_yandex(db, driver, application)
                    action = "updated"
        except Exception as exc:
            ticket["status"] = "open"
            ticket["yandex_error"] = str(exc)
            context["manager_ticket"] = ticket
            context.pop("pending_menu", None)
            driver.requires_attention = True
            self._save_context(driver, context)
            details = format_yandex_error_for_user(str(exc))
            reply = StructuredReply(
                text=(
                    f"Сохранил локально: {', '.join(f'{k}={v}' for k, v in updates.items())}.\n"
                    f"В Яндекс автоматически не получилось: {details}\n\n"
                    "Передал менеджеру — доделают вручную."
                ),
                flow="profile_update",
                state="yandex_error",
                requires_manager=True,
                metadata={"intent": "profile_update", "ticket": ticket, "yandex_error": str(exc)},
            )
            self.bus.emit(db, driver, "profile_update_yandex_failed", {"field": field, "error": str(exc)}, reply=reply)
            return reply

        ticket["status"] = "done"
        ticket["yandex_result"] = result
        context["manager_ticket"] = ticket
        context.pop("pending_menu", None)
        driver.requires_attention = False
        self._save_context(driver, context)

        if action == "created_and_bound":
            text = (
                "Готово: новая машина добавлена в парк и привязана к вам в Яндексе.\n"
                f"Данные: {', '.join(f'{k}={v}' for k, v in updates.items())}."
            )
        else:
            text = (
                "Готово: обновил авто в Яндексе.\n"
                f"Данные: {', '.join(f'{k}={v}' for k, v in updates.items())}."
            )
        reply = StructuredReply(
            text=text,
            flow="profile_update",
            state="done",
            requires_manager=False,
            metadata={"intent": "profile_update", "ticket": ticket, "yandex_action": action},
        )
        self.bus.emit(db, driver, "profile_update_yandex_ok", {"field": field, "action": action, "updates": updates}, reply=reply)
        return reply
