from __future__ import annotations

from app.dialog.prompts import DOCUMENT_STATE_MAP, PROMPTS
from app.dialog.states import DialogueState
from app.drivers.models import Driver
from app.vehicles.models import Vehicle

DOCUMENT_SEQUENCE: list[tuple[DialogueState, str]] = [
    (DialogueState.ASK_DRIVER_LICENSE_FRONT, "driver_license_front"),
    (DialogueState.ASK_DRIVER_LICENSE_BACK, "driver_license_back"),
    (DialogueState.ASK_ID_CARD, "id_card"),
    (DialogueState.ASK_VEHICLE_REGISTRATION_DOC, "vehicle_registration_doc"),
    (DialogueState.ASK_SELFIE_WITH_LICENSE, "selfie_with_license"),
]

DATA_DOCUMENT_TYPES = {
    "driver_license_front",
    "driver_license_back",
    "id_card",
    "vehicle_registration_doc",
}

SATISFIED_DOCUMENT_STATUSES = {
    "uploaded",
    "stored_in_whatsapp",
    "debug_saved",
    "skipped_manual",
}

DOCUMENT_STATES = frozenset(state for state, _ in DOCUMENT_SEQUENCE)

MANUAL_DATA_ENTRY_REPLY = (
    "✅ Хорошо, заполним данные вручную по шагам — как в удостоверении и документах.\n"
    "📸 Селфи с правами всё равно понадобится перед отправкой в парк."
)

TEXT_FIELD_SEQUENCE: list[DialogueState] = [
    DialogueState.ASK_FULL_NAME,
    DialogueState.ASK_PHONE,
    DialogueState.ASK_CITY,
    DialogueState.ASK_ADDRESS,
    DialogueState.ASK_IIN,
    DialogueState.ASK_BIRTH_DATE,
    DialogueState.ASK_DRIVING_EXPERIENCE_SINCE,
    DialogueState.ASK_CAR_BRAND,
    DialogueState.ASK_CAR_MODEL,
    DialogueState.ASK_CAR_YEAR,
    DialogueState.ASK_CAR_PLATE,
    DialogueState.ASK_CAR_COLOR,
    DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE,
    DialogueState.ASK_DRIVER_LICENSE_NUMBER,
    DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE,
    DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT,
    DialogueState.ASK_EMPLOYMENT_TYPE,
    DialogueState.ASK_HIRED_AT,
    DialogueState.ASK_HEARING_IMPAIRED,
]


def next_text_state_after(state: DialogueState) -> DialogueState:
    try:
        index = TEXT_FIELD_SEQUENCE.index(state)
    except ValueError:
        return state
    if index + 1 < len(TEXT_FIELD_SEQUENCE):
        return TEXT_FIELD_SEQUENCE[index + 1]
    return DialogueState.CONFIRM_DATA

STATE_TO_DRIVER_FIELD: dict[DialogueState, str] = {
    DialogueState.ASK_FULL_NAME: "full_name",
    DialogueState.ASK_PHONE: "phone",
    DialogueState.ASK_CITY: "city",
    DialogueState.ASK_ADDRESS: "address",
    DialogueState.ASK_IIN: "iin",
    DialogueState.ASK_BIRTH_DATE: "birth_date",
    DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "driving_experience_since",
    DialogueState.ASK_DRIVER_LICENSE_NUMBER: "driver_license_number",
    DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "driver_license_issue_date",
    DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "driver_license_expires_at",
    DialogueState.ASK_EMPLOYMENT_TYPE: "employment_type",
    DialogueState.ASK_HIRED_AT: "hired_at",
    DialogueState.ASK_HEARING_IMPAIRED: "is_hearing_impaired",
}

STATE_TO_VEHICLE_FIELD: dict[DialogueState, str] = {
    DialogueState.ASK_CAR_BRAND: "brand",
    DialogueState.ASK_CAR_MODEL: "model",
    DialogueState.ASK_CAR_YEAR: "year",
    DialogueState.ASK_CAR_PLATE: "plate_number",
    DialogueState.ASK_CAR_COLOR: "color",
    DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "registration_certificate",
}

LICENSE_DOCUMENT_TYPES = {"driver_license_front", "driver_license_back"}


def expand_uploaded_document_types(
    primary_type: str,
    *,
    mime_type: str | None = None,
    contains_both_license_sides: bool = False,
    additional_document_types: list[str] | None = None,
) -> list[str]:
    """Mark every recognized document slot from one upload."""
    ordered: list[str] = [primary_type]
    for document_type in additional_document_types or []:
        if document_type != "unknown" and document_type not in ordered:
            ordered.append(document_type)

    if primary_type in LICENSE_DOCUMENT_TYPES:
        partner = "driver_license_back" if primary_type == "driver_license_front" else "driver_license_front"
        is_pdf = (mime_type or "").lower() == "application/pdf"
        if (is_pdf or contains_both_license_sides) and partner not in ordered:
            ordered.append(partner)
    return ordered


DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "driver_license_front": "водительское удостоверение (лицевая)",
    "driver_license_back": "водительское удостоверение (обратная)",
    "id_card": "удостоверение личности",
    "vehicle_registration_doc": "техпаспорт / СТС",
    "selfie_with_license": "селфи с правами",
}

EXTRACTED_FIELD_LABELS: dict[str, str] = {
    "full_name": "ФИО",
    "iin": "ИИН",
    "birth_date": "Дата рождения",
    "address": "Адрес",
    "driver_license_number": "Номер ВУ",
    "driver_license_issue_date": "ВУ выдано",
    "driver_license_expires_at": "ВУ действует до",
    "driving_experience_since": "Стаж с",
    "brand": "Марка",
    "model": "Модель",
    "year": "Год авто",
    "plate_number": "Госномер",
    "color": "Цвет",
    "registration_certificate": "Номер СТС",
    "vin": "VIN",
}


def prefers_manual_data_entry(driver: Driver) -> bool:
    context = driver.support_context_json or {}
    return bool(context.get("manual_data_entry"))


def set_manual_data_entry(driver: Driver, *, enabled: bool = True) -> None:
    context = dict(driver.support_context_json or {})
    if enabled:
        context["manual_data_entry"] = True
    else:
        context.pop("manual_data_entry", None)
    driver.support_context_json = context or None


def uploaded_document_types(driver: Driver) -> set[str]:
    return {
        document.document_type
        for document in driver.documents
        if document.document_type
        and document.status in SATISFIED_DOCUMENT_STATUSES
    }


def skip_data_documents_for_manual_entry(db, driver: Driver) -> list[str]:
    from app.documents.service import upsert_document

    skipped: list[str] = []
    for document_type in DATA_DOCUMENT_TYPES:
        if document_type in uploaded_document_types(driver):
            continue
        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=None,
            google_drive_file_id=None,
            whatsapp_media_id=None,
            status="skipped_manual",
            storage_provider="manual_entry",
        )
        skipped.append(document_type)
    set_manual_data_entry(driver, enabled=True)
    db.add(driver)
    db.flush()
    return skipped


def is_text_field_filled(driver: Driver, vehicle: Vehicle | None, state: DialogueState) -> bool:
    if state == DialogueState.ASK_FULL_NAME:
        return bool((driver.full_name or "").strip())
    if state in STATE_TO_DRIVER_FIELD:
        value = getattr(driver, STATE_TO_DRIVER_FIELD[state], None)
        return bool(str(value or "").strip())
    if state in STATE_TO_VEHICLE_FIELD:
        if vehicle is None:
            return False
        value = getattr(vehicle, STATE_TO_VEHICLE_FIELD[state], None)
        return bool(str(value or "").strip())
    return False


def next_registration_state(driver: Driver, vehicle: Vehicle | None = None) -> DialogueState:
    uploaded = uploaded_document_types(driver)
    manual = prefers_manual_data_entry(driver)

    if not manual:
        for state, document_type in DOCUMENT_SEQUENCE:
            if document_type not in uploaded:
                return state

    for state in TEXT_FIELD_SEQUENCE:
        if not is_text_field_filled(driver, vehicle, state):
            return state

    if "selfie_with_license" not in uploaded:
        return DialogueState.ASK_SELFIE_WITH_LICENSE

    return DialogueState.CONFIRM_DATA


def is_expecting_data_document(driver: Driver, state: DialogueState) -> bool:
    if prefers_manual_data_entry(driver):
        return False
    if state in DOCUMENT_STATE_MAP and DOCUMENT_STATE_MAP[state] in DATA_DOCUMENT_TYPES:
        return True
    next_state = next_registration_state(driver, driver.vehicle)
    return next_state in DOCUMENT_STATE_MAP and DOCUMENT_STATE_MAP[next_state] in DATA_DOCUMENT_TYPES


def is_registration_collecting_state(state: DialogueState) -> bool:
    return state in {
        *TEXT_FIELD_SEQUENCE,
        *[item[0] for item in DOCUMENT_SEQUENCE],
        DialogueState.ASK_HAS_CAR,
        DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER,
    }


def resolve_document_type_for_upload(
    state: DialogueState,
    driver: Driver,
    *,
    detected_type: str | None = None,
) -> str | None:
    uploaded = uploaded_document_types(driver)
    if detected_type and detected_type not in uploaded:
        return detected_type
    if state in DOCUMENT_STATE_MAP:
        return DOCUMENT_STATE_MAP[state]
    return None


def prompt_for_state(state: DialogueState) -> str:
    return PROMPTS.get(state, "")


def build_recognition_reply(
    document_types: list[str] | str,
    recognized_fields: dict[str, str],
    next_state: DialogueState,
) -> str:
    types = [document_types] if isinstance(document_types, str) else document_types
    labels = [DOCUMENT_TYPE_LABELS.get(document_type, document_type) for document_type in types]
    if len(labels) == 1:
        lines = [f"✅ Принял фото: {labels[0]}."]
    elif len(labels) == 2 and set(types) == LICENSE_DOCUMENT_TYPES:
        lines = ["✅ Принял PDF: водительское удостоверение (лицевая и обратная сторона)."]
    else:
        lines = [f"✅ Принял: {', '.join(labels)}."]
    if recognized_fields:
        lines.append("🔍 Распознал:")
        for key, value in recognized_fields.items():
            label = EXTRACTED_FIELD_LABELS.get(key, key)
            lines.append(f"• {label}: {value}")
    else:
        lines.append("📋 Данные заполним на следующих шагах.")
    next_prompt = prompt_for_state(next_state)
    if next_state == DialogueState.CONFIRM_DATA:
        return "\n".join(lines)
    if next_prompt:
        lines.append(f"\n📋 Следующий шаг:\n{next_prompt}")
    return "\n".join(lines)
