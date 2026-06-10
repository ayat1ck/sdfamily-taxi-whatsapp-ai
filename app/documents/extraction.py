from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.utils.validators import (
    normalize_car_brand,
    normalize_car_model,
    normalize_driver_license_number,
    normalize_plate_number,
    normalize_registration_certificate,
    parse_date,
    split_full_name,
    validate_kz_iin,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None
    genai_types = None


class DocumentExtractionResult(BaseModel):
    document_type: str | None = None
    full_name: str | None = None
    iin: str | None = None
    birth_date: str | None = None
    address: str | None = None
    driver_license_number: str | None = None
    driver_license_issue_date: str | None = None
    driver_license_expires_at: str | None = None
    driving_experience_since: str | None = None
    brand: str | None = None
    model: str | None = None
    year: str | None = None
    plate_number: str | None = None
    color: str | None = None
    registration_certificate: str | None = None
    vin: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    contains_both_license_sides: bool = False
    additional_document_types: list[str] = Field(default_factory=list)


DOCUMENT_TYPES = {
    "driver_license_front",
    "driver_license_back",
    "id_card",
    "vehicle_registration_doc",
    "selfie_with_license",
    "unknown",
}

EXTRACTION_PROMPT = """Ты помощник таксопарка в Казахстане. По фото или PDF документа извлеки данные для регистрации водителя.

Ожидаемый тип документа: {expected_type}
Если на фото другой документ — укажи его в document_type.

В Казахстане PDF из eGov или Kaspi часто содержит обе стороны водительского удостоверения на одной странице.
Если видишь лицевую и обратную сторону ВУ — поставь contains_both_license_sides=true и добавь оба типа в additional_document_types.

Верни JSON:
{{
  "document_type": "driver_license_front|driver_license_back|id_card|vehicle_registration_doc|selfie_with_license|unknown",
  "contains_both_license_sides": false,
  "additional_document_types": [],
  "full_name": "ФИО полностью или null",
  "iin": "12 цифр ИИН или null",
  "birth_date": "YYYY-MM-DD или null",
  "address": "адрес или null",
  "driver_license_number": "серия и номер ВУ или null",
  "driver_license_issue_date": "YYYY-MM-DD или null",
  "driver_license_expires_at": "YYYY-MM-DD или null",
  "driving_experience_since": "YYYY-MM-DD стаж с или null",
  "brand": "марка авто или null",
  "model": "модель авто или null",
  "year": "год выпуска 4 цифры или null",
  "plate_number": "госномер KZ или null",
  "color": "цвет авто или null",
  "registration_certificate": "номер СТС или null",
  "vin": "VIN или null",
  "confidence": 0.0
}}

Правила:
- Даты только YYYY-MM-DD. Если на документе ДД.ММ.ГГГГ — конвертируй.
- ИИН ровно 12 цифр.
- Не выдумывай значения — только то, что видно на фото.
- selfie_with_license обычно не содержит читаемых полей — верни пустые поля.
"""


class DocumentExtractionService:
    def is_enabled(self) -> bool:
        settings = get_settings()
        if not settings.document_extraction_enabled:
            return False
        return bool(settings.gemini_api_key or settings.openai_api_key)

    def extract(
        self,
        image_bytes: bytes,
        *,
        mime_type: str | None,
        expected_document_type: str,
    ) -> DocumentExtractionResult:
        if not self.is_enabled():
            return DocumentExtractionResult()
        settings = get_settings()
        if settings.gemini_api_key and genai is not None:
            try:
                return self._extract_with_gemini(image_bytes, mime_type=mime_type, expected_document_type=expected_document_type)
            except Exception as exc:
                logger.warning("Gemini document extraction failed: %s", exc)
        return DocumentExtractionResult()

    def _extract_with_gemini(
        self,
        image_bytes: bytes,
        *,
        mime_type: str | None,
        expected_document_type: str,
    ) -> DocumentExtractionResult:
        settings = get_settings()
        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = EXTRACTION_PROMPT.format(expected_type=expected_document_type)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/jpeg"),
                        genai_types.Part.from_text(text=prompt),
                    ],
                )
            ],
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        raw_text = getattr(response, "text", "") or ""
        if not raw_text:
            return DocumentExtractionResult()
        payload = json.loads(raw_text)
        parsed = DocumentExtractionResult.model_validate(payload)
        if parsed.document_type not in DOCUMENT_TYPES:
            parsed.document_type = expected_document_type
        parsed.additional_document_types = [
            item for item in parsed.additional_document_types if item in DOCUMENT_TYPES and item != "unknown"
        ]
        return parsed


def normalize_extracted_fields(
    result: DocumentExtractionResult,
    *,
    document_type: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (fields_to_apply, recognized_for_reply)."""
    raw: dict[str, Any] = result.model_dump()
    fields: dict[str, str] = {}
    recognized: dict[str, str] = {}

    def add_field(key: str, value: str | None) -> None:
        cleaned = (value or "").strip()
        if not cleaned:
            return
        fields[key] = cleaned
        recognized[key] = cleaned

    if raw.get("full_name"):
        full_name = re.sub(r"\s+", " ", str(raw["full_name"]).strip())
        if len(full_name.split()) >= 2:
            add_field("full_name", full_name)
            last_name, first_name, middle_name = split_full_name(full_name)
            if last_name:
                fields["last_name"] = last_name
            if first_name:
                fields["first_name"] = first_name
            if middle_name:
                fields["middle_name"] = middle_name

    iin_digits = re.sub(r"\D+", "", str(raw.get("iin") or ""))
    if len(iin_digits) == 12 and not validate_kz_iin(iin_digits):
        add_field("iin", iin_digits)

    for date_key in (
        "birth_date",
        "driver_license_issue_date",
        "driver_license_expires_at",
        "driving_experience_since",
    ):
        parsed = parse_date(str(raw.get(date_key) or "")) or _coerce_iso_date(raw.get(date_key))
        if parsed:
            add_field(date_key, parsed)

    if raw.get("address"):
        add_field("address", str(raw["address"]))

    if raw.get("driver_license_number"):
        normalized_license = normalize_driver_license_number(str(raw["driver_license_number"]))
        if normalized_license:
            add_field("driver_license_number", normalized_license)

    if raw.get("brand"):
        add_field("brand", normalize_car_brand(str(raw["brand"])))
    if raw.get("model"):
        add_field("model", normalize_car_model(str(raw["model"])))

    year_match = re.search(r"\d{4}", str(raw.get("year") or ""))
    if year_match:
        add_field("year", year_match.group(0))

    if raw.get("plate_number"):
        plate = normalize_plate_number(str(raw["plate_number"]))
        if plate:
            add_field("plate_number", plate)

    if raw.get("color"):
        add_field("color", str(raw["color"]).strip())

    if raw.get("registration_certificate"):
        certificate = normalize_registration_certificate(str(raw["registration_certificate"]))
        if certificate:
            add_field("registration_certificate", certificate)

    if raw.get("vin"):
        vin = re.sub(r"\s+", "", str(raw["vin"]).upper())
        if len(vin) >= 11:
            add_field("vin", vin)

    if document_type == "driver_license_back" and "driving_experience_since" not in fields:
        pass

    if document_type == "selfie_with_license":
        return {}, {}

    if result.confidence < 0.35 and not recognized:
        return {}, {}

    return fields, recognized


def _coerce_iso_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return parse_date(text)
