from __future__ import annotations

from app.applications.service import get_or_create_application, set_application_status
from app.dialog_v2.document_types import DocumentTypeResolver
from app.dialog_v2.draft_merger import DraftMerger
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.missing_fields import MissingFieldsCalculator
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.states import DialogV2State
from app.dialog_v2.summary_builder import SummaryBuilder
from app.documents.extraction import DocumentExtractionService, normalize_extracted_fields
from app.documents.service import upsert_document
from app.messages.service import create_message
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


DOCUMENT_OPTIONS_REPLY = (
    "Получил файл, но не уверен, что это за документ.\n"
    "Это:\n"
    "1. Водительское удостоверение\n"
    "2. Удостоверение личности\n"
    "3. Техпаспорт / СТС\n"
    "4. Селфи с ВУ"
)

DOCUMENT_PROMPT = (
    "Для регистрации отправьте документы в любом порядке:\n"
    "1. водительское удостоверение\n"
    "2. удостоверение личности\n"
    "3. техпаспорт / СТС\n"
    "4. селфи с ВУ"
)


def _blank_draft() -> dict[str, object]:
    return {
        "driver": {
            "full_name": None,
            "iin": None,
            "birth_date": None,
            "driver_license_number": None,
            "driver_license_issue_date": None,
            "driver_license_expires_at": None,
            "driving_experience_since": None,
            "phone": None,
            "city": None,
            "address": None,
            "employment_type": None,
            "hired_at": None,
            "is_hearing_impaired": None,
        },
        "vehicle": {
            "brand": None,
            "model": None,
            "year": None,
            "plate_number": None,
            "color": None,
            "registration_certificate": None,
            "vin": None,
        },
        "documents": {
            "driver_license": None,
            "id_card": None,
            "vehicle_registration_doc": None,
            "selfie_with_license": None,
        },
        "missing_fields": [],
        "confidence_by_field": {},
        "pending_action": None,
        "last_document": None,
        "document_confidence_by_type": {},
    }


def _ensure_registration_context(driver) -> dict:
    context = dict(driver.support_context_json or {})
    draft = context.get("registration_draft")
    if not isinstance(draft, dict):
        draft = _blank_draft()
    else:
        default = _blank_draft()
        for key, value in default.items():
            draft.setdefault(key, value)
    context["registration_draft"] = draft
    context["registration_mode"] = "document_first"
    driver.support_context_json = context
    return draft


def _store_draft(driver, draft: dict) -> None:
    context = dict(driver.support_context_json or {})
    context["registration_draft"] = draft
    context["registration_mode"] = "document_first"
    driver.support_context_json = context


def _normalize_text(value: str | None) -> str:
    return normalize_text_token(repair_mojibake(value or ""))


def _is_registration_start(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(token in normalized for token in {"1", "регистрация", "tirkel", "подключ"})


class RegistrationFlow:
    def __init__(self) -> None:
        self.resolver = DocumentTypeResolver()
        self.merger = DraftMerger()
        self.missing = MissingFieldsCalculator()
        self.summary = SummaryBuilder()
        self.extractor = DocumentExtractionService()
        self.bus = EventBus()

    def _store_message(self, db, driver, message) -> None:
        create_message(
            db,
            driver=driver,
            direction="incoming",
            sender_type="customer",
            message_type=message.message_type,
            text=message.text,
            provider_message_id=message.provider_message_id,
            mime_type=message.mime_type,
            raw_payload=message.raw_payload,
            delivery_status="received",
        )

    def _save_document(self, db, driver, message, document_type: str, status: str = "uploaded") -> None:
        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=None,
            google_drive_file_id=None,
            whatsapp_media_id=message.media_id,
            status=status,
            message_id=None,
            file_name=message.filename,
            mime_type=message.mime_type,
            storage_provider="whatsapp",
        )
        self.bus.emit(db, driver, "document_uploaded", {"document_type": document_type, "file_name": message.filename})

    def _apply_extraction(self, db, driver, message, draft: dict, document_type: str) -> tuple[str, dict[str, str], list[str]]:
        extraction = self.extractor.extract(b"", mime_type=message.mime_type, expected_document_type=document_type)
        normalized_fields, _ = normalize_extracted_fields(extraction, document_type=extraction.document_type or document_type)
        ocr_text = " ".join(str(value) for value in normalized_fields.values())
        resolved = self.resolver.resolve(
            current_flow="registration_document_collection",
            current_state=driver.state,
            mime_type=message.mime_type,
            filename=message.filename,
            extracted_fields=normalized_fields,
            ocr_text=ocr_text,
            confidence=extraction.confidence,
        )
        merge_result = self.merger.merge(
            current_draft=draft,
            document_type=resolved.document_type,
            extracted_fields=normalized_fields,
            confidence=resolved.confidence,
        )
        merged = merge_result.draft
        merged["last_document"] = {
            "file_name": message.filename,
            "mime_type": message.mime_type,
            "document_type": resolved.document_type,
            "confidence": resolved.confidence,
        }
        merged["pending_action"] = None if resolved.document_type != "unknown" else "confirm_document_type"
        missing_fields = self.missing.calculate(merged)
        _store_draft(driver, merged)
        self._save_document(db, driver, message, resolved.document_type)
        self.bus.emit(db, driver, "document_recognized", {"document_type": resolved.document_type, "confidence": resolved.confidence})
        self.bus.emit(db, driver, "draft_updated", {"missing_fields": missing_fields, "updated_fields": merge_result.updated_fields})
        return resolved.document_type, normalized_fields, missing_fields

    def _unknown_document_reply(self, draft: dict, message) -> StructuredReply:
        draft["pending_action"] = "confirm_document_type"
        draft["last_document"] = {"file_name": message.filename, "mime_type": message.mime_type}
        return StructuredReply(
            text=DOCUMENT_OPTIONS_REPLY,
            next_flow=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            flow_state=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            metadata={"intent": "document_confirmation", "pending_action": "confirm_document_type"},
        )

    def _document_reply(self, document_type: str, extracted_fields: dict[str, str], missing_fields: list[str]) -> str:
        return self.summary.build_document_reply(document_type, extracted_fields, missing_fields)

    def _final_summary(self, draft: dict) -> str:
        return self.summary.build_final_summary(draft)

    def _post_document_reply(self, document_type: str, extracted_fields: dict[str, str], missing_fields: list[str], draft: dict) -> StructuredReply:
        text = self._document_reply(document_type, extracted_fields, missing_fields)
        if missing_fields:
            driver_state = DialogV2State.REGISTRATION_MISSING_FIELDS
        else:
            driver_state = DialogV2State.REGISTRATION_CONFIRMATION
            text = f"{text}\n\n{self._final_summary(draft)}"
        return StructuredReply(
            text=text,
            next_flow=driver_state,
            flow_state=driver_state,
            metadata={
                "intent": "document",
                "document_type": document_type,
                "extracted_fields": extracted_fields,
                "missing_fields": missing_fields,
            },
        )

    def start(self, db, driver, application) -> StructuredReply:
        draft = _ensure_registration_context(driver)
        driver.state = DialogV2State.REGISTRATION_DOCUMENT_COLLECTION
        set_application_status(db, application, "waiting_documents")
        _store_draft(driver, draft)
        return StructuredReply(
            text=DOCUMENT_PROMPT,
            next_flow=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            flow_state=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            metadata={"intent": "registration"},
        )

    def handle_document(self, db, driver, application, message) -> StructuredReply:
        application = get_or_create_application(db, driver)
        draft = _ensure_registration_context(driver)
        self._store_message(db, driver, message)
        if driver.state == DialogV2State.NEW:
            driver.state = DialogV2State.REGISTRATION_DOCUMENT_COLLECTION
            set_application_status(db, application, "waiting_documents")

        if draft.get("pending_action") == "confirm_document_type" and message.message_type == "text":
            return self.handle_text(db, driver, application, message)

        if message.message_type in {"image", "document"}:
            extraction = self.extractor.extract(b"", mime_type=message.mime_type, expected_document_type="unknown")
            normalized_fields, _ = normalize_extracted_fields(extraction, document_type=extraction.document_type or "unknown")
            resolved = self.resolver.resolve(
                current_flow="registration_document_collection",
                current_state=driver.state,
                mime_type=message.mime_type,
                filename=message.filename,
                extracted_fields=normalized_fields,
                ocr_text=" ".join(f"{k}:{v}" for k, v in normalized_fields.items()),
                confidence=extraction.confidence,
            )
            if resolved.document_type == "unknown":
                self._save_document(db, driver, message, "unknown", status="pending_confirmation")
                return self._unknown_document_reply(draft, message)
            resolved_type, extracted_fields, missing_fields = self._apply_extraction(db, driver, message, draft, resolved.document_type)
            driver.state = DialogV2State.REGISTRATION_MISSING_FIELDS if missing_fields else DialogV2State.REGISTRATION_CONFIRMATION
            reply = self._post_document_reply(resolved_type, extracted_fields, missing_fields, draft)
            self.bus.emit(db, driver, "summary_shown" if not missing_fields else "draft_updated", {"missing_fields": missing_fields})
            return reply

        return self.handle_text(db, driver, application, message)

    def _confirm_document_type(self, db, driver, application, message, draft: dict, selected_type: str) -> StructuredReply:
        last_doc = draft.get("last_document") or {}
        message.filename = last_doc.get("file_name")
        message.mime_type = last_doc.get("mime_type")
        draft["pending_action"] = None
        resolved_type, extracted_fields, missing_fields = self._apply_extraction(db, driver, message, draft, selected_type)
        driver.state = DialogV2State.REGISTRATION_MISSING_FIELDS if missing_fields else DialogV2State.REGISTRATION_CONFIRMATION
        return self._post_document_reply(resolved_type, extracted_fields, missing_fields, draft)

    def handle_text(self, db, driver, application, message) -> StructuredReply:
        text = repair_mojibake((message.text or "").strip())
        self._store_message(db, driver, message)
        draft = _ensure_registration_context(driver)

        if driver.state == DialogV2State.NEW and _is_registration_start(text):
            return self.start(db, driver, application)

        normalized = _normalize_text(text)
        if draft.get("pending_action") == "confirm_document_type" and normalized in {"1", "2", "3", "4"}:
            mapping = {
                "1": "driver_license",
                "2": "id_card",
                "3": "vehicle_registration_doc",
                "4": "selfie_with_license",
            }
            return self._confirm_document_type(db, driver, application, message, draft, mapping[normalized])

        if normalized in {"подтверждаю", "да", "ок", "ok"}:
            driver.state = DialogV2State.READY_TO_SEND_YANDEX
            set_application_status(db, application, "ready_to_send_yandex")
            self.bus.emit(db, driver, "registration_confirmed", {"draft": draft})
            return StructuredReply(
                text="Принято. Заявка готова к следующему этапу.",
                next_flow=DialogV2State.READY_TO_SEND_YANDEX,
                flow_state=DialogV2State.READY_TO_SEND_YANDEX,
                metadata={"intent": "confirmation"},
            )

        if driver.state in {
            DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            DialogV2State.REGISTRATION_MISSING_FIELDS,
            DialogV2State.REGISTRATION_CONFIRMATION,
            DialogV2State.READY_TO_SEND_YANDEX,
        }:
            self.missing.calculate(draft)
            _store_draft(driver, draft)
            missing = draft["missing_fields"]
            if missing:
                driver.state = DialogV2State.REGISTRATION_MISSING_FIELDS
                reply = StructuredReply(
                    text=self.summary.build_missing_text(missing),
                    next_flow=DialogV2State.REGISTRATION_MISSING_FIELDS,
                    flow_state=DialogV2State.REGISTRATION_MISSING_FIELDS,
                    metadata={"intent": "missing_fields", "missing_fields": missing},
                )
                return reply
            driver.state = DialogV2State.REGISTRATION_CONFIRMATION
            self.bus.emit(db, driver, "summary_shown", {"draft": draft})
            return StructuredReply(
                text=self._final_summary(draft),
                next_flow=DialogV2State.REGISTRATION_CONFIRMATION,
                flow_state=DialogV2State.REGISTRATION_CONFIRMATION,
                metadata={"intent": "summary", "draft": draft},
            )

        if message.message_type in {"image", "document"}:
            return self.handle_document(db, driver, application, message)

        if driver.state == DialogV2State.NEW:
            driver.state = DialogV2State.REGISTRATION_DOCUMENT_COLLECTION
            return StructuredReply(
                text=DOCUMENT_PROMPT,
                next_flow=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
                flow_state=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
                metadata={"intent": "registration"},
            )

        return StructuredReply(
            text="Отправьте документы для регистрации.",
            next_flow=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            flow_state=DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
            metadata={"intent": "registration"},
        )
