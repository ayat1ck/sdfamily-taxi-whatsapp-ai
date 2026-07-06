from __future__ import annotations

from dataclasses import dataclass

from app.utils.validators import normalize_text_token


@dataclass(slots=True)
class DocumentTypeResolution:
    document_type: str
    confidence: float
    reason: str = ""


class DocumentTypeResolver:
    def resolve(
        self,
        *,
        current_flow: str | None,
        current_state: str | None,
        mime_type: str | None,
        filename: str | None,
        extracted_fields: dict[str, str] | None = None,
        ocr_text: str | None = None,
        confidence: float | None = None,
    ) -> DocumentTypeResolution:
        extracted_fields = extracted_fields or {}
        text = normalize_text_token(" ".join([filename or "", ocr_text or "", " ".join(extracted_fields.values())]))
        mime = (mime_type or "").lower()
        score = confidence or 0.0

        if self._looks_like_vehicle_doc(text, mime, extracted_fields):
            return DocumentTypeResolution("vehicle_registration_doc", max(score, 0.8), "vehicle_doc")
        if self._looks_like_id_card(text, mime, extracted_fields):
            return DocumentTypeResolution("id_card", max(score, 0.8), "id_card")
        if self._looks_like_driver_license(text, mime, current_state, extracted_fields):
            return DocumentTypeResolution("driver_license", max(score, 0.8), "driver_license")
        if self._looks_like_selfie(text, mime, extracted_fields, current_flow, current_state):
            return DocumentTypeResolution("selfie_with_license", max(score, 0.7), "selfie")
        if extracted_fields:
            if {"brand", "model", "plate_number"} & extracted_fields.keys():
                return DocumentTypeResolution("vehicle_registration_doc", max(score, 0.65), "extracted_vehicle")
            if {"iin", "birth_date"} & extracted_fields.keys():
                return DocumentTypeResolution("id_card", max(score, 0.65), "extracted_id")
            if {"driver_license_number", "driver_license_issue_date", "driver_license_expires_at"} & extracted_fields.keys():
                return DocumentTypeResolution("driver_license", max(score, 0.65), "extracted_license")
        if current_flow == "registration_document_collection" and mime == "application/pdf":
            return DocumentTypeResolution("driver_license", max(score, 0.6), "pdf_default")
        return DocumentTypeResolution("unknown", score, "unknown")

    def _looks_like_driver_license(self, text: str, mime: str, current_state: str | None, extracted_fields: dict[str, str]) -> bool:
        markers = (
            "водительское удостоверение",
            "водительское",
            "driver license",
            "driver_license",
            "driver_license_front",
            "driver_license_back",
            "driving license",
            "license number",
            "categories",
            "дата выдачи",
            "действует до",
            "удостовер",
            "водител",
            "права",
            "vu",
            "w/u",
        )
        text_match = any(marker in text for marker in markers)
        field_match = {"driver_license_number", "driver_license_issue_date", "driver_license_expires_at"} & extracted_fields.keys()
        return text_match or bool(field_match) or "pdf" in mime or current_state == "ask_driver_license_front"

    def _looks_like_id_card(self, text: str, mime: str, extracted_fields: dict[str, str]) -> bool:
        markers = ("удостоверение личности", "identity card", "id card", "ии", "иин", "дата рождения", "место рождения")
        return any(marker in text for marker in markers) or bool({"iin", "birth_date"} & extracted_fields.keys()) or "id" in text

    def _looks_like_vehicle_doc(self, text: str, mime: str, extracted_fields: dict[str, str]) -> bool:
        markers = (
            "свидетельство о регистрации",
            "техпаспорт",
            "vehicle registration",
            "vehicle_registration_doc",
            "registration certificate",
            "марка",
            "модель",
            "госномер",
            "vin",
            "кузов",
            "цвет",
            "стс",
        )
        return any(marker in text for marker in markers) or bool({"brand", "model", "plate_number", "vin"} & extracted_fields.keys()) or "sts" in text or "tech" in text

    def _looks_like_selfie(self, text: str, mime: str, extracted_fields: dict[str, str], current_flow: str | None, current_state: str | None) -> bool:
        if "селфи" in text or "selfie" in text:
            return True
        if current_flow == "registration_document_collection" and current_state == "registration_selfie_with_license" and not extracted_fields and mime.startswith("image/"):
            return True
        return False
