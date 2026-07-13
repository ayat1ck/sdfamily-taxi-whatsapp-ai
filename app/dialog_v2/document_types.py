from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.validators import normalize_text_token


LICENSE_FIELDS = {
    "driver_license_number",
    "driver_license_issue_date",
    "driver_license_expires_at",
}


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
        has_license_fields = bool(LICENSE_FIELDS & extracted_fields.keys())

        if self._looks_like_vehicle_doc(text, mime, extracted_fields):
            return DocumentTypeResolution("vehicle_registration_doc", max(score, 0.8), "vehicle_doc")

        # KZ driver licenses also contain IIN/birth date — license fields win over id_card.
        if has_license_fields or self._looks_like_driver_license(text, mime, current_state, extracted_fields):
            return DocumentTypeResolution("driver_license", max(score, 0.8), "driver_license")

        if self._looks_like_id_card(text, mime, extracted_fields):
            return DocumentTypeResolution("id_card", max(score, 0.8), "id_card")

        if self._looks_like_selfie(text, mime, extracted_fields, current_flow, current_state):
            return DocumentTypeResolution("selfie_with_license", max(score, 0.7), "selfie")

        if extracted_fields:
            if {"brand", "model", "plate_number"} & extracted_fields.keys():
                return DocumentTypeResolution("vehicle_registration_doc", max(score, 0.65), "extracted_vehicle")
            if LICENSE_FIELDS & extracted_fields.keys():
                return DocumentTypeResolution("driver_license", max(score, 0.65), "extracted_license")
            if {"iin", "birth_date"} & extracted_fields.keys():
                return DocumentTypeResolution("id_card", max(score, 0.65), "extracted_id")

        if current_flow == "registration_document_collection" and mime == "application/pdf":
            return DocumentTypeResolution("driver_license", max(score, 0.6), "pdf_default")
        return DocumentTypeResolution("unknown", score, "unknown")

    def _looks_like_driver_license(self, text: str, mime: str, current_state: str | None, extracted_fields: dict[str, str]) -> bool:
        markers = (
            "водительское удостоверение",
            "водительское",
            "жүргізуші куәлігі",
            "жүргізуші",
            "driver license",
            "driving licence",
            "driving license",
            "driver_license",
            "driver_license_front",
            "driver_license_back",
            "license number",
            "categories",
            "категори",
            "дата выдачи",
            "действует до",
            "водител",
            "права",
            "vu",
            "w/u",
        )
        if "удостоверение личности" in text and not (LICENSE_FIELDS & extracted_fields.keys()):
            return False
        text_match = any(marker in text for marker in markers) or (
            "удостовер" in text and "личност" not in text
        )
        field_match = bool(LICENSE_FIELDS & extracted_fields.keys())
        return text_match or field_match or current_state == "ask_driver_license_front"

    def _looks_like_id_card(self, text: str, mime: str, extracted_fields: dict[str, str]) -> bool:
        # Do not treat a driver license as an ID card just because IIN/birth date were read.
        if LICENSE_FIELDS & extracted_fields.keys():
            return False
        markers = (
            "удостоверение личности",
            "жеке куәлік",
            "identity card",
            "id card",
            "место рождения",
        )
        text_match = any(marker in text for marker in markers)
        # IIN alone is weak: KZ VU also has IIN. Require id-card wording or iin+birth without license fields.
        field_match = {"iin", "birth_date"}.issubset(extracted_fields.keys()) and (
            "личност" in text or "identity" in text or "id card" in text or "жеке" in text
        )
        return text_match or field_match

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
            "кузов",
            "цвет",
            "стс",
        )
        # Avoid matching "vin" inside words like "driving".
        return (
            any(marker in text for marker in markers)
            or bool({"brand", "model", "plate_number", "vin"} & extracted_fields.keys())
            or bool(re.search(r"(?<![a-zа-яё])vin(?![a-zа-яё])", text))
            or bool(re.search(r"(?<![a-zа-яё])sts(?![a-zа-яё])", text))
            or "tech" in text
        )

    def _looks_like_selfie(self, text: str, mime: str, extracted_fields: dict[str, str], current_flow: str | None, current_state: str | None) -> bool:
        if "селфи" in text or "selfie" in text:
            return True
        if current_flow == "registration_document_collection" and current_state == "registration_selfie_with_license" and not extracted_fields and mime.startswith("image/"):
            return True
        return False
