from datetime import datetime, timedelta
import re

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai_traces.service import upsert_message_ai_trace
from app.applications.service import get_or_create_application, set_application_status
from app.audit.service import create_audit_log
from app.config import get_settings
from app.conversation_events.service import create_conversation_event
from app.dialog.ai import AIResult, get_ai_service
from app.dialog.prompts import (
    DOCUMENT_STATE_MAP,
    PROMPTS,
    STATUS_COLLECTING_DATA_TEMPLATE,
    STATUS_FALLBACK_TEMPLATE,
    STATUS_REPLIES,
    YANDEX_PRO_INSTALL_TEMPLATE,
    YANDEX_PRO_START_TEMPLATE,
    OFFICE_HOURS,
    REGISTRATION_START_CTA,
    format_in_flow_reply,
)
from app.dialog.faq import classify_dialog_intent, resolve_faq_replies, SMALLTALK_REPLY, FALLBACK_MANAGER_REPLY
from app.dialog.states import DialogueState
from app.documents.extraction import DocumentExtractionService, normalize_extracted_fields
from app.documents.registration_flow import (
    DOCUMENT_TYPE_LABELS,
    MANUAL_DATA_ENTRY_REPLY,
    build_recognition_reply,
    expand_uploaded_document_types,
    is_expecting_data_document,
    is_registration_collecting_state,
    next_text_state_after,
    next_registration_state,
    resolve_document_type_for_upload,
    set_manual_data_entry,
    skip_data_documents_for_manual_entry,
)
from app.documents.service import upsert_document
from app.drivers.models import Driver
from app.drivers.service import (
    find_driver_by_iin,
    find_driver_by_phone,
    find_driver_by_whatsapp_phone,
    find_other_driver_by_iin,
    update_driver_state,
)
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.yandex.catalog import resolve_brand_input, resolve_model_input
from app.integrations.yandex.client import YandexPartialSubmissionError
from app.integrations.yandex.messages import (
    build_yandex_error_reply,
    format_validation_errors_for_user,
    format_yandex_error_for_user,
)
from app.integrations.yandex.service import YandexSubmissionService
from app.messages.service import create_message
from app.unknown_intents.service import create_unknown_intent
from app.messages.models import Message
from app.utils.logger import get_logger
from app.utils.text import repair_mojibake
from app.utils.validators import (
    looks_like_manual_data_entry,
    normalize_car_brand,
    normalize_car_model,
    normalize_plate_number,
    normalize_registration_certificate,
    normalize_text_token,
)
from app.vehicles.service import find_vehicle_by_plate_number, get_or_create_vehicle
from app.whatsapp.media import WhatsAppMediaClient
from app.whatsapp.parser import ParsedWhatsAppMessage

logger = get_logger(__name__)

NON_WORD_INPUT_RE = re.compile(r"^[\W_]+$", re.UNICODE)

SUPPORT_INTENTS = {
    "existing_driver_support",
    "human_operator",
    "payout_support",
    "tariff_support",
    "yandex_problem",
    "blocking_support",
    "rental_car_question",
    "courier_registration",
}

DUPLICATE_REJECTED_REPLY = (
    "Р СћР В°Р С”Р С•Р в„– Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р….\n\n"
    "Р вЂќР С•РЎРѓРЎвЂљРЎС“Р С—Р Р…РЎвЂ№Р Вµ Р Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†Р С‘РЎРЏ:\n"
    "1. Р РЋРЎвЂљР В°РЎвЂљРЎРЉ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљРЎвЂ№Р С\n"
    "2. Р ВР В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ\n"
    "3. Р РЋР СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ\n"
    "4. Р СџР С•Р СР С•РЎвЂ°РЎРЉ РЎРѓР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С•Р С\n"
    "5. Р РЋР Р†РЎРЏР В·Р В°РЎвЂљРЎРЉРЎРѓРЎРЏ РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р С•Р С"
)

YANDEX_PRO_SUCCESS_KEYWORDS = {
    "Р Р†Р С•РЎв‚¬Р ВµР В»",
    "Р Р†Р С•РЎв‚¬РЎвЂР В»",
    "voshyol",
    "voshel",
    "voshol",
    "gotovo",
    "Р С–Р С•РЎвЂљР С•Р Р†Р С•",
    "Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В»Р С•РЎРѓРЎРЉ",
    "Р В°Р Р†РЎвЂљР С•РЎР‚Р С‘Р В·Р С•Р Р†Р В°Р В»РЎРѓРЎРЏ",
    "Р В·Р В°РЎв‚¬Р ВµР В»",
    "Р В·Р В°РЎв‚¬РЎвЂР В»",
}

SUPPORT_FLOWS = {
    "yandex_login": {
        "intro": "Р СџР С•Р СР С•Р С–РЎС“ РЎРѓР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С•Р С Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•. Р СџРЎР‚Р С•Р в„–Р Т‘Р ВµР С РЎв‚¬Р В°Р С–Р С‘ Р С—Р С• Р С—Р С•РЎР‚РЎРЏР Т‘Р С”РЎС“.",
        "reply": "Р РЋР ВµР в„–РЎвЂЎР В°РЎРѓ РЎР‚Р В°Р В·Р В±Р ВµРЎР‚Р ВµР С Р Р†РЎвЂ¦Р С•Р Т‘ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• Р С—Р С•РЎв‚¬Р В°Р С–Р С•Р Р†Р С•.",
        "completed": "Р С›РЎвЂљР В»Р С‘РЎвЂЎР Р…Р С•. Р вЂўРЎРѓР В»Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘ Р Р†РЎвЂ№Р С—Р С•Р В»Р Р…Р ВµР Р… Р С‘ Р С—РЎР‚Р С‘Р В»Р С•Р В¶Р ВµР Р…Р С‘Р Вµ Р С•РЎвЂљР С”РЎР‚РЎвЂ№Р В»Р С•РЎРѓРЎРЉ, Р СР С•Р В¶Р ВµРЎвЂљР Вµ Р Р†РЎвЂ№РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљРЎРЉ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹. Р вЂўРЎРѓР В»Р С‘ РЎвЂЎРЎвЂљР С•-РЎвЂљР С• Р ВµРЎвЂ°Р Вµ Р СР ВµРЎв‚¬Р В°Р ВµРЎвЂљ, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С•.",
        "steps": [
            "Р С›РЎвЂљР С”РЎР‚Р С•Р в„–РЎвЂљР Вµ Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• Р С‘ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљР Вµ Р С—Р С• РЎвЂљР С•Р СРЎС“ Р В¶Р Вµ Р Р…Р С•Р СР ВµРЎР‚РЎС“ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°, Р С”Р С•РЎвЂљР С•РЎР‚РЎвЂ№Р в„– РЎС“Р С”Р В°Р В·РЎвЂ№Р Р†Р В°Р В»Р С‘ Р Р† Р В°Р Р…Р С”Р ВµРЎвЂљР Вµ.",
            "Р вЂўРЎРѓР В»Р С‘ Р С—РЎР‚Р С‘Р В»Р С•Р В¶Р ВµР Р…Р С‘Р Вµ Р С—РЎР‚Р С•РЎРѓР С‘РЎвЂљ Р С”Р С•Р Т‘, Р Т‘Р С•Р В¶Р Т‘Р С‘РЎвЂљР ВµРЎРѓРЎРЉ SMS Р С‘ Р Р†Р Р†Р ВµР Т‘Р С‘РЎвЂљР Вµ Р С”Р С•Р Т‘ Р С—Р С•Р Т‘РЎвЂљР Р†Р ВµРЎР‚Р В¶Р Т‘Р ВµР Р…Р С‘РЎРЏ Р В±Р ВµР В· Р С•РЎв‚¬Р С‘Р В±Р С•Р С”.",
            "Р СџР С•РЎРѓР В»Р Вµ Р Р†РЎвЂ¦Р С•Р Т‘Р В° Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ, Р С•РЎвЂљР С”РЎР‚РЎвЂ№Р Р†Р В°Р ВµРЎвЂљРЎРѓРЎРЏ Р В»Р С‘ Р С–Р В»Р В°Р Р†Р Р…РЎвЂ№Р в„– РЎРЊР С”РЎР‚Р В°Р Р… Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЏ Р С‘ Р Р†Р С‘Р Т‘Р Р…РЎвЂ№ Р В»Р С‘ РЎР‚Р В°Р В±Р С•РЎвЂЎР С‘Р Вµ РЎР‚Р В°Р В·Р Т‘Р ВµР В»РЎвЂ№.",
        ],
    },
    "yandex_sms": {
        "intro": "Р СџР С•Р СР С•Р С–РЎС“, Р ВµРЎРѓР В»Р С‘ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ SMS Р С•РЎвЂљ Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•.",
        "reply": "Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘Р С Р С—Р С• РЎв‚¬Р В°Р С–Р В°Р С, Р С—Р С•РЎвЂЎР ВµР СРЎС“ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ SMS.",
        "completed": "Р вЂўРЎРѓР В»Р С‘ Р С”Р С•Р Т‘ Р С—РЎР‚Р С‘РЎв‚¬Р ВµР В» Р С‘ Р Р†РЎвЂ№ Р Р†Р С•РЎв‚¬Р В»Р С‘, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, Р ВµРЎРѓР В»Р С‘ Р Р…РЎС“Р В¶Р Р…Р В° Р С—Р С•Р СР С•РЎвЂ°РЎРЉ РЎРѓР С• РЎРѓР В»Р ВµР Т‘РЎС“РЎР‹РЎвЂ°Р С‘Р С РЎв‚¬Р В°Р С–Р С•Р С.",
        "steps": [
            "Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В° Р Р†Р Р†Р ВµР Т‘Р ВµР Р… Р В±Р ВµР В· Р С•РЎв‚¬Р С‘Р В±Р С”Р С‘ Р С‘ РЎРѓР С•Р Р†Р С—Р В°Р Т‘Р В°Р ВµРЎвЂљ РЎРѓ Р Р…Р С•Р СР ВµРЎР‚Р С•Р С, Р С”Р С•РЎвЂљР С•РЎР‚РЎвЂ№Р в„– Р Р†РЎвЂ№ РЎС“Р С”Р В°Р В·Р В°Р В»Р С‘ Р С—РЎР‚Р С‘ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘Р С‘.",
            "Р СџР С•Р Т‘Р С•Р В¶Р Т‘Р С‘РЎвЂљР Вµ 1-2 Р СР С‘Р Р…РЎС“РЎвЂљРЎвЂ№ Р С‘ Р В·Р В°Р С—РЎР‚Р С•РЎРѓР С‘РЎвЂљР Вµ Р С”Р С•Р Т‘ Р ВµРЎвЂ°Р Вµ РЎР‚Р В°Р В·. Р ВР Р…Р С•Р С–Р Т‘Р В° SMS Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ Р Р…Р Вµ РЎРѓРЎР‚Р В°Р В·РЎС“.",
            "Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ РЎРѓР Р†РЎРЏР В·РЎРЉ, Р С—Р ВµРЎР‚Р ВµР В·Р В°Р С—РЎС“РЎРѓРЎвЂљР С‘РЎвЂљР Вµ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р… Р С‘Р В»Р С‘ Р С•РЎвЂљР С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљР Вµ РЎР‚Р ВµР В¶Р С‘Р С Р С—Р С•Р В»Р ВµРЎвЂљР В°, Р В·Р В°РЎвЂљР ВµР С РЎРѓР Р…Р С•Р Р†Р В° Р В·Р В°Р С—РЎР‚Р С•РЎРѓР С‘РЎвЂљР Вµ Р С”Р С•Р Т‘.",
        ],
    },
    "account_inactive": {
        "intro": "Р СџР С•Р СР С•Р С–РЎС“ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘РЎвЂљРЎРЉ, Р С—Р С•РЎвЂЎР ВµР СРЎС“ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р….",
        "reply": "Р В Р В°Р В·Р В±Р ВµРЎР‚Р ВµР С РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В° Р С—Р С• РЎв‚¬Р В°Р С–Р В°Р С.",
        "completed": "Р вЂўРЎРѓР В»Р С‘ РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В° Р С•Р В±Р Р…Р С•Р Р†Р С‘Р В»РЎРѓРЎРЏ Р С‘ Р СР С•Р В¶Р Р…Р С• Р С—РЎР‚Р С•Р Т‘Р С•Р В»Р В¶Р В°РЎвЂљРЎРЉ, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, Р ВµРЎРѓР В»Р С‘ Р Р…РЎС“Р В¶Р Р…Р В° Р С—Р С•Р СР С•РЎвЂ°РЎРЉ Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ.",
        "steps": [
            "Р вЂ”Р В°Р С”РЎР‚Р С•Р в„–РЎвЂљР Вµ Р С‘ Р В·Р В°Р Р…Р С•Р Р†Р С• Р С•РЎвЂљР С”РЎР‚Р С•Р в„–РЎвЂљР Вµ Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•, Р В·Р В°РЎвЂљР ВµР С Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ, Р С‘Р В·Р СР ВµР Р…Р С‘Р В»РЎРѓРЎРЏ Р В»Р С‘ РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В°.",
            "Р Р€Р В±Р ВµР Т‘Р С‘РЎвЂљР ВµРЎРѓРЎРЉ, РЎвЂЎРЎвЂљР С• РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р Р† Р С—Р В°РЎР‚Р С”Р Вµ РЎС“Р В¶Р Вµ Р В·Р В°Р Р†Р ВµРЎР‚РЎв‚¬Р ВµР Р…Р В° Р С‘ Р Р†РЎвЂ№ Р Р†РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљР Вµ Р С—Р С• Р С—РЎР‚Р В°Р Р†Р С‘Р В»РЎРЉР Р…Р С•Р СРЎС“ Р Р…Р С•Р СР ВµРЎР‚РЎС“.",
            "Р вЂўРЎРѓР В»Р С‘ РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ Р Р…Р Вµ Р СР ВµР Р…РЎРЏР ВµРЎвЂљРЎРѓРЎРЏ, Р С—Р С•Р Т‘Р С–Р С•РЎвЂљР С•Р Р†РЎРЉРЎвЂљР Вµ Р С”Р С•РЎР‚Р С•РЎвЂљР С”Р С•Р Вµ Р С•Р С—Р С‘РЎРѓР В°Р Р…Р С‘Р Вµ Р С•РЎв‚¬Р С‘Р В±Р С”Р С‘ Р С‘Р В»Р С‘ РЎвЂљР ВµР С”РЎРѓРЎвЂљ Р Р…Р В° РЎРЊР С”РЎР‚Р В°Р Р…Р Вµ Р Т‘Р В»РЎРЏ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р В°.",
        ],
    },
    "go_online": {
        "intro": "Р СџР С•Р СР С•Р С–РЎС“ Р Р†РЎвЂ№Р в„–РЎвЂљР С‘ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•.",
        "reply": "Р ВР Т‘Р ВµР С Р С—Р С• РЎв‚¬Р В°Р С–Р В°Р С, РЎвЂЎРЎвЂљР С•Р В±РЎвЂ№ Р Р†РЎвЂ№Р в„–РЎвЂљР С‘ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹.",
        "completed": "Р вЂњР С•РЎвЂљР С•Р Р†Р С•. Р вЂўРЎРѓР В»Р С‘ Р В»Р С‘Р Р…Р С‘РЎРЏ Р С•РЎвЂљР С”РЎР‚РЎвЂ№Р В»Р В°РЎРѓРЎРЉ, Р СР С•Р В¶Р ВµРЎвЂљР Вµ Р Р…Р В°РЎвЂЎР С‘Р Р…Р В°РЎвЂљРЎРЉ РЎР‚Р В°Р В±Р С•РЎвЂљРЎС“. Р вЂўРЎРѓР В»Р С‘ РЎвЂЎРЎвЂљР С•-РЎвЂљР С• Р СР ВµРЎв‚¬Р В°Р ВµРЎвЂљ Р С—РЎР‚Р С‘Р Р…РЎРЏРЎвЂљРЎРЉ Р В·Р В°Р С”Р В°Р В·, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С•.",
        "steps": [
            "Р С›РЎвЂљР С”РЎР‚Р С•Р в„–РЎвЂљР Вµ Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• Р С‘ РЎС“Р В±Р ВµР Т‘Р С‘РЎвЂљР ВµРЎРѓРЎРЉ, РЎвЂЎРЎвЂљР С• Р Р†РЎвЂ¦Р С•Р Т‘ Р Р†РЎвЂ№Р С—Р С•Р В»Р Р…Р ВµР Р… Р С—Р С•Р Т‘ Р Р†Р В°РЎв‚¬Р С‘Р С РЎР‚Р В°Р В±Р С•РЎвЂЎР С‘Р С Р Р…Р С•Р СР ВµРЎР‚Р С•Р С.",
            "Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р Р† Р С—РЎР‚Р С‘Р В»Р С•Р В¶Р ВµР Р…Р С‘Р С‘ Р В·Р В°Р С—Р С•Р В»Р Р…Р ВµР Р…РЎвЂ№ Р С•Р В±РЎРЏР В·Р В°РЎвЂљР ВµР В»РЎРЉР Р…РЎвЂ№Р Вµ РЎв‚¬Р В°Р С–Р С‘ Р С‘ Р Р…Р ВµРЎвЂљ Р В±Р В»Р С•Р С”Р С‘РЎР‚РЎС“РЎР‹РЎвЂ°Р С‘РЎвЂ¦ Р С—РЎР‚Р ВµР Т‘РЎС“Р С—РЎР‚Р ВµР В¶Р Т‘Р ВµР Р…Р С‘Р в„–.",
            "Р СњР В°Р В¶Р СР С‘РЎвЂљР Вµ Р С”Р Р…Р С•Р С—Р С”РЎС“ Р Р†РЎвЂ№РЎвЂ¦Р С•Р Т‘Р В° Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹ Р С‘ Р Т‘Р С•Р В¶Р Т‘Р С‘РЎвЂљР ВµРЎРѓРЎРЉ, Р С—Р С•Р С”Р В° Р С—РЎР‚Р С‘Р В»Р С•Р В¶Р ВµР Р…Р С‘Р Вµ Р С—Р С•Р С”Р В°Р В¶Р ВµРЎвЂљ Р В°Р С”РЎвЂљР С‘Р Р†Р Р…РЎвЂ№Р в„– РЎР‚Р В°Р В±Р С•РЎвЂЎР С‘Р в„– РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ.",
        ],
    },
}


class DialogueEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ai = get_ai_service()
        self.drive = GoogleDriveClient()
        self.sheets = GoogleSheetsClient()
        self.yandex = YandexSubmissionService()
        self.media = WhatsAppMediaClient()
        self.document_extractor = DocumentExtractionService()

    def handle_message(self, db: Session, driver: Driver, incoming: ParsedWhatsAppMessage) -> str:
        application = get_or_create_application(db, driver)
        driver.last_message_at = datetime.utcnow()
        driver.unread_count = (driver.unread_count or 0) + 1
        incoming_message = create_message(
            db,
            driver=driver,
            direction="incoming",
            sender_type="customer",
            message_type=incoming.message_type,
            text=incoming.text,
            provider_message_id=incoming.provider_message_id,
            media_url=incoming.media_id,
            mime_type=incoming.mime_type,
            delivery_status="received",
            raw_payload=incoming.raw_payload,
        )
        db.add(driver)
        db.flush()
        self._reset_stale_support_context(driver)
        memory = self._load_conversation_memory(db, driver)
        self._remember_message_context(driver, incoming, memory)
        state = DialogueState(driver.state or DialogueState.NEW.value)
        self._touch_support_context(driver)

        if (
            self._is_active_flow(state)
            and incoming.message_type == "text"
            and self._is_non_answer_text(incoming.text or "")
        ):
            self._register_fallback(
                db,
                driver,
                application,
                state_before=state.value,
                reason="non_answer_text_in_active_flow",
                message_id=incoming_message.id,
                message_text=incoming.text or "",
                message_type=incoming.message_type,
            )
            self._record_registration_debug_event(
                db,
                driver,
                state_before=state.value,
                message_type=incoming.message_type,
                media_context="text_message",
                detected_document_type=None,
                extracted_fields={},
                state_after=state.value,
                submit_called=False,
                message_id=incoming_message.id,
                mime_type=incoming.mime_type,
                debug_source="non_answer_text_in_active_flow",
            )
            return self._respond(
                db,
                driver,
                application,
                self._repeat_current_question(state, "Нужен ответ по текущему шагу."),
            )

        pending_menu_reply = self._handle_pending_menu(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if pending_menu_reply:
            return self._respond(db, driver, application, pending_menu_reply)

        active_flow_reply = self._handle_active_pending_action(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if active_flow_reply:
            return self._respond(db, driver, application, active_flow_reply)

        if incoming.message_type == "text" and _looks_like_operator_request(normalize_text_token(incoming.text or "")):
            priority_reply = self._handle_priority_interrupts(
                db,
                driver,
                application,
                state,
                incoming.text or "",
                incoming_message.id,
            )
            if priority_reply:
                return self._respond(db, driver, application, priority_reply)

        if state == DialogueState.DUPLICATE_REJECTED and incoming.message_type in {"unsupported", "image", "document"}:
            return self._respond(db, driver, application, DUPLICATE_REJECTED_REPLY)

        if incoming.message_type == "unsupported":
            return self._respond(db, driver, application, "Поддерживаются только текст, изображение и документ.")

        support_menu_reply = self._handle_stateful_support_menu(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if support_menu_reply:
            return self._respond(db, driver, application, support_menu_reply)

        command_reply = self._handle_special_commands(db, driver, application, incoming.text or "")
        if command_reply:
            self._record_system_trace(
                db,
                incoming_message.id,
                driver,
                state.value,
                incoming.text or "",
                intent="special_command",
                reply=command_reply,
                reasoning_summary="special_command",
            )
            return self._respond(db, driver, application, command_reply)

        priority_reply = self._handle_priority_interrupts(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if priority_reply:
            return self._respond(db, driver, application, priority_reply)

        if incoming.message_type in {"image", "document"}:
            return self._handle_document(db, driver, application, incoming, incoming_message.id)

        if incoming.message_type == "text" and state in DOCUMENT_STATE_MAP and not looks_like_manual_data_entry(incoming.text or ""):
            self._record_registration_debug_event(
                db,
                driver,
                state_before=state.value,
                message_type=incoming.message_type,
                media_context="text_message",
                detected_document_type=None,
                extracted_fields={},
                state_after=state.value,
                submit_called=False,
                message_id=incoming_message.id,
                mime_type=incoming.mime_type,
                debug_source="text_during_doc_step",
            )
            return self._respond(
                db,
                driver,
                application,
                self._repeat_current_question(
                    state,
                    "РЎРµР№С‡Р°СЃ РЅСѓР¶РµРЅ РґРѕРєСѓРјРµРЅС‚. РћС‚РїСЂР°РІСЊС‚Рµ С„РѕС‚Рѕ РёР»Рё PDF С‚РѕРіРѕ РґРѕРєСѓРјРµРЅС‚Р°, РєРѕС‚РѕСЂС‹Р№ СЏ Р·Р°РїСЂРѕСЃРёР» РЅР° СЌС‚РѕРј С€Р°РіРµ.",
                ),
            )

        if looks_like_manual_data_entry(incoming.text or "") and is_expecting_data_document(driver, state):
            reply = (
                "Р вЂќР В»РЎРЏ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘Р С‘ Р С•РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ PDF Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР В°. "
                "Р СџР С• РЎвЂћР С•РЎвЂљР С• Р В±Р С•РЎвЂљ Р В·Р В°Р С—Р С•Р В»Р Р…Р С‘РЎвЂљ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р В°Р Р†РЎвЂљР С•Р СР В°РЎвЂљР С‘РЎвЂЎР ВµРЎРѓР С”Р С‘.\n\n"
                f"{PROMPTS[state]}"
            )
            return self._respond(db, driver, application, reply)

        pending_field = self._get_pending_field_edit(driver)
        if pending_field and state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return self._handle_pending_field_edit_value(
                db,
                driver,
                application,
                state,
                incoming.text or "",
                incoming_message.id,
                pending_field,
            )

        if state == DialogueState.COMPLETED:
            return self._handle_registered_driver_support(db, driver, application, incoming.text or "", incoming_message.id)

        if self._is_yandex_pro_followup_state(state):
            return self._handle_yandex_pro_followup(db, driver, application, state, incoming.text or "", incoming_message.id)

        if state == DialogueState.NEW:
            normalized_new_message = normalize_text_token(incoming.text or "")
            deterministic_intent = classify_dialog_intent(incoming.text or "", current_state=state.value)
            if normalized_new_message == "2":
                faq_reply = resolve_faq_replies(
                    "какие условия",
                    self.ai.knowledge_base,
                    office_address=self.settings.public_site_address,
                ) or FALLBACK_MANAGER_REPLY
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(faq_reply))

            if deterministic_intent == "registration" and _looks_like_registration_start_request(incoming.text or ""):
                create_conversation_event(db, driver, "started_onboarding")
                set_application_status(db, application, "collecting_data")
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply("Отлично! Начинаем регистрацию."),
                )
            ai_result = self.ai.respond(state.value, incoming.text or "", driver)
            if ai_result.confidence < 0.75 and ai_result.intent not in {"faq", "help", "smalltalk"}:
                ai_result.action = "ask_clarification"
            self._record_ai_trace(
                db,
                incoming_message.id,
                driver,
                state.value,
                incoming.text or "",
                ai_result,
                active_flow_after=state.value,
                decision_source="backend_router",
            )
            normalized_new_message = normalize_text_token(incoming.text or "")
            if normalized_new_message in {"2", "РЎС“РЎРѓР В»Р С•Р Р†Р С‘РЎРЏ", "РЎС“РЎРѓР В»Р С•Р Р†Р С‘Р Вµ", "РЎвЂљР В°РЎР‚Р С‘РЎвЂћРЎвЂ№", "Р С”Р С•Р СР С‘РЎРѓРЎРѓР С‘РЎРЏ"}:
                faq_reply = resolve_faq_replies(
                    "Р С”Р В°Р С”Р С‘Р Вµ РЎС“РЎРѓР В»Р С•Р Р†Р С‘РЎРЏ",
                    self.ai.knowledge_base,
                    office_address=self.settings.public_site_address,
                ) or FALLBACK_MANAGER_REPLY
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(faq_reply))

            if normalized_new_message in {
                "3",
                "Р Р†РЎвЂ¦Р С•Р Т‘",
                "Р Р†РЎвЂ¦Р С•Р Т‘ Р Р† РЎРЏР Р…Р Т‘Р ВµР С”РЎРѓ Р С—РЎР‚Р С•",
                "РЎРЏР Р…Р Т‘Р ВµР С”РЎРѓ Р С—РЎР‚Р С•",
                "Р С—Р С•Р СР С•РЎвЂ°РЎРЉ РЎРѓР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С•Р С",
                "Р В»Р С•Р С–Р С‘Р Р…",
            }:
                reply = (
                    "Р СџР С•Р СР С•Р С–РЎС“ РЎРѓР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С•Р С Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•.\n\n"
                    "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С• Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ:\n"
                    "1. Р СњР Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ SMS\n"
                    "2. Р СњР Вµ Р Р†Р С‘Р В¶РЎС“ Р С—Р В°РЎР‚Р С”\n"
                    "3. Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С—РЎР‚Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘Р Вµ"
                )
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(reply))
            if any(marker in normalized_new_message for marker in ("РЎвЂљРЎвЂ№РЎР‚Р С”Р ВµР В»", "РЎвЂљРЎР‚Р С”Р ВµР В»", "РЎвЂљРЎвЂ№РЎР‚Р С”Р ВµРЎС“", "РЎвЂљРЎР‚Р С”Р ВµРЎС“")):
                create_conversation_event(db, driver, "started_onboarding")
                set_application_status(db, application, "collecting_data")
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply("СЂСџвЂвЂ№ Р С›РЎвЂљР В»Р С‘РЎвЂЎР Р…Р С•! Р СњР В°РЎвЂЎР С‘Р Р…Р В°Р ВµР С РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎР‹."),
                )

            if _looks_like_registration_start_request(incoming.text or ""):
                create_conversation_event(db, driver, "started_onboarding")
                set_application_status(db, application, "collecting_data")
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply("Р вЂќР В°, Р СР С•Р В¶Р Р…Р С• Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°РЎвЂљРЎРЉРЎРѓРЎРЏ Р Р† SD Family Taxi."),
                )

            if ai_result.intent in {"faq", "help", "smalltalk", *SUPPORT_INTENTS}:
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(ai_result.reply))

            create_conversation_event(db, driver, "started_onboarding")
            set_application_status(db, application, "collecting_data")

            if ai_result.intent == "employment_type_change":
                return self._respond(db, driver, application, "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, Р С—Р С•Р В¶Р В°Р В»РЎС“Р в„–РЎРѓРЎвЂљР В°, Р С”Р В°Р С”Р С•Р в„– РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В° Р Р…РЎС“Р В¶Р ВµР Р…: РЎв‚¬РЎвЂљР В°РЎвЂљР Р…РЎвЂ№Р в„– Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ, Р РЋР СљР вЂ” Р С‘Р В»Р С‘ Р ВР Сџ.")

            if ai_result.intent == "registration" and ai_result.extracted_fields and ai_result.confidence >= 0.75:
                self._mark_successful_progress(driver)
                self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
                next_state = DialogueState.ASK_PHONE
                update_driver_state(db, driver, next_state.value)
                set_application_status(db, application, _application_status_from_state(next_state))
                reply = "СЂСџвЂвЂ№ Р С›РЎвЂљР В»Р С‘РЎвЂЎР Р…Р С•! Р СњР В°РЎвЂЎР С‘Р Р…Р В°Р ВµР С РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎР‹.\n\n" + PROMPTS[next_state]
                return self._respond(db, driver, application, reply)

            if (
                ai_result.suggested_next_action == DialogueState.ASK_FULL_NAME.value
                or ai_result.next_state == DialogueState.ASK_FULL_NAME.value
                or (ai_result.intent == "registration" and not ai_result.extracted_fields)
            ):
                self._mark_successful_progress(driver)
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply(ai_result.reply),
                )

            if ai_result.action == "ask_clarification":
                self._register_fallback(
                    db,
                    driver,
                    application,
                    state_before=state.value,
                    reason="new_state_clarification",
                    message_id=incoming_message.id,
                    message_text=incoming.text or "",
                    message_type=incoming.message_type,
                )
                return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

            return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

        if state == DialogueState.ASK_EXECUTOR_TYPE:
            update_driver_state(db, driver, DialogueState.ASK_PHONE.value)
            set_application_status(db, application, "collecting_data")
            state = DialogueState.ASK_PHONE

        if state == DialogueState.DUPLICATE_REJECTED:
            return self._respond(db, driver, application, DUPLICATE_REJECTED_REPLY)

        if self._is_active_flow(state) and _looks_like_current_step_help_request(incoming.text or ""):
            return self._respond(db, driver, application, self._step_instruction_reply(state))

        if state == DialogueState.ASK_CITY:
            fallback_city = self._extract_city_value(incoming.text or "")
            if fallback_city:
                next_state_value = next_text_state_after(state).value
                ai_result = AIResult(
                    reply="",
                    intent="registration",
                    extracted_fields={"city": fallback_city},
                    next_state=next_state_value,
                    confidence=0.95,
                    normalized_fields={"city": fallback_city},
                    reasoning_summary="engine_direct_city_parse",
                    suggested_next_action=next_state_value,
                )
            else:
                ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        elif state == DialogueState.ASK_ADDRESS:
            fallback_address = self._extract_address_value(incoming.text or "")
            if fallback_address:
                next_state_value = next_text_state_after(state).value
                ai_result = AIResult(
                    reply="",
                    intent="registration",
                    extracted_fields={"address": fallback_address},
                    next_state=next_state_value,
                    confidence=0.95,
                    normalized_fields={"address": fallback_address},
                    reasoning_summary="engine_direct_address_parse",
                    suggested_next_action=next_state_value,
                )
            else:
                ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        else:
            ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        self._record_ai_trace(
            db,
            incoming_message.id,
            driver,
            state.value,
            incoming.text or "",
            ai_result,
            active_flow_after=state.value,
            decision_source="backend_router",
        )
        if self._is_active_flow(state):
            self._record_registration_debug_event(
                db,
                driver,
                state_before=state.value,
                message_type=incoming.message_type,
                media_context="text_message",
                detected_document_type=None,
                extracted_fields=ai_result.normalized_fields or ai_result.extracted_fields or {},
                state_after=ai_result.suggested_next_action or ai_result.next_state or state.value,
                submit_called=False,
                message_id=incoming_message.id,
                mime_type=incoming.mime_type,
                debug_source="active_flow_text",
            )
        if self._is_active_flow(state) and ai_result.intent in {"faq", "help", "smalltalk"}:
            self._register_fallback(
                db,
                driver,
                application,
                state_before=state.value,
                reason=f"active_flow_{ai_result.intent}",
                message_id=incoming_message.id,
                message_text=incoming.text or "",
                message_type=incoming.message_type,
            )
            return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply))
        if ai_result.intent in {"faq", "help", "smalltalk", *SUPPORT_INTENTS}:
            if self._is_active_flow(state) and not self._should_interrupt_active_flow(ai_result):
                self._register_fallback(
                    db,
                    driver,
                    application,
                    state_before=state.value,
                    reason=f"active_flow_{ai_result.intent}",
                    message_id=incoming_message.id,
                    message_text=incoming.text or "",
                    message_type=incoming.message_type,
                )
                return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply))
            return self._respond(db, driver, application, ai_result.reply.strip())
        if ai_result.intent == "clarification":
            self._register_fallback(
                db,
                driver,
                application,
                state_before=state.value,
                reason="clarification_no_progress",
                message_id=incoming_message.id,
                message_text=incoming.text or "",
                message_type=incoming.message_type,
            )
            if ai_result.clear_suggested_clarification:
                self._clear_pending_car_model_suggestion(driver)
            elif ai_result.suggested_clarification_value:
                self._set_pending_car_model_suggestion(driver, ai_result.suggested_clarification_value)
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply))
        if ai_result.intent == "employment_type_change":
            if ai_result.confidence < 0.75:
                return self._respond(db, driver, application, self._repeat_current_question(state, "Р Р€РЎвЂљР С•РЎвЂЎР Р…Р С‘РЎвЂљР Вµ, Р С—Р С•Р В¶Р В°Р В»РЎС“Р в„–РЎРѓРЎвЂљР В°, РЎвЂ¦Р С•РЎвЂљР С‘РЎвЂљР Вµ РЎРѓР СР ВµР Р…Р С‘РЎвЂљРЎРЉ РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В° Р Р…Р В° Р РЋР СљР вЂ”, РЎв‚¬РЎвЂљР В°РЎвЂљР Р…РЎвЂ№Р в„– РЎвЂћР С•РЎР‚Р СР В°РЎвЂљ Р С‘Р В»Р С‘ Р ВР Сџ?"))
            return self._respond(db, driver, application, self._repeat_current_question(state, "Р СџР С•Р Р…РЎРЏР В». Р СџР С•РЎРѓР В»Р Вµ Р В·Р В°Р Р†Р ВµРЎР‚РЎв‚¬Р ВµР Р…Р С‘РЎРЏ РЎвЂљР ВµР С”РЎС“РЎвЂ°Р ВµР С–Р С• РЎв‚¬Р В°Р С–Р В° Р С—Р С•Р СР С•Р С–РЎС“ РЎРѓР СР ВµР Р…Р С‘РЎвЂљРЎРЉ РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В°."))
        if ai_result.intent == "field_edit":
            return self._handle_field_edit(db, driver, application, state, ai_result)
        if ai_result.intent == "correction":
            if ai_result.confidence < 0.75:
                return self._respond(db, driver, application, self._repeat_current_question(state, "Р Р€РЎвЂљР С•РЎвЂЎР Р…Р С‘РЎвЂљР Вµ, Р С—Р С•Р В¶Р В°Р В»РЎС“Р в„–РЎРѓРЎвЂљР В°, Р С”Р В°Р С”Р С•Р Вµ Р С‘Р СР ВµР Р…Р Р…Р С• Р С—Р С•Р В»Р Вµ Р Р…РЎС“Р В¶Р Р…Р С• Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ."))
            correction_state = DialogueState(ai_result.suggested_next_action or ai_result.next_state or state.value)
            pending_target_field = self._correction_state_to_field_name(correction_state)
            if pending_target_field and state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
                self._set_pending_field_edit(driver, pending_target_field, state.value)
                create_conversation_event(
                    db,
                    driver,
                    "field_edit_requested",
                    {
                        "from_state": state.value,
                        "target_field": pending_target_field,
                        "message": incoming.text or "",
                    },
                )
                return self._respond(
                    db,
                    driver,
                    application,
                    f"Р ТђР С•РЎР‚Р С•РЎв‚¬Р С•. Р С›РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ Р Р…Р С•Р Р†Р С•Р Вµ Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ Р Т‘Р В»РЎРЏ Р С—Р С•Р В»РЎРЏ Р’В«{self._field_label(pending_target_field)}Р’В» Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.",
                )
            update_driver_state(db, driver, correction_state.value)
            set_application_status(db, application, _application_status_from_state(correction_state))
            create_conversation_event(
                db,
                driver,
                "correction_requested",
                {"from_state": state.value, "to_state": correction_state.value, "message": incoming.text or ""},
            )
            return self._respond(db, driver, application, ai_result.reply or PROMPTS[correction_state])

        duplicate_reply = self._check_duplicate_constraints(db, driver, application, state, ai_result.extracted_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        if state == DialogueState.ASK_CITY and not ai_result.extracted_fields:
            fallback_city = self._extract_city_value(incoming.text or "")
            if fallback_city:
                next_state_value = next_text_state_after(state).value
                ai_result = AIResult(
                    reply="",
                    intent="registration",
                    extracted_fields={"city": fallback_city},
                    next_state=next_state_value,
                    confidence=0.95,
                    normalized_fields={"city": fallback_city},
                    reasoning_summary="engine_fallback:city",
                    suggested_next_action=next_state_value,
                )

        if ai_result.confidence < 0.75 and ai_result.intent == "registration":
            self._register_fallback(
                db,
                driver,
                application,
                state_before=state.value,
                reason="low_confidence_registration",
                message_id=incoming_message.id,
                message_text=incoming.text or "",
                message_type=incoming.message_type,
            )
            return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply or "Р Р€РЎвЂљР С•РЎвЂЎР Р…Р С‘РЎвЂљР Вµ, Р С—Р С•Р В¶Р В°Р В»РЎС“Р в„–РЎРѓРЎвЂљР В°, Р С•РЎвЂљР Р†Р ВµРЎвЂљ Р Р…Р В° РЎвЂљР ВµР С”РЎС“РЎвЂ°Р С‘Р в„– Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ."))

        self._mark_successful_progress(driver)
        self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
        if "model" in ai_result.extracted_fields:
            self._clear_pending_car_model_suggestion(driver)
        next_state = next_text_state_after(state) if ai_result.extracted_fields else state
        submit_called = False

        if next_state == DialogueState.READY_TO_SEND_YANDEX:
            validation = self.yandex.validate_driver(driver)
            if validation["errors"]:
                retry_state = state if state == DialogueState.YANDEX_ERROR else DialogueState.CONFIRM_DATA
                update_driver_state(db, driver, retry_state.value)
                set_application_status(db, application, "confirming_data" if retry_state == DialogueState.CONFIRM_DATA else "yandex_error")
                issues = format_validation_errors_for_user(validation["errors"])
                return self._respond(
                    db,
                    driver,
                    application,
                    (
                        "Р СџР ВµРЎР‚Р ВµР Т‘ Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С”Р С•Р в„– Р Р…РЎС“Р В¶Р Р…Р С• Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ:\n\n"
                        f"{issues}\n\n"
                        f"{self._build_confirmation(driver, validation=validation)}"
                    ),
                )

            application.yandex_error = None
            db.add(application)
            update_driver_state(db, driver, DialogueState.SENDING_TO_YANDEX.value)
            set_application_status(db, application, "sending_to_yandex")
            self._respond(db, driver, application, PROMPTS[DialogueState.READY_TO_SEND_YANDEX])
            try:
                submit_called = True
                self._record_registration_debug_event(
                    db,
                    driver,
                    state_before=state.value,
                    message_type=incoming.message_type,
                    media_context="text_message",
                    detected_document_type=None,
                    extracted_fields=ai_result.extracted_fields,
                    state_after=DialogueState.SENDING_TO_YANDEX.value,
                    submit_called=True,
                    message_id=incoming_message.id,
                    mime_type=incoming.mime_type,
                    debug_source="submit_attempt",
                )
                self.yandex.submit(db, driver, application)
                update_driver_state(db, driver, DialogueState.ASK_YANDEX_PRO_LOGIN.value)
                set_application_status(db, application, "sent_to_yandex", yandex_status="sent_to_yandex")
                create_conversation_event(db, driver, "submitted_to_yandex")
                create_conversation_event(db, driver, "yandex_pro_guidance_started")
                reply = self._build_yandex_pro_start_reply(driver)
            except YandexPartialSubmissionError as exc:
                update_driver_state(db, driver, DialogueState.YANDEX_ERROR.value)
                set_application_status(db, application, "yandex_error", yandex_status="partial_success", yandex_error=str(exc))
                driver.requires_attention = True
                db.add(driver)
                create_conversation_event(
                    db,
                    driver,
                    "submitted_to_yandex",
                    {
                        "status": "partial_success",
                        "stage": exc.stage,
                        "driver_id": exc.yandex_driver_id,
                        "vehicle_id": exc.yandex_vehicle_id,
                    },
                )
                create_conversation_event(
                    db,
                    driver,
                    "yandex_partial_success",
                    {
                        "error": str(exc),
                        "stage": exc.stage,
                        "driver_id": exc.yandex_driver_id,
                        "vehicle_id": exc.yandex_vehicle_id,
                    },
                )
                if exc.yandex_driver_id and not exc.yandex_vehicle_id:
                    reply = (
                        "Р вЂ™Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ РЎС“Р В¶Р Вµ РЎРѓР С•Р В·Р Т‘Р В°Р Р… Р Р† Р С—Р В°РЎР‚Р С”Р Вµ, Р Р…Р С• Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ Р Р…Р Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ Р Т‘Р С•Р В±Р В°Р Р†Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•Р СР В°РЎвЂљР С‘РЎвЂЎР ВµРЎРѓР С”Р С‘.\n\n"
                        f"{format_yandex_error_for_user(str(exc))}\n\n"
                        "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р С—РЎР‚Р В°Р Р†Р С‘Р В»РЎРЉР Р…РЎС“РЎР‹ Р СР В°РЎР‚Р С”РЎС“ Р С‘ Р СР С•Р Т‘Р ВµР В»РЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С, Р Р…Р В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Toyota Camry. "
                        "Р СџР С•РЎРѓР В»Р Вµ Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р В»Р ВµР Р…Р С‘РЎРЏ РЎРЏ РЎРѓР Р…Р С•Р Р†Р В° Р С—Р С•Р С—РЎР‚Р С•РЎв‚¬РЎС“ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ."
                    )
                else:
                    reply = build_yandex_error_reply(str(exc))
            except Exception as exc:
                update_driver_state(db, driver, DialogueState.YANDEX_ERROR.value)
                set_application_status(db, application, "yandex_error", yandex_status="error", yandex_error=str(exc))
                driver.requires_attention = True
                db.add(driver)
                create_conversation_event(db, driver, "yandex_failed", {"error": str(exc)})
                reply = build_yandex_error_reply(str(exc))
            return self._respond(db, driver, application, reply)

        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        self._record_registration_debug_event(
            db,
            driver,
            state_before=state.value,
            message_type=incoming.message_type,
            media_context="text_message",
            detected_document_type=None,
            extracted_fields=ai_result.extracted_fields,
            state_after=next_state.value,
            submit_called=submit_called,
            message_id=incoming_message.id,
            mime_type=incoming.mime_type,
            debug_source="text_step_applied",
        )
        reply = ai_result.reply or PROMPTS[next_state]
        if next_state == DialogueState.CONFIRM_DATA and ai_result.intent != "faq":
            reply = ai_result.reply or self._build_confirmation(driver, validation=self.yandex.validate_driver(driver))
        return self._respond(db, driver, application, reply)

    def handle_debug_document(
        self,
        db: Session,
        driver: Driver,
        filename: str,
        content: bytes,
        upload_to_drive: bool = True,
    ) -> dict[str, object]:
        application = get_or_create_application(db, driver)
        state = DialogueState(driver.state or DialogueState.NEW.value)
        incoming_message = create_message(
            db,
            driver=driver,
            direction="incoming",
            sender_type="customer",
            message_type="document",
            text=filename,
            delivery_status="received",
            raw_payload={"source": "debug", "filename": filename},
        )
        if state not in DOCUMENT_STATE_MAP:
            raise ValueError(f"Current state {state.value} does not expect a document")

        document_type = DOCUMENT_STATE_MAP[state]
        file_url = None
        file_id = None
        status = "debug_saved"
        if upload_to_drive:
            upload_result = self.drive.upload_driver_document(driver, document_type, content, filename)
            file_url = upload_result["file_url"]
            file_id = upload_result["file_id"]
            status = "uploaded"

        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=file_url,
            google_drive_file_id=file_id,
            whatsapp_media_id="debug-upload",
            status=status,
            message_id=incoming_message.id,
            file_name=filename,
            storage_provider="google_drive" if upload_to_drive else "debug",
            storage_path=file_id,
        )
        create_conversation_event(db, driver, "document_uploaded", {"document_type": document_type, "status": status})
        next_state = next_registration_state(driver, driver.vehicle)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        reply = self._build_confirmation(driver) if next_state == DialogueState.CONFIRM_DATA else PROMPTS[next_state]
        return {
            "document_type": document_type,
            "status": status,
            "next_state": next_state.value,
            "reply": self._respond(db, driver, application, reply),
        }

    def _handle_document(
        self,
        db: Session,
        driver: Driver,
        application,
        incoming: ParsedWhatsAppMessage,
        incoming_message_id: int | None = None,
    ) -> str:
        state = DialogueState(driver.state or DialogueState.NEW.value)
        media_context = self._classify_media_context(driver, state)
        support_context = self._get_support_context(driver)
        if support_context.get("mode") == "driver_profile_update":
            driver.requires_attention = True
            db.add(driver)
            create_conversation_event(
                db,
                driver,
                "profile_update_attachment_received",
                {
                    "message_type": incoming.message_type,
                    "mime_type": incoming.mime_type,
                    "filename": incoming.filename,
                    "field": support_context.get("field"),
                },
            )
            return self._respond(
                db,
                driver,
                application,
                "Р В¤Р В°Р в„–Р В» Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В». Р ВРЎРѓР С—Р С•Р В»РЎРЉР В·РЎС“РЎР‹ Р ВµР С–Р С• Р Т‘Р В»РЎРЏ Р С•Р В±Р Р…Р С•Р Р†Р В»Р ВµР Р…Р С‘РЎРЏ Р Т‘Р В°Р Р…Р Р…РЎвЂ№РЎвЂ¦ Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЏ. Р вЂўРЎРѓР В»Р С‘ Р Р…РЎС“Р В¶Р Р…Р С•, Р С•РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ Р ВµРЎвЂ°РЎвЂ Р С•Р Т‘Р Р…Р С• РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ РЎС“РЎвЂљР С•РЎвЂЎР Р…Р ВµР Р…Р С‘Р Вµ РЎвЂљР ВµР С”РЎРѓРЎвЂљР С•Р С.",
            )
        if media_context == "correction_context":
            return self._respond(
                db,
                driver,
                application,
                "Р В¤Р В°Р в„–Р В» Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В». Р вЂќР В»РЎРЏ Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р В»Р ВµР Р…Р С‘РЎРЏ Р Т‘Р В°Р Р…Р Р…РЎвЂ№РЎвЂ¦ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†Р С•Р Вµ Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ РЎвЂљР ВµР С”РЎРѓРЎвЂљР С•Р С Р С‘Р В»Р С‘ Р С—Р С•Р С—РЎР‚Р С•РЎРѓР С‘РЎвЂљР Вµ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р В°.",
            )
        if media_context == "text_registration_context":
            reply = self._repeat_current_question(state, "Сейчас мне нужен текст, а не фото.")
            self._record_registration_debug_event(
                db,
                driver,
                state_before=state.value,
                message_type=incoming.message_type,
                media_context=media_context,
                detected_document_type=None,
                extracted_fields={},
                state_after=state.value,
                submit_called=False,
                message_id=incoming_message_id,
                mime_type=incoming.mime_type,
                debug_source="media_during_text_step",
            )
            return self._respond(db, driver, application, reply)
        if media_context == "unknown_context":
            return self._respond(
                db,
                driver,
                application,
                "Р В¤Р С•РЎвЂљР С• Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В». Р РЋР ВµР в„–РЎвЂЎР В°РЎРѓ Р С•Р Р…Р С• Р Р…Р Вµ РЎРѓРЎвЂЎР С‘РЎвЂљР В°Р ВµРЎвЂљРЎРѓРЎРЏ Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР С•Р С Р В°Р Р†РЎвЂљР С•Р СР В°РЎвЂљР С‘РЎвЂЎР ВµРЎРѓР С”Р С‘. Р С›РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р Р…Р В° РЎв‚¬Р В°Р С–Р Вµ, Р С–Р Т‘Р Вµ Р В±Р С•РЎвЂљ Р С—РЎР‚РЎРЏР СР С• Р С—РЎР‚Р С•РЎРѓР С‘РЎвЂљ Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљ, Р С‘Р В»Р С‘ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎР ВµР С Р С—Р С•Р СР С•РЎвЂЎРЎРЉ.",
            )
        if media_context in {"support_context", "existing_driver_support_context"}:
            driver.requires_attention = True
            db.add(driver)
            create_conversation_event(
                db,
                driver,
                "support_attachment_received",
                {
                    "media_context": media_context,
                    "message_type": incoming.message_type,
                    "mime_type": incoming.mime_type,
                    "filename": incoming.filename,
                },
            )
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            return self._respond(
                db,
                driver,
                application,
                "Р В¤Р В°Р в„–Р В» Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В». Р СљР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚ РЎС“Р Р†Р С‘Р Т‘Р С‘РЎвЂљ Р ВµР С–Р С• Р Р† РЎвЂЎР В°РЎвЂљР Вµ Р С‘ Р С—Р С•Р СР С•Р В¶Р ВµРЎвЂљ Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ. Р вЂўРЎРѓР В»Р С‘ Р Р†РЎвЂ№ РЎС“Р В¶Р Вµ Р Р†Р С•РЎв‚¬Р В»Р С‘ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: Р вЂ™Р С•РЎв‚¬Р ВµР В».",
            )

        vehicle = get_or_create_vehicle(db, driver)
        image_bytes: bytes | None = None
        mime_type = incoming.mime_type
        detected_type: str | None = None
        extraction = None

        if incoming.media_id:
            try:
                image_bytes, mime_type = self.media.fetch_media(incoming.media_id)
            except Exception as exc:
                logger.warning("Failed to download WhatsApp media %s: %s", incoming.media_id, exc)

        if self.document_extractor.is_enabled() and image_bytes:
            extraction = self.document_extractor.extract(
                image_bytes,
                mime_type=mime_type,
                expected_document_type=DOCUMENT_STATE_MAP.get(state, "unknown"),
            )
            if extraction.document_type and extraction.document_type != "unknown":
                detected_type = extraction.document_type

        document_type = resolve_document_type_for_upload(state, driver, detected_type=detected_type)
        if not document_type:
            create_conversation_event(
                db,
                driver,
                "document_type_not_determined",
                {
                    "state": state.value,
                    "message_type": incoming.message_type,
                    "mime_type": mime_type,
                    "media_id": incoming.media_id,
                    "extractor_enabled": self.document_extractor.is_enabled(),
                    "media_downloaded": bool(image_bytes),
                },
            )
            self._record_registration_debug_event(
                db,
                driver,
                state_before=state.value,
                message_type=incoming.message_type,
                media_context=media_context,
                detected_document_type=detected_type,
                extracted_fields={},
                state_after=state.value,
                submit_called=False,
                message_id=incoming_message_id,
                mime_type=mime_type,
                debug_source="document_type_not_determined",
            )
            self._increment_ocr_failure_counter(driver)
            return self._respond(
                db,
                driver,
                application,
                (
                    "Р В¤Р С•РЎвЂљР С• Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В», Р Р…Р С• Р Р…Р Вµ РЎРѓР СР С•Р С– РЎвЂљР С•РЎвЂЎР Р…Р С• Р С•Р С—РЎР‚Р ВµР Т‘Р ВµР В»Р С‘РЎвЂљРЎРЉ РЎвЂљР С‘Р С— Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР В°.\n\n"
                    "Р С›РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ Р С•Р Т‘Р Р…Р С‘Р С РЎвЂћР С•РЎвЂљР С• Р С•Р Т‘Р С‘Р Р… Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљ Р В±Р ВµР В· Р В±Р В»Р С‘Р С”Р С•Р Р†: Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р Вµ РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘Р Вµ, РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘Р Вµ Р В»Р С‘РЎвЂЎР Р…Р С•РЎРѓРЎвЂљР С‘ Р С‘Р В»Р С‘ Р РЋР СћР РЋ. "
                    "Р вЂўРЎРѓР В»Р С‘ Р ВµРЎРѓРЎвЂљРЎРЉ PDF Р С‘Р В· eGov Р С‘Р В»Р С‘ Kaspi, РЎвЂљР С•Р В¶Р Вµ Р С—Р С•Р Т‘Р С•Р в„–Р Т‘Р ВµРЎвЂљ."
                ),
            )

        recognized: dict[str, str] = {}
        if image_bytes and self.document_extractor.is_enabled():
            if extraction is None or extraction.document_type != document_type:
                extraction = self.document_extractor.extract(
                    image_bytes,
                    mime_type=mime_type,
                    expected_document_type=document_type,
                )

        stored_document_types = expand_uploaded_document_types(
            document_type,
            mime_type=mime_type,
            contains_both_license_sides=bool(extraction and extraction.contains_both_license_sides),
            additional_document_types=extraction.additional_document_types if extraction else None,
        )
        for stored_type in stored_document_types:
            upsert_document(
                db,
                driver,
                document_type=stored_type,
                file_url=None,
                google_drive_file_id=None,
                whatsapp_media_id=incoming.media_id,
                message_id=incoming_message_id,
                file_name=incoming.filename,
                mime_type=mime_type,
                storage_provider="whatsapp",
                storage_path=incoming.media_id,
                status="stored_in_whatsapp",
            )
            create_conversation_event(
                db,
                driver,
                "document_uploaded",
                {"document_type": stored_type, "status": "stored_in_whatsapp", "source_mime_type": mime_type},
            )

        if image_bytes and self.document_extractor.is_enabled() and extraction is not None:
            fields, recognized = normalize_extracted_fields(extraction, document_type=document_type)
            if not detected_type and not fields:
                self._increment_ocr_failure_counter(driver)
                self._record_registration_debug_event(
                    db,
                    driver,
                    state_before=state.value,
                    message_type=incoming.message_type,
                    media_context=media_context,
                    detected_document_type=document_type,
                    extracted_fields={},
                    state_after=state.value,
                    submit_called=False,
                    message_id=incoming_message_id,
                    mime_type=mime_type,
                    debug_source="ocr_empty_result",
                )
                if self._ocr_failure_count(driver) >= 2:
                    self._set_manual_data_entry_enabled(driver, True)
                    self._record_registration_debug_event(
                        db,
                        driver,
                        state_before=state.value,
                        message_type=incoming.message_type,
                        media_context=media_context,
                        detected_document_type=document_type,
                        extracted_fields={},
                        state_after=state.value,
                        submit_called=False,
                        message_id=incoming_message_id,
                        mime_type=mime_type,
                        debug_source="ocr_manual_mode_enabled",
                    )
                    return self._handle_manual_data_entry(
                        db,
                        driver,
                        application,
                        state,
                        "manual_data_entry",
                        incoming_message_id,
                    )
                return self._respond(
                    db,
                    driver,
                    application,
                    "РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕС‡РёС‚Р°С‚СЊ РґРѕРєСѓРјРµРЅС‚. РџРµСЂРµС€Р»РёС‚Рµ С„РѕС‚Рѕ Р±РµР· Р±Р»РёРєРѕРІ, С‡С‚РѕР±С‹ РІРµСЃСЊ РґРѕРєСѓРјРµРЅС‚ Р±С‹Р» РІ РєР°РґСЂРµ.",
                )
            if fields:
                self._reset_ocr_failure_counter(driver)
                if "iin" in fields:
                    duplicate_reply = self._check_duplicate_constraints(
                        db, driver, application, DialogueState.ASK_IIN, fields
                    )
                    if duplicate_reply:
                        return self._respond(db, driver, application, duplicate_reply)
                if "plate_number" in fields:
                    duplicate_reply = self._check_duplicate_constraints(
                        db, driver, application, DialogueState.ASK_CAR_PLATE, fields
                    )
                    if duplicate_reply:
                        return self._respond(db, driver, application, duplicate_reply)
                self._apply_extracted_fields(
                    driver,
                    fields,
                    db,
                    application=application,
                    audit_action="document_ocr_extracted",
                    actor_type="system",
                )
                create_conversation_event(
                    db,
                    driver,
                    "document_fields_extracted",
                    {"document_type": document_type, "fields": sorted(fields.keys())},
                )

        db.refresh(driver)
        vehicle = driver.vehicle or vehicle
        next_state = next_registration_state(driver, vehicle)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        self._record_registration_debug_event(
            db,
            driver,
            state_before=state.value,
            message_type=incoming.message_type,
            media_context=media_context,
            detected_document_type=document_type,
            extracted_fields=recognized,
            state_after=next_state.value,
            submit_called=False,
            message_id=incoming_message_id,
            mime_type=mime_type,
            debug_source="document_processed",
        )
        if next_state == DialogueState.CONFIRM_DATA:
            reply = self._build_confirmation(driver)
        else:
            reply = build_recognition_reply(stored_document_types, recognized, next_state)
        return self._respond(db, driver, application, reply)

    def _classify_media_context(self, driver: Driver, state: DialogueState) -> str:
        if driver.dialog_mode in {"manual", "paused", "closed"}:
            return "support_context"
        if state == DialogueState.COMPLETED:
            return "existing_driver_support_context"
        context = self._get_support_context(driver)
        if context.get("mode") == "driver_profile_update":
            return "support_context"
        if self._is_yandex_pro_followup_state(state) or driver.active_support_topic:
            return "support_context"
        if self._get_pending_field_edit(driver) or state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return "correction_context"
        if state == DialogueState.NEW or state in DOCUMENT_STATE_MAP:
            return "registration_context"
        if is_registration_collecting_state(state):
            return "text_registration_context"
        return "unknown_context"

    def _check_duplicate_constraints(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        fields: dict[str, str],
    ) -> str | None:
        if state == DialogueState.ASK_IIN and fields.get("iin"):
            existing_driver = find_other_driver_by_iin(db, fields["iin"], exclude_driver_id=driver.id)
            if existing_driver:
                reply = (
                    f"Р В Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р С—Р С• Р ВР ВР Сњ {fields['iin']} РЎС“Р В¶Р Вµ Р Р…Р В°Р в„–Р Т‘Р ВµР Р…Р В° Р Р† РЎРѓР С‘РЎРѓРЎвЂљР ВµР СР Вµ Р Т‘Р В»РЎРЏ Р Р…Р С•Р СР ВµРЎР‚Р В° "
                    f"{existing_driver.whatsapp_phone}. Р СџР С•Р Р†РЎвЂљР С•РЎР‚Р Р…Р В°РЎРЏ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р С•РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р В»Р ВµР Р…Р В°."
                )
                create_conversation_event(db, driver, "duplicate_detected_iin", {"iin": fields["iin"], "existing_phone": existing_driver.whatsapp_phone})
                self._mark_duplicate_rejected(db, driver, application, reply)
                return reply

        if state == DialogueState.ASK_CAR_PLATE and fields.get("plate_number"):
            normalized_plate = normalize_plate_number(fields["plate_number"])
            existing_vehicle = find_vehicle_by_plate_number(db, normalized_plate, exclude_driver_id=driver.id)
            if existing_vehicle:
                owner = existing_vehicle.driver.whatsapp_phone if existing_vehicle.driver else "Р Т‘РЎР‚РЎС“Р С–Р С•Р С–Р С• Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЏ"
                reply = (
                    f"Р С’Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ РЎРѓ Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚Р С•Р С {normalized_plate} РЎС“Р В¶Р Вµ Р Р…Р В°Р в„–Р Т‘Р ВµР Р… Р Р† РЎРѓР С‘РЎРѓРЎвЂљР ВµР СР Вµ "
                    f"Р С‘ Р С—РЎР‚Р С‘Р Р†РЎРЏР В·Р В°Р Р… Р С” {owner}. Р СџР С•Р Р†РЎвЂљР С•РЎР‚Р Р…Р В°РЎРЏ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р С•РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р В»Р ВµР Р…Р В°."
                )
                create_conversation_event(db, driver, "duplicate_detected_plate", {"plate_number": normalized_plate, "owner": owner})
                self._mark_duplicate_rejected(db, driver, application, reply)
                return reply

        return None

    def _handle_manual_data_entry(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int | None = None,
    ) -> str:
        skipped = skip_data_documents_for_manual_entry(db, driver)
        vehicle = get_or_create_vehicle(db, driver)
        next_state = next_registration_state(driver, vehicle)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        create_conversation_event(
            db,
            driver,
            "manual_data_entry_selected",
            {"from_state": state.value, "skipped_documents": skipped},
        )
        next_prompt = (
            self._build_confirmation(driver)
            if next_state == DialogueState.CONFIRM_DATA
            else PROMPTS[next_state]
        )
        reply = f"{MANUAL_DATA_ENTRY_REPLY}\n\nСЂСџвЂњвЂ№ Р РЋР В»Р ВµР Т‘РЎС“РЎР‹РЎвЂ°Р С‘Р в„– РЎв‚¬Р В°Р С–:\n{next_prompt}"
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent="manual_data_entry",
            reply=reply,
            reasoning_summary="manual_data_entry",
        )
        return self._respond(db, driver, application, reply)

    def _handle_field_edit(self, db: Session, driver: Driver, application, state: DialogueState, ai_result: AIResult) -> str:
        if state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply or PROMPTS[state]))

        if ai_result.validation_errors or not ai_result.normalized_fields:
            if ai_result.target_field and "missing_new_value" in (ai_result.validation_errors or []):
                self._set_pending_field_edit(driver, ai_result.target_field, state.value)
                create_conversation_event(
                    db,
                    driver,
                    "field_edit_requested",
                    {
                        "from_state": state.value,
                        "target_field": ai_result.target_field,
                        "message": ai_result.new_value_raw or "",
                    },
                )
            elif ai_result.fallback_used:
                self._register_fallback(
                    db,
                    driver,
                    application,
                    state_before=state.value,
                    reason="field_edit_fallback",
                )
            return self._respond(db, driver, application, ai_result.reply or "Р СњР Вµ Р С—Р С•Р Р…РЎРЏР В», РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С• Р Р…РЎС“Р В¶Р Р…Р С• Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ.")

        duplicate_state = state
        if "iin" in ai_result.normalized_fields:
            duplicate_state = DialogueState.ASK_IIN
        elif "plate_number" in ai_result.normalized_fields:
            duplicate_state = DialogueState.ASK_CAR_PLATE
        duplicate_reply = self._check_duplicate_constraints(db, driver, application, duplicate_state, ai_result.normalized_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        changed_fields = self._apply_extracted_fields(driver, ai_result.normalized_fields, db, application=application, audit_action="field_corrected_by_user", actor_type="driver")
        self._mark_successful_progress(driver)
        create_conversation_event(
            db,
            driver,
            "field_corrected_by_user",
            {
                "target_field": ai_result.target_field,
                "changed_fields": changed_fields,
                "message": ai_result.new_value_raw,
            },
        )
        update_driver_state(db, driver, DialogueState.CONFIRM_DATA.value)
        set_application_status(db, application, "confirming_data", yandex_status="needs_resubmit")
        application.yandex_error = None
        db.add(application)
        return self._respond(
            db,
            driver,
            application,
            f"РІСљвЂ¦ Р вЂњР С•РЎвЂљР С•Р Р†Р С•, Р С•Р В±Р Р…Р С•Р Р†Р С‘Р В» Р С—Р С•Р В»Р Вµ Р’В«{self._field_label(ai_result.target_field)}Р’В». Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р ВµРЎвЂ°РЎвЂ РЎР‚Р В°Р В·.\n\n{self._build_confirmation(driver)}",
        )

    def _handle_pending_field_edit_value(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        target_field: str,
    ) -> str:
        target_state = self._field_name_to_correction_state(target_field)
        if target_state is None:
            self._clear_pending_field_edit(driver)
            return self._respond(db, driver, application, "Р СњР Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ Р С•Р С—РЎР‚Р ВµР Т‘Р ВµР В»Р С‘РЎвЂљРЎРЉ Р С—Р С•Р В»Р Вµ Р Т‘Р В»РЎРЏ Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р В»Р ВµР Р…Р С‘РЎРЏ. Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С• Р Р…РЎС“Р В¶Р Р…Р С• Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ.")

        ai_result = self.ai.respond(target_state.value, message_text, driver)
        ai_result.target_field = ai_result.target_field or target_field
        ai_result.reasoning_summary = ai_result.reasoning_summary or f"pending_field_edit:{target_field}"
        ai_result.suggested_next_action = ai_result.suggested_next_action or DialogueState.CONFIRM_DATA.value
        self._record_ai_trace(db, incoming_message_id, driver, state.value, message_text, ai_result)

        normalized_fields = ai_result.normalized_fields or ai_result.extracted_fields or {}
        if ai_result.validation_errors or not normalized_fields:
            self._register_fallback(
                db,
                driver,
                application,
                state_before=state.value,
                reason="pending_field_edit_invalid",
                message_id=incoming_message_id,
                message_text=message_text,
            )
            reply = ai_result.reply or f"Р С›РЎвЂљР С—РЎР‚Р В°Р Р†РЎРЉРЎвЂљР Вµ Р С”Р С•РЎР‚РЎР‚Р ВµР С”РЎвЂљР Р…Р С•Р Вµ Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ Р Т‘Р В»РЎРЏ Р С—Р С•Р В»РЎРЏ Р’В«{self._field_label(target_field)}Р’В»."
            return self._respond(db, driver, application, reply)

        duplicate_state = state
        if "iin" in normalized_fields:
            duplicate_state = DialogueState.ASK_IIN
        elif "plate_number" in normalized_fields:
            duplicate_state = DialogueState.ASK_CAR_PLATE
        duplicate_reply = self._check_duplicate_constraints(db, driver, application, duplicate_state, normalized_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        changed_fields = self._apply_extracted_fields(
            driver,
            normalized_fields,
            db,
            application=application,
            audit_action="field_corrected_by_user",
            actor_type="driver",
        )
        self._mark_successful_progress(driver)
        self._clear_pending_field_edit(driver)
        update_driver_state(db, driver, DialogueState.CONFIRM_DATA.value)
        set_application_status(db, application, "confirming_data", yandex_status="needs_resubmit")
        application.yandex_error = None
        db.add(application)
        create_conversation_event(
            db,
            driver,
            "field_corrected_by_user",
            {
                "target_field": target_field,
                "changed_fields": changed_fields,
                "message": message_text,
                "source": "pending_field_edit",
            },
        )
        return self._respond(
            db,
            driver,
            application,
            f"Р вЂњР С•РЎвЂљР С•Р Р†Р С•, Р С•Р В±Р Р…Р С•Р Р†Р С‘Р В» Р С—Р С•Р В»Р Вµ Р’В«{self._field_label(target_field)}Р’В». Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р ВµРЎвЂ°Р Вµ РЎР‚Р В°Р В·.\n\n{self._build_confirmation(driver)}",
        )

    def _record_ai_trace(
        self,
        db: Session,
        message_id: int,
        driver: Driver,
        state_before: str,
        input_text: str,
        ai_result: AIResult,
        *,
        active_flow_after: str | None = None,
        decision_source: str = "ai_router",
    ) -> None:
        incoming_message = next((message for message in driver.messages if message.id == message_id), None)
        if incoming_message is None:
            return
        upsert_message_ai_trace(
            db,
            message=incoming_message,
            driver_id=driver.id,
            state_before=state_before,
            input_text=input_text,
            provider=ai_result.provider,
            intent=ai_result.intent,
            confidence=ai_result.confidence,
            next_state=ai_result.next_state,
            reply_preview=ai_result.reply,
            extracted_fields_json=ai_result.extracted_fields or None,
            normalized_fields_json=ai_result.normalized_fields or ai_result.extracted_fields or None,
            reasoning_summary=ai_result.reasoning_summary,
            fallback_used=ai_result.fallback_used,
            fallback_reason=ai_result.fallback_reason,
            validation_errors_json=ai_result.validation_errors or None,
            suggested_next_action=ai_result.suggested_next_action,
            raw_decision_json=ai_result.raw_decision or None,
            final_decision_json=self._trace_payload(
                ai_result,
                active_flow_before=state_before,
                active_flow_after=active_flow_after or ai_result.suggested_next_action or state_before,
                decision_source=decision_source,
            ),
        )

    def _record_system_trace(
        self,
        db: Session,
        message_id: int,
        driver: Driver,
        state_before: str,
        input_text: str,
        *,
        intent: str,
        reply: str,
        reasoning_summary: str,
        priority_intent: str | None = None,
        matched_rule: str | None = None,
    ) -> None:
        incoming_message = next((message for message in driver.messages if message.id == message_id), None)
        if incoming_message is None:
            return
        decision = {"intent": intent, "reply": reply}
        if priority_intent:
            decision["priority_intent"] = priority_intent
        if matched_rule:
            decision["matched_rule"] = matched_rule
        upsert_message_ai_trace(
            db,
            message=incoming_message,
            driver_id=driver.id,
            state_before=state_before,
            input_text=input_text,
            provider="system",
            intent=intent,
            confidence=1.0,
            next_state=state_before,
            reply_preview=reply,
            extracted_fields_json=None,
            normalized_fields_json=None,
            reasoning_summary=reasoning_summary,
            fallback_used=False,
            fallback_reason=None,
            validation_errors_json=None,
            suggested_next_action=state_before,
            raw_decision_json=decision,
            final_decision_json=decision,
        )

    def _handle_support_flow(
        self,
        db: Session,
        driver: Driver,
        application,
        message_text: str,
        *,
        source_state: str,
    ) -> str | None:
        normalized = normalize_text_token(message_text)
        topic = _detect_support_topic(normalized, driver.active_support_topic)
        if not topic:
            return None

        flow = SUPPORT_FLOWS[topic]
        progress_words = {"РЎРѓР Т‘Р ВµР В»Р В°Р В»", "Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ", "Р С–Р С•РЎвЂљР С•Р Р†Р С•", "Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В»Р С•РЎРѓРЎРЉ", "Р С•Р С”", "ok"}
        problem_words = {"Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ", "Р Р…Р Вµ Р Р†РЎвЂ№РЎв‚¬Р В»Р С•", "Р Р…Р Вµ РЎР‚Р В°Р В±Р С•РЎвЂљР В°Р ВµРЎвЂљ", "Р С•РЎв‚¬Р С‘Р В±Р С”Р В°", "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ", "Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…", "Р Р…Р ВµР В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…"}

        if driver.active_support_topic != topic:
            driver.active_support_topic = topic
            driver.active_support_step = "0"
            self._set_support_context(driver, {"source_state": source_state, "mode": "support_flow", "topic": topic})
            self._mark_successful_progress(driver)
            db.add(driver)
            create_conversation_event(db, driver, "support_flow_started", {"topic": topic})
            return flow["intro"] + "\n\n" + self._support_step_text(topic, 0)

        current_step = int(driver.active_support_step or "0")
        if any(word in normalized for word in problem_words):
            driver.requires_attention = True
            driver.active_support_step = str(current_step)
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "support_escalated_to_manager", {"topic": topic, "message": message_text})
            return "Р СџР С•Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р СџР С•Р С”Р В° Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЏР ВµРЎвЂљ, Р С•Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р С”Р С•РЎР‚Р С•РЎвЂљР С”Р С•, Р Р…Р В° Р С”Р В°Р С”Р С•Р С Р С‘Р СР ВµР Р…Р Р…Р С• РЎв‚¬Р В°Р С–Р Вµ Р Р†Р С•Р В·Р Р…Р С‘Р С”Р В»Р В° Р С—РЎР‚Р С•Р В±Р В»Р ВµР СР В°."

        if any(word in normalized for word in progress_words):
            next_step = current_step + 1
            if next_step >= len(flow["steps"]):
                driver.active_support_topic = None
                driver.active_support_step = None
                driver.support_context_json = None
                self._mark_successful_progress(driver)
                db.add(driver)
                create_conversation_event(db, driver, "support_flow_completed", {"topic": topic})
                return flow["completed"]
            driver.active_support_step = str(next_step)
            self._touch_support_context(driver)
            self._mark_successful_progress(driver)
            db.add(driver)
            return self._support_step_text(topic, next_step)

        return flow["reply"] + "\n\n" + self._support_step_text(topic, current_step)

    def _support_step_text(self, topic: str, step_index: int) -> str:
        flow = SUPPORT_FLOWS[topic]
        step = flow["steps"][step_index]
        return f"Р РЃР В°Р С– {step_index + 1}: {step}\n\nР С™Р С•Р С–Р Т‘Р В° РЎРѓР Т‘Р ВµР В»Р В°Р ВµРЎвЂљР Вµ, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: РЎРѓР Т‘Р ВµР В»Р В°Р В». Р вЂўРЎРѓР В»Р С‘ Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р СР ВµР Р…Р Р…Р С• Р Р…Р Вµ Р Р†РЎвЂ№РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ."

    def _is_yandex_pro_followup_state(self, state: DialogueState) -> bool:
        return state in {
            DialogueState.ASK_YANDEX_PRO_LOGIN,
            DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS,
        }

    def _handle_yandex_pro_followup(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str:
        normalized = normalize_text_token(message_text)
        ai_result = self.ai.respond(state.value, message_text, driver)
        self._record_ai_trace(db, incoming_message_id, driver, state.value, message_text, ai_result)

        support_reply = self._handle_support_flow(db, driver, application, message_text, source_state=state.value)
        if support_reply:
            return self._respond(db, driver, application, support_reply)

        if _looks_like_yandex_pro_success(normalized):
            update_driver_state(db, driver, DialogueState.COMPLETED.value)
            driver.requires_attention = False
            driver.active_support_topic = None
            driver.active_support_step = None
            driver.support_context_json = None
            db.add(driver)
            set_application_status(db, application, "completed", yandex_status="driver_login_confirmed")
            create_conversation_event(db, driver, "yandex_pro_login_confirmed")
            return self._respond(
                db,
                driver,
                application,
                (
                    "СЂСџР‹вЂ° Р С›РЎвЂљР В»Р С‘РЎвЂЎР Р…Р С•, Р Р†РЎвЂ№ Р Р†Р С•РЎв‚¬Р В»Р С‘ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•! Р СљР С•Р В¶Р Р…Р С• Р Р†РЎвЂ№РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљРЎРЉ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹.\n"
                    "СЂСџвЂ™В¬ Р вЂўРЎРѓР В»Р С‘ Р С—Р С• РЎР‚Р В°Р В±Р С•РЎвЂљР Вµ Р С—Р С•РЎРЏР Р†РЎРЏРЎвЂљРЎРѓРЎРЏ Р Р†Р С•Р С—РЎР‚Р С•РЎРѓРЎвЂ№, Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ РЎРѓРЎР‹Р Т‘Р В°.\n\n"
                    f"{self._build_office_bonus_block()}"
                ),
            )

        if _looks_like_yandex_pro_install_request(normalized):
            create_conversation_event(db, driver, "yandex_pro_install_help_sent")
            return self._respond(db, driver, application, self._build_yandex_pro_install_reply(driver))

        if _looks_like_yandex_pro_issue(normalized):
            update_driver_state(db, driver, DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS.value)
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_pro_help_requested", {"message": message_text})
            return self._respond(
                db,
                driver,
                application,
                "СЂСџвЂРЉ Р СџР С•Р Р…РЎРЏР В». Р С›Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ Р С—РЎР‚Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘Р Вµ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• РІР‚вЂќ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р С Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“.\n"
                "Р Р€Р В¶Р Вµ Р Р†Р С•РЎв‚¬Р В»Р С‘ РІР‚вЂќ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: Р вЂ™Р С•РЎв‚¬Р ВµР В»",
            )

        if state == DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS and message_text.strip():
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_pro_problem_reported", {"message": message_text})
            return self._respond(
                db,
                driver,
                application,
                "РІСљвЂ¦ Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР С•РЎРѓР В»Р Вµ РЎС“РЎРѓР С—Р ВµРЎв‚¬Р Р…Р С•Р С–Р С• Р Р†РЎвЂ¦Р С•Р Т‘Р В° Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: Р вЂ™Р С•РЎв‚¬Р ВµР В».",
            )

        if ai_result.intent in {"faq", "help", "smalltalk", "clarification"} and ai_result.reply:
            return self._respond(db, driver, application, self._format_post_yandex_reply(state, ai_result.reply))

        return self._respond(db, driver, application, self._build_yandex_pro_start_reply(driver))

    def _handle_registered_driver_support(
        self,
        db: Session,
        driver: Driver,
        application,
        message_text: str,
        incoming_message_id: int,
    ) -> str:
        ai_result = self.ai.respond(DialogueState.COMPLETED.value, message_text, driver)
        self._record_ai_trace(db, incoming_message_id, driver, DialogueState.COMPLETED.value, message_text, ai_result)
        support_reply = self._handle_support_flow(
            db,
            driver,
            application,
            message_text,
            source_state=DialogueState.COMPLETED.value,
        )
        if support_reply:
            return self._respond(db, driver, application, support_reply)
        if ai_result.reply:
            return self._respond(db, driver, application, self._format_registered_driver_reply(ai_result.reply))
        return self._respond(
            db,
            driver,
            application,
            self._format_registered_driver_reply(
                "Р В Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ РЎС“Р В¶Р Вµ Р В·Р В°Р Р†Р ВµРЎР‚РЎв‚¬Р ВµР Р…Р В°. Р СљР С•Р С–РЎС“ Р С—Р С•Р СР С•РЎвЂЎРЎРЉ Р С—Р С• Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•, Р Р†РЎвЂ№РЎвЂ¦Р С•Р Т‘РЎС“ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹, РЎС“РЎРѓР В»Р С•Р Р†Р С‘РЎРЏР С Р С—Р В°РЎР‚Р С”Р В°, Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљР В°Р С, Р С•РЎвЂћР С‘РЎРѓРЎС“ Р С‘ Р Т‘Р В°Р В»РЎРЉР Р…Р ВµР в„–РЎв‚¬Р С‘Р С РЎв‚¬Р В°Р С–Р В°Р С."
            ),
        )

    def _handle_special_commands(self, db: Session, driver: Driver, application, message_text: str) -> str | None:
        normalized = normalize_text_token(message_text)
        if _looks_like_status_request(normalized):
            return self._build_status_reply(driver, application)

        if _looks_like_restart_request(normalized):
            self._reset_registration(db, driver, application)
            create_conversation_event(db, driver, "registration_restarted")
            return f"СЂСџвЂќвЂћ Р С’Р Р…Р С”Р ВµРЎвЂљР В° РЎРѓР В±РЎР‚Р С•РЎв‚¬Р ВµР Р…Р В°. Р СњР В°РЎвЂЎР С‘Р Р…Р В°Р ВµР С Р В·Р В°Р Р…Р С•Р Р†Р С•.\n\n{REGISTRATION_START_CTA}"

        if _looks_like_delete_request(normalized):
            reply = (
                "Р вЂ”Р В°Р С—РЎР‚Р С•РЎРѓ Р Р…Р В° РЎС“Р Т‘Р В°Р В»Р ВµР Р…Р С‘Р Вµ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В° Р В·Р В°РЎвЂћР С‘Р С”РЎРѓР С‘РЎР‚Р С•Р Р†Р В°Р Р…. "
                "Р СџРЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ Р Р…Р Вµ РЎС“Р Т‘Р В°Р В»РЎРЏР ВµРЎвЂљРЎРѓРЎРЏ Р В°Р Р†РЎвЂљР С•Р СР В°РЎвЂљР С‘РЎвЂЎР ВµРЎРѓР С”Р С‘. Р СљР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚ Р С—Р В°РЎР‚Р С”Р В° Р Т‘Р С•Р В»Р В¶Р ВµР Р… Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘РЎвЂљРЎРЉ Р С‘ РЎС“Р Т‘Р В°Р В»Р С‘РЎвЂљРЎРЉ Р ВµР С–Р С• Р Р†РЎР‚РЎС“РЎвЂЎР Р…РЎС“РЎР‹ Р Р† РЎРѓР С‘РЎРѓРЎвЂљР ВµР СР Вµ."
            )
            set_application_status(
                db,
                application,
                "deletion_requested",
                yandex_status="deletion_requested",
                yandex_error=reply,
            )
            driver.deletion_requested_at = datetime.utcnow()
            driver.requires_attention = True
            db.add(driver)
            db.flush()
            create_conversation_event(db, driver, "deletion_requested", {"source": "driver_command"})
            if self.settings.google_sheets_id and self.settings.get_google_service_account_info():
                try:
                    self.sheets.sync_deletion_request(driver, application)
                except Exception as exc:
                    logger.exception("Failed to sync deletion request to Google Sheets for driver %s: %s", driver.whatsapp_phone, exc)
            return reply

        return None

    def _handle_priority_interrupts(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        normalized = normalize_text_token(message_text)
        if not normalized:
            return None

        detected_intent = classify_dialog_intent(message_text, current_state=state.value)
        if detected_intent == "human_operator" or _looks_like_operator_request(normalized):
            return self._activate_manual_mode(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                intent="human_operator",
            )

        if _looks_like_self_employed_request(normalized):
            driver.requires_attention = True
            self._set_support_context(
                driver,
                {
                    "mode": "employment_type_change",
                    "menu": "smz_request",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="self_employed_requested")
            create_conversation_event(db, driver, "self_employed_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="employment_type_change",
                reply="Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°РЎРЏР Р†Р С”РЎС“ Р Р…Р В° Р С—Р ВµРЎР‚Р ВµР Р†Р С•Р Т‘ Р Р† РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљР С•Р С–Р С•.",
                reasoning_summary="priority:employment_type_change",
                priority_intent="employment_type_change",
            )
            return "Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°РЎРЏР Р†Р С”РЎС“ Р Р…Р В° Р С—Р ВµРЎР‚Р ВµР Р†Р С•Р Т‘ Р Р† РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљР С•Р С–Р С•."

        if detected_intent == "existing_driver_support" or _looks_like_existing_driver_intent(normalized):
            self._set_support_context(
                driver,
                {
                    "mode": "existing_driver_support",
                    "menu": "existing_driver_main",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            create_conversation_event(db, driver, "existing_driver_support_menu_opened", {"message": message_text})
            reply = _existing_driver_options_reply()
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="existing_driver_support",
                reply=reply,
                reasoning_summary="priority:existing_driver_support",
                priority_intent="existing_driver_support",
            )
            return reply

        matched_rule = self._detect_driver_update_request(message_text)
        if matched_rule:
            return self._handle_driver_profile_update_entry(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                matched_rule=matched_rule,
            )

        support_intent = _classify_priority_support_intent(normalized)
        if support_intent:
            reply = _priority_support_reply(support_intent)
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status=support_intent)
            create_conversation_event(db, driver, support_intent, {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent=support_intent,
                reply=reply,
                reasoning_summary=f"priority:{support_intent}",
            )
            return reply

        if detected_intent == "smalltalk":
            reply = SMALLTALK_REPLY
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="smalltalk",
                reply=reply,
                reasoning_summary="priority:smalltalk",
            )
            return reply

        if detected_intent == "faq":
            reply = resolve_faq_replies(message_text, self.ai.knowledge_base, office_address=self.settings.public_site_address) or FALLBACK_MANAGER_REPLY
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="faq",
                reply=reply,
                reasoning_summary="priority:faq",
            )
            return reply

        if _looks_like_operator_request(normalized):
            driver.requires_attention = True
            driver.dialog_mode = "manual"
            driver.active_support_topic = None
            driver.active_support_step = None
            driver.support_context_json = {"human_required": True, "source_state": state.value}
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
            create_conversation_event(db, driver, "human_required", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="human_required",
                reply="Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°.",
                reasoning_summary="priority:human_required",
            )
            return "Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°."

        if _looks_like_existing_driver_intent(normalized):
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="existing_driver",
                reply=_existing_driver_options_reply(),
                reasoning_summary="priority:existing_driver",
            )
            return _existing_driver_options_reply()

        if _looks_like_yandex_login_support(normalized):
            support_reply = self._handle_support_flow(db, driver, application, message_text, source_state=state.value)
            if support_reply:
                return support_reply
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_login_help_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="yandex_login_support",
                reply="Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р С‘Р Р…РЎвЂћР С•РЎР‚Р СР В°РЎвЂ Р С‘РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘Р В° Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•.",
                reasoning_summary="priority:yandex_login_support",
            )
            return "Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р С‘Р Р…РЎвЂћР С•РЎР‚Р СР В°РЎвЂ Р С‘РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘Р В° Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•."

        if _looks_like_application_status_issue(normalized):
            reply = self._build_status_reply(driver, application)
            if not reply or application.status in {None, "", "collecting_data"}:
                reply = "Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р С‘Р Р…РЎвЂћР С•РЎР‚Р СР В°РЎвЂ Р С‘РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р В·Р В°РЎРЏР Р†Р С”Р С‘."
                driver.requires_attention = True
                db.add(driver)
                set_application_status(db, application, "awaiting_manager_review", yandex_status="status_check_required")
                create_conversation_event(db, driver, "application_status_check_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="application_status",
                reply=reply,
                reasoning_summary="priority:application_status",
            )
            return reply

        if _looks_like_tariff_issue(normalized):
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="tariff_support_required")
            create_conversation_event(db, driver, "tariff_support_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="tariff_support",
                reply="Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р С—Р С• РЎвЂљР В°РЎР‚Р С‘РЎвЂћР В°Р С Р С‘ Р Т‘Р С•РЎРѓРЎвЂљРЎС“Р С—Р В°Р С.",
                reasoning_summary="priority:tariff_support",
            )
            return "Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р С—Р С• РЎвЂљР В°РЎР‚Р С‘РЎвЂћР В°Р С Р С‘ Р Т‘Р С•РЎРѓРЎвЂљРЎС“Р С—Р В°Р С."

        if _looks_like_data_change_request(normalized) and state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="data_change_requested")
            create_conversation_event(db, driver, "data_change_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="data_change_request",
                reply="Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р Р…Р В° Р С‘Р В·Р СР ВµР Р…Р ВµР Р…Р С‘Р Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№РЎвЂ¦ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЏ.",
                reasoning_summary="priority:data_change_request",
            )
            return "Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р Р…Р В° Р С‘Р В·Р СР ВµР Р…Р ВµР Р…Р С‘Р Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№РЎвЂ¦ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЏ."

        return None

    def _activate_manual_mode(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        *,
        intent: str,
    ) -> str:
        reply = "Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°."
        driver.requires_attention = True
        driver.dialog_mode = "manual"
        driver.active_support_topic = None
        driver.active_support_step = None
        driver.support_context_json = {"human_required": True, "source_state": state.value}
        db.add(driver)
        set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
        create_conversation_event(db, driver, "human_required", {"message": message_text})
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent=intent,
            reply=reply,
            reasoning_summary=f"priority:{intent}",
        )
        return reply

    def _build_status_reply(self, driver: Driver, application) -> str:
        status = application.status or "collecting_data"
        if status == "collecting_data":
            current_step = driver.state or DialogueState.NEW.value
            return STATUS_COLLECTING_DATA_TEMPLATE.format(state=current_step)
        if status == "duplicate_rejected":
            return DUPLICATE_REJECTED_REPLY
        if status == "yandex_error":
            return build_yandex_error_reply(application.yandex_error)
        return STATUS_REPLIES.get(status, STATUS_FALLBACK_TEMPLATE.format(status=status))

    def _reset_registration(self, db: Session, driver: Driver, application) -> None:
        driver.full_name = None
        driver.last_name = None
        driver.first_name = None
        driver.middle_name = None
        driver.phone = driver.whatsapp_phone
        driver.city = None
        driver.address = None
        driver.iin = None
        driver.birth_date = None
        driver.driving_experience_since = None
        driver.driver_license_number = None
        driver.driver_license_issue_date = None
        driver.driver_license_expires_at = None
        driver.executor_type = None
        driver.employment_type = None
        driver.hired_at = None
        driver.is_hearing_impaired = None
        driver.requires_attention = False
        driver.fallback_count = 0
        driver.duplicate_flag = False
        driver.dialog_mode = "bot_active"
        driver.unread_count = 0
        driver.deletion_requested_at = None
        driver.paused_at = None
        driver.closed_at = None
        driver.active_support_topic = None
        driver.active_support_step = None
        driver.support_context_json = None
        update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)

        if driver.vehicle:
            db.delete(driver.vehicle)
            db.flush()

        for document in list(driver.documents):
            db.delete(document)

        application.status = "collecting_data"
        application.yandex_status = None
        application.yandex_driver_id = None
        application.yandex_vehicle_id = None
        application.yandex_error = None
        application.sent_to_yandex_at = None
        db.add(driver)
        db.add(application)
        db.flush()

    def _mark_duplicate_rejected(self, db: Session, driver: Driver, application, reply: str) -> None:
        driver.duplicate_flag = True
        driver.requires_attention = True
        db.add(driver)
        update_driver_state(db, driver, DialogueState.DUPLICATE_REJECTED.value)
        set_application_status(
            db,
            application,
            "duplicate_rejected",
            yandex_status="duplicate_rejected",
            yandex_error=reply,
        )
        create_conversation_event(db, driver, "duplicate_rejected", {"reply": reply})

    def _apply_extracted_fields(
        self,
        driver: Driver,
        fields: dict[str, str],
        db: Session,
        *,
        application=None,
        audit_action: str | None = None,
        actor_type: str = "shared_admin",
    ) -> list[str]:
        vehicle = get_or_create_vehicle(db, driver)
        changed_fields: list[str] = []
        for key, value in fields.items():
            if key == "plate_number":
                value = normalize_plate_number(value)
            if key == "registration_certificate":
                value = normalize_registration_certificate(value)
            if key == "brand":
                resolved, _ = resolve_brand_input(value)
                value = resolved or normalize_car_brand(value)
            if key == "model":
                brand = vehicle.brand or fields.get("brand")
                if brand:
                    resolved, _ = resolve_model_input(brand, value)
                    value = resolved or normalize_car_model(value)
                else:
                    value = normalize_car_model(value)
            if hasattr(driver, key):
                old_value = getattr(driver, key)
                setattr(driver, key, value)
                if old_value != value:
                    changed_fields.append(key)
                    if audit_action:
                        create_audit_log(
                            db,
                            driver=driver,
                            application=application,
                            field_name=key,
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(value) if value is not None else None,
                            action_type=audit_action,
                            actor_type=actor_type,
                        )
            elif hasattr(vehicle, key):
                old_value = getattr(vehicle, key)
                setattr(vehicle, key, value)
                if old_value != value:
                    changed_fields.append(key)
                    if audit_action:
                        create_audit_log(
                            db,
                            driver=driver,
                            application=application,
                            field_name=f"vehicle.{key}",
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(value) if value is not None else None,
                            action_type=audit_action,
                            actor_type=actor_type,
                        )
        db.add(driver)
        db.add(vehicle)
        db.flush()
        return changed_fields

    def _build_confirmation(self, driver: Driver, validation: dict[str, list[str]] | None = None) -> str:
        vehicle = driver.vehicle
        if validation is None:
            validation = self.yandex.validate_driver(driver)
        issues_block = ""
        if validation.get("errors"):
            issues_block = (
                "\n\nРІС™В  Р СџР ВµРЎР‚Р ВµР Т‘ Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С”Р С•Р в„– Р Р…РЎС“Р В¶Р Р…Р С• Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ:\n"
                f"{format_validation_errors_for_user(validation['errors'])}\n"
            )
        hearing_impaired = {
            "true": "Р Т‘Р В°",
            "false": "Р Р…Р ВµРЎвЂљ",
        }.get((driver.is_hearing_impaired or "").strip().lower(), driver.is_hearing_impaired or "-")
        return (
            "Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚РЎРЉРЎвЂљР Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ:\n\n"
            f"Р В¤Р ВР С›: {driver.full_name or '-'}\n"
            f"Р В¤Р В°Р СР С‘Р В»Р С‘РЎРЏ: {driver.last_name or '-'}\n"
            f"Р ВР СРЎРЏ: {driver.first_name or '-'}\n"
            f"Р С›РЎвЂљРЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р С•: {driver.middle_name or '-'}\n"
            f"Р вЂњР С•РЎР‚Р С•Р Т‘: {driver.city or '-'}\n"
            f"Р С’Р Т‘РЎР‚Р ВµРЎРѓ: {driver.address or '-'}\n"
            f"Р ВР ВР Сњ: {driver.iin or '-'}\n"
            f"Р вЂќР В°РЎвЂљР В° РЎР‚Р С•Р В¶Р Т‘Р ВµР Р…Р С‘РЎРЏ: {driver.birth_date or '-'}\n"
            f"Р вЂ™Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С‘Р в„– РЎРѓРЎвЂљР В°Р В¶ РЎРѓ: {driver.driving_experience_since or '-'}\n"
            f"Р вЂ™Р Р€ Р Р…Р С•Р СР ВµРЎР‚: {driver.driver_license_number or '-'}\n"
            f"Р вЂ™Р Р€ Р Р†РЎвЂ№Р Т‘Р В°Р Р…Р С•: {driver.driver_license_issue_date or '-'}\n"
            f"Р вЂ™Р Р€ Р Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†РЎС“Р ВµРЎвЂљ Р Т‘Р С•: {driver.driver_license_expires_at or '-'}\n"
            f"Р Р€РЎРѓР В»Р С•Р Р†Р С‘Р Вµ РЎР‚Р В°Р В±Р С•РЎвЂљРЎвЂ№: {driver.employment_type or '-'}\n"
            f"Р вЂќР В°РЎвЂљР В° Р С—РЎР‚Р С‘Р Р…РЎРЏРЎвЂљР С‘РЎРЏ: {driver.hired_at or '-'}\n"
            f"Р РЋР В»Р В°Р В±Р С•РЎРѓР В»РЎвЂ№РЎв‚¬Р В°РЎвЂ°Р С‘Р в„– Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ: {hearing_impaired}\n"
            f"Р С’Р Р†РЎвЂљР С•: {(vehicle.brand + ' ' + vehicle.model) if vehicle and vehicle.brand and vehicle.model else '-'}\n"
            f"Р вЂњР С•Р Т‘: {vehicle.year if vehicle else '-'}\n"
            f"Р вЂњР С•РЎРѓР Р…Р С•Р СР ВµРЎР‚: {vehicle.plate_number if vehicle else '-'}\n"
            f"Р В¦Р Р†Р ВµРЎвЂљ: {vehicle.color if vehicle else '-'}\n"
            f"Р СњР С•Р СР ВµРЎР‚ Р РЋР СћР РЋ: {vehicle.registration_certificate if vehicle else '-'}"
            f"{issues_block}\n\n"
            'Р вЂўРЎРѓР В»Р С‘ Р Р†РЎРѓР Вµ Р Р†Р ВµРЎР‚Р Р…Р С•, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ "Р СџР С•Р Т‘РЎвЂљР Р†Р ВµРЎР‚Р В¶Р Т‘Р В°РЎР‹". Р вЂўРЎРѓР В»Р С‘ Р Р…РЎС“Р В¶Р Р…Р С• Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ, Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ.'
        )

    def _build_yandex_pro_start_reply(self, driver: Driver) -> str:
        contact_phone = driver.phone or driver.whatsapp_phone
        greeting_name = driver.first_name or driver.full_name or "Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ"
        return (
            f"{greeting_name}, РЎРѓР С—Р В°РЎРѓР С‘Р В±Р С• РІР‚вЂќ Р В·Р В°РЎРЏР Р†Р С”Р В° РЎС“Р В¶Р Вµ Р Р† Р С—Р В°РЎР‚Р С”Р Вµ! СЂСџР‹вЂ°\n\n"
            f"{YANDEX_PRO_START_TEMPLATE.format(phone=contact_phone)}\n\n"
            f"{self._build_office_bonus_block()}"
        )

    def _build_yandex_pro_install_reply(self, driver: Driver) -> str:
        contact_phone = driver.phone or driver.whatsapp_phone
        return YANDEX_PRO_INSTALL_TEMPLATE.format(phone=contact_phone)

    def _format_new_state_assistant_reply(self, base_reply: str) -> str:
        return base_reply.strip()

    def _build_registration_start_reply(self, base_reply: str | None = None) -> str:
        reply = (base_reply or "СЂСџвЂвЂ№ Р С›РЎвЂљР В»Р С‘РЎвЂЎР Р…Р С•! Р СњР В°РЎвЂЎР С‘Р Р…Р В°Р ВµР С РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎР‹.").strip()
        next_step = "РІСљРЊРїС‘РЏ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р В¤Р ВР С› Р С—Р С•Р В»Р Р…Р С•РЎРѓРЎвЂљРЎРЉРЎР‹ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Р С’Р В±Р В°Р в„– Р С’РЎРЏРЎвЂљ Р вЂ“Р В°Р Р…РЎвЂ№Р В±Р ВµР С”РЎС“Р В»РЎвЂ№."
        if next_step not in reply:
            reply = f"{reply}\n\n{next_step}"
        return reply

    def _format_in_flow_assistant_reply(self, state: DialogueState, base_reply: str) -> str:
        return format_in_flow_reply(base_reply, state)

    def _format_post_yandex_reply(self, state: DialogueState, base_reply: str) -> str:
        if state == DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS:
            reminder = "РІСљвЂ¦ Р Р€Р В¶Р Вµ Р Р†Р С•РЎв‚¬Р В»Р С‘ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С• РІР‚вЂќ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: Р вЂ™Р С•РЎв‚¬Р ВµР В». Р СњР ВµРЎвЂљ РІР‚вЂќ Р С•Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ, РЎвЂЎРЎвЂљР С• Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ."
        else:
            reminder = (
                "СЂСџвЂњВ± Р РЋР ВµР в„–РЎвЂЎР В°РЎРѓ РЎв‚¬Р В°Р С–: Р Р†Р С•Р в„–РЎвЂљР С‘ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•. "
                "Р вЂ™Р С•РЎв‚¬Р В»Р С‘ РІР‚вЂќ Р Р…Р В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ: Р вЂ™Р С•РЎв‚¬Р ВµР В». Р СџРЎР‚Р С•Р В±Р В»Р ВµР СР В° РІР‚вЂќ Р С•Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р† РЎвЂЎР В°РЎвЂљ."
            )
        if base_reply.strip() == reminder.strip():
            return base_reply
        return f"{base_reply}\n\n{reminder}"

    def _format_registered_driver_reply(self, base_reply: str) -> str:
        return base_reply.strip()

    def _build_office_bonus_block(self) -> str:
        office_address = self.settings.public_site_address
        return (
            "СЂСџР‹Рѓ Р СџР С•РЎРѓР В»Р Вµ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘Р С‘ Р СР С•Р В¶Р Р…Р С• Р С—РЎР‚Р С‘Р ВµРЎвЂ¦Р В°РЎвЂљРЎРЉ Р Р† Р С•РЎвЂћР С‘РЎРѓ Р С‘ Р В·Р В°Р В±РЎР‚Р В°РЎвЂљРЎРЉ Р С—РЎР‚Р С‘Р Р†Р ВµРЎвЂљРЎРѓРЎвЂљР Р†Р ВµР Р…Р Р…РЎвЂ№Р в„– Р В±Р С•Р Р…РЎС“РЎРѓ.\n"
            "Р вЂ™ Р В±Р С•Р С”РЎРѓ Р Р†РЎвЂ¦Р С•Р Т‘РЎРЏРЎвЂљ: Р В·Р В°РЎР‚РЎРЏР Т‘Р С”Р В° 3 Р Р† 1, Р Т‘Р ВµРЎР‚Р В¶Р В°РЎвЂљР ВµР В»РЎРЉ Р Т‘Р В»РЎРЏ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°, РЎРѓР В°Р В»РЎвЂћР ВµРЎвЂљР С”Р В° Р С‘ РЎвЂљРЎР‚РЎРЏР С—Р С”Р В°.\n"
            "Р вЂќР В»РЎРЏ Р В±Р С‘Р В·Р Р…Р ВµРЎРѓ-Р С”Р В»Р В°РЎРѓРЎРѓР В° Р Т‘Р С•Р С—Р С•Р В»Р Р…Р С‘РЎвЂљР ВµР В»РЎРЉР Р…Р С• Р Р†РЎвЂ№Р Т‘Р В°Р ВµР С Р В±Р В»Р С•Р С” Р Р†Р С•Р Т‘РЎвЂ№.\n"
            f"СЂСџвЂњРЊ Р С›РЎвЂћР С‘РЎРѓ: {office_address}\n"
            f"{OFFICE_HOURS}"
        )

    def _get_support_context(self, driver: Driver) -> dict:
        context = driver.support_context_json or {}
        return context if isinstance(context, dict) else {}

    def _set_support_context(self, driver: Driver, context: dict | None) -> None:
        if context:
            now = datetime.utcnow()
            context = dict(context)
            context.setdefault("created_at", now.isoformat())
            context["last_updated"] = now.isoformat()
            context.setdefault("expires_at", (now + timedelta(minutes=30)).isoformat())
        driver.support_context_json = context or None
        driver.updated_at = datetime.utcnow()

    def _clear_support_context(self, driver: Driver) -> None:
        driver.support_context_json = None
        driver.updated_at = datetime.utcnow()

    def _support_context_is_expired(self, context: dict) -> bool:
        if self._support_context_is_stale(context):
            return True
        expires_at = context.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return False
        try:
            return datetime.fromisoformat(expires_at) <= datetime.utcnow()
        except ValueError:
            return False

    def _support_context_is_stale(self, context: dict) -> bool:
        reference = context.get("last_updated") or context.get("expires_at") or context.get("created_at")
        if not isinstance(reference, str) or not reference:
            return False
        try:
            return datetime.fromisoformat(reference) <= datetime.utcnow() - timedelta(hours=24)
        except ValueError:
            return False

    def _reset_stale_support_context(self, driver: Driver) -> None:
        context = self._get_support_context(driver)
        if not context or not self._support_context_is_stale(context):
            return
        self._clear_support_context(driver)
        driver.active_support_topic = None
        driver.active_support_step = None

    def _touch_support_context(self, driver: Driver) -> None:
        context = self._get_support_context(driver)
        if not context:
            return
        context["last_updated"] = datetime.utcnow().isoformat()
        self._set_support_context(driver, context)

    def _ocr_failure_count(self, driver: Driver) -> int:
        context = self._get_support_context(driver)
        return int(context.get("consecutive_ocr_failures") or 0)

    def _increment_ocr_failure_counter(self, driver: Driver) -> None:
        context = self._get_support_context(driver)
        context["consecutive_ocr_failures"] = int(context.get("consecutive_ocr_failures") or 0) + 1
        self._set_support_context(driver, context)

    def _reset_ocr_failure_counter(self, driver: Driver) -> None:
        context = self._get_support_context(driver)
        if context.get("consecutive_ocr_failures"):
            context["consecutive_ocr_failures"] = 0
            self._set_support_context(driver, context)

    def _set_manual_data_entry_enabled(self, driver: Driver, enabled: bool) -> None:
        set_manual_data_entry(driver, enabled=enabled)
        context = self._get_support_context(driver)
        context["manual_data_entry"] = bool(enabled)
        self._set_support_context(driver, context)

    def _looks_like_driver_lookup_payload(self, message_text: str) -> bool:
        normalized = normalize_text_token(repair_mojibake(message_text))
        digits = "".join(ch for ch in normalized if ch.isdigit())
        if len(digits) in {10, 11, 12}:
            return True
        compact = "".join(ch for ch in normalized if ch.isalnum())
        if len(compact) == 12 and compact.isdigit():
            return True
        return any(marker in normalized for marker in ("iin", "Р С‘Р С‘РњвЂ Р Р…", "Р С‘Р С‘Р С‘Р Р…"))

    def _find_existing_yandex_driver(
        self,
        db: Session,
        driver: Driver,
        application,
        lookup: str,
        *,
        source: str,
    ) -> Driver | None:
        try:
            profile = self.yandex.find_and_sync_existing_driver(db, driver, lookup)
        except Exception as exc:
            logger.warning("Yandex driver lookup failed for %s: %s", lookup, exc)
            application.yandex_error = f"Yandex lookup failed: {exc}"
            db.add(application)
            create_conversation_event(
                db,
                driver,
                "yandex_driver_lookup_failed",
                {"lookup": lookup, "source": source, "error": str(exc)},
            )
            return None
        if not profile:
            create_conversation_event(
                db,
                driver,
                "yandex_driver_lookup_empty",
                {"lookup": lookup, "source": source},
            )
            return None
        create_conversation_event(
            db,
            driver,
            "yandex_driver_lookup_found",
            {"lookup": lookup, "source": source, "driver_id": profile.id},
        )
        return profile

    def _build_driver_profile_card(self, driver: Driver) -> str:
        vehicle = driver.vehicle
        docs = []
        if getattr(vehicle, "registration_certificate", None):
            docs.append(f"Р РЋР СћР РЋ: {vehicle.registration_certificate}")
        if driver.driver_license_number:
            docs.append(f"Р вЂ™Р Р€: {driver.driver_license_number}")
        if driver.iin:
            docs.append(f"Р ВР ВР Сњ: {driver.iin}")
        vehicle_name = " ".join(part for part in [getattr(vehicle, "brand", None), getattr(vehicle, "model", None)] if part) or "Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…"
        return (
            "Р СњР В°РЎв‚¬РЎвЂР В» Р Р†Р В°РЎв‚¬ Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ:\n"
            f"Р В¤Р ВР С›: {driver.full_name or 'Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…'}\n"
            f"Р СћР ВµР В»Р ВµРЎвЂћР С•Р Р…: {driver.phone or driver.whatsapp_phone or 'Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…'}\n"
            f"Р С’Р Р†РЎвЂљР С•: {vehicle_name} {getattr(vehicle, 'year', None) or 'Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…'}\n"
            f"Р вЂњР С•РЎРѓР Р…Р С•Р СР ВµРЎР‚: {getattr(vehicle, 'plate_number', None) or 'Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…'}\n"
            f"Р вЂќР С•Р С”РЎС“Р СР ВµР Р…РЎвЂљРЎвЂ№: {', '.join(docs) if docs else 'Р Р…Р Вµ РЎС“Р С”Р В°Р В·Р В°Р Р…РЎвЂ№'}\n"
            "Р В§РЎвЂљР С• РЎвЂ¦Р С•РЎвЂљР С‘РЎвЂљР Вµ Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ?\n"
            "1. Р С’Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ\n"
            "2. Р вЂњР С•РЎРѓР Р…Р С•Р СР ВµРЎР‚\n"
            "3. Р РЋР СћР РЋ/РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљ\n"
            "4. Р вЂ™Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р Вµ РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘Р Вµ\n"
            "5. Р СњР С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°\n"
            "6. Р СљР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚"
        )

    def _build_profile_update_menu(self, driver: Driver) -> str:
        base_card = self._build_driver_profile_card(driver).split("Р В§РЎвЂљР С• РЎвЂ¦Р С•РЎвЂљР С‘РЎвЂљР Вµ Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ?")[0].rstrip()
        return base_card + (
            "\nР В§РЎвЂљР С• РЎвЂ¦Р С•РЎвЂљР С‘РЎвЂљР Вµ Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ?\n"
            "1. Р В¤Р ВР С›\n"
            "2. Р СћР ВµР В»Р ВµРЎвЂћР С•Р Р…\n"
            "3. Р вЂњР С•РЎР‚Р С•Р Т‘/Р В°Р Т‘РЎР‚Р ВµРЎРѓ\n"
            "4. Р С’Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ\n"
            "5. Р вЂњР С•РЎРѓР Р…Р С•Р СР ВµРЎР‚\n"
            "6. Р РЋР СћР РЋ/РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљ\n"
            "7. Р вЂ™Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р Вµ РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘Р Вµ\n"
            "8. Р РЋР СљР вЂ”/РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В°\n"
            "9. Р СљР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚"
        )

    def _detect_driver_update_request(self, message_text: str) -> str | None:
        normalized = normalize_text_token(repair_mojibake(message_text)).lower().strip(" ?!.,")
        markers = (
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "РЎвЂ¦Р С•РЎвЂЎРЎС“ Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "РЎвЂ¦Р С•РЎвЂЎРЎС“ Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "РЎРѓР СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "РЎРѓР СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "РЎРѓР СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ",
            "Р В·Р В°Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "Р В·Р В°Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "Р С—Р С•Р СР ВµР Р…РЎРЏР В» Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "Р С”РЎС“Р С—Р С‘Р В» Р Р…Р С•Р Р†РЎС“РЎР‹ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
            "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р В°Р Р†РЎвЂљР С•",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚",
            "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ РЎРѓРЎвЂљРЎРѓ",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ РЎРѓРЎвЂљРЎРѓ",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљ",
            "Р В·Р В°Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљ",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р С—РЎР‚Р В°Р Р†Р В°",
            "Р В·Р В°Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р С—РЎР‚Р В°Р Р†Р В°",
            "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р С—РЎР‚Р В°Р Р†Р В°",
            "Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р Вµ РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘Р Вµ Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…",
            "Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ РЎвЂћР С‘Р С•",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ РЎвЂћР С‘Р С•",
            "Р С‘РЎРѓР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ Р С‘Р СРЎРЏ",
            "Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р Р…Р ВµР С—РЎР‚Р В°Р Р†Р С‘Р В»РЎРЉР Р…Р С•",
            "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ",
            "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљРЎвЂ№",
            "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљРЎвЂ№",
            "Р С”РЈВ©Р В»РЎвЂ“Р С”РЎвЂљРЎвЂ“ Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "Р СР В°РЎв‚¬Р С‘Р Р…Р В° Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "Р В°Р Р†РЎвЂљР С•Р С”РЈВ©Р В»РЎвЂ“Р С” Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљ Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "РўвЂєРўВ±Р В¶Р В°РЎвЂљ Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "РўвЂєРўВ±Р В¶Р В°РЎвЂљРЎвЂљР В°РЎР‚Р Т‘РЎвЂ№ Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "РўвЂєРўВ±РўвЂєРЎвЂ№РўвЂє Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "Р Р…Р С•Р СР ВµРЎР‚ Р В°РЎС“РЎвЂ№РЎРѓРЎвЂљРЎвЂ№РЎР‚РЎС“",
            "Р Т‘Р ВµРЎР‚Р ВµР С”РЎвЂљР ВµРЎР‚Р Т‘РЎвЂ“ РЈВ©Р В·Р С–Р ВµРЎР‚РЎвЂљРЎС“",
        )
        if any(marker in normalized for marker in markers):
            return next((marker for marker in markers if marker in normalized), "driver_update_request")
        return None

    def _load_conversation_memory(self, db: Session, driver: Driver) -> list[dict[str, object]]:
        rows = db.scalars(
            select(Message)
            .where(Message.driver_id == driver.id)
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(5)
        ).all()
        memory: list[dict[str, object]] = []
        for message in reversed(rows):
            memory.append(
                {
                    "id": message.id,
                    "direction": message.direction,
                    "sender_type": message.sender_type,
                    "message_type": message.message_type,
                    "text": message.text,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
            )
        return memory

    def _remember_message_context(self, driver: Driver, incoming: ParsedWhatsAppMessage, last_messages: list[dict[str, object]]) -> None:
        context = dict(driver.support_context_json or {})
        context["last_messages"] = last_messages
        if last_messages:
            last_bot = next((item for item in reversed(last_messages) if item.get("direction") == "outgoing"), None)
            if last_bot:
                context["last_bot_question"] = last_bot.get("text")
                context["last_intent"] = context.get("last_intent") or last_bot.get("message_type")
        if "pending_menu" not in context:
            context["pending_menu"] = context.get("menu")
        if incoming.message_type in {"image", "document"}:
            context["last_intent"] = "media"
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _handle_driver_profile_update_entry(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        *,
        matched_rule: str,
    ) -> str:
        profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
        if profile:
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": "profile_update_menu",
                    "driver_id": profile.id,
                    "vehicle_id": getattr(profile.vehicle, "id", None),
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            reply = self._build_profile_update_menu(profile)
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="driver_update_request",
                reply=reply,
                reasoning_summary="priority:driver_update_request",
                priority_intent="driver_update_request",
                matched_rule=matched_rule,
            )
            create_conversation_event(db, driver, "driver_profile_update_started", {"matched_rule": matched_rule, "driver_id": profile.id})
            return reply

        profile = self._find_existing_yandex_driver(
            db,
            driver,
            application,
            driver.whatsapp_phone,
            source="driver_update_request",
        )
        if profile:
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": "profile_update_menu",
                    "driver_id": profile.id,
                    "vehicle_id": getattr(profile.vehicle, "id", None),
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            reply = self._build_profile_update_menu(profile)
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="driver_update_request",
                reply=reply,
                reasoning_summary="priority:driver_update_request_yandex_found",
                priority_intent="driver_update_request",
                matched_rule=matched_rule,
            )
            return reply

        self._set_support_context(
            driver,
            {
                "mode": "driver_lookup",
                "reason": "driver_update_request",
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
            },
        )
        db.add(driver)
        reply = "Р СњР Вµ Р Р…Р В°РЎв‚¬РЎвЂР В» Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ Р С—Р С• РЎРЊРЎвЂљР С•Р СРЎС“ WhatsApp-Р Р…Р С•Р СР ВµРЎР‚РЎС“. Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р ВР ВР Сњ Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°, Р Р…Р В° Р С”Р С•РЎвЂљР С•РЎР‚РЎвЂ№Р в„– Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…РЎвЂ№ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•."
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent="driver_update_request",
            reply=reply,
            reasoning_summary="priority:driver_update_request_no_profile",
            priority_intent="driver_update_request",
            matched_rule=matched_rule,
        )
        create_conversation_event(db, driver, "driver_profile_update_lookup_needed", {"matched_rule": matched_rule})
        return reply

    def _handle_stateful_support_menu(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        if not context:
            return None
        if self._support_context_is_expired(context):
            self._clear_support_context(driver)
            db.add(driver)
            return None

        normalized = normalize_text_token(repair_mojibake(message_text)).strip()
        if context.get("mode") == "existing_driver_support" and context.get("menu") == "existing_driver_main":
            menu_map = {
                "1": "payout_support",
                "2": "yandex_problem",
                "3": "tariff_support",
                "4": "driver_update_request",
                "5": "human_operator",
            }
            choice = menu_map.get(normalized)
            if choice == "human_operator":
                self._set_support_context(
                    driver,
                    {
                        "mode": "manual",
                        "menu": "manual_mode",
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                driver.dialog_mode = "manual"
                driver.requires_attention = True
                db.add(driver)
                set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                create_conversation_event(db, driver, "human_required", {"source": "existing_driver_support_menu"})
                self._record_system_trace(
                    db,
                    incoming_message_id,
                    driver,
                    state.value,
                    message_text,
                    intent="human_operator",
                    reply="Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°.",
                    reasoning_summary="stateful_support_menu:human_operator",
                    priority_intent="human_operator",
                )
                return "Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°."
            if choice == "driver_update_request":
                profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
                if not profile:
                    profile = self._find_existing_yandex_driver(
                        db,
                        driver,
                        application,
                        driver.whatsapp_phone,
                        source="existing_driver_support_menu",
                    )
                if profile:
                    self._set_support_context(
                        driver,
                        {
                            "mode": "driver_profile_update",
                            "menu": "profile_update_menu",
                            "driver_id": profile.id,
                            "vehicle_id": getattr(profile.vehicle, "id", None),
                            "created_at": datetime.utcnow().isoformat(),
                            "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                        },
                    )
                    db.add(driver)
                    create_conversation_event(db, driver, "driver_update_profile_found", {"driver_id": profile.id})
                    return self._build_profile_update_menu(profile)
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_lookup",
                        "reason": "driver_update_request",
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, "driver_update_profile_missing", {"source": "existing_driver_support_menu"})
                return "Р СњР Вµ Р Р…Р В°РЎв‚¬РЎвЂР В» Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ Р С—Р С• РЎРЊРЎвЂљР С•Р СРЎС“ WhatsApp-Р Р…Р С•Р СР ВµРЎР‚РЎС“. Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р ВР ВР Сњ Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°, Р Р…Р В° Р С”Р С•РЎвЂљР С•РЎР‚РЎвЂ№Р в„– Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…РЎвЂ№ Р Р† Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•."
            if choice:
                self._set_support_context(
                    driver,
                    {
                        "mode": choice,
                        "menu": choice,
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, choice, {"source": "existing_driver_support_menu"})
                reply_map = {
                    "payout_support": _priority_support_reply("payout_support"),
                    "yandex_problem": _priority_support_reply("yandex_problem"),
                    "tariff_support": _priority_support_reply("tariff_support"),
                }
                return reply_map[choice]

        if context.get("mode") == "driver_lookup":
            if not self._looks_like_driver_lookup_payload(message_text):
                return None
            lookup_value = "".join(ch for ch in message_text if ch.isdigit())
            profile = (
                find_driver_by_phone(db, lookup_value)
                or find_driver_by_whatsapp_phone(db, lookup_value)
                or find_driver_by_iin(db, lookup_value)
            )
            if not profile:
                profile = self._find_existing_yandex_driver(
                    db,
                    driver,
                    application,
                    lookup_value,
                    source="driver_lookup",
                )
            if profile:
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_profile_update",
                        "menu": "profile_update_menu",
                        "driver_id": profile.id,
                        "vehicle_id": getattr(profile.vehicle, "id", None),
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, "driver_update_profile_found", {"driver_id": profile.id, "lookup": lookup_value})
                return self._build_profile_update_menu(profile)
            driver.dialog_mode = "manual"
            driver.requires_attention = True
            db.add(driver)
            self._set_support_context(
                driver,
                {
                    "mode": "manual",
                    "menu": "manual_mode",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_lookup_failed")
            create_conversation_event(db, driver, "driver_update_profile_missing", {"lookup": lookup_value})
            return "Р СњР Вµ Р Р…Р В°РЎв‚¬РЎвЂР В» Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“."

        if context.get("mode") == "driver_profile_update" and context.get("menu") == "profile_update_menu":
            menu_map = {
                "1": "full_name",
                "2": "phone",
                "3": "location",
                "4": "vehicle",
                "5": "plate_number",
                "6": "registration_certificate",
                "7": "driver_license_number",
                "8": "employment_type",
                "9": "human_operator",
            }
            choice = menu_map.get(normalized)
            if not choice:
                return None
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": f"profile_update_{choice}",
                    "driver_id": context.get("driver_id"),
                    "vehicle_id": context.get("vehicle_id"),
                    "created_at": context.get("created_at") or datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    "field": choice,
                    "active_flow": "driver_profile_update",
                    "pending_action": {
                        "full_name": "waiting_new_full_name",
                        "phone": "waiting_new_phone",
                        "location": "waiting_new_city_address",
                        "vehicle": "waiting_new_vehicle",
                        "plate_number": "waiting_new_plate",
                        "registration_certificate": "waiting_new_sts",
                        "driver_license_number": "waiting_new_driver_license",
                        "employment_type": "waiting_new_employment_type",
                        "human_operator": "waiting_manager",
                    }.get(choice),
                },
            )
            db.add(driver)
            if choice == "human_operator":
                driver.dialog_mode = "manual"
                driver.requires_attention = True
                set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                create_conversation_event(db, driver, "human_required", {"source": "profile_update_menu"})
                return "Р вЂ™Р В°РЎв‚¬ Р В·Р В°Р С—РЎР‚Р С•РЎРѓ Р С—Р ВµРЎР‚Р ВµР Т‘Р В°Р Р… Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“. Р С›Р В¶Р С‘Р Т‘Р В°Р в„–РЎвЂљР Вµ Р С•РЎвЂљР Р†Р ВµРЎвЂљР В°."
            create_conversation_event(db, driver, "driver_profile_update_requested", {"field": choice})
            prompt_map = {
                "full_name": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р Вµ Р В¤Р ВР С› Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.",
                "phone": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°.",
                "location": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р С–Р С•РЎР‚Р С•Р Т‘ Р С‘Р В»Р С‘ Р В°Р Т‘РЎР‚Р ВµРЎРѓ.",
                "vehicle": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р С—Р С• Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎР‹ Р С‘Р В»Р С‘ РЎвЂћР С•РЎвЂљР С• Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР С•Р Р†.",
                "plate_number": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚.",
                "registration_certificate": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ Р РЋР СћР РЋ/РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљР В°.",
                "driver_license_number": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р С–Р С• РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘РЎРЏ.",
                "employment_type": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В° Р С‘Р В»Р С‘ Р РЋР СљР вЂ”.",
            }
            return prompt_map[choice]

        return None

    def _handle_pending_menu(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        pending_menu = context.get("pending_menu") or context.get("menu")
        if not pending_menu:
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        if pending_menu == "existing_driver_main":
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        if pending_menu == "profile_update_menu" or context.get("mode") == "driver_profile_update":
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        return None

    def _is_active_flow(self, state: DialogueState) -> bool:
        return state.value.startswith("ask_") or state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}

    def _repeat_current_question(self, state: DialogueState, base_reply: str) -> str:
        current_prompt = PROMPTS.get(state, "")
        if not current_prompt:
            return base_reply.strip()
        if not base_reply.strip():
            return current_prompt
        return f"{base_reply.strip()}\n\n{current_prompt}"

    def _is_non_answer_text(self, message_text: str) -> bool:
        normalized = normalize_text_token(message_text).strip()
        if not normalized:
            return True
        return bool(NON_WORD_INPUT_RE.fullmatch(normalized))

    def _extract_city_fallback(self, message_text: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", (message_text or "").strip(" \t\r\n.,!?;:()[]{}\"'")).strip()
        if not cleaned:
            return None
        normalized = normalize_text_token(cleaned)
        parts = [part for part in normalized.split() if part]
        if not (1 <= len(parts) <= 3):
            return None
        if all(re.sub(r"[^a-zР В°-РЎРЏРЈв„ўРЎвЂ“РўР€РўвЂњРўР‡РўВ±РўвЂєРЈВ©РўВ»-]", "", part).replace("-", "").isalpha() for part in parts):
            return cleaned
        return None

    def _extract_city_value(self, message_text: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", (message_text or "").strip(" \t\r\n.,!?;:()[]{}\"'")).strip()
        if not cleaned:
            return None
        normalized = normalize_text_token(cleaned)
        normalized = re.sub(r"\b(РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎвЂљРЎРЉ|РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎР‹|Р В±РЎС“Р Т‘РЎС“|РЎвЂ¦Р С•РЎвЂЎРЎС“|Р В±РЎС“Р Т‘Р ВµР С|Р С–Р С•РЎР‚Р С•Р Т‘|Р С–Р С•РЎР‚Р С•Р Т‘Р Вµ|Р С–|Р С–\.|Р Р†|Р Р†Р С•)\b", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None
        city_aliases = {
            "Р В°РЎРѓРЎвЂљР В°Р Р…Р В°": "Р С’РЎРѓРЎвЂљР В°Р Р…Р В°",
            "Р В°РЎРѓРЎвЂљР В°Р Р…Р Вµ": "Р С’РЎРѓРЎвЂљР В°Р Р…Р В°",
            "Р В°Р В»Р СР В°РЎвЂљРЎвЂ№": "Р С’Р В»Р СР В°РЎвЂљРЎвЂ№",
            "Р В°Р В»Р СР В°РЎвЂљР Вµ": "Р С’Р В»Р СР В°РЎвЂљРЎвЂ№",
            "РЎв‚¬РЎвЂ№Р СР С”Р ВµР Р…РЎвЂљ": "Р РЃРЎвЂ№Р СР С”Р ВµР Р…РЎвЂљ",
            "РЎв‚¬РЎвЂ№Р СР С”Р ВµР Р…РЎвЂљР Вµ": "Р РЃРЎвЂ№Р СР С”Р ВµР Р…РЎвЂљ",
            "Р С”Р В°РЎР‚Р В°Р С–Р В°Р Р…Р Т‘Р В°": "Р С™Р В°РЎР‚Р В°Р С–Р В°Р Р…Р Т‘Р В°",
            "Р С”Р В°РЎР‚Р В°Р С–Р В°Р Р…Р Т‘Р Вµ": "Р С™Р В°РЎР‚Р В°Р С–Р В°Р Р…Р Т‘Р В°",
            "Р В°Р С”РЎвЂљР С•Р В±Р Вµ": "Р С’Р С”РЎвЂљР С•Р В±Р Вµ",
            "Р В°Р С”РЎвЂљР В°РЎС“": "Р С’Р С”РЎвЂљР В°РЎС“",
            "Р В°РЎвЂљРЎвЂ№РЎР‚Р В°РЎС“": "Р С’РЎвЂљРЎвЂ№РЎР‚Р В°РЎС“",
            "Р С—Р В°Р Р†Р В»Р С•Р Т‘Р В°РЎР‚": "Р СџР В°Р Р†Р В»Р С•Р Т‘Р В°РЎР‚",
            "Р С—Р В°Р Р†Р В»Р С•Р Т‘Р В°РЎР‚Р Вµ": "Р СџР В°Р Р†Р В»Р С•Р Т‘Р В°РЎР‚",
            "Р С”Р С•РЎРѓРЎвЂљР В°Р Р…Р В°Р в„–": "Р С™Р С•РЎРѓРЎвЂљР В°Р Р…Р В°Р в„–",
            "Р С”Р С•РЎРѓРЎвЂљР В°Р Р…Р В°Р Вµ": "Р С™Р С•РЎРѓРЎвЂљР В°Р Р…Р В°Р в„–",
        }
        if normalized in city_aliases:
            return city_aliases[normalized]
        parts = [part for part in normalized.split() if part]
        if not (1 <= len(parts) <= 3):
            return None
        if all(re.sub(r"[^a-zР В°-РЎРЏРЈв„ўРЎвЂ“РўР€РўвЂњРўР‡РўВ±РўвЂєРЈВ©РўВ»-]", "", part).replace("-", "").isalpha() for part in parts):
            return " ".join(part.capitalize() for part in parts)
        return None

    def _extract_address_value(self, message_text: str) -> str | None:
        cleaned = re.sub(r"\s+", " ", (message_text or "").strip()).strip(".,!?;:()[]{}\"'")
        if not cleaned:
            return None
        normalized = normalize_text_token(cleaned)
        has_digit = any(char.isdigit() for char in cleaned)
        address_markers = (
            "Р С—РЎР‚",
            "Р С—РЎР‚.",
            "Р С—РЎР‚Р С•РЎРѓР С—Р ВµР С”РЎвЂљ",
            "РЎС“Р В»Р С‘РЎвЂ ",
            "РЎС“Р В»",
            "РЎС“Р В».",
            "Р Т‘Р С•Р С",
            "Р Т‘.",
            "Р СР С”РЎР‚",
            "Р СР С‘Р С”РЎР‚Р С•РЎР‚Р В°Р в„–Р С•Р Р…",
            "Р С”Р Р†",
            "Р С”Р Р†Р В°РЎР‚РЎвЂљР С‘РЎР‚Р В°",
            "Р В¶Р С”",
            "РЎР‚Р В°Р в„–Р С•Р Р…",
            "Р В°РЎРѓРЎвЂљР В°Р Р…Р В°",
            "Р В°Р В»Р СР В°РЎвЂљРЎвЂ№",
            "РЎв‚¬РЎвЂ№Р СР С”Р ВµР Р…РЎвЂљ",
        )
        if len(normalized) < 5 or not has_digit:
            return None
        if any(marker in normalized for marker in address_markers) or len(normalized.split()) >= 2:
            return cleaned
        return None

    def _step_instruction_reply(self, state: DialogueState) -> str:
        if state == DialogueState.ASK_FULL_NAME:
            return "РІСљРЊРїС‘РЏ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р В¤Р ВР С› Р С—Р С•Р В»Р Р…Р С•РЎРѓРЎвЂљРЎРЉРЎР‹ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Р С’Р В±Р В°Р в„– Р С’РЎРЏРЎвЂљ Р вЂ“Р В°Р Р…РЎвЂ№Р В±Р ВµР С”РЎС“Р В»РЎвЂ№."
        if state in {DialogueState.ASK_EXECUTOR_TYPE, DialogueState.ASK_PHONE}:
            return "СЂСџвЂњВ± Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В° Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: +77766170666."
        if state == DialogueState.ASK_CITY:
            return "СЂСџРЏв„ўРїС‘РЏ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ РЎвЂљР С•Р В»РЎРЉР С”Р С• Р С–Р С•РЎР‚Р С•Р Т‘, Р С–Р Т‘Р Вµ Р В±РЎС“Р Т‘Р ВµРЎвЂљР Вµ РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎвЂљРЎРЉ.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Р С’РЎРѓРЎвЂљР В°Р Р…Р В°."
        if state == DialogueState.ASK_ADDRESS:
            return "СЂСџвЂњРЊ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р С—Р С•Р В»Р Р…РЎвЂ№Р в„– Р В°Р Т‘РЎР‚Р ВµРЎРѓ Р С—РЎР‚Р С•Р В¶Р С‘Р Р†Р В°Р Р…Р С‘РЎРЏ Р С‘Р В»Р С‘ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘Р С‘ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Р С—РЎР‚. Р В Р ВµРЎРѓР С—РЎС“Р В±Р В»Р С‘Р С”Р С‘ 12, Р С’РЎРѓРЎвЂљР В°Р Р…Р В°."
        if state in {
            DialogueState.ASK_HAS_CAR,
            DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER,
            DialogueState.ASK_CAR_BRAND,
        }:
            return "СЂСџС™В Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р СР В°РЎР‚Р С”РЎС“ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Toyota."
        if state == DialogueState.ASK_CAR_MODEL:
            return "СЂСџС™В Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р СР С•Р Т‘Р ВµР В»РЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Camry."
        if state == DialogueState.ASK_CAR_YEAR:
            return "СЂСџвЂњвЂ¦ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р С–Р С•Р Т‘ Р Р†РЎвЂ№Р С—РЎС“РЎРѓР С”Р В° Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: 2018."
        if state == DialogueState.ASK_CAR_PLATE:
            return "СЂСџвЂќСћ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ Р С”Р В°Р С” Р Р† Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР В°РЎвЂ¦.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: 123ABC01."
        if state == DialogueState.ASK_CAR_COLOR:
            return "СЂСџР‹РЃ Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ РЎвЂ Р Р†Р ВµРЎвЂљ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏ Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.\nР СњР В°Р С—РЎР‚Р С‘Р СР ВµРЎР‚: Р В±Р ВµР В»РЎвЂ№Р в„–."
        return format_in_flow_reply("", state)

    def _looks_like_cancel_request(self, message_text: str) -> bool:
        normalized = normalize_text_token(repair_mojibake(message_text))
        return normalized in {"Р С•РЎвЂљР СР ВµР Р…Р В°", "Р С•РЎвЂљР СР ВµР Р…Р С‘РЎвЂљРЎРЉ", "РЎРѓРЎвЂљР С•Р С—", "cancel", "cancel flow", "РЎвЂљР С•РўвЂєРЎвЂљР В°РЎвЂљ", "Р В±Р В°РЎРѓ РЎвЂљР В°РЎР‚РЎвЂљРЎС“"}

    def _profile_update_prompt_for_action(self, pending_action: str | None) -> str:
        prompts = {
            "waiting_new_full_name": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р Вµ Р В¤Р ВР С› Р С•Р Т‘Р Р…Р С‘Р С РЎРѓР С•Р С•Р В±РЎвЂ°Р ВµР Р…Р С‘Р ВµР С.",
            "waiting_new_phone": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р Р…Р С•Р СР ВµРЎР‚ РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…Р В°.",
            "waiting_new_city_address": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р С–Р С•РЎР‚Р С•Р Т‘ Р С‘Р В»Р С‘ Р В°Р Т‘РЎР‚Р ВµРЎРѓ.",
            "waiting_new_vehicle": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р С—Р С• Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎР‹ Р С‘Р В»Р С‘ РЎвЂћР С•РЎвЂљР С• Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљР С•Р Р†.",
            "waiting_new_plate": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚.",
            "waiting_new_sts": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ Р РЋР СћР РЋ/РЎвЂљР ВµРЎвЂ¦Р С—Р В°РЎРѓР С—Р С•РЎР‚РЎвЂљР В°.",
            "waiting_new_driver_license": "Р СџРЎР‚Р С‘РЎв‚¬Р В»Р С‘РЎвЂљР Вµ РЎвЂћР С•РЎвЂљР С• Р С‘Р В»Р С‘ Р Р…Р С•Р СР ВµРЎР‚ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С•Р С–Р С• РЎС“Р Т‘Р С•РЎРѓРЎвЂљР С•Р Р†Р ВµРЎР‚Р ВµР Р…Р С‘РЎРЏ.",
            "waiting_new_employment_type": "Р СњР В°Р С—Р С‘РЎв‚¬Р С‘РЎвЂљР Вµ Р Р…Р С•Р Р†РЎвЂ№Р в„– РЎвЂљР С‘Р С— РЎРѓР С•РЎвЂљРЎР‚РЎС“Р Т‘Р Р…Р С‘РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р В° Р С‘Р В»Р С‘ Р РЋР СљР вЂ”.",
        }
        return prompts.get(pending_action or "", "")

    def _handle_active_pending_action(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        pending_action = context.get("pending_action")
        if not pending_action:
            return None

        if _looks_like_operator_request(normalize_text_token(message_text)):
            return self._activate_manual_mode(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                intent="human_operator",
            )

        if self._looks_like_cancel_request(message_text):
            profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
            if profile:
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_profile_update",
                        "menu": "profile_update_menu",
                        "driver_id": profile.id,
                        "vehicle_id": getattr(profile.vehicle, "id", None),
                        "created_at": context.get("created_at") or datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                return self._build_profile_update_menu(profile)
            self._clear_support_context(driver)
            db.add(driver)
            return _existing_driver_options_reply()

        if _looks_like_self_employed_request(normalize_text_token(message_text)):
            driver.requires_attention = True
            self._set_support_context(
                driver,
                {
                    "mode": "employment_type_change",
                    "menu": "smz_request",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="self_employed_requested")
            create_conversation_event(db, driver, "self_employed_requested", {"message": message_text, "source": "pending_action"})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="employment_type_change",
                reply="Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°РЎРЏР Р†Р С”РЎС“ Р Р…Р В° Р С—Р ВµРЎР‚Р ВµР Р†Р С•Р Т‘ Р Р† РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљР С•Р С–Р С•.",
                reasoning_summary="pending_action:employment_type_change",
                priority_intent="employment_type_change",
            )
            return "Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р В·Р В°РЎРЏР Р†Р С”РЎС“ Р Р…Р В° Р С—Р ВµРЎР‚Р ВµР Р†Р С•Р Т‘ Р Р† РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљР С•Р С–Р С•."

        faq_reply = resolve_faq_replies(message_text, self.ai.knowledge_base, office_address=self.settings.public_site_address)
        if faq_reply:
            prompt = self._profile_update_prompt_for_action(pending_action)
            return f"{faq_reply}\n\n{prompt}" if prompt else faq_reply
        if looks_like_greeting(message_text):
            prompt = self._profile_update_prompt_for_action(pending_action)
            return f"{SMALLTALK_REPLY}\n\n{prompt}" if prompt else SMALLTALK_REPLY
        return None

    def _should_interrupt_active_flow(self, ai_result: AIResult) -> bool:
        if ai_result.intent in {"human_operator"}:
            return True
        if ai_result.should_interrupt_current_flow:
            return True
        if ai_result.intent in {"existing_driver_support", "driver_profile_update", "employment_type_change"} and ai_result.confidence >= 0.75:
            return True
        if ai_result.intent in {"payout_support", "tariff_support", "yandex_problem", "blocking_support"} and ai_result.confidence >= 0.85:
            return True
        return False

    def _get_pending_field_edit(self, driver: Driver) -> str | None:
        context = driver.support_context_json or {}
        pending = context.get("pending_field_edit")
        if isinstance(pending, dict):
            target_field = pending.get("target_field")
            if isinstance(target_field, str) and target_field:
                return target_field
        return None

    def _set_pending_field_edit(self, driver: Driver, target_field: str, source_state: str) -> None:
        context = dict(driver.support_context_json or {})
        context["pending_field_edit"] = {
            "target_field": target_field,
            "source_state": source_state,
            "requested_at": datetime.utcnow().isoformat(),
        }
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _clear_pending_field_edit(self, driver: Driver) -> None:
        context = dict(driver.support_context_json or {})
        context.pop("pending_field_edit", None)
        driver.support_context_json = context or None

    def _set_pending_car_model_suggestion(self, driver: Driver, suggested_model: str) -> None:
        context = dict(driver.support_context_json or {})
        context["pending_car_model_suggestion"] = suggested_model
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _clear_pending_car_model_suggestion(self, driver: Driver) -> None:
        context = dict(driver.support_context_json or {})
        if "pending_car_model_suggestion" not in context:
            return
        context.pop("pending_car_model_suggestion", None)
        driver.support_context_json = context or None
        driver.updated_at = datetime.utcnow()

    def _correction_state_to_field_name(self, state: DialogueState) -> str | None:
        mapping = {
            DialogueState.ASK_FULL_NAME: "full_name",
            DialogueState.ASK_PHONE: "phone",
            DialogueState.ASK_CITY: "city",
            DialogueState.ASK_ADDRESS: "address",
            DialogueState.ASK_IIN: "iin",
            DialogueState.ASK_BIRTH_DATE: "birth_date",
            DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "driving_experience_since",
            DialogueState.ASK_CAR_BRAND: "brand",
            DialogueState.ASK_CAR_MODEL: "model",
            DialogueState.ASK_CAR_YEAR: "year",
            DialogueState.ASK_CAR_PLATE: "plate_number",
            DialogueState.ASK_CAR_COLOR: "color",
            DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "registration_certificate",
            DialogueState.ASK_DRIVER_LICENSE_NUMBER: "driver_license_number",
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "driver_license_issue_date",
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "driver_license_expires_at",
            DialogueState.ASK_EMPLOYMENT_TYPE: "employment_type",
            DialogueState.ASK_HIRED_AT: "hired_at",
            DialogueState.ASK_HEARING_IMPAIRED: "is_hearing_impaired",
        }
        return mapping.get(state)

    def _field_name_to_correction_state(self, field_name: str) -> DialogueState | None:
        mapping = {
            "full_name": DialogueState.ASK_FULL_NAME,
            "phone": DialogueState.ASK_PHONE,
            "city": DialogueState.ASK_CITY,
            "address": DialogueState.ASK_ADDRESS,
            "iin": DialogueState.ASK_IIN,
            "birth_date": DialogueState.ASK_BIRTH_DATE,
            "driving_experience_since": DialogueState.ASK_DRIVING_EXPERIENCE_SINCE,
            "brand": DialogueState.ASK_CAR_BRAND,
            "model": DialogueState.ASK_CAR_MODEL,
            "year": DialogueState.ASK_CAR_YEAR,
            "plate_number": DialogueState.ASK_CAR_PLATE,
            "color": DialogueState.ASK_CAR_COLOR,
            "registration_certificate": DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE,
            "driver_license_number": DialogueState.ASK_DRIVER_LICENSE_NUMBER,
            "driver_license_issue_date": DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE,
            "driver_license_expires_at": DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT,
            "employment_type": DialogueState.ASK_EMPLOYMENT_TYPE,
            "hired_at": DialogueState.ASK_HIRED_AT,
            "is_hearing_impaired": DialogueState.ASK_HEARING_IMPAIRED,
        }
        return mapping.get(field_name)

    def _field_label(self, field_name: str | None) -> str:
        labels = {
            "full_name": "Р В¤Р ВР С›",
            "last_name": "РЎвЂћР В°Р СР С‘Р В»Р С‘РЎРЏ",
            "first_name": "Р С‘Р СРЎРЏ",
            "middle_name": "Р С•РЎвЂљРЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р С•",
            "phone": "РЎвЂљР ВµР В»Р ВµРЎвЂћР С•Р Р…",
            "city": "Р С–Р С•РЎР‚Р С•Р Т‘",
            "address": "Р В°Р Т‘РЎР‚Р ВµРЎРѓ",
            "iin": "Р ВР ВР Сњ",
            "birth_date": "Р Т‘Р В°РЎвЂљР В° РЎР‚Р С•Р В¶Р Т‘Р ВµР Р…Р С‘РЎРЏ",
            "driving_experience_since": "Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉРЎРѓР С”Р С‘Р в„– РЎРѓРЎвЂљР В°Р В¶",
            "driver_license_number": "Р Р…Р С•Р СР ВµРЎР‚ Р вЂ™Р Р€",
            "driver_license_issue_date": "Р Т‘Р В°РЎвЂљР В° Р Р†РЎвЂ№Р Т‘Р В°РЎвЂЎР С‘ Р вЂ™Р Р€",
            "driver_license_expires_at": "РЎРѓРЎР‚Р С•Р С” Р Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†Р С‘РЎРЏ Р вЂ™Р Р€",
            "employment_type": "РЎС“РЎРѓР В»Р С•Р Р†Р С‘Р Вµ РЎР‚Р В°Р В±Р С•РЎвЂљРЎвЂ№",
            "hired_at": "Р Т‘Р В°РЎвЂљР В° Р С—РЎР‚Р С‘Р Р…РЎРЏРЎвЂљР С‘РЎРЏ",
            "is_hearing_impaired": "РЎРѓР В»Р В°Р В±Р С•РЎРѓР В»РЎвЂ№РЎв‚¬Р В°РЎвЂ°Р С‘Р в„– Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ",
            "brand": "Р СР В°РЎР‚Р С”Р В° Р В°Р Р†РЎвЂљР С•",
            "model": "Р СР С•Р Т‘Р ВµР В»РЎРЉ Р В°Р Р†РЎвЂљР С•",
            "year": "Р С–Р С•Р Т‘ Р В°Р Р†РЎвЂљР С•",
            "plate_number": "Р С–Р С•РЎРѓР Р…Р С•Р СР ВµРЎР‚",
            "color": "РЎвЂ Р Р†Р ВµРЎвЂљ Р В°Р Р†РЎвЂљР С•",
            "registration_certificate": "Р Р…Р С•Р СР ВµРЎР‚ Р РЋР СћР РЋ",
            "vin": "VIN",
            "service_class": "Р С”Р В»Р В°РЎРѓРЎРѓ Р В°Р Р†РЎвЂљР С•",
        }
        return labels.get(field_name or "", field_name or "Р С—Р С•Р В»Р Вµ")

    def _trace_payload(
        self,
        ai_result: AIResult,
        *,
        active_flow_before: str | None = None,
        active_flow_after: str | None = None,
        decision_source: str = "ai_router",
    ) -> dict[str, object]:
        return {
            "reply": ai_result.reply,
            "intent": ai_result.intent,
            "ai_intent": ai_result.intent,
            "ai_action": ai_result.action,
            "ai_field": ai_result.field,
            "next_state": ai_result.next_state,
            "confidence": ai_result.confidence,
            "target_field": ai_result.target_field,
            "extracted_value": ai_result.extracted_value,
            "reply_hint": ai_result.reply_hint,
            "should_interrupt_current_flow": ai_result.should_interrupt_current_flow,
            "new_value_raw": ai_result.new_value_raw,
            "extracted_fields": ai_result.extracted_fields,
            "normalized_fields": ai_result.normalized_fields,
            "reasoning_summary": ai_result.reasoning_summary,
            "fallback_used": ai_result.fallback_used,
            "fallback_reason": ai_result.fallback_reason,
            "validation_errors": ai_result.validation_errors,
            "suggested_next_action": ai_result.suggested_next_action,
            "provider": ai_result.provider,
            "active_flow_before": active_flow_before,
            "active_flow_after": active_flow_after,
            "decision_source": decision_source,
        }

    def _record_registration_debug_event(
        self,
        db: Session,
        driver: Driver,
        *,
        state_before: str,
        message_type: str,
        media_context: str | None,
        state_after: str,
        submit_called: bool,
        detected_document_type: str | None = None,
        extracted_fields: dict[str, object] | None = None,
        message_id: int | None = None,
        mime_type: str | None = None,
        debug_source: str = "registration_flow",
    ) -> None:
        create_conversation_event(
            db,
            driver,
            "registration_debug_trace",
            {
                "state_before": state_before,
                "message_type": message_type,
                "media_context": media_context,
                "detected_document_type": detected_document_type,
                "extracted_fields": extracted_fields or {},
                "state_after": state_after,
                "submit_called": submit_called,
                "message_id": message_id,
                "mime_type": mime_type,
                "debug_source": debug_source,
            },
        )

    def _reset_fallback_count(self, driver: Driver) -> None:
        driver.fallback_count = 0
        if driver.support_context_json and isinstance(driver.support_context_json, dict):
            context = dict(driver.support_context_json)
            context["last_fallback_reason"] = None
            self._set_support_context(driver, context)

    def _register_fallback(
        self,
        db: Session,
        driver: Driver,
        application,
        *,
        state_before: str,
        reason: str,
        message_id: int | None = None,
        message_text: str = "",
        message_type: str | None = "text",
    ) -> None:
        driver.fallback_count = (driver.fallback_count or 0) + 1
        context = self._get_support_context(driver)
        context["last_fallback_reason"] = reason
        self._set_support_context(driver, context)
        db.add(driver)
        create_unknown_intent(
            db,
            driver_id=driver.id,
            message_id=message_id,
            state_before=state_before,
            message_text=message_text,
            normalized_text=normalize_text_token(message_text) if message_text else None,
            message_type=message_type,
            reason=reason,
        )
        if driver.fallback_count >= 3 and not driver.requires_attention:
            driver.requires_attention = True
            driver.dialog_mode = "manual"
            driver.active_support_topic = None
            driver.active_support_step = None
            self._set_support_context(
                driver,
                {
                    "human_required": True,
                    "mode": "manual",
                    "source_state": state_before,
                    "fallback_reason": reason,
                },
            )
            set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
            create_conversation_event(
                db,
                driver,
                "human_required",
                {"reason": "repeated_fallbacks", "fallback_count": driver.fallback_count, "state": state_before},
            )

    def _mark_successful_progress(self, driver: Driver) -> None:
        self._reset_fallback_count(driver)
        self._reset_ocr_failure_counter(driver)
        if driver.dialog_mode == "manual" and not driver.requires_attention:
            driver.dialog_mode = "bot_active"

    def _respond(self, db: Session, driver: Driver, application, reply: str) -> str:
        reply = repair_mojibake(reply)
        create_message(
            db,
            driver=driver,
            direction="outgoing",
            sender_type="bot",
            message_type="text",
            text=reply,
            delivery_status="pending",
        )
        if self.settings.google_sheets_id and self.settings.get_google_service_account_info():
            try:
                self.sheets.sync_application(driver, application)
            except Exception as exc:
                logger.exception("Failed to sync Google Sheets for driver %s: %s", driver.whatsapp_phone, exc)
        db.flush()
        return reply
def _looks_like_status_request(normalized: str) -> bool:
    exact = {
        "status",
        "application status",
        "status zayavki",
        "moya zayavka",
        "zayavka status",
    }
    contains = [
        "РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓ",
        "Р В·Р В°РЎРЏР Р†Р С”",
        "Р Р…Р В° Р С”Р В°Р С”Р С•Р С РЎРЊРЎвЂљР В°Р С—Р Вµ",
        "Р С–Р Т‘Р Вµ Р СР С•РЎРЏ",
        "my application",
        "my status",
        "what status",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _looks_like_operator_request_legacy(normalized: str) -> bool:
    markers = (
        "Р С•Р С—Р ВµРЎР‚Р В°РЎвЂљР С•РЎР‚",
        "Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚",
        "Р В¶Р С‘Р Р†Р С•Р в„– РЎвЂЎР ВµР В»Р С•Р Р†Р ВµР С”",
        "РЎРѓР С•Р ВµР Т‘Р С‘Р Р…Р С‘РЎвЂљР Вµ",
        "Р С—Р С•Р В·Р С•Р Р†Р С‘РЎвЂљР Вµ РЎвЂЎР ВµР В»Р С•Р Р†Р ВµР С”Р В°",
        "Р С—Р С•Р В·Р С•Р Р†Р С‘РЎвЂљР Вµ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р В°",
        "РЎвЂљР ВµРЎвЂ¦Р С—Р С•Р Т‘Р Т‘Р ВµРЎР‚Р В¶Р С”Р В°",
        "Р С—Р С•Р Т‘Р Т‘Р ВµРЎР‚Р В¶Р С”Р В°",
        "РЎвЂ¦Р С•РЎвЂЎРЎС“ Р С—Р С•Р С–Р С•Р Р†Р С•РЎР‚Р С‘РЎвЂљРЎРЉ РЎРѓ РЎвЂЎР ВµР В»Р С•Р Р†Р ВµР С”Р С•Р С",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_operator_request(normalized: str) -> bool:
    exact = {
        "Р С•Р С—Р ВµРЎР‚Р В°РЎвЂљР С•РЎР‚",
        "Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚",
        "РЎвЂљР ВµРЎвЂ¦Р С—Р С•Р Т‘Р Т‘Р ВµРЎР‚Р В¶Р С”Р В°",
        "Р В¶Р С‘Р Р†Р С•Р в„– РЎвЂЎР ВµР В»Р С•Р Р†Р ВµР С”",
        "РЎРѓР Р†РЎРЏР В¶Р С‘РЎвЂљР Вµ РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р С•Р С",
        "Р В°Р Т‘Р В°Р С Р С•Р С—Р ВµРЎР‚Р В°РЎвЂљР С•РЎР‚",
        "Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚ Р С”Р ВµРЎР‚Р ВµР С”",
        "РЎвЂљРЎвЂ“РЎР‚РЎвЂ“ Р В°Р Т‘Р В°Р С",
        "РўвЂєР С•Р В»Р Т‘Р В°РЎС“ Р С”Р ВµРЎР‚Р ВµР С”",
    }
    if normalized.strip(" ?!.,") in exact:
        return True
    markers = (
        "operator",
        "manager",
        "support",
        "Р С•Р С—Р ВµРЎР‚Р В°РЎвЂљР С•РЎР‚",
        "Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚",
        "РЎвЂљР ВµРЎвЂ¦Р С—Р С•Р Т‘Р Т‘Р ВµРЎР‚Р В¶Р С”Р В°",
        "Р С—Р С•Р Т‘Р Т‘Р ВµРЎР‚Р В¶Р С”Р В°",
        "Р В¶Р С‘Р Р†Р С•Р в„– РЎвЂЎР ВµР В»Р С•Р Р†Р ВµР С”",
        "РЎРѓР Р†РЎРЏР В¶Р С‘РЎвЂљР Вµ РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р С•Р С",
        "РЎРѓР С•Р ВµР Т‘Р С‘Р Р…Р С‘РЎвЂљР Вµ РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р С•Р С",
        "Р С—Р С•Р В·Р С•Р Р†Р С‘РЎвЂљР Вµ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р В°",
        "Р В°Р Т‘Р В°Р С Р С•Р С—Р ВµРЎР‚Р В°РЎвЂљР С•РЎР‚",
        "Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚ Р С”Р ВµРЎР‚Р ВµР С”",
        "РЎвЂљРЎвЂ“РЎР‚РЎвЂ“ Р В°Р Т‘Р В°Р С",
        "РўвЂєР С•Р В»Р Т‘Р В°РЎС“ Р С”Р ВµРЎР‚Р ВµР С”",
    )
    return any(marker in normalized for marker in markers)


def _classify_priority_support_intent(normalized: str) -> str | None:
    if _looks_like_payout_support(normalized):
        return "payout_support"
    if _looks_like_tariff_issue(normalized) or _looks_like_tariff_support(normalized):
        return "tariff_support"
    if _looks_like_yandex_login_support(normalized) or _looks_like_yandex_problem(normalized):
        return "yandex_problem"
    if _looks_like_blocking_support(normalized):
        return "blocking_support"
    if _looks_like_rental_car_question(normalized):
        return "rental_car_question"
    if _looks_like_courier_registration(normalized):
        return "courier_registration"
    return None


def _priority_support_reply(intent: str) -> str:
    replies = {
        "payout_support": "Р СџРЎР‚Р С‘Р Р…РЎРЏР В» Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р С—Р С• Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљР В°Р С. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р В±Р В°Р В»Р В°Р Р…РЎРѓР В°, Р Р†РЎвЂ№Р Р†Р С•Р Т‘Р В° Р С‘Р В»Р С‘ Р В·Р В°Р Т‘Р ВµРЎР‚Р В¶Р С”Р С‘ Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљРЎвЂ№.",
        "tariff_support": "Р СџРЎР‚Р С‘Р Р…РЎРЏР В» Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р С—Р С• РЎвЂљР В°РЎР‚Р С‘РЎвЂћР В°Р С. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“, РЎвЂЎРЎвЂљР С•Р В±РЎвЂ№ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘РЎвЂљРЎРЉ Р Т‘Р С•РЎРѓРЎвЂљРЎС“Р С—РЎвЂ№ Р С‘ Р Р…Р В°РЎРѓРЎвЂљРЎР‚Р С•Р в„–Р С”Р С‘ РЎвЂљР В°РЎР‚Р С‘РЎвЂћР С•Р Р†.",
        "yandex_problem": "Р СџРЎР‚Р С‘Р Р…РЎРЏР В» Р С—РЎР‚Р С•Р В±Р В»Р ВµР СРЎС“ РЎРѓ Р Р‡Р Р…Р Т‘Р ВµР С”РЎРѓ Р СџРЎР‚Р С•. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р Р†РЎвЂ¦Р С•Р Т‘Р В°, Р С—Р В°РЎР‚Р С”Р В°, Р С—РЎР‚Р С‘Р С–Р В»Р В°РЎв‚¬Р ВµР Р…Р С‘РЎРЏ Р С‘Р В»Р С‘ РЎРѓРЎвЂљР В°РЎвЂљРЎС“РЎРѓР В° Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В°.",
        "blocking_support": "Р СџРЎР‚Р С‘Р Р…РЎРЏР В» Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р С—Р С• Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р С”Р Вµ. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“ Р Т‘Р В»РЎРЏ Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р С‘ Р С—РЎР‚Р С‘РЎвЂЎР С‘Р Р…РЎвЂ№ Р С‘ Р Т‘Р В°Р В»РЎРЉР Р…Р ВµР в„–РЎв‚¬Р С‘РЎвЂ¦ Р Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†Р С‘Р в„–.",
        "rental_car_question": "Р СџР С•Р С”Р В° РЎвЂЎРЎвЂљР С• Р В°РЎР‚Р ВµР Р…Р Т‘РЎвЂ№ Р СР В°РЎв‚¬Р С‘Р Р… РЎС“ РЎвЂљР В°Р С”РЎРѓР С•Р С—Р В°РЎР‚Р С”Р В° Р Р…Р ВµРЎвЂљ. Р РЋР ВµР в„–РЎвЂЎР В°РЎРѓ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР В°Р ВµР С РЎвЂљР С•Р В»РЎРЉР С”Р С• Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»Р ВµР в„– РЎРѓР С• РЎРѓР Р†Р С•Р С‘Р СР С‘ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЏР СР С‘.",
        "courier_registration": "Р СџРЎР‚Р С‘Р Р…РЎРЏР В» Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р С—Р С• Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚РЎРѓР С”Р С•Р в„– РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘Р С‘. Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“, РЎвЂЎРЎвЂљР С•Р В±РЎвЂ№ Р С•РЎвЂљР Т‘Р ВµР В»РЎРЉР Р…Р С• Р С—РЎР‚Р С•Р Р†Р ВµРЎР‚Р С‘РЎвЂљРЎРЉ Р Р†Р С•Р В·Р СР С•Р В¶Р Р…Р С•РЎРѓРЎвЂљРЎРЉ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р…Р С‘РЎРЏ.",
    }
    return replies.get(intent, "Р СџРЎР‚Р С‘Р Р…РЎРЏР В». Р СџР ВµРЎР‚Р ВµР Т‘Р В°РЎР‹ Р Р†Р С•Р С—РЎР‚Р С•РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚РЎС“.")


def _looks_like_payout_support(normalized: str) -> bool:
    markers = (
        "Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљР В°",
        "Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљРЎвЂ№",
        "Р Р†РЎвЂ№Р Р†Р С•Р Т‘",
        "Р Т‘Р ВµР Р…РЎРЉР С–Р С‘",
        "Р В±Р В°Р В»Р В°Р Р…РЎРѓ",
        "Р СР С•Р СР ВµР Р…РЎвЂљР В°Р В»РЎРЉР Р…Р В°РЎРЏ Р Р†РЎвЂ№Р С—Р В»Р В°РЎвЂљР В°",
        "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎв‚¬Р В»Р С‘ Р Т‘Р ВµР Р…РЎРЉР С–Р С‘",
        "Р В°РўвЂєРЎв‚¬Р В°",
        "РЎвЂљРЈВ©Р В»Р ВµР С",
        "РЎвЂљРЈВ©Р В»Р ВµР С РўвЂєР В°РЎв‚¬Р В°Р Р…",
        "Р В°РўвЂєРЎв‚¬Р В° РЎвЂљРўР‡РЎРѓР С—Р ВµР Т‘РЎвЂ“",
        "Р В±Р В°Р В»Р В°Р Р…РЎРѓ РЎв‚¬РЎвЂ№РўвЂєР С—Р В°Р в„– РЎвЂљРўВ±РЎР‚",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_tariff_support(normalized: str) -> bool:
    markers = (
        "РЎвЂљР В°РЎР‚Р С‘РЎвЂћ",
        "Р С”Р С•Р СРЎвЂћР С•РЎР‚РЎвЂљ",
        "Р В±Р С‘Р В·Р Р…Р ВµРЎРѓ",
        "Р СР ВµР В¶Р С–Р С•РЎР‚Р С•Р Т‘",
        "РЎРЊР С”РЎРѓР С—РЎР‚Р ВµРЎРѓРЎРѓ",
        "Р С–РЎР‚РЎС“Р В·Р С•Р Р†Р С•Р в„–",
        "Р Р…Р ВµРЎвЂљ Р В·Р В°Р С”Р В°Р В·Р С•Р Р†",
        "Р В·Р В°Р С”Р В°Р В·РЎвЂ№ Р Р…Р Вµ Р С‘Р Т‘РЎС“РЎвЂљ",
        "РЎвЂљР В°РЎР‚Р С‘РЎвЂћ Р В°РЎв‚¬РЎвЂ№Р В»Р СР В°Р в„– РЎвЂљРўВ±РЎР‚",
        "Р С”Р С•Р СРЎвЂћР С•РЎР‚РЎвЂљ РўвЂєР С•РЎРѓРЎвЂ№РўР€РЎвЂ№Р В·",
        "РЎвЂљР В°Р С—РЎРѓРЎвЂ№РЎР‚РЎвЂ№РЎРѓ Р В¶Р С•РўвЂє",
        "Р В·Р В°Р С”Р В°Р В· Р В¶Р С•РўвЂє",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_yandex_problem(normalized: str) -> bool:
    markers = (
        "РЎРЏР Р…Р Т‘Р ВµР С”РЎРѓ Р С—РЎР‚Р С•",
        "Р Р…Р Вµ Р СР С•Р С–РЎС“ Р Р†Р С•Р в„–РЎвЂљР С‘",
        "Р Р…Р Вµ Р В·Р В°РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р С—Р В°РЎР‚Р С” Р Р…Р Вµ Р Р†Р С‘Р В¶РЎС“",
        "Р Р…Р ВµРЎвЂљ Р С—Р В°РЎР‚Р С”Р В°",
        "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎв‚¬Р В»Р С• Р С—РЎР‚Р С‘Р С–Р В»Р В°РЎв‚¬Р ВµР Р…Р С‘Р Вµ",
        "Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…",
        "Р С”Р С•Р Т‘ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "РЎРѓР СРЎРѓ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "yandex pro",
        "РЎРЏР Р…Р Т‘Р ВµР С”РЎРѓ Р С”РЎвЂ“РЎР‚Р СР ВµР в„– РЎвЂљРўВ±РЎР‚",
        "Р С—Р В°РЎР‚Р С” Р С”РЈВ©РЎР‚РЎвЂ“Р Р…Р В±Р ВµР в„– РЎвЂљРўВ±РЎР‚",
        "Р С”Р С•Р Т‘ Р С”Р ВµР В»Р СР ВµР Т‘РЎвЂ“",
        "РЎРѓР СРЎРѓ Р С”Р ВµР В»Р СР ВµР Т‘РЎвЂ“",
        "Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р В°РЎв‚¬РЎвЂ№Р В»Р СР В°Р в„– РЎвЂљРўВ±РЎР‚",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_blocking_support(normalized: str) -> bool:
    markers = (
        "Р В·Р В°Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р В°Р В»Р С‘",
        "Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р С”Р В°",
        "Р В·Р В°Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "Р Т‘Р С•РЎРѓРЎвЂљРЎС“Р С— Р В·Р В°Р С”РЎР‚РЎвЂ№РЎвЂљ",
        "Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р В·Р В°Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ Р В·Р В°Р В±Р В»Р С•Р С”Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "Р В±РўВ±РўвЂњР В°РЎвЂљРЎвЂљР В°Р В»Р Т‘РЎвЂ№",
        "Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р В±РўВ±РўвЂњР В°РЎвЂљ",
        "Р С”РЎвЂ“РЎР‚Р Вµ Р В°Р В»Р СР В°Р в„–Р СРЎвЂ№Р Р… Р В±Р В»Р С•Р С”",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_rental_car_question(normalized: str) -> bool:
    markers = (
        "Р В°РЎР‚Р ВµР Р…Р Т‘Р В° Р В°Р Р†РЎвЂљР С•",
        "Р В°РЎР‚Р ВµР Р…Р Т‘Р Р…Р В°РЎРЏ Р СР В°РЎв‚¬Р С‘Р Р…Р В°",
        "Р СР В°РЎв‚¬Р С‘Р Р…Р В° Р Р† Р В°РЎР‚Р ВµР Р…Р Т‘РЎС“",
        "Р ВµРЎРѓРЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•",
        "Р Р…РЎС“Р В¶Р Р…Р В° Р СР В°РЎв‚¬Р С‘Р Р…Р В°",
        "РЎвЂљР В°Р С”РЎРѓР С•Р С—Р В°РЎР‚Р С” Р Т‘Р В°Р ВµРЎвЂљ Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
        "Р С”РЈВ©Р В»РЎвЂ“Р С” Р В¶Р В°Р В»РўвЂњР В°",
        "Р В°РЎР‚Р ВµР Р…Р Т‘Р В° Р С”РЈВ©Р В»РЎвЂ“Р С”",
        "Р СР В°РЎв‚¬Р С‘Р Р…Р В° Р С”Р ВµРЎР‚Р ВµР С”",
        "Р С”РЈВ©Р В»РЎвЂ“Р С” Р В±Р В°РЎР‚ Р СР В°",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_courier_registration(normalized: str) -> bool:
    markers = (
        "Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚",
        "Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚Р С•Р С",
        "Р Т‘Р С•РЎРѓРЎвЂљР В°Р Р†Р С”Р В°",
        "Р ВµР Т‘Р В°",
        "РЎвЂ¦Р С•РЎвЂЎРЎС“ Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚Р С•Р С",
        "Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚РЎРѓР С”Р В°РЎРЏ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ",
        "Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚ Р В±Р С•Р В»РЎвЂ№Р С—",
        "Р В¶Р ВµРЎвЂљР С”РЎвЂ“Р В·РЎС“",
        "Р Т‘Р С•РЎРѓРЎвЂљР В°Р Р†Р С”Р В°РўвЂњР В° РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµР В»",
        "Р С”РЎС“РЎР‚РЎРЉР ВµРЎР‚ РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµРЎС“",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_existing_driver_intent(normalized: str) -> bool:
    plain = normalize_text_token(repair_mojibake(normalized)).strip(" ?!.,")
    strong_markers = (
        "РЎРЏ РЎС“Р В¶Р Вµ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р…",
        "РЎРЏ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р… РЎС“Р В¶Р Вµ",
        "РЎРЏ РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "РЎРЏ РЎС“Р В¶Р Вµ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ",
        "РЎРЏ РЎС“Р В¶Р Вµ РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎР‹",
        "РЎРЏ Р Р† Р Р†Р В°РЎв‚¬Р ВµР С Р С—Р В°РЎР‚Р С”Р Вµ",
        "РЎРЏ Р ВµРЎРѓРЎвЂљРЎРЉ Р Р† РЎРѓР С‘РЎРѓРЎвЂљР ВµР СР Вµ",
        "РЎС“Р В¶Р Вµ РЎР‚Р ВµР С–Р В°Р В»РЎРѓРЎРЏ",
        "Р СР ВµР Р… РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµР В»Р С–Р ВµР Р…Р СРЎвЂ“Р Р…",
        "Р СР ВµР Р… Р В¶РўР‡РЎР‚Р С–РЎвЂ“Р В·РЎС“РЎв‚¬РЎвЂ“Р СРЎвЂ“Р Р…",
    )
    if any(marker in plain for marker in strong_markers):
        return True
    readable_markers = (
        "РЎРЏ РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "РЎРЏ РЎС“Р В¶Р Вµ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ",
        "РЎРЏ РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎР‹ РЎС“ Р Р†Р В°РЎРѓ",
        "РЎРЏ Р ВµРЎРѓРЎвЂљРЎРЉ Р Р† Р В±Р В°Р В·Р Вµ",
        "РЎРЏ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р…",
        "РЎРЏ Р Р† Р Р†Р В°РЎв‚¬Р ВµР С Р С—Р В°РЎР‚Р С”Р Вµ",
        "РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "РЎС“Р В¶Р Вµ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ",
        "Р СР ВµР Р… РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµР В»Р С–Р ВµР Р…Р СРЎвЂ“Р Р…",
        "Р СР ВµР Р… Р В¶РўР‡РЎР‚Р С–РЎвЂ“Р В·РЎС“РЎв‚¬РЎвЂ“Р СРЎвЂ“Р Р…",
        "РЎРѓРЎвЂ“Р В·Р Т‘РЎвЂ“РўР€ Р С—Р В°РЎР‚Р С”РЎвЂљР ВµР СРЎвЂ“Р Р…",
        "Р С—Р В°РЎР‚Р С”Р С”Р Вµ РўвЂєР С•РЎРѓРЎвЂ№Р В»РўвЂњР В°Р Р…Р СРЎвЂ№Р Р…",
    )
    if any(marker in normalized for marker in readable_markers):
        return True
    markers = (
        "РЎРЏ РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…",
        "РЎРЏ РЎС“Р В¶Р Вµ Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ",
        "РЎРЏ РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎР‹ РЎС“ Р Р†Р В°РЎРѓ",
        "РЎРЏ Р ВµРЎРѓРЎвЂљРЎРЉ Р Р† Р В±Р В°Р В·Р Вµ",
        "РЎРЏ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р…",
        "РЎРЏ Р Р† Р Р†Р В°РЎв‚¬Р ВµР С Р С—Р В°РЎР‚Р С”Р Вµ",
    )
    if not any(marker in normalized for marker in markers):
        return False
    if _looks_like_self_employed_request(normalized):
        return False
    if _looks_like_yandex_login_support(normalized):
        return False
    if _looks_like_application_status_issue(normalized):
        return False
    if _looks_like_tariff_issue(normalized):
        return False
    if _looks_like_data_change_request(normalized):
        return False
    return True


def _looks_like_application_status_issue(normalized: str) -> bool:
    markers = (
        "Р С”Р С•Р С–Р Т‘Р В° Р В±РЎС“Р Т‘Р ВµРЎвЂљ Р С–Р С•РЎвЂљР С•Р Р†Р С•",
        "Р С–Р Т‘Р Вµ Р СР С•РЎРЏ Р В·Р В°РЎРЏР Р†Р С”Р В°",
        "Р СР ВµР Р…РЎРЏ Р Р…Р ВµРЎвЂљ Р Р† Р С—Р В°РЎР‚Р С”Р Вµ",
        "Р Р…Р Вµ Р Р†Р С‘Р В¶РЎС“ Р С—Р В°РЎР‚Р С”",
        "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎв‚¬Р В»Р С• Р С—РЎР‚Р С‘Р С–Р В»Р В°РЎв‚¬Р ВµР Р…Р С‘Р Вµ",
        "Р Р…Р Вµ Р С•РЎвЂљР С•Р В±РЎР‚Р В°Р В¶Р В°Р ВµРЎвЂљРЎРѓРЎРЏ Р С—Р В°РЎР‚Р С”",
        "Р В·Р В°РЎРЏР Р†Р С”Р В° Р Р…Р Вµ Р С•Р В±РЎР‚Р В°Р В±Р С•РЎвЂљР В°Р Р…Р В°",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_yandex_login_support(normalized: str) -> bool:
    markers = (
        "Р Р…Р Вµ Р СР С•Р С–РЎС“ Р Р†Р С•Р в„–РЎвЂљР С‘",
        "Р Р…Р Вµ Р В·Р В°РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р С•РЎв‚¬Р С‘Р В±Р С”Р В° Р Р†РЎвЂ¦Р С•Р Т‘Р В°",
        "Р Р…Р ВµРЎвЂљ Р С—Р В°РЎР‚Р С”Р В°",
        "Р Р…Р Вµ Р Р†Р С‘Р В¶РЎС“ Р С—Р В°РЎР‚Р С”",
        "Р Р…Р ВµРЎвЂљ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљР В°",
        "Р Р…Р Вµ Р С•РЎвЂљР С•Р В±РЎР‚Р В°Р В¶Р В°Р ВµРЎвЂљРЎРѓРЎРЏ РЎвЂљР В°Р С”РЎРѓР С•Р С—Р В°РЎР‚Р С”",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_tariff_issue(normalized: str) -> bool:
    markers = (
        "Р Р…Р Вµ Р СР С•Р С–РЎС“ Р С•РЎвЂљР С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљРЎРЉ РЎвЂљР В°РЎР‚Р С‘РЎвЂћ",
        "Р Р†Р С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљР Вµ Р С”Р С•Р СРЎвЂћР С•РЎР‚РЎвЂљ",
        "Р Р†Р С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљР Вµ Р СР ВµР В¶Р С–Р С•РЎР‚Р С•Р Т‘",
        "Р Р†Р С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљР Вµ РЎРЊР С”РЎРѓР С—РЎР‚Р ВµРЎРѓРЎРѓ",
        "Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљР Вµ РЎвЂљР В°РЎР‚Р С‘РЎвЂћ",
        "Р С—Р С•РЎвЂЎР ВµР СРЎС“ Р Р…Р ВµРЎвЂљ Р С”Р С•Р СРЎвЂћР С•РЎР‚РЎвЂљР В°",
        "Р С—Р С•РЎвЂЎР ВµР СРЎС“ Р Р…Р ВµРЎвЂљ Р В·Р В°Р С”Р В°Р В·Р С•Р Р†",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_data_change_request(normalized: str) -> bool:
    markers = (
        "Р С—Р С•Р СР ВµР Р…РЎРЏР В» Р СР В°РЎв‚¬Р С‘Р Р…РЎС“",
        "РЎРѓР СР ВµР Р…Р С‘Р В» Р Р…Р С•Р СР ВµРЎР‚",
        "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ",
        "Р С—Р С•Р СР ВµР Р…РЎРЏРЎвЂљРЎРЉ Р С‘Р С‘Р Р…",
        "Р С‘Р В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р С—РЎР‚Р В°Р Р†Р В°",
        "Р С•Р В±Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р Т‘Р С•Р С”РЎС“Р СР ВµР Р…РЎвЂљРЎвЂ№",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_self_employed_request(normalized: str) -> bool:
    markers = (
        "РЎвЂ¦Р С•РЎвЂЎРЎС“ РЎРѓРЎвЂљР В°РЎвЂљРЎРЉ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљРЎвЂ№Р С",
        "РЎРѓР Т‘Р ВµР В»Р В°Р в„–РЎвЂљР Вµ РЎРѓР СР В·",
        "Р С—Р В°РЎР‚Р С”Р С•Р Р†РЎвЂ№Р в„– РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљРЎвЂ№Р в„–",
    )
    return any(marker in normalized for marker in markers)


def _existing_driver_options_reply() -> str:
    return (
        "Понял, вы уже подключены. Что нужно сделать?\n"
        "1. Вывод денег\n"
        "2. Вход в Яндекс Про\n"
        "3. Тарифы\n"
        "4. Изменить авто/документы\n"
        "5. Менеджер"
    )


def _looks_like_restart_request(normalized: str) -> bool:
    exact = {
        "restart",
        "start over",
        "new registration",
        "new account",
        "zanovo",
        "po novoi",
        "po novoy",
        "snachala",
    }
    contains = [
        "Р В·Р В°Р Р…Р С•Р Р†Р С•",
        "Р Р…Р В°РЎвЂЎР В°РЎвЂљРЎРЉ Р В·Р В°Р Р…Р С•Р Р†Р С•",
        "Р Р…Р С•Р Р†Р В°РЎРЏ РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ",
        "Р Р…Р С•Р Р†РЎвЂ№Р в„– Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ",
        "Р С—Р С• Р Р…Р С•Р Р†Р С•Р в„–",
        "РЎРѓ Р Р…РЎС“Р В»РЎРЏ",
        "РЎвЂ¦Р С•РЎвЂЎРЎС“ Р В·Р В°Р Р…Р С•Р Р†Р С•",
        "register new",
        "from scratch",
        "restart",
        "start over",
        "new registration",
        "new account",
        "zanovo",
        "po novoi",
        "po novoy",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _looks_like_delete_request(normalized: str) -> bool:
    exact = {
        "delete account",
        "delete profile",
        "remove account",
        "remove profile",
        "delete me",
    }
    contains = [
        "РЎС“Р Т‘Р В°Р В»Р С‘",
        "РЎС“Р Т‘Р В°Р В»Р С‘РЎвЂљРЎРЉ",
        "Р В·Р В°Р С”РЎР‚РЎвЂ№РЎвЂљРЎРЉ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ",
        "РЎРѓР Р…Р ВµРЎРѓРЎвЂљР С‘ Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ",
        "delete account",
        "delete profile",
        "remove account",
        "remove profile",
        "delete me",
        "remove me",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _looks_like_yandex_pro_install_request(normalized: str) -> bool:
    contains = [
        "Р Р…Р Вµ РЎРѓР С”Р В°РЎвЂЎР В°Р В»",
        "Р Р…Р Вµ РЎС“РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р С‘Р В»",
        "Р С”Р В°Р С” РЎРѓР С”Р В°РЎвЂЎР В°РЎвЂљРЎРЉ",
        "Р С–Р Т‘Р Вµ РЎРѓР С”Р В°РЎвЂЎР В°РЎвЂљРЎРЉ",
        "РЎРѓР С”Р В°РЎвЂЎР В°РЎвЂљРЎРЉ",
        "РЎС“РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ",
        "install",
        "download",
    ]
    return normalized in {"Р Р…Р Вµ РЎРѓР С”Р В°РЎвЂЎР В°Р В»", "РЎРѓР С”Р В°РЎвЂЎР В°РЎвЂљРЎРЉ"} or any(token in normalized for token in contains)


def _looks_like_yandex_pro_issue(normalized: str) -> bool:
    contains = [
        "Р С•РЎв‚¬Р С‘Р В±",
        "Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ",
        "Р Р…Р Вµ Р СР С•Р С–РЎС“ Р Р†Р С•Р в„–РЎвЂљР С‘",
        "Р Р…Р Вµ Р Р†РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р Р…Р Вµ Р В·Р В°РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р С—Р С•Р СР С•РЎвЂ°",
        "help",
        "support",
        "РЎРѓР СРЎРѓ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р С”Р С•Р Т‘ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ",
        "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ Р С”Р С•Р Т‘",
    ]
    return normalized in {"Р С•РЎв‚¬Р С‘Р В±Р С”Р В°", "Р С—Р С•Р СР С•РЎвЂ°РЎРЉ", "help"} or any(token in normalized for token in contains)


def _looks_like_yandex_pro_success(normalized: str) -> bool:
    return normalized in YANDEX_PRO_SUCCESS_KEYWORDS


def _looks_like_registration_start_request(text: str) -> bool:
    normalized = normalize_text_token(text)
    if not normalized:
        return False
    if any(marker in normalized for marker in ("регист", "зарег", "подключ", "тіркел", "тірке", "тыркел", "тырке", "жазылай", "жазыл")):
        return True
    if normalized in {"1", "РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ", "РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµР В»РЎС“", "РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµРЎС“", "РЎвЂљРЎвЂ№РЎР‚Р С”Р ВµР В»РЎС“", "РЎвЂљРЎвЂ№РЎР‚Р С”Р ВµРЎС“"}:
        return True
    if any(marker in normalized for marker in ("РЎС“Р В¶Р Вµ Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎ", "РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚", "Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎР ВµР Р… РЎС“Р В¶Р Вµ", "Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎРЎвЂР Р… РЎС“Р В¶Р Вµ")):
        return False
    start_markers = (
        "Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚",
        "РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ ",
        "Р С—Р С•Р Т‘Р С”Р В»РЎР‹РЎвЂЎ",
        "РЎвЂљР С‘РЎР‚Р С”",
        "РЎвЂљРЎвЂ“РЎР‚Р С”",
        "РЎвЂљРЎвЂ“РЎР‚Р С”Р ВµР В»",
        "Р С—Р В°РЎР‚Р С”Р С”Р В°",
    )
    intent_markers = (
        "Р СР С•Р В¶Р Р…Р С•",
        "РЎвЂ¦Р С•РЎвЂЎРЎС“",
        "Р Р…Р В°Р Т‘Р С•",
        "Р Р…РЎС“Р В¶Р Р…Р С•",
        "Р Т‘Р В°Р Р†Р В°Р в„–РЎвЂљР Вµ",
        "Р Р…Р В°РЎвЂЎР В°РЎвЂљРЎРЉ",
        "Р Р…Р В°РЎвЂЎР С‘Р Р…Р В°",
        "Р С—РЎР‚Р С•Р в„–РЎвЂљР С‘",
        "Р Т‘Р ВµР С–Р ВµР Р…",
        "Р ВµР Т‘Р С‘Р С",
    )
    if any(marker in normalized for marker in start_markers):
        return True
    return "РЎвЂљР В°Р С”РЎРѓР С•Р С—Р В°РЎР‚Р С”" in normalized and any(marker in normalized for marker in intent_markers)


def _looks_like_current_step_help_request(text: str) -> bool:
    normalized = normalize_text_token(text)
    compact = normalized.strip(" ?!.")
    if not compact:
        return False
    if set(compact) <= {"?"}:
        return True
    markers = (
        "РЎвЂЎРЎвЂљР С• Р Т‘Р ВµР В»Р В°РЎвЂљРЎРЉ",
        "РЎвЂЎРЎвЂљР С• Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ",
        "РЎвЂЎРЎвЂљР С• Р С—Р С‘РЎРѓР В°РЎвЂљРЎРЉ",
        "РЎвЂЎРЎвЂљР С• Р Р…Р В°Р С—Р С‘РЎРѓР В°РЎвЂљРЎРЉ",
        "Р С”Р В°Р С”Р С•Р в„– Р С•РЎвЂљР Р†Р ВµРЎвЂљ",
        "Р С”Р В°Р С”Р С•Р в„– Р С•РЎвЂљР Р†Р ВµРЎвЂљ Р С—Р С‘РЎРѓР В°РЎвЂљРЎРЉ",
        "Р С”Р В°Р С” Р С•РЎвЂљР Р†Р ВµРЎвЂљР С‘РЎвЂљРЎРЉ",
        "Р Р…Р Вµ Р С—Р С•Р Р…РЎРЏР В»",
        "Р Р…Р Вµ Р С—Р С•Р Р…Р С‘Р СР В°РЎР‹",
        "Р С•Р В±РЎР‰РЎРЏРЎРѓР Р…Р С‘",
        "Р С—Р С•РЎРЏРЎРѓР Р…Р С‘",
    )
    return any(marker in normalized for marker in markers)


def _detect_support_topic(normalized: str, active_topic: str | None) -> str | None:
    if active_topic and normalized in {"РЎРѓР Т‘Р ВµР В»Р В°Р В»", "Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ", "Р С–Р С•РЎвЂљР С•Р Р†Р С•", "Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В»Р С•РЎРѓРЎРЉ", "Р С•Р С”", "ok"}:
        return active_topic
    if active_topic and any(
        token in normalized
        for token in {"Р Р…Р Вµ Р С—Р С•Р В»РЎС“РЎвЂЎР В°Р ВµРЎвЂљРЎРѓРЎРЏ", "Р Р…Р Вµ Р Р†РЎвЂ№РЎв‚¬Р В»Р С•", "Р Р…Р Вµ РЎР‚Р В°Р В±Р С•РЎвЂљР В°Р ВµРЎвЂљ", "Р С•РЎв‚¬Р С‘Р В±Р С”Р В°", "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ", "Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…", "Р Р…Р ВµР В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…"}
    ):
        return active_topic

    topic_keywords = {
        "yandex_login": {"Р Р…Р Вµ Р СР С•Р С–РЎС“ Р Р†Р С•Р в„–РЎвЂљР С‘", "Р Р…Р Вµ Р СР С•Р С–РЎС“ Р В·Р В°Р в„–РЎвЂљР С‘", "Р Р†Р С•Р в„–РЎвЂљР С‘ Р Р† РЎРЏР Р…Р Т‘Р ВµР С”РЎРѓ", "Р В»Р С•Р С–Р С‘Р Р…", "Р Р†РЎвЂ¦Р С•Р Т‘", "Р В°Р Р†РЎвЂљР С•РЎР‚Р С‘Р В·Р В°РЎвЂ Р С‘РЎРЏ"},
        "yandex_sms": {"РЎРѓР СРЎРѓ", "sms", "Р С”Р С•Р Т‘ Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ", "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎвЂ¦Р С•Р Т‘Р С‘РЎвЂљ Р С”Р С•Р Т‘", "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎв‚¬Р ВµР В» Р С”Р С•Р Т‘", "Р Р…Р Вµ Р С—РЎР‚Р С‘РЎв‚¬Р В»Р В° РЎРѓР СРЎРѓ"},
        "account_inactive": {
            "Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…",
            "Р Р…Р ВµР В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…",
            "Р В°Р С”Р С”Р В°РЎС“Р Р…РЎвЂљ Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…",
            "Р С—РЎР‚Р С•РЎвЂћР С‘Р В»РЎРЉ Р Р…Р Вµ Р В°Р С”РЎвЂљР С‘Р Р†Р ВµР Р…",
            "РЎРѓ Р Р…РЎС“Р В»РЎРЏ",
            "Р С•Р В±РЎР‚Р В°РЎвЂљР Р…Р С• Р С”Р С‘Р Р…РЎС“Р В»Р С‘",
            "Р В·Р В°Р Р…Р С•Р Р†Р С• Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°РЎвЂљРЎРЉРЎРѓРЎРЏ",
            "Р С•Р В±РЎР‚Р В°РЎвЂљР Р…Р С• Р Р…Р В° РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎР‹",
            "Р С”Р С‘Р Р…РЎС“Р В»Р С‘ Р Р…Р В° РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎР‹",
        },
        "go_online": {
            "Р Р†РЎвЂ№Р в„–РЎвЂљР С‘ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹",
            "Р С”Р В°Р С” Р Р†РЎвЂ№Р в„–РЎвЂљР С‘ Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹",
            "Р Р…Р В° Р В»Р С‘Р Р…Р С‘РЎР‹",
            "Р Р†Р С”Р В»РЎР‹РЎвЂЎР С‘РЎвЂљРЎРЉ Р В»Р С‘Р Р…Р С‘РЎР‹",
            "Р С”Р В°Р С” Р Р…Р В°РЎвЂЎР В°РЎвЂљРЎРЉ РЎР‚Р В°Р В±Р С•РЎвЂљР В°РЎвЂљРЎРЉ",
            "Р Р…Р ВµРЎвЂљ Р В·Р В°Р С”Р В°Р В·Р С•Р Р†",
            "Р Р…Р С‘ Р С•Р Т‘Р Р…Р С•Р С–Р С• Р В·Р В°Р С”Р В°Р В·Р В°",
            "Р Р…Р Вµ Р С•Р Т‘Р Р…Р С•Р С–Р С• Р В·Р В°Р С”Р В°Р В·Р В°",
            "Р В·Р В°Р С”Р В°Р В·Р С•Р Р† Р Р…Р Вµ Р Т‘Р В°Р В»Р С‘",
            "РЎвЂЎР В°РЎРѓ Р С—Р С•РЎРѓР С‘Р Т‘Р ВµР В»",
            "Р Т‘Р С•Р В»Р С–Р С• Р В±Р ВµР В· Р В·Р В°Р С”Р В°Р В·Р С•Р Р†",
            "Р Р…Р ВµРЎвЂљ Р В·Р В°Р С”Р В°Р В·Р В°",
        },
    }
    for topic, keywords in topic_keywords.items():
        if any(keyword in normalized for keyword in keywords):
            return topic
    return active_topic if active_topic and normalized in {"РЎРѓР Т‘Р ВµР В»Р В°Р В»", "Р Т‘Р В°Р В»РЎРЉРЎв‚¬Р Вµ", "Р С–Р С•РЎвЂљР С•Р Р†Р С•", "Р С—Р С•Р В»РЎС“РЎвЂЎР С‘Р В»Р С•РЎРѓРЎРЉ"} else None


def _application_status_from_state(state: DialogueState) -> str:
    if state in {
        DialogueState.ASK_DRIVER_LICENSE_FRONT,
        DialogueState.ASK_DRIVER_LICENSE_BACK,
        DialogueState.ASK_ID_CARD,
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC,
    }:
        return "waiting_documents"
    if state == DialogueState.CONFIRM_DATA:
        return "confirming_data"
    if state == DialogueState.READY_TO_SEND_YANDEX:
        return "ready_to_send_yandex"
    if state == DialogueState.SENDING_TO_YANDEX:
        return "sending_to_yandex"
    if state in {
        DialogueState.SENT_TO_YANDEX,
        DialogueState.ASK_YANDEX_PRO_LOGIN,
        DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS,
    }:
        return "sent_to_yandex"
    if state == DialogueState.YANDEX_ERROR:
        return "yandex_error"
    if state == DialogueState.DUPLICATE_REJECTED:
        return "duplicate_rejected"
    if state == DialogueState.COMPLETED:
        return "completed"
    return "collecting_data"
DUPLICATE_REJECTED_REPLY = (
    "Р СћР В°Р С”Р С•Р в„– Р Р†Р С•Р Т‘Р С‘РЎвЂљР ВµР В»РЎРЉ РЎС“Р В¶Р Вµ Р В·Р В°РЎР‚Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р….\n\n"
    "Р вЂќР С•РЎРѓРЎвЂљРЎС“Р С—Р Р…РЎвЂ№Р Вµ Р Т‘Р ВµР в„–РЎРѓРЎвЂљР Р†Р С‘РЎРЏ:\n"
    "1. Р РЋРЎвЂљР В°РЎвЂљРЎРЉ РЎРѓР В°Р СР С•Р В·Р В°Р Р…РЎРЏРЎвЂљРЎвЂ№Р С\n"
    "2. Р ВР В·Р СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ\n"
    "3. Р РЋР СР ВµР Р…Р С‘РЎвЂљРЎРЉ Р В°Р Р†РЎвЂљР С•Р СР С•Р В±Р С‘Р В»РЎРЉ\n"
    "4. Р СџР С•Р СР С•РЎвЂ°РЎРЉ РЎРѓР С• Р Р†РЎвЂ¦Р С•Р Т‘Р С•Р С\n"
    "5. Р РЋР Р†РЎРЏР В·Р В°РЎвЂљРЎРЉРЎРѓРЎРЏ РЎРѓ Р СР ВµР Р…Р ВµР Т‘Р В¶Р ВµРЎР‚Р С•Р С"
)


def _existing_driver_options_reply() -> str:
    return (
        "Понял, вы уже подключены. Что нужно сделать?\n"
        "1. Вывод денег\n"
        "2. Вход в Яндекс Про\n"
        "3. Тарифы\n"
        "4. Изменить авто/документы\n"
        "5. Менеджер"
    )
