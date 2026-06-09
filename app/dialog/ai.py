from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
import re

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dialog.faq import find_faq_answer, load_knowledge_base
from app.dialog.llm_prompt import build_system_prompt, build_user_prompt
from app.dialog.prompts import PROMPTS
from app.dialog.states import DialogueState
from app.drivers.models import Driver
from app.utils.logger import get_logger
from app.utils.validators import (
    extract_known_car_brand,
    looks_like_iin,
    looks_like_phone,
    looks_like_precise_car_model,
    normalize_car_brand,
    normalize_car_model,
    normalize_employment_type,
    normalize_phone,
    normalize_plate_number,
    normalize_text_token,
    parse_confirmation,
    parse_date,
    parse_iso_date,
    parse_year,
    parse_yes_no,
    split_full_name,
    validate_birth_date,
    validate_driver_dates,
    validate_hired_at,
    validate_kz_iin,
)

try:
    from google import genai
except ImportError:
    genai = None

logger = get_logger(__name__)


@dataclass
class AIResult:
    reply: str
    intent: str
    extracted_fields: dict[str, str] = field(default_factory=dict)
    next_state: str | None = None
    confidence: float = 0.6
    provider: str = "deterministic"
    target_field: str | None = None
    new_value_raw: str | None = None
    normalized_fields: dict[str, str] = field(default_factory=dict)
    reasoning_summary: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    suggested_next_action: str | None = None
    raw_decision: dict[str, object] = field(default_factory=dict)


class AIModelResponse(BaseModel):
    reply: str = ""
    intent: str = "clarification"
    extracted_fields: dict[str, str] = Field(default_factory=dict)
    next_state: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target_field: str | None = None
    new_value_raw: str | None = None
    normalized_fields: dict[str, str] = Field(default_factory=dict)
    reasoning_summary: str | None = None
    suggested_next_action: str | None = None


class AIService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.knowledge_base = load_knowledge_base()
        self.deterministic = DeterministicAIProvider(self.knowledge_base)
        self.llm = self._build_llm_provider()

    def respond(self, state: str, message: str, driver: Driver) -> AIResult:
        fallback = self.deterministic.respond(state, message, driver)
        if self.llm is None:
            return fallback
        try:
            current_state = DialogueState(state)
            llm_result = self.llm.respond(state, message, driver, self.knowledge_base)
            normalized = _normalize_llm_result(llm_result, current_state, fallback)
            if normalized.fallback_used:
                logger.warning(
                    "AI normalization fallback for state=%s reason=%s raw=%s",
                    state,
                    normalized.fallback_reason,
                    normalized.raw_decision,
                )
            return normalized
        except Exception as exc:
            logger.exception("AI provider failed for state %s: %s", state, exc)
            fallback.fallback_used = True
            fallback.fallback_reason = "provider_exception"
            fallback.reasoning_summary = "fallback:provider_exception"
            fallback.validation_errors.append(str(exc))
            return fallback

    def _build_llm_provider(self) -> "OpenAIProvider | GeminiProvider | None":
        if self.settings.ai_provider == "openai":
            if not self.settings.openai_api_key:
                logger.warning("OPENAI_API_KEY is not configured, falling back to deterministic AI")
                return None
            return OpenAIProvider()
        if self.settings.ai_provider == "gemini":
            if not self.settings.gemini_api_key:
                logger.warning("GEMINI_API_KEY is not configured, falling back to deterministic AI")
                return None
            if genai is None:
                logger.warning("google-genai package is not installed, falling back to deterministic AI")
                return None
            return GeminiProvider()
        logger.warning("Unsupported AI_PROVIDER=%s, falling back to deterministic AI", self.settings.ai_provider)
        return None


class OpenAIProvider:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def respond(self, state: str, message: str, driver: Driver, knowledge_base: dict[str, str]) -> AIResult:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": build_system_prompt()},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        state=state,
                        message=message,
                        driver=driver,
                        knowledge_base=knowledge_base,
                        allowed_states=_allowed_next_states(DialogueState(state)),
                    ),
                },
            ],
            text_format=AIModelResponse,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI returned no parsed structured output")
        return _result_from_model(parsed, provider="openai")


class GeminiProvider:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model

    def respond(self, state: str, message: str, driver: Driver, knowledge_base: dict[str, str]) -> AIResult:
        response = self.client.models.generate_content(
            model=self.model,
            contents=build_user_prompt(
                state=state,
                message=message,
                driver=driver,
                knowledge_base=knowledge_base,
                allowed_states=_allowed_next_states(DialogueState(state)),
            ),
            config={
                "system_instruction": build_system_prompt(),
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, AIModelResponse):
            return _result_from_model(parsed, provider="gemini")
        if isinstance(parsed, dict):
            return _result_from_model(_coerce_model_response(parsed, state), provider="gemini")

        raw_text = getattr(response, "text", "") or ""
        if not raw_text:
            raise RuntimeError("Gemini returned no structured output")
        try:
            payload = json.loads(raw_text)
            return _result_from_model(_coerce_model_response(payload, state), provider="gemini")
        except json.JSONDecodeError:
            return _result_from_model(AIModelResponse.model_validate_json(raw_text), provider="gemini")


class DeterministicAIProvider:
    def __init__(self, knowledge_base: dict[str, str]) -> None:
        self.knowledge_base = knowledge_base

    def respond(self, state: str, message: str, driver: Driver) -> AIResult:
        faq_answer = _match_faq(message, self.knowledge_base)
        if faq_answer:
            return AIResult(
                faq_answer,
                "faq",
                {},
                state,
                0.9,
                reasoning_summary="matched_kb:faq",
                suggested_next_action=state,
            )

        current_state = DialogueState(state)
        text = message.strip()

        if not text:
            return AIResult(
                _clarification_reply(current_state),
                "clarification",
                {},
                state,
                0.4,
                reasoning_summary="clarification:empty_message",
                suggested_next_action=state,
            )

        step_help_reply = _build_step_help_reply(current_state, text)
        if step_help_reply:
            return AIResult(
                step_help_reply,
                "help",
                {},
                state,
                0.88,
                reasoning_summary=f"step_help:{current_state.value}",
                suggested_next_action=state,
            )

        field_edit = _parse_confirm_field_edit(current_state, text)
        if field_edit:
            return field_edit

        if current_state == DialogueState.NEW:
            if _looks_like_full_name(text):
                last_name, first_name, middle_name = split_full_name(text)
                extracted = {"full_name": text}
                if last_name:
                    extracted["last_name"] = last_name
                if first_name:
                    extracted["first_name"] = first_name
                if middle_name:
                    extracted["middle_name"] = middle_name
                return AIResult(
                    "Здравствуйте. Начинаем регистрацию.",
                    "registration",
                    extracted,
                    DialogueState.ASK_PHONE.value,
                    0.95,
                    reasoning_summary="registration_extract:full_name",
                    suggested_next_action=DialogueState.ASK_PHONE.value,
                )
            if _looks_like_onboarding_intent(text):
                return AIResult(
                    PROMPTS[DialogueState.NEW],
                    "clarification",
                    {},
                    DialogueState.ASK_FULL_NAME.value,
                    0.75,
                    reasoning_summary="onboarding_intent:new",
                    suggested_next_action=DialogueState.ASK_FULL_NAME.value,
                )
            return AIResult(
                "Здравствуйте. Я могу рассказать об условиях парка и помочь пройти регистрацию. Если хотите начать, напишите ваше ФИО полностью.",
                "clarification",
                {},
                DialogueState.NEW.value,
                0.55,
                reasoning_summary="new_state:greeting",
                suggested_next_action=DialogueState.ASK_FULL_NAME.value,
            )

        correction_state = _detect_correction_state(current_state, text)
        if correction_state is not None:
            return AIResult(
                f"Хорошо, исправим этот пункт. {PROMPTS[correction_state]}",
                "correction",
                {},
                correction_state.value,
                0.85,
                reasoning_summary=f"correction:{correction_state.value}",
                suggested_next_action=correction_state.value,
            )

        if current_state == DialogueState.ASK_FULL_NAME:
            if _looks_like_full_name(text):
                last_name, first_name, middle_name = split_full_name(text)
                extracted = {"full_name": text}
                if last_name:
                    extracted["last_name"] = last_name
                if first_name:
                    extracted["first_name"] = first_name
                if middle_name:
                    extracted["middle_name"] = middle_name
                return AIResult(
                    "",
                    "registration",
                    extracted,
                    DialogueState.ASK_PHONE.value,
                    0.9,
                    reasoning_summary="registration_extract:full_name",
                    suggested_next_action=DialogueState.ASK_PHONE.value,
                )
            return AIResult(
                _clarification_reply(current_state),
                "clarification",
                {},
                state,
                0.45,
                reasoning_summary="clarification:full_name",
                suggested_next_action=state,
            )

        if current_state == DialogueState.ASK_PHONE and looks_like_phone(text):
            return AIResult(
                "",
                "registration",
                {"phone": normalize_phone(text)},
                DialogueState.ASK_CITY.value,
                0.95,
                normalized_fields={"phone": normalize_phone(text)},
                reasoning_summary="registration_extract:phone",
                suggested_next_action=DialogueState.ASK_CITY.value,
            )
        if current_state == DialogueState.ASK_IIN and looks_like_iin(text):
            normalized_iin = re.sub(r"\D+", "", text)
            iin_errors = validate_kz_iin(normalized_iin)
            if iin_errors:
                return AIResult(
                    "ИИН выглядит некорректным. Проверьте 12 цифр и дату рождения, зашитую в ИИН, затем отправьте ИИН еще раз.",
                    "clarification",
                    {},
                    state,
                    0.7,
                    reasoning_summary="validation:iin_impossible",
                    validation_errors=iin_errors,
                    suggested_next_action=state,
                )
            return AIResult(
                "",
                "registration",
                {"iin": normalized_iin},
                DialogueState.ASK_BIRTH_DATE.value,
                0.95,
                normalized_fields={"iin": normalized_iin},
                reasoning_summary="registration_extract:iin",
                suggested_next_action=DialogueState.ASK_BIRTH_DATE.value,
            )
        if current_state == DialogueState.ASK_CAR_YEAR:
            year = parse_year(text)
            if year:
                return AIResult(
                    "",
                    "registration",
                    {"year": str(year)},
                    DialogueState.ASK_CAR_PLATE.value,
                    0.9,
                    normalized_fields={"year": str(year)},
                    reasoning_summary="registration_extract:year",
                    suggested_next_action=DialogueState.ASK_CAR_PLATE.value,
                )

        date_steps = {
            DialogueState.ASK_BIRTH_DATE: ("birth_date", DialogueState.ASK_DRIVING_EXPERIENCE_SINCE.value),
            DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: ("driving_experience_since", DialogueState.ASK_CAR_BRAND.value),
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: (
                "driver_license_issue_date",
                DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
            ),
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: (
                "driver_license_expires_at",
                DialogueState.ASK_EMPLOYMENT_TYPE.value,
            ),
            DialogueState.ASK_HIRED_AT: ("hired_at", DialogueState.ASK_HEARING_IMPAIRED.value),
        }
        if current_state in date_steps:
            parsed_date = parse_date(text)
            if parsed_date:
                field_name, next_state = date_steps[current_state]
                validation_errors: list[str] = []
                if current_state == DialogueState.ASK_BIRTH_DATE:
                    validation_errors = validate_birth_date(parsed_date)
                elif current_state == DialogueState.ASK_DRIVING_EXPERIENCE_SINCE:
                    validation_errors = validate_driver_dates(
                        birth_date=getattr(driver, "birth_date", None),
                        driving_experience_since=parsed_date,
                    )
                elif current_state == DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE:
                    validation_errors = validate_driver_dates(
                        birth_date=getattr(driver, "birth_date", None),
                        driver_license_issue_date=parsed_date,
                    )
                elif current_state == DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT:
                    validation_errors = validate_driver_dates(
                        birth_date=getattr(driver, "birth_date", None),
                        driver_license_issue_date=getattr(driver, "driver_license_issue_date", None),
                        driver_license_expires_at=parsed_date,
                    )
                elif current_state == DialogueState.ASK_HIRED_AT:
                    validation_errors = validate_hired_at(parsed_date)

                if validation_errors:
                    return AIResult(
                        _validation_error_reply(current_state, validation_errors),
                        "clarification",
                        {},
                        state,
                        0.72,
                        reasoning_summary=f"validation:{field_name}",
                        validation_errors=validation_errors,
                        suggested_next_action=state,
                    )
                return AIResult(
                    "",
                    "registration",
                    {field_name: parsed_date},
                    next_state,
                    0.9,
                    normalized_fields={field_name: parsed_date},
                    reasoning_summary=f"registration_extract:{field_name}",
                    suggested_next_action=next_state,
                )

        if current_state == DialogueState.ASK_HEARING_IMPAIRED:
            parsed = parse_yes_no(text)
            if parsed is not None:
                value = str(parsed).lower()
                return AIResult(
                    "",
                    "registration",
                    {"is_hearing_impaired": value},
                    DialogueState.ASK_DRIVER_LICENSE_FRONT.value,
                    0.9,
                    normalized_fields={"is_hearing_impaired": value},
                    reasoning_summary="registration_extract:is_hearing_impaired",
                    suggested_next_action=DialogueState.ASK_DRIVER_LICENSE_FRONT.value,
                )

        if current_state == DialogueState.CONFIRM_DATA and parse_confirmation(text):
            return AIResult(
                "",
                "confirmation",
                {},
                DialogueState.READY_TO_SEND_YANDEX.value,
                0.99,
                reasoning_summary="confirmation:confirm_data",
                suggested_next_action=DialogueState.READY_TO_SEND_YANDEX.value,
            )

        extracted = _extract_safe_field_answer(current_state, text)
        if extracted:
            next_state = _default_next_state(current_state).value
            return AIResult(
                "",
                "registration",
                extracted,
                next_state,
                0.8,
                normalized_fields=extracted.copy(),
                reasoning_summary=f"registration_extract:{','.join(sorted(extracted))}",
                suggested_next_action=next_state,
            )

        return AIResult(
            _clarification_reply(current_state),
            "clarification",
            {},
            state,
            0.4,
            reasoning_summary="clarification:unrecognized_message",
            suggested_next_action=state,
        )


def _result_from_model(model: AIModelResponse, *, provider: str) -> AIResult:
    return AIResult(
        reply=model.reply,
        intent=model.intent,
        extracted_fields=dict(model.extracted_fields),
        next_state=model.next_state,
        confidence=model.confidence,
        provider=provider,
        target_field=model.target_field,
        new_value_raw=model.new_value_raw,
        normalized_fields=dict(model.normalized_fields),
        reasoning_summary=model.reasoning_summary,
        suggested_next_action=model.suggested_next_action,
        raw_decision=model.model_dump(),
    )


def _normalize_llm_result(result: AIResult, current_state: DialogueState, fallback: AIResult) -> AIResult:
    normalized = AIResult(**asdict(result))
    normalized.reply = _cleanup_text(normalized.reply)
    normalized.new_value_raw = _cleanup_text(normalized.new_value_raw)
    normalized.reasoning_summary = normalized.reasoning_summary or f"llm:{normalized.intent}"
    normalized.suggested_next_action = normalized.suggested_next_action or normalized.next_state or current_state.value

    if normalized.intent not in {"registration", "confirmation", "correction", "faq", "help", "smalltalk", "clarification", "field_edit"}:
        return _fallback_from(fallback, result, "unknown_intent")

    try:
        next_state = DialogueState(normalized.next_state or current_state.value)
    except ValueError:
        return _fallback_from(fallback, result, "invalid_next_state")

    if next_state.value not in set(_allowed_next_states(current_state)):
        return _fallback_from(fallback, result, "disallowed_next_state")

    normalized.next_state = next_state.value
    normalized.normalized_fields = _normalize_fields_map(normalized.normalized_fields or normalized.extracted_fields)
    normalized.extracted_fields = _normalize_fields_map(normalized.extracted_fields)

    if normalized.intent not in {"registration", "confirmation", "correction", "field_edit"} and not normalized.reply:
        return _fallback_from(fallback, result, "missing_reply")

    if normalized.intent == "registration" and not _is_safe_registration_result(normalized, current_state):
        return _fallback_from(fallback, result, "unsafe_registration_fields")

    if normalized.intent == "field_edit":
        if current_state != DialogueState.CONFIRM_DATA:
            return _fallback_from(fallback, result, "field_edit_outside_confirm")
        if not normalized.target_field:
            return _fallback_from(fallback, result, "field_edit_missing_target")
        normalized_edit, errors = _normalize_field_edit(normalized.target_field, normalized.new_value_raw or "")
        if errors:
            return _fallback_from(fallback, result, "field_edit_invalid_value", errors)
        normalized.normalized_fields = normalized_edit
        normalized.extracted_fields = normalized_edit
        normalized.reply = normalized.reply or "Хорошо, данные обновил. Проверьте сводку еще раз."
        normalized.reasoning_summary = normalized.reasoning_summary or f"field_edit:{normalized.target_field}"
        normalized.suggested_next_action = "confirm_data"
        normalized.next_state = DialogueState.CONFIRM_DATA.value

    return normalized


def _fallback_from(fallback: AIResult, raw_result: AIResult, reason: str, errors: list[str] | None = None) -> AIResult:
    resolved = AIResult(**asdict(fallback))
    resolved.fallback_used = True
    resolved.fallback_reason = reason
    resolved.reasoning_summary = f"fallback:{reason}"
    resolved.validation_errors = list(errors or [])
    resolved.raw_decision = raw_result.raw_decision or _trace_payload(raw_result)
    resolved.provider = raw_result.provider or resolved.provider
    return resolved


def _allowed_next_states(current_state: DialogueState) -> list[str]:
    if current_state == DialogueState.NEW:
        return [
            DialogueState.NEW.value,
            DialogueState.ASK_FULL_NAME.value,
            DialogueState.ASK_PHONE.value,
        ]
    if current_state == DialogueState.CONFIRM_DATA:
        return [
            DialogueState.CONFIRM_DATA.value,
            DialogueState.READY_TO_SEND_YANDEX.value,
            DialogueState.ASK_FULL_NAME.value,
            DialogueState.ASK_PHONE.value,
            DialogueState.ASK_CITY.value,
            DialogueState.ASK_ADDRESS.value,
            DialogueState.ASK_IIN.value,
            DialogueState.ASK_BIRTH_DATE.value,
            DialogueState.ASK_DRIVING_EXPERIENCE_SINCE.value,
            DialogueState.ASK_CAR_BRAND.value,
            DialogueState.ASK_CAR_MODEL.value,
            DialogueState.ASK_CAR_YEAR.value,
            DialogueState.ASK_CAR_PLATE.value,
            DialogueState.ASK_CAR_COLOR.value,
            DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
            DialogueState.ASK_EMPLOYMENT_TYPE.value,
            DialogueState.ASK_HIRED_AT.value,
            DialogueState.ASK_HEARING_IMPAIRED.value,
        ]
    if current_state == DialogueState.YANDEX_ERROR:
        return [
            DialogueState.YANDEX_ERROR.value,
            DialogueState.CONFIRM_DATA.value,
            DialogueState.ASK_FULL_NAME.value,
            DialogueState.ASK_PHONE.value,
            DialogueState.ASK_CITY.value,
            DialogueState.ASK_ADDRESS.value,
            DialogueState.ASK_IIN.value,
            DialogueState.ASK_BIRTH_DATE.value,
            DialogueState.ASK_DRIVING_EXPERIENCE_SINCE.value,
            DialogueState.ASK_CAR_BRAND.value,
            DialogueState.ASK_CAR_MODEL.value,
            DialogueState.ASK_CAR_YEAR.value,
            DialogueState.ASK_CAR_PLATE.value,
            DialogueState.ASK_CAR_COLOR.value,
            DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
            DialogueState.ASK_EMPLOYMENT_TYPE.value,
            DialogueState.ASK_HIRED_AT.value,
            DialogueState.ASK_HEARING_IMPAIRED.value,
        ]
    return [current_state.value, _default_next_state(current_state).value]


def _default_next_state(state: DialogueState) -> DialogueState:
    order = [
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
        DialogueState.ASK_DRIVER_LICENSE_NUMBER,
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE,
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT,
        DialogueState.ASK_EMPLOYMENT_TYPE,
        DialogueState.ASK_HIRED_AT,
        DialogueState.ASK_HEARING_IMPAIRED,
        DialogueState.ASK_DRIVER_LICENSE_FRONT,
        DialogueState.ASK_DRIVER_LICENSE_BACK,
        DialogueState.ASK_ID_CARD,
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC,
        DialogueState.ASK_SELFIE_WITH_LICENSE,
        DialogueState.CONFIRM_DATA,
    ]
    index = order.index(state)
    return order[min(index + 1, len(order) - 1)]


def _looks_like_full_name(value: str) -> bool:
    parts = [part for part in normalize_text_token(value).split() if part]
    if len(parts) < 2:
        return False
    if any(part.isdigit() for part in parts):
        return False
    return len(parts[0]) >= 2 and len(parts[1]) >= 2


def _looks_like_onboarding_intent(value: str) -> bool:
    normalized = normalize_text_token(value)
    triggers = (
        "привет",
        "здравствуйте",
        "добрый день",
        "салам",
        "хочу подключиться",
        "хочу работать",
        "хочу в парк",
        "хочу зарегистрироваться",
        "хочу регистрацию",
        "как подключиться",
        "как устроиться",
        "интересует работа",
        "нужна работа",
        "подключение",
        "регистрация",
        "подключиться",
    )
    return any(trigger in normalized for trigger in triggers)


def _match_faq(message: str, knowledge_base: dict[str, str]) -> str | None:
    return find_faq_answer(message, knowledge_base)


def _is_safe_registration_result(result: AIResult, current_state: DialogueState) -> bool:
    if current_state == DialogueState.NEW:
        return True
    if not result.extracted_fields:
        return False
    expected_fields = _expected_fields_for_state(current_state)
    if not expected_fields:
        return False
    return any(field in result.extracted_fields for field in expected_fields)


def _expected_fields_for_state(state: DialogueState) -> set[str]:
    mapping = {
        DialogueState.ASK_FULL_NAME: {"full_name", "last_name", "first_name", "middle_name"},
        DialogueState.ASK_PHONE: {"phone"},
        DialogueState.ASK_CITY: {"city"},
        DialogueState.ASK_ADDRESS: {"address"},
        DialogueState.ASK_IIN: {"iin"},
        DialogueState.ASK_BIRTH_DATE: {"birth_date"},
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: {"driving_experience_since"},
        DialogueState.ASK_CAR_BRAND: {"brand"},
        DialogueState.ASK_CAR_MODEL: {"model"},
        DialogueState.ASK_CAR_YEAR: {"year"},
        DialogueState.ASK_CAR_PLATE: {"plate_number"},
        DialogueState.ASK_CAR_COLOR: {"color"},
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: {"driver_license_number"},
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: {"driver_license_issue_date"},
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: {"driver_license_expires_at"},
        DialogueState.ASK_EMPLOYMENT_TYPE: {"employment_type"},
        DialogueState.ASK_HIRED_AT: {"hired_at"},
        DialogueState.ASK_HEARING_IMPAIRED: {"is_hearing_impaired"},
    }
    return mapping.get(state, set())


def _extract_safe_field_answer(current_state: DialogueState, text: str) -> dict[str, str]:
    if _looks_like_non_field_message(text):
        return {}

    if current_state == DialogueState.ASK_CITY and _looks_like_city_answer(text):
        return {"city": text.strip()}
    if current_state == DialogueState.ASK_ADDRESS and _looks_like_address_answer(text):
        return {"address": text.strip()}
    if current_state == DialogueState.ASK_CAR_BRAND and _looks_like_short_entity_answer(text):
        known_brand = extract_known_car_brand(text)
        if known_brand:
            return {"brand": known_brand}
        return {"brand": normalize_car_brand(text)}
    if current_state == DialogueState.ASK_CAR_MODEL and _looks_like_short_entity_answer(text):
        if not looks_like_precise_car_model(text):
            return {}
        return {"model": normalize_car_model(text)}
    if current_state == DialogueState.ASK_CAR_PLATE and _looks_like_plate_answer(text):
        return {"plate_number": normalize_plate_number(text)}
    if current_state == DialogueState.ASK_CAR_COLOR and _looks_like_short_entity_answer(text):
        return {"color": text.strip()}
    if current_state == DialogueState.ASK_DRIVER_LICENSE_NUMBER and _looks_like_license_number(text):
        return {"driver_license_number": text.strip()}
    if current_state == DialogueState.ASK_EMPLOYMENT_TYPE:
        normalized = normalize_employment_type(text)
        if normalized in {"штатный", "самозанятый", "ип"} or normalized != text.strip():
            return {"employment_type": normalized}
    return {}


def _clarification_reply(current_state: DialogueState) -> str:
    clean_custom = {
        DialogueState.ASK_FULL_NAME: "Напишите ваше ФИО полностью. Например: Абай Аят Жаныбекулы.",
        DialogueState.ASK_PHONE: "Укажите контактный номер телефона в формате +7XXXXXXXXXX.",
        DialogueState.ASK_CITY: "Напишите только город, в котором будете работать. Например: Астана.",
        DialogueState.ASK_ADDRESS: "Укажите адрес проживания или регистрации. Например: Балкантау 117, Астана.",
        DialogueState.ASK_IIN: "Укажите ИИН из 12 цифр.",
        DialogueState.ASK_BIRTH_DATE: "Укажите дату рождения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "Укажите дату начала водительского стажа в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_CAR_BRAND: "Напишите точную марку автомобиля. Например: Toyota, Mercedes, Haval, Changan, Hyundai.",
        DialogueState.ASK_CAR_MODEL: "Напишите точное название модели автомобиля. Например: Camry, C-Class, E-Class, Jolion, CS55 Plus.",
        DialogueState.ASK_CAR_YEAR: "Укажите год выпуска автомобиля. Например: 2018.",
        DialogueState.ASK_CAR_PLATE: "Укажите госномер автомобиля без лишних пояснений.",
        DialogueState.ASK_CAR_COLOR: "Укажите цвет автомобиля. Например: белый.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: "Напишите серию и номер водительского удостоверения.",
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "Укажите дату выдачи водительского удостоверения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "Укажите срок действия водительского удостоверения до даты в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "Укажите условие работы: штатный, самозанятый или ИП.",
        DialogueState.ASK_HIRED_AT: "Укажите дату принятия в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_HEARING_IMPAIRED: "Ответьте коротко: да или нет.",
        DialogueState.CONFIRM_DATA: "Напишите, какое поле исправить и на какое значение. Например: исправь город на Алматы.",
    }
    if current_state in clean_custom:
        return clean_custom[current_state]
    custom = {
        DialogueState.ASK_FULL_NAME: "Напишите ваше ФИО полностью. Например: Абай Аят Жаныбекулы.",
        DialogueState.ASK_PHONE: "Укажите контактный номер телефона в формате +7XXXXXXXXXX.",
        DialogueState.ASK_CITY: "Напишите только город, в котором будете работать. Например: Астана.",
        DialogueState.ASK_ADDRESS: "Укажите адрес проживания или регистрации. Например: Балкантау 117, Астана.",
        DialogueState.ASK_IIN: "Укажите ИИН из 12 цифр.",
        DialogueState.ASK_BIRTH_DATE: "Укажите дату рождения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "Укажите дату начала водительского стажа в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_CAR_BRAND: "Напишите марку автомобиля. Например: Toyota.",
        DialogueState.ASK_CAR_MODEL: "Напишите модель автомобиля. Например: Camry.",
        DialogueState.ASK_CAR_YEAR: "Укажите год выпуска автомобиля. Например: 2018.",
        DialogueState.ASK_CAR_PLATE: "Укажите госномер автомобиля без лишних пояснений.",
        DialogueState.ASK_CAR_COLOR: "Укажите цвет автомобиля. Например: белый.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: "Напишите серию и номер водительского удостоверения.",
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "Укажите дату выдачи водительского удостоверения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "Укажите срок действия водительского удостоверения до даты в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "Укажите условие работы: штатный, самозанятый или ИП.",
        DialogueState.ASK_HIRED_AT: "Укажите дату принятия в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_HEARING_IMPAIRED: "Ответьте коротко: да или нет.",
        DialogueState.CONFIRM_DATA: "Напишите, какое поле исправить и на какое значение. Например: исправь город на Алматы.",
    }
    return custom.get(current_state, PROMPTS.get(current_state, "Пожалуйста, ответьте на текущий вопрос."))


def _build_step_help_reply(current_state: DialogueState, text: str) -> str | None:
    normalized = normalize_text_token(text)
    help_markers = (
        "??? ????",
        "?????",
        "??????",
        "??? ???",
        "??? ??????",
        "??? ?????",
        "??? ???? ???",
        "??? ???? ???",
        "?????? ?????",
        "?????? ????",
        "????? ???",
        "????? ?????",
        "????? ????",
        "???????",
        "???????",
        "?? ?????",
        "?? ??????",
        "?? ???????",
        "?? ????? ?????",
        "?? ????? ??? ????",
        "???? ???????? ????",
        "???????? ????",
        "??? ?????????",
        "??? ??????",
    )
    if not any(marker in normalized for marker in help_markers):
        return None

    explanations = {
        DialogueState.ASK_FULL_NAME: "??? ????? ??? ??????????? ???????? ? ??????? ?????. ??????? ???????, ??? ? ??? ??????? ???????? ???, ??? ? ??????????.",
        DialogueState.ASK_PHONE: "????? ???????? ????? ??? ????? ? ????, ??????????? ? ??????? ????? ? ??????????? ????? ? ?????? ??? ?? ????? ??????.",
        DialogueState.ASK_CITY: "????? ?????, ????? ??????, ??? ?? ?????? ???????? ? ? ?????? ?????? ????????? ???????????.",
        DialogueState.ASK_ADDRESS: "????? ????? ??? ?????? ???????? ? ?????????? ???????? ? ??????? ?????.",
        DialogueState.ASK_IIN: "??? ????? ??? ??????????? ???????? ? ???????? ?????? ? ??????? ?????. ????????? ?????? 12 ???? ??? ????????.",
        DialogueState.ASK_BIRTH_DATE: "???? ???????? ????? ??? ?????? ???????? ? ?????? ?????? ????????????? ?????????????.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "???? ?????? ????? ?????, ????? ??????? ??? ???????????? ???? ? ??????. ?????? ??? ????, ? ??????? ? ??? ???? ???????????? ????.",
        DialogueState.ASK_CAR_BRAND: "????? ?????????? ????? ??? ???????? ?????? ? ?????????? ???????? ?????? ? ???? ? ??????.",
        DialogueState.ASK_CAR_MODEL: "?????? ?????????? ????? ??? ?????????? ???????? ??????. ???????? ?????? ???????? ??????, ???????? Camry, C-Class, E-Class, Jolion ??? CS55 Plus.",
        DialogueState.ASK_CAR_YEAR: "??? ??????? ????? ??? ?????????? ?????? ?????????? ? ??????.",
        DialogueState.ASK_CAR_PLATE: "???????? ?????, ????? ???????????????? ? ????????? ?????????? ? ??????? ?????.",
        DialogueState.ASK_CAR_COLOR: "???? ?????????? ????? ??? ???????? ?????? ? ???????? ?????? ????.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: "????? ????????????? ????????????? ????? ??? ??????????? ???????? ? ???????? ????.",
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "???? ?????? ???? ????? ??? ?????????? ?????? ????????.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "???? ???????? ???? ?????, ????? ?????????, ??? ???????????? ????????????? ?????????.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "????? ????? ??????? ?????? ?????? ? ??????: ???????, ??????????? ??? ??. ??? ?????? ?? ?????????? ??????.",
        DialogueState.ASK_HIRED_AT: "???? ???????? ? ??? ???? ??????????? ??? ?????????? ? ????. ???? ????????? ??? ??????, ?????? ????? ??????? ??????????? ????.",
        DialogueState.ASK_HEARING_IMPAIRED: "???? ????? ????? ??? ?????? ????????, ????? ???? ????????? ??????? ??????????? ?????, ???? ??? ????.",
        DialogueState.ASK_DRIVER_LICENSE_FRONT: "???? ??????? ??????? ???? ????? ??? ???????? ????????????? ????????????? ??? ???????????.",
        DialogueState.ASK_DRIVER_LICENSE_BACK: "???? ???????? ??????? ???? ????? ??? ?????? ???????? ????????????? ?????????????.",
        DialogueState.ASK_ID_CARD: "???? ????????????? ???????? ????? ??? ????????????? ???????? ????????.",
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC: "???? ??????????? ??? ??? ????? ??? ????????????? ?????? ??????????.",
        DialogueState.ASK_SELFIE_WITH_LICENSE: "????? ? ???????????? ?????????????? ????? ??? ?????????????, ??? ????????? ??????????? ?????? ???.",
    }
    explanation = explanations.get(current_state)
    if not explanation:
        return None
    reminder = _clarification_reply(current_state)
    return f"{explanation}\n\n{reminder}"


def _validation_error_reply(current_state: DialogueState, errors: list[str]) -> str:
    if current_state == DialogueState.ASK_IIN or "invalid_iin_birth_date" in errors or "invalid_iin_length" in errors:
        return "ИИН выглядит некорректным. Проверьте 12 цифр и отправьте реальный ИИН еще раз."
    if "driver_underage" in errors:
        return "Дата рождения указывает на возраст младше 18 лет. Проверьте дату рождения и отправьте ее еще раз в формате ДД.ММ.ГГГГ."
    if "birth_date_in_future" in errors:
        return "Дата рождения не может быть в будущем. Отправьте корректную дату в формате ДД.ММ.ГГГГ."
    if "driving_experience_too_early" in errors or "driving_experience_before_birth" in errors:
        return "Дата начала стажа выглядит невозможной. Проверьте стаж и отправьте дату начала водительского стажа еще раз."
    if "driving_experience_in_future" in errors:
        return "Дата начала стажа не может быть в будущем. Отправьте корректную дату в формате ДД.ММ.ГГГГ."
    if "license_issue_before_birth" in errors or "license_issue_too_early" in errors:
        return "Дата выдачи прав выглядит невозможной. Проверьте дату выдачи и отправьте ее еще раз."
    if "license_issue_in_future" in errors:
        return "Дата выдачи прав не может быть в будущем. Отправьте корректную дату в формате ДД.ММ.ГГГГ."
    if "license_expires_before_issue" in errors:
        return "Срок действия прав не может быть раньше даты выдачи. Отправьте корректную дату окончания действия прав."
    if "license_expired" in errors:
        return "Срок действия прав уже истек. Проверьте дату и отправьте актуальную дату окончания действия прав."
    if "hired_at_in_future" in errors:
        return "Дата принятия не может быть в будущем. Обычно указывают дату подключения в парк или сегодняшнюю дату."
    return _clarification_reply(current_state)


def _looks_like_non_field_message(text: str) -> bool:
    normalized = normalize_text_token(text)
    if "?" in text:
        return True
    if len(normalized.split()) >= 5 and not looks_like_phone(text) and not looks_like_iin(text) and parse_date(text) is None:
        return True
    question_markers = (
        "???",
        "???",
        "????? ???????",
        "????? ? ??? ???????",
        "??????? ??????",
        "???????",
        "???",
        "??????",
        "?????",
        "??? ????",
        "???",
        "????? ??",
        "??????",
        "???????",
        "???????",
        "?? ?????",
        "?? ???????",
        "??? ???",
        "??? ??????",
        "??? ?????",
    )
    return any(marker in normalized for marker in question_markers)


def _looks_like_city_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    parts = [part for part in normalized.split() if part]
    if not (1 <= len(parts) <= 3):
        return False
    return all(part.replace("-", "").isalpha() for part in parts)


def _looks_like_address_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    has_digit = any(char.isdigit() for char in normalized)
    address_markers = ("ул", "улица", "пр", "проспект", "дом", "мкр", "микрорайон", "кв", "район")
    return len(normalized) >= 5 and (has_digit or any(marker in normalized for marker in address_markers) or len(normalized.split()) >= 2)


def _looks_like_short_entity_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    parts = [part for part in normalized.split() if part]
    return 1 <= len(parts) <= 4 and len(normalized) <= 32


def _looks_like_plate_answer(text: str) -> bool:
    token = text.strip().replace(" ", "").replace("-", "")
    return 5 <= len(token) <= 10 and token.isalnum()


def _looks_like_license_number(text: str) -> bool:
    token = text.strip().replace(" ", "")
    return 4 <= len(token) <= 20 and token.isalnum()


def _coerce_model_response(payload: dict[str, object], current_state: str) -> AIModelResponse:
    normalized = {str(key): value for key, value in dict(payload).items()}
    normalized["reply"] = _cleanup_text(str(normalized.get("reply", "")))
    normalized["intent"] = str(normalized.get("intent", "clarification")).strip() or "clarification"
    normalized["extracted_fields"] = _coerce_dict_str(normalized.get("extracted_fields"))
    normalized["normalized_fields"] = _coerce_dict_str(normalized.get("normalized_fields"))
    normalized["next_state"] = str(normalized.get("next_state", current_state)).strip() or current_state
    normalized["confidence"] = _coerce_confidence(normalized.get("confidence"))
    normalized["target_field"] = _cleanup_text(str(normalized["target_field"])) if normalized.get("target_field") is not None else None
    normalized["new_value_raw"] = _cleanup_text(str(normalized["new_value_raw"])) if normalized.get("new_value_raw") is not None else None
    normalized["reasoning_summary"] = _cleanup_text(str(normalized["reasoning_summary"])) if normalized.get("reasoning_summary") is not None else None
    normalized["suggested_next_action"] = _cleanup_text(str(normalized["suggested_next_action"])) if normalized.get("suggested_next_action") is not None else None
    return AIModelResponse.model_validate(normalized)


def _coerce_confidence(value: object) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, resolved))


def _coerce_dict_str(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, item in value.items():
        if key is None or item is None:
            continue
        resolved[str(key)] = _cleanup_text(str(item))
    return resolved


def _detect_correction_state(current_state: DialogueState, text: str) -> DialogueState | None:
    normalized = normalize_text_token(text)
    correction_markers = ("исправ", "измен", "ошибка", "неверно", "не правильно", "другое", "поменяй")
    if not any(marker in normalized for marker in correction_markers):
        return None

    field_mapping: list[tuple[tuple[str, ...], DialogueState]] = [
        (("фио", "полное имя"), DialogueState.ASK_FULL_NAME),
        (("телефон", "номер"), DialogueState.ASK_PHONE),
        (("город",), DialogueState.ASK_CITY),
        (("адрес",), DialogueState.ASK_ADDRESS),
        (("иин",), DialogueState.ASK_IIN),
        (("дата рождения", "рождение"), DialogueState.ASK_BIRTH_DATE),
        (("стаж", "опыт"), DialogueState.ASK_DRIVING_EXPERIENCE_SINCE),
        (("марка", "бренд"), DialogueState.ASK_CAR_BRAND),
        (("модель",), DialogueState.ASK_CAR_MODEL),
        (("год",), DialogueState.ASK_CAR_YEAR),
        (("госномер", "номер машины", "номер авто", "номер автомобиля"), DialogueState.ASK_CAR_PLATE),
        (("цвет",), DialogueState.ASK_CAR_COLOR),
        (("права", "ву", "номер прав", "водительское удостоверение"), DialogueState.ASK_DRIVER_LICENSE_NUMBER),
        (("дата выдачи", "выдано"), DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE),
        (("срок действия", "действует до"), DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT),
        (("условие работы", "самозанятый", "штатный"), DialogueState.ASK_EMPLOYMENT_TYPE),
        (("дата принятия", "принятия"), DialogueState.ASK_HIRED_AT),
        (("слабослышащий",), DialogueState.ASK_HEARING_IMPAIRED),
    ]
    for markers, state in field_mapping:
        if any(marker in normalized for marker in markers):
            return state

    if current_state == DialogueState.CONFIRM_DATA:
        return DialogueState.ASK_FULL_NAME
    return None


def _parse_confirm_field_edit(current_state: DialogueState, text: str) -> AIResult | None:
    if current_state != DialogueState.CONFIRM_DATA:
        return None
    normalized = normalize_text_token(text)
    if not any(marker in normalized for marker in ("исправ", "измени", "поменяй", "замени")):
        return None

    marker_match = re.match(r"^(исправь|исправить|измени|измени|поменяй|замени)\s+(.*)$", normalized)
    if not marker_match:
        return None
    tail = marker_match.group(2).strip()
    raw_tail = text.strip()[len(text.strip().split(maxsplit=1)[0]) :].strip()

    target_field = None
    field_phrase = ""
    raw_value = ""
    if " на " in tail:
        field_phrase, _, value_part = tail.partition(" на ")
        target_field = _resolve_field_name(field_phrase)
        raw_value = _extract_raw_value(raw_tail)
    else:
        target_field = _resolve_field_name(tail)

    if not target_field:
        return AIResult(
            "Напишите, какое именно поле исправить. Например: исправь город на Алматы.",
            "clarification",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.6,
            reasoning_summary="clarification:unknown_edit_field",
            suggested_next_action="confirm_data",
        )

    if not raw_value:
        return AIResult(
            f"Понял. Напишите новое значение для поля «{_human_field_label(target_field)}».",
            "field_edit",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.82,
            target_field=target_field,
            reasoning_summary=f"field_edit:{target_field}",
            validation_errors=["missing_new_value"],
            suggested_next_action="confirm_data",
        )

    normalized_fields, errors = _normalize_field_edit(target_field, raw_value)
    if errors:
        return AIResult(
            _field_edit_error_reply(target_field),
            "field_edit",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.82,
            target_field=target_field,
            new_value_raw=raw_value,
            reasoning_summary=f"field_edit:{target_field}",
            validation_errors=errors,
            suggested_next_action="confirm_data",
        )

    return AIResult(
        "Хорошо, сразу обновляю это поле.",
        "field_edit",
        normalized_fields.copy(),
        DialogueState.CONFIRM_DATA.value,
        0.93,
        target_field=target_field,
        new_value_raw=raw_value,
        normalized_fields=normalized_fields,
        reasoning_summary=f"field_edit:{target_field}",
        suggested_next_action="confirm_data",
    )


def _extract_raw_value(raw_tail: str) -> str:
    separators = (" на ", " : ", ": ")
    lowered = raw_tail.lower()
    for separator in separators:
        index = lowered.find(separator)
        if index != -1:
            return raw_tail[index + len(separator) :].strip().strip("\"' ")
    return ""


def _resolve_field_name(value: str) -> str | None:
    normalized = normalize_text_token(value)
    mapping: list[tuple[tuple[str, ...], str]] = [
        (("фио", "полное имя"), "full_name"),
        (("фамилия",), "last_name"),
        (("имя",), "first_name"),
        (("отчество",), "middle_name"),
        (("телефон", "контактный номер", "номер телефона"), "phone"),
        (("город",), "city"),
        (("адрес",), "address"),
        (("иин",), "iin"),
        (("дата рождения", "рождение"), "birth_date"),
        (("стаж", "водительский стаж", "опыт"), "driving_experience_since"),
        (("номер прав", "права", "ву", "водительское удостоверение"), "driver_license_number"),
        (("дата выдачи", "выдано"), "driver_license_issue_date"),
        (("срок действия", "действует до"), "driver_license_expires_at"),
        (("условие работы", "тип занятости"), "employment_type"),
        (("дата принятия",), "hired_at"),
        (("слабослышащий",), "is_hearing_impaired"),
        (("марка", "бренд"), "brand"),
        (("модель",), "model"),
        (("год", "год выпуска"), "year"),
        (("госномер", "номер машины", "номер авто", "номер автомобиля"), "plate_number"),
        (("цвет",), "color"),
        (("vin", "вин"), "vin"),
        (("класс", "класс авто", "тариф"), "service_class"),
    ]
    for markers, field_name in mapping:
        if any(marker in normalized for marker in markers):
            return field_name
    return None



def _parse_confirm_field_edit(current_state: DialogueState, text: str) -> AIResult | None:
    if current_state != DialogueState.CONFIRM_DATA:
        return None

    normalized = normalize_text_token(text)
    if not any(marker in normalized for marker in ("исправ", "измен", "поменя", "замен")):
        return None

    normalized_compact = normalized.strip()
    raw_text = text.strip()
    tail = normalized_compact
    raw_tail = raw_text

    prefix_match = re.match(
        r"^(?:исправь|исправить|измени|изменить|поменяй|поменять|замени|заменить)\s+(.*)$",
        normalized_compact,
    )
    if prefix_match:
        tail = prefix_match.group(1).strip()
        raw_tail = raw_text.split(maxsplit=1)[1].strip() if len(raw_text.split(maxsplit=1)) > 1 else ""
    else:
        suffix_match = re.match(
            r"^(.*?)\s+(?:поменять|изменить|исправить|заменить)$",
            normalized_compact,
        )
        if suffix_match:
            tail = suffix_match.group(1).strip()
            raw_tail = raw_text.rsplit(" ", 1)[0].strip() if " " in raw_text else raw_text

    target_field = None
    raw_value = ""
    if " на " in tail:
        field_phrase, _, _ = tail.partition(" на ")
        target_field = _resolve_field_name(field_phrase)
        raw_value = _extract_raw_value(raw_tail)
    else:
        target_field = _resolve_field_name(tail)

    if not target_field:
        return AIResult(
            "Напишите, какое именно поле исправить. Например: исправь город на Алматы.",
            "clarification",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.6,
            reasoning_summary="clarification:unknown_edit_field",
            suggested_next_action="confirm_data",
        )

    if not raw_value:
        return AIResult(
            f"Хорошо. Отправьте новое значение для поля «{_human_field_label(target_field)}» одним сообщением.",
            "field_edit",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.82,
            target_field=target_field,
            reasoning_summary=f"field_edit:{target_field}",
            validation_errors=["missing_new_value"],
            suggested_next_action="confirm_data",
        )

    normalized_fields, errors = _normalize_field_edit(target_field, raw_value)
    if errors:
        return AIResult(
            _field_edit_error_reply(target_field),
            "field_edit",
            {},
            DialogueState.CONFIRM_DATA.value,
            0.82,
            target_field=target_field,
            new_value_raw=raw_value,
            reasoning_summary=f"field_edit:{target_field}",
            validation_errors=errors,
            suggested_next_action="confirm_data",
        )

    return AIResult(
        "Хорошо, сразу обновляю это поле.",
        "field_edit",
        normalized_fields,
        DialogueState.CONFIRM_DATA.value,
        0.9,
        target_field=target_field,
        new_value_raw=raw_value,
        normalized_fields=normalized_fields,
        reasoning_summary=f"field_edit:{target_field}",
        suggested_next_action="confirm_data",
    )


def _extract_raw_value(raw_tail: str) -> str:
    lowered = raw_tail.lower()
    for separator in (" на ", " : ", ": "):
        index = lowered.find(separator)
        if index != -1:
            return raw_tail[index + len(separator):].strip().strip("\"' ")
    return ""


def _resolve_field_name(value: str) -> str | None:
    normalized = normalize_text_token(value)
    mapping: list[tuple[tuple[str, ...], str]] = [
        (("фио", "полное имя"), "full_name"),
        (("фамилия",), "last_name"),
        (("имя",), "first_name"),
        (("отчество",), "middle_name"),
        (("телефон", "контактный номер", "номер телефона"), "phone"),
        (("город",), "city"),
        (("адрес",), "address"),
        (("иин",), "iin"),
        (("дата рождения", "рождение"), "birth_date"),
        (("стаж", "водительский стаж", "опыт"), "driving_experience_since"),
        (("номер прав", "права", "ву", "водительское удостоверение"), "driver_license_number"),
        (("дата выдачи", "выдано"), "driver_license_issue_date"),
        (("срок действия", "действует до"), "driver_license_expires_at"),
        (("условие работы", "тип занятости"), "employment_type"),
        (("дата принятия",), "hired_at"),
        (("слабослышащий",), "is_hearing_impaired"),
        (("марка", "бренд"), "brand"),
        (("модель",), "model"),
        (("год", "год выпуска"), "year"),
        (("госномер", "номер машины", "номер авто", "номер автомобиля"), "plate_number"),
        (("цвет",), "color"),
        (("vin", "вин"), "vin"),
        (("класс", "класс авто", "тариф"), "service_class"),
    ]
    for markers, field_name in mapping:
        if any(marker in normalized for marker in markers):
            return field_name
    if any(marker in normalized for marker in ("авто", "машин", "автомобил")):
        return "vehicle_descriptor"
    return None


def _normalize_field_edit(target_field: str, raw_value: str) -> tuple[dict[str, str], list[str]]:
    value = raw_value.strip().strip("\"'")
    if not value:
        return {}, ["empty_value"]

    if target_field == "full_name":
        if not _looks_like_full_name(value):
            return {}, ["invalid_full_name"]
        last_name, first_name, middle_name = split_full_name(value)
        payload = {"full_name": value}
        if last_name:
            payload["last_name"] = last_name
        if first_name:
            payload["first_name"] = first_name
        if middle_name:
            payload["middle_name"] = middle_name
        return payload, []
    if target_field in {"last_name", "first_name", "middle_name", "city", "address", "color", "vin"}:
        return {target_field: value}, []
    if target_field == "brand":
        return {"brand": normalize_car_brand(value)}, []
    if target_field == "model":
        normalized_model = normalize_car_model(value)
        if not looks_like_precise_car_model(normalized_model):
            return {}, ["invalid_model"]
        return {"model": normalized_model}, []
    if target_field == "vehicle_descriptor":
        brand = extract_known_car_brand(value)
        model = normalize_car_model(value)
        if not brand or not looks_like_precise_car_model(model):
            return {}, ["invalid_vehicle_descriptor"]
        return {"brand": brand, "model": model}, []
    if target_field == "phone":
        if not looks_like_phone(value):
            return {}, ["invalid_phone"]
        return {"phone": normalize_phone(value)}, []
    if target_field == "iin":
        digits = re.sub(r"\D+", "", value)
        errors = validate_kz_iin(digits)
        if errors:
            return {}, errors
        return {"iin": digits}, []
    if target_field in {"birth_date", "driving_experience_since", "driver_license_issue_date", "driver_license_expires_at", "hired_at"}:
        parsed = parse_date(value)
        if not parsed:
            return {}, ["invalid_date"]
        if target_field == "birth_date":
            errors = validate_birth_date(parsed)
            if errors:
                return {}, errors
        if target_field == "hired_at":
            errors = validate_hired_at(parsed)
            if errors:
                return {}, errors
        return {target_field: parsed}, []
    if target_field == "year":
        year = parse_year(value)
        if not year:
            return {}, ["invalid_year"]
        return {"year": str(year)}, []
    if target_field == "plate_number":
        if not _looks_like_plate_answer(value):
            return {}, ["invalid_plate"]
        return {"plate_number": normalize_plate_number(value)}, []
    if target_field == "driver_license_number":
        if not _looks_like_license_number(value):
            return {}, ["invalid_license_number"]
        return {"driver_license_number": value.replace(" ", "")}, []
    if target_field == "employment_type":
        normalized_employment = normalize_employment_type(value)
        if normalized_employment.lower() not in {"штатный", "самозанятый", "ип"} and normalized_employment == value:
            return {}, ["invalid_employment_type"]
        return {"employment_type": normalized_employment}, []
    if target_field == "is_hearing_impaired":
        parsed = parse_yes_no(value)
        if parsed is None:
            return {}, ["invalid_yes_no"]
        return {"is_hearing_impaired": str(parsed).lower()}, []
    if target_field == "service_class":
        return {"service_class": normalize_text_token(value)}, []
    return {}, ["unsupported_field"]


def _field_edit_error_reply(target_field: str) -> str:
    examples = {
        "phone": "Например: исправь телефон на +77071234567.",
        "city": "Например: измени город на Алматы.",
        "address": "Например: исправь адрес на Балкантау 117.",
        "iin": "Например: исправь ИИН на 070404550345.",
        "birth_date": "Например: исправь дату рождения на 04.04.2007.",
        "driver_license_issue_date": "Например: измени дату выдачи на 17.03.2015.",
        "driver_license_expires_at": "Например: измени срок действия на 17.03.2030.",
        "plate_number": "Например: исправь госномер на 004YAT03.",
        "model": "Например: измени модель авто на Camry.",
        "vehicle_descriptor": "Например: исправь авто на Mercedes-Benz S-Class.",
    }
    return f"Не удалось обновить поле «{_human_field_label(target_field)}». Проверьте формат. {examples.get(target_field, '')}".strip()


def _human_field_label(target_field: str) -> str:
    return {
        "full_name": "ФИО",
        "last_name": "фамилия",
        "first_name": "имя",
        "middle_name": "отчество",
        "phone": "телефон",
        "city": "город",
        "address": "адрес",
        "iin": "ИИН",
        "birth_date": "дата рождения",
        "driving_experience_since": "водительский стаж",
        "driver_license_number": "номер ВУ",
        "driver_license_issue_date": "дата выдачи ВУ",
        "driver_license_expires_at": "срок действия ВУ",
        "employment_type": "условие работы",
        "hired_at": "дата принятия",
        "is_hearing_impaired": "слабослышащий водитель",
        "brand": "марка авто",
        "model": "модель авто",
        "vehicle_descriptor": "авто",
        "year": "год выпуска",
        "plate_number": "госномер",
        "color": "цвет авто",
        "vin": "VIN",
        "service_class": "класс авто",
    }.get(target_field, target_field)


def _normalize_fields_map(fields: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        cleaned = _cleanup_text(str(value))
        if key == "phone":
            cleaned = normalize_phone(cleaned)
        elif key in {"iin"}:
            cleaned = re.sub(r"\D+", "", cleaned)
        elif key in {"birth_date", "driving_experience_since", "driver_license_issue_date", "driver_license_expires_at", "hired_at"}:
            cleaned = parse_date(cleaned) or cleaned
        elif key == "year":
            parsed_year = parse_year(cleaned)
            cleaned = str(parsed_year) if parsed_year else cleaned
        elif key == "plate_number":
            cleaned = normalize_plate_number(cleaned)
        elif key == "brand":
            cleaned = normalize_car_brand(cleaned)
        elif key == "model":
            cleaned = normalize_car_model(cleaned)
        elif key == "employment_type":
            cleaned = normalize_employment_type(cleaned)
        elif key == "is_hearing_impaired":
            parsed = parse_yes_no(cleaned)
            if parsed is not None:
                cleaned = str(parsed).lower()
        normalized[key] = cleaned
    return normalized


def _cleanup_text(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def _trace_payload(result: AIResult) -> dict[str, object]:
    return {
        "intent": result.intent,
        "next_state": result.next_state,
        "confidence": result.confidence,
        "reply": result.reply,
        "target_field": result.target_field,
        "new_value_raw": result.new_value_raw,
        "extracted_fields": result.extracted_fields,
        "normalized_fields": result.normalized_fields,
        "reasoning_summary": result.reasoning_summary,
        "suggested_next_action": result.suggested_next_action,
    }


@lru_cache
def get_ai_service() -> AIService:
    return AIService()
