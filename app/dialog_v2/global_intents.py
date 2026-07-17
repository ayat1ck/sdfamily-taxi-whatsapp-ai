from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.missing_fields import MissingFieldsCalculator
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.states import DialogV2State
from app.dialog_v2.summary_builder import SummaryBuilder
from app.dialog_v2.ui import (
    CONFIRM_BUTTONS,
    EDIT_ACTION_BY_ID,
    REGISTRATION_EDIT_LIST,
    buttons_reply,
    is_confirm_choice,
    is_edit_choice,
    is_manager_choice,
    list_reply,
)
from app.dialog_v2.yandex_auto_submit import DialogV2YandexAutoSubmit
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


REGISTRATION_STATES = {
    DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
    DialogV2State.REGISTRATION_MISSING_FIELDS,
    DialogV2State.REGISTRATION_CONFIRMATION,
    DialogV2State.READY_TO_SEND_YANDEX,
}

TEXT_PENDING_FIELDS = {
    "replace_plate": ("vehicle", "plate_number", "госномер"),
    "replace_full_name": ("driver", "full_name", "ФИО"),
    "replace_phone": ("driver", "phone", "телефон"),
    "replace_city_address": ("driver", "address", "город/адрес"),
    "replace_iin": ("driver", "iin", "ИИН"),
    "replace_driving_experience": ("driver", "driving_experience_since", "стаж"),
}

DOCUMENT_PENDING_ACTIONS = {
    "replace_driver_license",
    "replace_vehicle",
    "replace_vehicle_registration_doc",
    "replace_document",
}


def _context(driver) -> dict:
    return deepcopy(driver.support_context_json or {})


def _draft(driver) -> dict | None:
    draft = _context(driver).get("registration_draft")
    return draft if isinstance(draft, dict) else None


def _save_context(driver, context: dict) -> None:
    driver.support_context_json = context
    flag_modified(driver, "support_context_json")


def _save_draft(driver, draft: dict) -> None:
    context = _context(driver)
    context["registration_draft"] = deepcopy(draft)
    context["registration_mode"] = "document_first"
    _save_context(driver, context)


def _normalized(text: str | None) -> str:
    return normalize_text_token(repair_mojibake(text or ""))


class GlobalIntentRouter:
    def __init__(self) -> None:
        self.summary = SummaryBuilder()
        self.missing = MissingFieldsCalculator()
        self.manager = ManagerHandoffFlow()
        self.yandex_auto_submit = DialogV2YandexAutoSubmit()

    def handle(self, db, driver, application, message, registration_flow=None) -> StructuredReply | None:
        pending_action = (_draft(driver) or {}).get("pending_action")
        pending_menu = _context(driver).get("pending_menu")

        if pending_action == "confirm_reset_registration" and message.message_type == "text":
            text = _normalized(message.text)
            if self._is_confirmation(text):
                return self._reset_registration(driver)
            return self._clear_pending(driver, "Ок, не сбрасываю анкету. Продолжаем с текущими данными.")

        if pending_action == "choose_edit_field" or pending_menu == "registration_edit_fields":
            if message.message_type == "text":
                return self._handle_edit_field_choice(driver, message.text or "")

        if pending_action in DOCUMENT_PENDING_ACTIONS and message.message_type in {"image", "document"} and registration_flow:
            reply = registration_flow.handle_document(db, driver, application, message)
            self._decorate(reply, "correction", pending_action, driver)
            return reply

        if pending_action in TEXT_PENDING_FIELDS and message.message_type == "text":
            return self._apply_text_pending(driver, pending_action, message.text or "")

        if message.message_type != "text":
            return None

        text = _normalized(message.text)
        if not text:
            return None

        if self._is_manager(text) or is_manager_choice(message.text or ""):
            reply = self.manager.handle(db, driver, application, message, reason="human_requested")
            intent = reply.metadata.get("intent") or "manager"
            action = "manager_triage" if intent == "manager_triage" else "manager_handoff"
            self._decorate(reply, intent, action, driver)
            return reply

        if self._is_cancel(text):
            return self._ask_reset(driver)

        if self._is_summary(text):
            return self._summary_reply(driver)

        if self._is_missing(text):
            return self._missing_reply(driver)

        if self._is_delete_last_photo(text):
            return self._delete_last_document(driver)

        if is_edit_choice(message.text or "") and driver.state in REGISTRATION_STATES | {"yandex_error"}:
            return self._show_edit_menu(driver)

        if self._is_confirmation(text):
            draft = _draft(driver)
            if draft:
                self.missing.calculate(draft)
                _save_draft(driver, draft)
                if draft.get("ready_for_yandex"):
                    reply = self.yandex_auto_submit.submit(db, driver, application, draft)
                    reply.metadata.update(self._metadata("confirmation", "submit_to_yandex", driver))
                    return reply

        if driver.state in REGISTRATION_STATES | {"yandex_error"}:
            correction_action = self._correction_action(text)
            if correction_action:
                return self._start_correction(driver, correction_action)

        if driver.state in REGISTRATION_STATES and _draft(driver):
            draft = _draft(driver) or {}
            self.missing.calculate(draft)
            _save_draft(driver, draft)

        return None

    def _is_manager(self, text: str) -> bool:
        return any(token in text for token in ("оператор", "менеджер", "живой человек", "техподдержка"))

    def _is_cancel(self, text: str) -> bool:
        return any(token in text for token in ("отмена", "стоп", "сбросить", "начать заново", "заново"))

    def _is_summary(self, text: str) -> bool:
        return any(token in text for token in ("показать анкету", "проверить данные", "что распознал", "что сохранилось"))

    def _is_missing(self, text: str) -> bool:
        return any(token in text for token in ("что осталось", "чего не хватает", "что еще нужно", "что ещё нужно"))

    def _is_delete_last_photo(self, text: str) -> bool:
        return "удалить" in text and any(token in text for token in ("последнее фото", "последний файл", "последний документ", "фото"))

    def _is_confirmation(self, text: str) -> bool:
        return is_confirm_choice(text)

    def _show_edit_menu(self, driver) -> StructuredReply:
        draft = _draft(driver) or {}
        draft["pending_action"] = "choose_edit_field"
        _save_draft(driver, draft)
        context = _context(driver)
        context["pending_menu"] = "registration_edit_fields"
        _save_context(driver, context)
        return list_reply(
            "Что нужно исправить?",
            REGISTRATION_EDIT_LIST,
            flow="global",
            state=driver.state,
            metadata=self._metadata("edit_menu", "choose_edit_field", driver),
        )

    def _handle_edit_field_choice(self, driver, raw_text: str) -> StructuredReply:
        choice = (raw_text or "").strip()
        action = EDIT_ACTION_BY_ID.get(choice)
        if not action:
            return self._show_edit_menu(driver)
        context = _context(driver)
        context.pop("pending_menu", None)
        _save_context(driver, context)
        return self._start_correction(driver, action)

    def _correction_action(self, text: str) -> str | None:
        if not any(token in text for token in ("изменить", "поменять", "исправить", "заменить", "не тот", "неправильно", "другой", "новое", "новый", "новая")):
            return None
        if any(token in text for token in ("ву", "права", "водительское")):
            return "replace_driver_license"
        if any(token in text for token in ("стс", "техпаспорт")):
            return "replace_vehicle_registration_doc"
        if any(token in text for token in ("машина", "авто", "автомобиль")):
            return "replace_vehicle"
        if any(token in text for token in ("госномер", "номер авто")):
            return "replace_plate"
        if any(token in text for token in ("фио", "имя")):
            return "replace_full_name"
        if "телефон" in text or "номер" in text:
            return "replace_phone"
        if "город" in text or "адрес" in text:
            return "replace_city_address"
        if "иин" in text:
            return "replace_iin"
        if "стаж" in text:
            return "replace_driving_experience"
        if "документ" in text or "фото" in text:
            return "replace_document"
        return None

    def _start_correction(self, driver, action: str) -> StructuredReply:
        draft = _draft(driver) or {}
        draft["pending_action"] = action
        _save_draft(driver, draft)
        texts = {
            "replace_driver_license": "Хорошо, заменим ВУ. Отправьте новое фото или PDF водительского удостоверения.",
            "replace_vehicle": "Хорошо, заменим авто. Отправьте новый техпаспорт / СТС или напишите данные авто.",
            "replace_vehicle_registration_doc": "Хорошо, заменим техпаспорт / СТС. Отправьте новый файл.",
            "replace_plate": "Хорошо, заменим госномер. Напишите новый госномер.",
            "replace_full_name": "Хорошо, заменим ФИО. Напишите правильное ФИО.",
            "replace_phone": "Хорошо, заменим телефон. Напишите новый номер.",
            "replace_city_address": "Хорошо, заменим город/адрес. Напишите правильные данные.",
            "replace_iin": "Хорошо, заменим ИИН. Напишите правильный ИИН.",
            "replace_driving_experience": "Хорошо, заменим стаж. Напишите дату начала стажа.",
            "replace_document": "Хорошо, заменим документ. Отправьте новое фото или PDF.",
        }
        return StructuredReply(
            text=texts.get(action, "Хорошо, заменим данные. Отправьте новое значение."),
            flow="global",
            state=driver.state,
            metadata=self._metadata("correction", action, driver),
        )

    def _apply_text_pending(self, driver, action: str, value: str) -> StructuredReply:
        draft = _draft(driver) or {}
        section, field, label = TEXT_PENDING_FIELDS[action]
        draft.setdefault(section, {})[field] = value.strip()
        draft["pending_action"] = None
        missing = self.missing.calculate(draft)
        _save_draft(driver, draft)
        prefix = f"Обновил: {label}."
        if missing:
            text = f"{prefix}\n\n{self.summary.build_missing_text(missing, draft)}"
            return StructuredReply(
                text=text,
                flow="global",
                state=driver.state,
                metadata=self._metadata("correction_applied", action, driver),
            )
        return buttons_reply(
            f"{prefix}\n\n{self.summary.build_final_summary(draft)}",
            CONFIRM_BUTTONS,
            flow="global",
            state=DialogV2State.REGISTRATION_CONFIRMATION,
            metadata=self._metadata("correction_applied", action, driver),
        )

    def _summary_reply(self, driver) -> StructuredReply:
        draft = _draft(driver)
        if not draft:
            return StructuredReply(text="Пока нет сохранённой анкеты.", flow="global", state=driver.state, metadata=self._metadata("summary", "empty", driver))
        self.missing.calculate(draft)
        _save_draft(driver, draft)
        if draft.get("ready_for_yandex"):
            return buttons_reply(
                self.summary.build_final_summary(draft),
                CONFIRM_BUTTONS,
                flow="global",
                state=driver.state,
                metadata=self._metadata("summary", "show_summary", driver),
            )
        return StructuredReply(
            text=self.summary.build_final_summary(draft),
            flow="global",
            state=driver.state,
            metadata=self._metadata("summary", "show_summary", driver),
        )

    def _missing_reply(self, driver) -> StructuredReply:
        draft = _draft(driver)
        if not draft:
            return StructuredReply(text="Пока нет сохранённой анкеты.", flow="global", state=driver.state, metadata=self._metadata("missing_fields", "empty", driver))
        missing = self.missing.calculate(draft)
        _save_draft(driver, draft)
        return StructuredReply(
            text=self.summary.build_missing_text(missing, draft),
            flow="global",
            state=driver.state,
            metadata=self._metadata("missing_fields", "show_missing", driver),
        )

    def _delete_last_document(self, driver) -> StructuredReply:
        draft = _draft(driver)
        if not draft:
            return StructuredReply(text="Пока нет сохранённой анкеты.", flow="global", state=driver.state, metadata=self._metadata("delete_last_document", "empty", driver))
        last_doc = draft.get("last_document") or {}
        doc_type = last_doc.get("document_type")
        documents = draft.setdefault("documents", {})
        if doc_type in documents:
            documents[doc_type] = None
        draft["pending_action"] = None
        missing = self.missing.calculate(draft)
        _save_draft(driver, draft)
        text = "Удалил последний документ из анкеты.\n\n" + self.summary.build_missing_text(missing)
        return StructuredReply(text=text, flow="global", state=driver.state, metadata=self._metadata("delete_last_document", "delete_last_document", driver))

    def _ask_reset(self, driver) -> StructuredReply:
        draft = _draft(driver)
        if draft:
            draft["pending_action"] = "confirm_reset_registration"
            _save_draft(driver, draft)
        context = _context(driver)
        context.pop("pending_menu", None)
        _save_context(driver, context)
        return buttons_reply(
            "Сбросить текущую анкету и начать заново?",
            [
                {"type": "reply", "reply": {"id": "confirm", "title": "Подтверждаю"}},
                {"type": "reply", "reply": {"id": "cancel_reset", "title": "Отмена"}},
            ],
            flow="global",
            state=driver.state,
            metadata=self._metadata("reset", "confirm_reset_registration", driver),
        )

    def _reset_registration(self, driver) -> StructuredReply:
        context = _context(driver)
        context.pop("registration_draft", None)
        context.pop("pending_menu", None)
        _save_context(driver, context)
        driver.state = DialogV2State.NEW
        return StructuredReply(
            text="Анкету сбросил. Можете начать заново: отправьте документы или напишите 'Регистрация'.",
            flow="global",
            state=DialogV2State.NEW,
            metadata=self._metadata("reset", "reset_registration", driver),
        )

    def _clear_pending(self, driver, text: str) -> StructuredReply:
        draft = _draft(driver) or {}
        draft["pending_action"] = None
        _save_draft(driver, draft)
        return StructuredReply(text=text, flow="global", state=driver.state, metadata=self._metadata("reset", "cancel_reset", driver))

    def _metadata(self, intent: str, action: str, driver) -> dict[str, object]:
        draft = _draft(driver) or {}
        missing = draft.get("missing_fields") or []
        return {
            "intent": intent,
            "global_intent": intent,
            "global_action": action,
            "pending_action": draft.get("pending_action"),
            "draft_ready_for_yandex": bool(draft.get("ready_for_yandex")),
            "missing_fields": missing,
        }

    def _decorate(self, reply: StructuredReply, intent: str, action: str, driver) -> None:
        reply.metadata.update(self._metadata(intent, action, driver))
