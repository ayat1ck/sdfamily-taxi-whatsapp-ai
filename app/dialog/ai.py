from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
import re

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dialog.faq import (
    build_office_invite_reply,
    load_knowledge_base,
    PAYOUT_WAITING_REPLY,
    looks_like_greeting,
    looks_like_support_question,
    resolve_faq_replies,
    split_field_and_support,
)
from app.dialog.llm_prompt import (
    build_faq_assist_system_prompt,
    build_faq_assist_user_prompt,
    build_system_prompt,
    build_user_prompt,
)
from app.dialog.prompts import (
    CAR_MODEL_PROMPT,
    PROMPTS,
)
from app.documents.registration_flow import next_registration_state, next_text_state_after
from app.dialog.states import DialogueState
from app.drivers.models import Driver
from app.integrations.yandex.catalog import (
    catalog_validation_error_message,
    resolve_brand_input,
    resolve_brand_model_input,
    resolve_model_input,
)
from app.utils.logger import get_logger
from app.utils.validators import (
    build_car_model_clarification_message,
    detect_car_model_clarification,
    extract_known_car_brand,
    looks_like_iin,
    looks_like_phone,
    looks_like_precise_car_model,
    looks_like_registration_certificate,
    normalize_car_brand,
    normalize_car_model,
    normalize_driver_license_number,
    normalize_employment_type,
    normalize_phone,
    normalize_plate_number,
    normalize_registration_certificate,
    normalize_service_class,
    normalize_text_token,
    parse_confirmation,
    parse_date,
    parse_iso_date,
    parse_year,
    parse_yes_no,
    split_full_name,
    validate_birth_date,
    validate_driver_dates,
    validate_driver_license_number,
    validate_hired_at,
    validate_kz_iin,
)

try:
    from google import genai
except ImportError:
    genai = None

logger = get_logger(__name__)

CASUAL_SMALLTALK_REPLY = "Здравствуйте! Я на связи."
SHORT_SUPPORT_REPLY = "Понял. Уточните, что именно не получается — помогу по шагам."


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
    suggested_clarification_value: str | None = None
    clear_suggested_clarification: bool = False
    raw_decision: dict[str, object] = field(default_factory=dict)


class FAQAssistantResponse(BaseModel):
    reply: str = ""


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
        """Backend-first dialog: deterministic state machine + RAG FAQ; LLM only as FAQ assistant."""
        backend = self.deterministic.respond(state, message, driver)
        current_state = DialogueState(state)

        if backend.intent in {"registration", "confirmation", "correction", "field_edit"}:
            return backend
        if backend.intent in {"faq", "help"} and backend.reply.strip():
            return backend

        if self.settings.llm_mode == "full" and current_state == DialogueState.COMPLETED:
            return self._respond_with_full_llm(state, message, driver, backend)

        if self.llm and self.settings.llm_faq_assist_enabled and _should_use_llm_faq_assist(message, backend):
            try:
                return self._respond_with_faq_assist(state, message, driver)
            except Exception as exc:
                logger.exception("FAQ assistant failed for state %s: %s", state, exc)

        if backend.intent == "clarification" and backend.reply.strip():
            return backend

        if looks_like_support_question(message) and not _backend_answered_support(backend):
            if not looks_like_greeting(message):
                return _office_fallback_result(state, message)

        return backend

    def _respond_with_faq_assist(self, state: str, message: str, driver: Driver) -> AIResult:
        assist = self.llm.respond_faq_assist(state, message, driver, self.knowledge_base)
        if not assist.reply.strip():
            raise RuntimeError("FAQ assistant returned empty reply")
        assist.fallback_used = True
        assist.fallback_reason = "llm_faq_assist"
        assist.suggested_next_action = state
        assist.next_state = state
        return assist

    def _respond_with_full_llm(self, state: str, message: str, driver: Driver, backend: AIResult) -> AIResult:
        if self.llm is None:
            return backend
        try:
            current_state = DialogueState(state)
            llm_result = self.llm.respond(state, message, driver, self.knowledge_base)
            normalized = _normalize_llm_result(llm_result, current_state, backend, driver)
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
            backend.fallback_used = True
            backend.fallback_reason = "provider_exception"
            backend.reasoning_summary = "fallback:provider_exception"
            backend.validation_errors.append(str(exc))
            return backend

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

    def respond_faq_assist(self, state: str, message: str, driver: Driver, knowledge_base: dict[str, str]) -> AIResult:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": build_faq_assist_system_prompt()},
                {
                    "role": "user",
                    "content": build_faq_assist_user_prompt(
                        state=state,
                        message=message,
                        driver=driver,
                        knowledge_base=knowledge_base,
                    ),
                },
            ],
            text_format=FAQAssistantResponse,
        )
        parsed = response.output_parsed
        if parsed is None or not parsed.reply.strip():
            raise RuntimeError("OpenAI FAQ assistant returned no reply")
        return AIResult(
            _cleanup_text(parsed.reply),
            "faq",
            {},
            state,
            0.78,
            provider="openai",
            reasoning_summary="llm_faq_assist",
            suggested_next_action=state,
        )


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

    def respond_faq_assist(self, state: str, message: str, driver: Driver, knowledge_base: dict[str, str]) -> AIResult:
        response = self.client.models.generate_content(
            model=self.model,
            contents=build_faq_assist_user_prompt(
                state=state,
                message=message,
                driver=driver,
                knowledge_base=knowledge_base,
            ),
            config={
                "system_instruction": build_faq_assist_system_prompt(),
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        )
        raw_text = getattr(response, "text", "") or ""
        if not raw_text:
            raise RuntimeError("Gemini FAQ assistant returned no output")
        payload = json.loads(raw_text)
        parsed = FAQAssistantResponse.model_validate(payload)
        if not parsed.reply.strip():
            raise RuntimeError("Gemini FAQ assistant returned empty reply")
        return AIResult(
            _cleanup_text(parsed.reply),
            "faq",
            {},
            state,
            0.78,
            provider="gemini",
            reasoning_summary="llm_faq_assist",
            suggested_next_action=state,
        )


class DeterministicAIProvider:
    def __init__(self, knowledge_base: dict[str, str]) -> None:
        self.knowledge_base = knowledge_base

    def respond(self, state: str, message: str, driver: Driver) -> AIResult:
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

        mixed = _try_mixed_field_and_support(current_state, text, driver, self.knowledge_base)
        if mixed:
            return mixed

        greeting_with_support = _try_greeting_with_support(current_state, text, self.knowledge_base)
        if greeting_with_support:
            return greeting_with_support

        greeting_reply = _build_greeting_reply(current_state, text)
        if greeting_reply:
            return AIResult(
                greeting_reply,
                "help",
                {},
                state,
                0.9,
                reasoning_summary="greeting",
                suggested_next_action=(
                    DialogueState.ASK_FULL_NAME.value
                    if current_state == DialogueState.NEW
                    else state
                ),
            )

        if current_state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            field_edit = _parse_confirm_field_edit(current_state, text, driver)
            if field_edit:
                return field_edit

        if current_state == DialogueState.NEW and _looks_like_support_only_topic(text):
            normalized = normalize_text_token(text)
            if any(marker in normalized for marker in ("выплат", "вывод", "моментальн")):
                return AIResult(
                    PAYOUT_WAITING_REPLY,
                    "help",
                    {},
                    state,
                    0.62,
                    reasoning_summary="new_state:payout_wait",
                    suggested_next_action=state,
                )
            faq_reply = _match_faq(text, self.knowledge_base)
            if faq_reply and not any(
                cue in normalize_text_token(faq_reply)
                for cue in ("фио", "контактн", "документ", "офис", "приглас", "подключ", "регистрац")
            ):
                return AIResult(
                    faq_reply,
                    "faq",
                    {},
                    state,
                    0.82,
                    reasoning_summary="new_state:support_faq",
                    suggested_next_action=state,
                )
            return AIResult(
                SHORT_SUPPORT_REPLY,
                "help",
                {},
                state,
                0.62,
                reasoning_summary="new_state:support_short",
                suggested_next_action=state,
            )

        if current_state == DialogueState.NEW and _looks_like_onboarding_intent(text):
            normalized = normalize_text_token(text)
            onboarding_reply = PROMPTS[DialogueState.NEW]
            if normalize_employment_type(normalized) == "самозанятый" or any(
                token in normalized for token in ("самозанят", "смз", "штатн", "ип")
            ):
                onboarding_reply = "Да, самозанятый подходит. Напишите ФИО полностью."
            return AIResult(
                onboarding_reply,
                "clarification",
                {},
                DialogueState.ASK_FULL_NAME.value,
                0.75,
                reasoning_summary="onboarding_intent:new",
                suggested_next_action=DialogueState.ASK_FULL_NAME.value,
            )

        if current_state != DialogueState.NEW and looks_like_support_question(text):
            support_reply = _resolve_support_during_registration(current_state, text, self.knowledge_base)
            if support_reply:
                return AIResult(
                    support_reply,
                    "help",
                    {},
                    state,
                    0.86,
                    reasoning_summary=f"support_before_registration:{current_state.value}",
                    suggested_next_action=state,
                )

        if _is_in_flow_registration_state(current_state):
            step_help = _step_help_result(current_state, text, state)
            if step_help:
                return step_help
            field_extract = _try_registration_field_extract(current_state, text, driver, state)
            if field_extract is not None:
                return field_extract

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

        step_help = _step_help_result(current_state, text, state)
        if step_help:
            return step_help

        side_reply = _registration_side_reply(current_state, text, self.knowledge_base)
        if side_reply:
            return AIResult(
                side_reply,
                "help",
                {},
                state,
                0.84,
                reasoning_summary=f"registration_side:{current_state.value}",
                suggested_next_action=state,
            )

        field_edit = _parse_confirm_field_edit(current_state, text, driver)
        if field_edit:
            return field_edit

        if current_state == DialogueState.NEW:
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
                    "рџ‘‹ РћС‚Р»РёС‡РЅРѕ! РќР°С‡РёРЅР°РµРј СЂРµРіРёСЃС‚СЂР°С†РёСЋ.",
                    "registration",
                    extracted,
                    DialogueState.ASK_PHONE.value,
                    0.95,
                    reasoning_summary="registration_extract:full_name",
                    suggested_next_action=DialogueState.ASK_PHONE.value,
                )
            return AIResult(
                CASUAL_SMALLTALK_REPLY,
                "smalltalk",
                {},
                DialogueState.NEW.value,
                0.55,
                reasoning_summary="new_state:smalltalk",
                suggested_next_action=DialogueState.NEW.value,
            )

        correction_state = _detect_correction_state(current_state, text)
        if correction_state is not None:
            return AIResult(
                f"РҐРѕСЂРѕС€Рѕ, РёСЃРїСЂР°РІРёРј СЌС‚РѕС‚ РїСѓРЅРєС‚. {PROMPTS[correction_state]}",
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

        field_extract = _try_registration_field_extract(current_state, text, driver, state)
        if field_extract is not None:
            return field_extract

        return AIResult(
            _unsupported_message_reply(current_state, text),
            "help" if looks_like_support_question(text) else "clarification",
            {},
            state,
            0.55 if looks_like_support_question(text) else 0.4,
            reasoning_summary=(
                "office_fallback:unrecognized_support_question"
                if looks_like_support_question(text)
                else "clarification:unrecognized_message"
            ),
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


def _normalize_llm_result(result: AIResult, current_state: DialogueState, fallback: AIResult, driver: Driver) -> AIResult:
    normalized = AIResult(**asdict(result))
    normalized.reply = _cleanup_text(normalized.reply)
    normalized.new_value_raw = _cleanup_text(normalized.new_value_raw)
    normalized.reasoning_summary = normalized.reasoning_summary or f"llm:{normalized.intent}"
    normalized.suggested_next_action = normalized.suggested_next_action or normalized.next_state or current_state.value

    if normalized.intent not in {
        "registration",
        "confirmation",
        "correction",
        "faq",
        "help",
        "smalltalk",
        "clarification",
        "field_edit",
        "existing_driver_support",
        "human_operator",
        "payout_support",
        "tariff_support",
        "yandex_problem",
        "blocking_support",
        "rental_car_question",
        "courier_registration",
    }:
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

    if normalized.intent == "registration":
        if current_state == DialogueState.ASK_CAR_MODEL and "model" in normalized.extracted_fields:
            model_result = _process_car_model_answer(normalized.extracted_fields["model"], driver)
            if model_result is not None and model_result.intent != "registration":
                return model_result
            if model_result is not None and model_result.intent == "registration":
                normalized = model_result
                normalized.provider = result.provider or normalized.provider
                normalized.fallback_used = True
                normalized.fallback_reason = "car_model_normalized"
        if "driver_license_number" in normalized.extracted_fields:
            normalized_license = normalize_driver_license_number(normalized.extracted_fields["driver_license_number"])
            license_errors = validate_driver_license_number(normalized_license)
            if license_errors:
                return _fallback_from(fallback, result, "driver_license_validation_failed", license_errors)
            normalized.extracted_fields["driver_license_number"] = normalized_license
            normalized.normalized_fields["driver_license_number"] = normalized_license
        date_validation = _validate_registration_fields_for_state(
            current_state,
            normalized.extracted_fields,
            driver,
        )
        if date_validation:
            return AIResult(
                _validation_error_reply(current_state, date_validation),
                "clarification",
                {},
                current_state.value,
                0.74,
                reasoning_summary=f"validation:{current_state.value}",
                validation_errors=date_validation,
                suggested_next_action=current_state.value,
                fallback_used=True,
                fallback_reason="registration_date_validation_failed",
                provider=result.provider or normalized.provider,
            )

    if normalized.intent == "field_edit":
        if current_state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return _fallback_from(fallback, result, "field_edit_outside_confirm")
        if not normalized.target_field:
            return _fallback_from(fallback, result, "field_edit_missing_target")
        normalized_edit, errors = _normalize_field_edit(
            normalized.target_field,
            normalized.new_value_raw or "",
            driver=driver,
        )
        if errors:
            return _fallback_from(fallback, result, "field_edit_invalid_value", errors)
        normalized.normalized_fields = normalized_edit
        normalized.extracted_fields = normalized_edit
        normalized.reply = normalized.reply or "РҐРѕСЂРѕС€Рѕ, РґР°РЅРЅС‹Рµ РѕР±РЅРѕРІРёР». РџСЂРѕРІРµСЂСЊС‚Рµ СЃРІРѕРґРєСѓ РµС‰Рµ СЂР°Р·."
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
            DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE.value,
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
            DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE.value,
            DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
            DialogueState.ASK_EMPLOYMENT_TYPE.value,
            DialogueState.ASK_HIRED_AT.value,
            DialogueState.ASK_HEARING_IMPAIRED.value,
        ]
    return [current_state.value, _default_next_state(current_state).value]


def _default_next_state(state: DialogueState, driver: Driver | None = None) -> DialogueState:
    if driver is not None:
        return next_registration_state(driver, driver.vehicle)
    order = [
        DialogueState.ASK_FULL_NAME,
        DialogueState.ASK_PHONE,
        DialogueState.ASK_CITY,
        DialogueState.ASK_DRIVER_LICENSE_FRONT,
        DialogueState.ASK_DRIVER_LICENSE_BACK,
        DialogueState.ASK_ID_CARD,
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC,
        DialogueState.ASK_SELFIE_WITH_LICENSE,
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
        DialogueState.CONFIRM_DATA,
    ]
    index = order.index(state)
    return order[min(index + 1, len(order) - 1)]


def _looks_like_full_name(value: str) -> bool:
    if looks_like_support_question(value):
        return False
    parts = [part for part in normalize_text_token(value).split() if part]
    if len(parts) < 2:
        return False
    if any(part.isdigit() for part in parts):
        return False
    question_words = {"РіРґРµ", "РєР°Рє", "С‡С‚Рѕ", "РєРѕРіРґР°", "Р·Р°С‡РµРј", "РїРѕС‡РµРјСѓ", "СЃРєРѕР»СЊРєРѕ", "РјРѕР¶РЅРѕ", "РЅСѓР¶РЅРѕ"}
    if any(part in question_words for part in parts):
        return False
    return len(parts[0]) >= 2 and len(parts[1]) >= 2


def _looks_like_support_only_topic(value: str) -> bool:
    normalized = normalize_text_token(value)
    if not normalized or _looks_like_onboarding_intent(value):
        return False
    support_markers = (
        "тариф",
        "услов",
        "комис",
        "выплат",
        "вывод",
        "моментальн",
        "байге",
        "бонус",
        "преми",
        "сухой туман",
        "поддерж",
        "офис",
        "адрес",
        "яндекс про",
        "войти",
        "линия",
        "онлайн",
        "смс",
        "код",
        "аккаунт",
        "активен",
        "неактив",
        "грузов",
        "доставка",
        "экспресс",
        "межгород",
    )
    if any(marker in normalized for marker in support_markers):
        return True
    return looks_like_support_question(value)


def _looks_like_onboarding_intent(value: str) -> bool:
    normalized = normalize_text_token(value)
    employment_hint = normalize_employment_type(normalized)
    work_hints = ("самозанят", "смз", "штатн", "ип", "тіркел", "керек", "қажет")
    onboarding_keywords = ("зарег", "подключ", "устро", "оформ", "работ", "парк", "услов", "тіркел", "жұмыс", "қажет", "керек")

    if any(token in normalized for token in work_hints) or employment_hint == "самозанятый":
        if any(keyword in normalized for keyword in onboarding_keywords):
            return True
        if "можно" in normalized and any(keyword in normalized for keyword in ("зарег", "подключ", "тіркел")):
            return True

    if looks_like_support_question(value) and not any(token in normalized for token in ("самозанят", "смз", "штатн", "ип", "тіркел", "керек", "қажет")) and any(
        keyword in normalized
        for keyword in (
            "сколько",
            "где",
            "какая",
            "какие",
            "зачем",
            "почему",
            "можно",
            "есть ли",
            "какой",
            "как ",
            "что ",
            "когда",
            "қалай",
            "қайда",
            "қашан",
            "қандай",
            "неше",
            "керек",
            "қажет",
            "тіркел",
        )
    ):
        return False
    triggers = (
        "привет",
        "здравствуйте",
        "добрый день",
        "салам",
        "ассаламуалейкум",
        "сәлем",
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
        "тіркелу",
        "жұмыс керек",
        "жұмыс қажет",
    )
    return any(trigger in normalized for trigger in triggers)

def _match_faq(message: str, knowledge_base: dict[str, str]) -> str | None:
    return resolve_faq_replies(message, knowledge_base, office_address=get_settings().public_site_address)


_MIXED_INELIGIBLE_STATES = {
    DialogueState.NEW,
    DialogueState.CONFIRM_DATA,
    DialogueState.READY_TO_SEND_YANDEX,
    DialogueState.SENDING_TO_YANDEX,
    DialogueState.YANDEX_ERROR,
    DialogueState.COMPLETED,
    DialogueState.DUPLICATE_REJECTED,
}

_IN_FLOW_REGISTRATION_STATES = frozenset(
    state
    for state in DialogueState
    if state not in _MIXED_INELIGIBLE_STATES
    and state
    not in {
        DialogueState.ASK_YANDEX_PRO_LOGIN,
        DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS,
        DialogueState.SENT_TO_YANDEX,
    }
    and (state.value.startswith("ask_") or state == DialogueState.ASK_EXECUTOR_TYPE)
)


def _is_in_flow_registration_state(state: DialogueState) -> bool:
    return state in _IN_FLOW_REGISTRATION_STATES


def _step_help_result(current_state: DialogueState, text: str, state_value: str) -> AIResult | None:
    step_help_reply = _build_step_help_reply(current_state, text)
    if not step_help_reply:
        return None
    return AIResult(
        step_help_reply,
        "help",
        {},
        state_value,
        0.88,
        reasoning_summary=f"step_help:{current_state.value}",
        suggested_next_action=state_value,
    )


def _resolve_support_during_registration(
    current_state: DialogueState,
    text: str,
    knowledge_base: dict[str, str],
) -> str | None:
    step_help = _build_step_help_reply(current_state, text)
    if step_help:
        return step_help

    faq_reply = resolve_faq_replies(text, knowledge_base, office_address=get_settings().public_site_address)
    if faq_reply:
        return faq_reply

    if looks_like_support_question(text):
        return SHORT_SUPPORT_REPLY
    return None


def _try_registration_field_extract(
    current_state: DialogueState,
    text: str,
    driver: Driver,
    state_value: str,
    *,
    allow_clarification: bool = True,
) -> AIResult | None:
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
        if looks_like_support_question(text):
            return None
        if allow_clarification:
            return AIResult(
                _clarification_reply(current_state),
                "clarification",
                {},
                state_value,
                0.45,
                reasoning_summary="clarification:full_name",
                suggested_next_action=state_value,
            )
        return None

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
                "РРРќ РІС‹РіР»СЏРґРёС‚ РЅРµРєРѕСЂСЂРµРєС‚РЅС‹Рј. РџСЂРѕРІРµСЂСЊС‚Рµ 12 С†РёС„СЂ Рё РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ, Р·Р°С€РёС‚СѓСЋ РІ РРРќ, Р·Р°С‚РµРј РѕС‚РїСЂР°РІСЊС‚Рµ РРРќ РµС‰Рµ СЂР°Р·.",
                "clarification",
                {},
                state_value,
                0.7,
                reasoning_summary="validation:iin_impossible",
                validation_errors=iin_errors,
                suggested_next_action=state_value,
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
                if not validation_errors and getattr(driver, "birth_date", None) == parsed_date:
                    validation_errors.append("driving_experience_same_as_birth")
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
                if (
                    not validation_errors
                    and getattr(driver, "driver_license_expires_at", None) == parsed_date
                ):
                    validation_errors.append("hired_at_same_as_license_expiry")

            if validation_errors:
                return AIResult(
                    _validation_error_reply(current_state, validation_errors),
                    "clarification",
                    {},
                    state_value,
                    0.72,
                    reasoning_summary=f"validation:{field_name}",
                    validation_errors=validation_errors,
                    suggested_next_action=state_value,
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

    if current_state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR} and parse_confirmation(text):
        return AIResult(
            "",
            "confirmation",
            {},
            DialogueState.READY_TO_SEND_YANDEX.value,
            0.99,
            reasoning_summary=f"confirmation:{current_state.value}",
            suggested_next_action=DialogueState.READY_TO_SEND_YANDEX.value,
        )

    if current_state == DialogueState.ASK_CAR_BRAND and _looks_like_short_entity_answer(text):
        brand, errors = resolve_brand_input(text)
        if brand:
            return AIResult(
                "",
                "registration",
                {"brand": brand},
                DialogueState.ASK_CAR_MODEL.value,
                0.9,
                normalized_fields={"brand": brand},
                reasoning_summary="registration_extract:brand",
                suggested_next_action=DialogueState.ASK_CAR_MODEL.value,
            )
        if errors:
            return AIResult(
                catalog_validation_error_message(errors),
                "clarification",
                {},
                state_value,
                0.72,
                reasoning_summary="validation:car_brand_catalog",
                validation_errors=errors,
                suggested_next_action=state_value,
            )

    if current_state == DialogueState.ASK_CAR_MODEL:
        car_model_result = _process_car_model_answer(text, driver)
        if car_model_result is not None:
            return car_model_result

    if current_state == DialogueState.ASK_DRIVER_LICENSE_NUMBER:
        license_result = _process_driver_license_answer(text)
        if license_result is not None:
            return license_result

    extracted = _extract_safe_field_answer(current_state, text, driver)
    if extracted:
        next_state = next_text_state_after(current_state).value
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

    return None


def _try_mixed_field_and_support(
    current_state: DialogueState,
    text: str,
    driver: Driver,
    knowledge_base: dict[str, str],
) -> AIResult | None:
    if current_state in _MIXED_INELIGIBLE_STATES:
        return None

    field_part, support_parts = split_field_and_support(text)
    if not field_part or not support_parts:
        return None

    field_extract = _try_registration_field_extract(
        current_state,
        field_part,
        driver,
        current_state.value,
        allow_clarification=True,
    )
    if field_extract is None:
        return None

    support_text = " ".join(support_parts)
    support_reply = _resolve_support_during_registration(current_state, support_text, knowledge_base)
    if not support_reply:
        return None

    if field_extract.intent == "clarification":
        return AIResult(
            f"{support_reply}\n\n{field_extract.reply}",
            "clarification",
            {},
            current_state.value,
            0.82,
            reasoning_summary="mixed:field_validation_and_support",
            validation_errors=field_extract.validation_errors,
            suggested_next_action=current_state.value,
        )

    if field_extract.intent != "registration" or not field_extract.extracted_fields:
        return None

    return AIResult(
        support_reply,
        "registration",
        field_extract.extracted_fields,
        field_extract.next_state,
        0.92,
        normalized_fields=field_extract.normalized_fields or field_extract.extracted_fields,
        reasoning_summary="mixed:field_and_support",
        suggested_next_action=field_extract.next_state,
    )


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
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: {"registration_certificate"},
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: {"driver_license_number"},
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: {"driver_license_issue_date"},
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: {"driver_license_expires_at"},
        DialogueState.ASK_EMPLOYMENT_TYPE: {"employment_type"},
        DialogueState.ASK_HIRED_AT: {"hired_at"},
        DialogueState.ASK_HEARING_IMPAIRED: {"is_hearing_impaired"},
    }
    return mapping.get(state, set())


def _extract_safe_field_answer(current_state: DialogueState, text: str, driver: Driver | None = None) -> dict[str, str]:
    if _looks_like_non_field_message(text):
        return {}

    if current_state == DialogueState.ASK_CITY and _looks_like_city_answer(text):
        return {"city": text.strip()}
    if current_state == DialogueState.ASK_ADDRESS and _looks_like_address_answer(text):
        return {"address": text.strip()}
    if current_state == DialogueState.ASK_CAR_BRAND and _looks_like_short_entity_answer(text):
        brand, errors = resolve_brand_input(text)
        if brand:
            return {"brand": brand}
        if errors:
            return {}
        return {"brand": normalize_car_brand(text)}
    if current_state == DialogueState.ASK_CAR_PLATE and _looks_like_plate_answer(text):
        return {"plate_number": normalize_plate_number(text)}
    if current_state == DialogueState.ASK_CAR_COLOR and _looks_like_short_entity_answer(text):
        return {"color": text.strip()}
    if current_state == DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE and looks_like_registration_certificate(text):
        return {"registration_certificate": normalize_registration_certificate(text)}
    if current_state == DialogueState.ASK_DRIVER_LICENSE_NUMBER and _looks_like_license_number(text):
        return {"driver_license_number": text.strip()}
    if current_state == DialogueState.ASK_EMPLOYMENT_TYPE:
        normalized = normalize_employment_type(text)
        if normalized in {"С€С‚Р°С‚РЅС‹Р№", "СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№", "РёРї"} or normalized != text.strip():
            return {"employment_type": normalized}
    return {}


def _clarification_reply(current_state: DialogueState) -> str:
    clean_custom = {
        DialogueState.ASK_FULL_NAME: "РќР°РїРёС€РёС‚Рµ РІР°С€Рµ Р¤РРћ РїРѕР»РЅРѕСЃС‚СЊСЋ. РќР°РїСЂРёРјРµСЂ: РђР±Р°Р№ РђСЏС‚ Р–Р°РЅС‹Р±РµРєСѓР»С‹.",
        DialogueState.ASK_PHONE: "РЈРєР°Р¶РёС‚Рµ РєРѕРЅС‚Р°РєС‚РЅС‹Р№ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР° РІ С„РѕСЂРјР°С‚Рµ +7XXXXXXXXXX.",
        DialogueState.ASK_CITY: "РќР°РїРёС€РёС‚Рµ С‚РѕР»СЊРєРѕ РіРѕСЂРѕРґ, РІ РєРѕС‚РѕСЂРѕРј Р±СѓРґРµС‚Рµ СЂР°Р±РѕС‚Р°С‚СЊ. РќР°РїСЂРёРјРµСЂ: РђСЃС‚Р°РЅР°.",
        DialogueState.ASK_ADDRESS: "рџ“Ќ РЈРєР°Р¶РёС‚Рµ Р°РґСЂРµСЃ РїСЂРѕР¶РёРІР°РЅРёСЏ РёР»Рё СЂРµРіРёСЃС‚СЂР°С†РёРё. РќР°РїСЂРёРјРµСЂ: РїСЂ. Р РµСЃРїСѓР±Р»РёРєРё 12, РђСЃС‚Р°РЅР°.",
        DialogueState.ASK_IIN: "РЈРєР°Р¶РёС‚Рµ РРРќ РёР· 12 С†РёС„СЂ.",
        DialogueState.ASK_BIRTH_DATE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РЅР°С‡Р°Р»Р° РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СЃС‚Р°Р¶Р° РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_CAR_BRAND: "РќР°РїРёС€РёС‚Рµ С‚РѕС‡РЅСѓСЋ РјР°СЂРєСѓ Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: Toyota, Mercedes, Haval, Changan, Hyundai.",
        DialogueState.ASK_CAR_MODEL: CAR_MODEL_PROMPT,
        DialogueState.ASK_CAR_YEAR: "РЈРєР°Р¶РёС‚Рµ РіРѕРґ РІС‹РїСѓСЃРєР° Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: 2018.",
        DialogueState.ASK_CAR_PLATE: "РЈРєР°Р¶РёС‚Рµ РіРѕСЃРЅРѕРјРµСЂ Р°РІС‚РѕРјРѕР±РёР»СЏ Р±РµР· Р»РёС€РЅРёС… РїРѕСЏСЃРЅРµРЅРёР№.",
        DialogueState.ASK_CAR_COLOR: "РЈРєР°Р¶РёС‚Рµ С†РІРµС‚ Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: Р±РµР»С‹Р№.",
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "РЈРєР°Р¶РёС‚Рµ РЅРѕРјРµСЂ С‚РµС…РїР°СЃРїРѕСЂС‚Р° (РЎРўРЎ) Р°РІС‚РѕРјРѕР±РёР»СЏ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ. РќР°РїСЂРёРјРµСЂ: AA12345678.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: (
            "РќР°РїРёС€РёС‚Рµ СЃРµСЂРёСЋ Рё РЅРѕРјРµСЂ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ "
            "(РЅР°РїСЂРёРјРµСЂ CQ 981709). РЎРµСЂРёСЋ Рё РЅРѕРјРµСЂ РјРѕР¶РЅРѕ С‡РµСЂРµР· РїСЂРѕР±РµР»."
        ),
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РІС‹РґР°С‡Рё РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "РЈРєР°Р¶РёС‚Рµ СЃСЂРѕРє РґРµР№СЃС‚РІРёСЏ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ РґРѕ РґР°С‚С‹ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "РЈРєР°Р¶РёС‚Рµ СѓСЃР»РѕРІРёРµ СЂР°Р±РѕС‚С‹: С€С‚Р°С‚РЅС‹Р№, СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№ РёР»Рё РРџ.",
        DialogueState.ASK_HIRED_AT: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РїСЂРёРЅСЏС‚РёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_HEARING_IMPAIRED: "РћС‚РІРµС‚СЊС‚Рµ РєРѕСЂРѕС‚РєРѕ: РґР° РёР»Рё РЅРµС‚.",
        DialogueState.CONFIRM_DATA: "РќР°РїРёС€РёС‚Рµ, РєР°РєРѕРµ РїРѕР»Рµ РёСЃРїСЂР°РІРёС‚СЊ Рё РЅР° РєР°РєРѕРµ Р·РЅР°С‡РµРЅРёРµ. РќР°РїСЂРёРјРµСЂ: РёСЃРїСЂР°РІСЊ РіРѕСЂРѕРґ РЅР° РђР»РјР°С‚С‹.",
    }
    if current_state in clean_custom:
        return clean_custom[current_state]
    custom = {
        DialogueState.ASK_FULL_NAME: "РќР°РїРёС€РёС‚Рµ РІР°С€Рµ Р¤РРћ РїРѕР»РЅРѕСЃС‚СЊСЋ. РќР°РїСЂРёРјРµСЂ: РђР±Р°Р№ РђСЏС‚ Р–Р°РЅС‹Р±РµРєСѓР»С‹.",
        DialogueState.ASK_PHONE: "РЈРєР°Р¶РёС‚Рµ РєРѕРЅС‚Р°РєС‚РЅС‹Р№ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР° РІ С„РѕСЂРјР°С‚Рµ +7XXXXXXXXXX.",
        DialogueState.ASK_CITY: "РќР°РїРёС€РёС‚Рµ С‚РѕР»СЊРєРѕ РіРѕСЂРѕРґ, РІ РєРѕС‚РѕСЂРѕРј Р±СѓРґРµС‚Рµ СЂР°Р±РѕС‚Р°С‚СЊ. РќР°РїСЂРёРјРµСЂ: РђСЃС‚Р°РЅР°.",
        DialogueState.ASK_ADDRESS: "рџ“Ќ РЈРєР°Р¶РёС‚Рµ Р°РґСЂРµСЃ РїСЂРѕР¶РёРІР°РЅРёСЏ РёР»Рё СЂРµРіРёСЃС‚СЂР°С†РёРё. РќР°РїСЂРёРјРµСЂ: РїСЂ. Р РµСЃРїСѓР±Р»РёРєРё 12, РђСЃС‚Р°РЅР°.",
        DialogueState.ASK_IIN: "РЈРєР°Р¶РёС‚Рµ РРРќ РёР· 12 С†РёС„СЂ.",
        DialogueState.ASK_BIRTH_DATE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РЅР°С‡Р°Р»Р° РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СЃС‚Р°Р¶Р° РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_CAR_BRAND: "РќР°РїРёС€РёС‚Рµ РјР°СЂРєСѓ Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: Toyota.",
        DialogueState.ASK_CAR_MODEL: (
            "РќСѓР¶РЅРѕ РЅР°Р·РІР°РЅРёРµ РјРѕРґРµР»Рё РёР· РґРѕРєСѓРјРµРЅС‚РѕРІ, РєР°Рє Camry, Rio, S-Class РёР»Рё X5. "
            "РљРѕРґ РєСѓР·РѕРІР° (w221, e90) Р»СѓС‡С€Рµ РЅРµ РїРёСЃР°С‚СЊ вЂ” СѓРєР°Р¶РёС‚Рµ РјРѕРґРµР»СЊ С†РµР»РёРєРѕРј."
        ),
        DialogueState.ASK_CAR_YEAR: "РЈРєР°Р¶РёС‚Рµ РіРѕРґ РІС‹РїСѓСЃРєР° Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: 2018.",
        DialogueState.ASK_CAR_PLATE: "РЈРєР°Р¶РёС‚Рµ РіРѕСЃРЅРѕРјРµСЂ Р°РІС‚РѕРјРѕР±РёР»СЏ Р±РµР· Р»РёС€РЅРёС… РїРѕСЏСЃРЅРµРЅРёР№.",
        DialogueState.ASK_CAR_COLOR: "РЈРєР°Р¶РёС‚Рµ С†РІРµС‚ Р°РІС‚РѕРјРѕР±РёР»СЏ. РќР°РїСЂРёРјРµСЂ: Р±РµР»С‹Р№.",
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "РЈРєР°Р¶РёС‚Рµ РЅРѕРјРµСЂ С‚РµС…РїР°СЃРїРѕСЂС‚Р° (РЎРўРЎ) Р°РІС‚РѕРјРѕР±РёР»СЏ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ. РќР°РїСЂРёРјРµСЂ: AA12345678.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: (
            "РќР°РїРёС€РёС‚Рµ СЃРµСЂРёСЋ Рё РЅРѕРјРµСЂ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ "
            "(РЅР°РїСЂРёРјРµСЂ CQ 981709). РЎРµСЂРёСЋ Рё РЅРѕРјРµСЂ РјРѕР¶РЅРѕ С‡РµСЂРµР· РїСЂРѕР±РµР»."
        ),
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РІС‹РґР°С‡Рё РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "РЈРєР°Р¶РёС‚Рµ СЃСЂРѕРє РґРµР№СЃС‚РІРёСЏ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ РґРѕ РґР°С‚С‹ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "РЈРєР°Р¶РёС‚Рµ СѓСЃР»РѕРІРёРµ СЂР°Р±РѕС‚С‹: С€С‚Р°С‚РЅС‹Р№, СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№ РёР»Рё РРџ.",
        DialogueState.ASK_HIRED_AT: "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РїСЂРёРЅСЏС‚РёСЏ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_HEARING_IMPAIRED: "РћС‚РІРµС‚СЊС‚Рµ РєРѕСЂРѕС‚РєРѕ: РґР° РёР»Рё РЅРµС‚.",
        DialogueState.CONFIRM_DATA: "РќР°РїРёС€РёС‚Рµ, РєР°РєРѕРµ РїРѕР»Рµ РёСЃРїСЂР°РІРёС‚СЊ Рё РЅР° РєР°РєРѕРµ Р·РЅР°С‡РµРЅРёРµ. РќР°РїСЂРёРјРµСЂ: РёСЃРїСЂР°РІСЊ РіРѕСЂРѕРґ РЅР° РђР»РјР°С‚С‹.",
    }
    return custom.get(current_state, PROMPTS.get(current_state, "РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РѕС‚РІРµС‚СЊС‚Рµ РЅР° С‚РµРєСѓС‰РёР№ РІРѕРїСЂРѕСЃ."))


def _should_use_llm_faq_assist(message: str, backend: AIResult) -> bool:
    if not looks_like_support_question(message):
        return False
    if backend.intent in {"faq", "help"} and backend.reply.strip():
        return False
    if backend.intent in {"registration", "confirmation", "correction", "field_edit"}:
        return False
    return True


def _backend_answered_support(backend: AIResult) -> bool:
    if backend.intent not in {"faq", "help"}:
        return False
    reply = backend.reply.strip()
    if not reply:
        return False
    office_address = get_settings().public_site_address
    if reply == build_office_invite_reply(office_address):
        return True
    return not _reply_is_bare_step_prompt(backend.reply, backend.next_state or "")


def _reply_is_bare_step_prompt(reply: str, state_value: str) -> bool:
    if not state_value:
        return False
    try:
        state = DialogueState(state_value)
    except ValueError:
        return False
    prompt = PROMPTS.get(state, "").strip()
    cleaned = reply.strip()
    return bool(prompt) and cleaned == prompt


def _unsupported_message_reply(current_state: DialogueState, text: str) -> str:
    if looks_like_support_question(text):
        return SHORT_SUPPORT_REPLY
    if current_state in {DialogueState.NEW, DialogueState.COMPLETED}:
        return CASUAL_SMALLTALK_REPLY
    return _clarification_reply(current_state)


def _office_fallback_result(state: str, _message: str) -> AIResult:
    reply = SHORT_SUPPORT_REPLY
    return AIResult(
        reply,
        "help",
        {},
        state,
        0.72,
        reasoning_summary="office_fallback:no_kb_answer",
        suggested_next_action=state,
        provider="deterministic",
    )


def _try_greeting_with_support(
    current_state: DialogueState,
    text: str,
    knowledge_base: dict[str, str],
) -> AIResult | None:
    if not looks_like_greeting(text) or not looks_like_support_question(text):
        return None
    faq_reply = resolve_faq_replies(text, knowledge_base, office_address=get_settings().public_site_address)
    if not faq_reply:
        return None
    return AIResult(
        faq_reply,
        "faq",
        {},
        current_state.value,
        0.9,
        reasoning_summary="greeting_with_support:faq",
        suggested_next_action=current_state.value,
    )


def _build_greeting_reply(current_state: DialogueState, text: str) -> str | None:
    if not looks_like_greeting(text):
        return None
    if current_state == DialogueState.NEW:
        return CASUAL_SMALLTALK_REPLY
    if current_state in {
        DialogueState.CONFIRM_DATA,
        DialogueState.READY_TO_SEND_YANDEX,
        DialogueState.SENDING_TO_YANDEX,
        DialogueState.YANDEX_ERROR,
        DialogueState.COMPLETED,
    }:
        return None
    return CASUAL_SMALLTALK_REPLY


def _registration_side_reply(current_state: DialogueState, text: str, knowledge_base: dict[str, str]) -> str | None:
    if current_state in {
        DialogueState.NEW,
        DialogueState.CONFIRM_DATA,
        DialogueState.READY_TO_SEND_YANDEX,
        DialogueState.SENDING_TO_YANDEX,
        DialogueState.YANDEX_ERROR,
        DialogueState.COMPLETED,
    }:
        return None

    if not looks_like_support_question(text) and not looks_like_greeting(text):
        return None

    normalized = normalize_text_token(text)

    if looks_like_greeting(text):
        greeting = _build_greeting_reply(current_state, text)
        if greeting:
            return greeting

    if any(marker in normalized for marker in ("РјРѕР¶РЅРѕ РїРѕ РґСЂСѓРіРѕРјСѓ", "РґСЂСѓРіРѕР№ РІРѕРїСЂРѕСЃ", "РЅРµ РїСЂРѕ СЌС‚Рѕ", "РїРѕС‚РѕРј РѕС‚РІРµС‚")):
        return (
            "рџ’¬ РЎРїСЂРѕСЃРёС‚Рµ РїСЂРѕ СѓСЃР»РѕРІРёСЏ, РѕС„РёСЃ, РґРѕРєСѓРјРµРЅС‚С‹ РёР»Рё РЇРЅРґРµРєСЃ РџСЂРѕ вЂ” РѕС‚РІРµС‡Сѓ. "
            "РџСЂРѕРґРѕР»Р¶Р°РµРј СЂРµРіРёСЃС‚СЂР°С†РёСЋ СЃ С‚РµРєСѓС‰РµРіРѕ С€Р°РіР°."
        )

    step_help = _build_step_help_reply(current_state, text)
    if step_help:
        return step_help

    faq_answer = resolve_faq_replies(text, knowledge_base, office_address=get_settings().public_site_address)
    if faq_answer:
        return faq_answer

    return SHORT_SUPPORT_REPLY


def _build_step_help_reply(current_state: DialogueState, text: str) -> str | None:
    normalized = normalize_text_token(text)
    help_markers = (
        "Р·Р°С‡РµРј",
        "РїРѕС‡РµРјСѓ",
        "РґР»СЏ С‡РµРіРѕ",
        "С‡С‚Рѕ С‚Р°РєРѕРµ",
        "С‡С‚Рѕ СЌС‚Рѕ",
        "РѕР±СЉСЏСЃРЅРё",
        "РѕР±СЉСЏСЃРЅРёС‚Рµ",
        "РїРѕСЏСЃРЅРё",
        "РїРѕСЏСЃРЅРёС‚Рµ",
        "РЅРµ РїРѕРЅСЏР»",
        "РЅРµ РїРѕРЅСЏР»Р°",
        "РЅРµ РїРѕРЅРёРјР°СЋ",
        "РЅРµ РїРѕРЅРёРјР°СЋ Р·Р°С‡РµРј",
        "С‡С‚Рѕ РЅСѓР¶РЅРѕ",
        "С‡С‚Рѕ РїРёСЃР°С‚СЊ",
        "С‡С‚Рѕ СѓРєР°Р·Р°С‚СЊ",
        "С‡С‚Рѕ РІРІРѕРґРёС‚СЊ",
        "РєР°РєРѕР№ С„РѕСЂРјР°С‚",
        "РїСЂРёРјРµСЂ",
        "РїРѕРјРѕРіРёС‚Рµ",
        "РїРѕРјРѕРіРё",
        "help",
    )
    if not any(marker in normalized for marker in help_markers):
        return None

    explanations = {
        DialogueState.ASK_FULL_NAME: "РќСѓР¶РЅРѕ РїРѕР»РЅРѕРµ Р¤РРћ, РєР°Рє РІ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёРё Р»РёС‡РЅРѕСЃС‚Рё вЂ” С„Р°РјРёР»РёСЏ, РёРјСЏ Рё РѕС‚С‡РµСЃС‚РІРѕ.",
        DialogueState.ASK_PHONE: "РљРѕРЅС‚Р°РєС‚РЅС‹Р№ РЅРѕРјРµСЂ РЅСѓР¶РµРЅ РґР»СЏ СЃРІСЏР·Рё СЃ РїР°СЂРєРѕРј Рё РґР»СЏ РІС…РѕРґР° РІ РЇРЅРґРµРєСЃ РџСЂРѕ.",
        DialogueState.ASK_CITY: "РЈРєР°Р¶РёС‚Рµ РіРѕСЂРѕРґ, РіРґРµ РїР»Р°РЅРёСЂСѓРµС‚Рµ СЂР°Р±РѕС‚Р°С‚СЊ вЂ” РѕС‚ СЌС‚РѕРіРѕ Р·Р°РІРёСЃСЏС‚ СѓСЃР»РѕРІРёСЏ Рё РїРѕРґРґРµСЂР¶РєР°.",
        DialogueState.ASK_ADDRESS: "РђРґСЂРµСЃ РЅСѓР¶РµРЅ РґР»СЏ Р°РЅРєРµС‚С‹ РІРѕРґРёС‚РµР»СЏ РІ СЃРёСЃС‚РµРјРµ РїР°СЂРєР°.",
        DialogueState.ASK_IIN: (
            "РРРќ РЅСѓР¶РµРЅ РґР»СЏ СЂРµРіРёСЃС‚СЂР°С†РёРё РІ С‚Р°РєСЃРѕРїР°СЂРєРµ Рё РЇРЅРґРµРєСЃ РџСЂРѕ вЂ” СЌС‚Рѕ СЃС‚Р°РЅРґР°СЂС‚РЅРѕРµ С‚СЂРµР±РѕРІР°РЅРёРµ РґР»СЏ РІРѕРґРёС‚РµР»РµР№ РІ РљР°Р·Р°С…СЃС‚Р°РЅРµ. "
            "РЈРєР°Р¶РёС‚Рµ 12 С†РёС„СЂ СЃ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ Р»РёС‡РЅРѕСЃС‚Рё."
        ),
        DialogueState.ASK_BIRTH_DATE: "Р”Р°С‚Р° СЂРѕР¶РґРµРЅРёСЏ РґРѕР»Р¶РЅР° СЃРѕРІРїР°РґР°С‚СЊ СЃ РґРѕРєСѓРјРµРЅС‚Р°РјРё. Р¤РѕСЂРјР°С‚: Р”Р”.РњРњ.Р“Р“Р“Р“.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: (
            "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ, РєРѕРіРґР° РЅР°С‡Р°Р»Рё РІРѕРґРёС‚СЊ вЂ” РѕР±С‹С‡РЅРѕ СЌС‚Рѕ РїРѕР»Рµ В«СЃС‚Р°Р¶ СЃВ» РІ РІРѕРґРёС‚РµР»СЊСЃРєРѕРј СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёРё, "
            "Р° РЅРµ РґР°С‚Р° СЂРѕР¶РґРµРЅРёСЏ."
        ),
        DialogueState.ASK_CAR_BRAND: "РќР°РїРёС€РёС‚Рµ РјР°СЂРєСѓ Р°РІС‚РѕРјРѕР±РёР»СЏ, РЅР° РєРѕС‚РѕСЂРѕРј Р±СѓРґРµС‚Рµ СЂР°Р±РѕС‚Р°С‚СЊ.",
        DialogueState.ASK_CAR_MODEL: CAR_MODEL_PROMPT,
        DialogueState.ASK_CAR_YEAR: "Р“РѕРґ РІС‹РїСѓСЃРєР° Р°РІС‚РѕРјРѕР±РёР»СЏ вЂ” РєР°Рє РІ С‚РµС…РїР°СЃРїРѕСЂС‚Рµ.",
        DialogueState.ASK_CAR_PLATE: "Р“РѕСЃРЅРѕРјРµСЂ Р°РІС‚РѕРјРѕР±РёР»СЏ Р±РµР· Р»РёС€РЅРёС… СЃР»РѕРІ, РєР°Рє РЅР° РЅРѕРјРµСЂРЅРѕРј Р·РЅР°РєРµ.",
        DialogueState.ASK_CAR_COLOR: "Р¦РІРµС‚ Р°РІС‚РѕРјРѕР±РёР»СЏ вЂ” РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Р°С…, РЅР°РїСЂРёРјРµСЂ Р±РµР»С‹Р№ РёР»Рё С‡С‘СЂРЅС‹Р№.",
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "РќРѕРјРµСЂ РЎРўРЎ (С‚РµС…РїР°СЃРїРѕСЂС‚Р°) вЂ” РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ РЅР° Р°РІС‚РѕРјРѕР±РёР»СЊ.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: (
            "РЎРµСЂРёСЏ Рё РЅРѕРјРµСЂ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ. "
            "РњРѕР¶РЅРѕ С‡РµСЂРµР· РїСЂРѕР±РµР», РЅР°РїСЂРёРјРµСЂ CQ 981709."
        ),
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "Р”Р°С‚Р° РІС‹РґР°С‡Рё РїСЂР°РІ вЂ” РЅР° Р»РёС†РµРІРѕР№ СЃС‚РѕСЂРѕРЅРµ Р’РЈ.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "РЎСЂРѕРє РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ вЂ” РґР°С‚Р° В«РґРµР№СЃС‚РІСѓРµС‚ РґРѕВ» РЅР° Р’РЈ.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "РЈСЃР»РѕРІРёРµ СЂР°Р±РѕС‚С‹: С€С‚Р°С‚РЅС‹Р№, СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№ РёР»Рё РРџ вЂ” РєР°Рє РґРѕРіРѕРІРѕСЂРёС‚РµСЃСЊ СЃ РїР°СЂРєРѕРј.",
        DialogueState.ASK_HIRED_AT: (
            "Р”Р°С‚Р° РїСЂРёРЅСЏС‚РёСЏ РІ РїР°СЂРє вЂ” РѕР±С‹С‡РЅРѕ СЃРµРіРѕРґРЅСЏС€РЅСЏСЏ РґР°С‚Р° РёР»Рё РґРµРЅСЊ РїРѕРґРєР»СЋС‡РµРЅРёСЏ. "
            "РќРµ РїСѓС‚Р°Р№С‚Рµ СЃРѕ СЃСЂРѕРєРѕРј РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ."
        ),
        DialogueState.ASK_HEARING_IMPAIRED: "Р­С‚Рѕ РЅСѓР¶РЅРѕ РґР»СЏ РєРѕСЂСЂРµРєС‚РЅРѕР№ РЅР°СЃС‚СЂРѕР№РєРё РїСЂРѕС„РёР»СЏ. РћС‚РІРµС‚СЊС‚Рµ: РґР° РёР»Рё РЅРµС‚.",
        DialogueState.ASK_DRIVER_LICENSE_FRONT: "РћС‚РїСЂР°РІСЊС‚Рµ С‡С‘С‚РєРѕРµ С„РѕС‚Рѕ Р»РёС†РµРІРѕР№ СЃС‚РѕСЂРѕРЅС‹ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ.",
        DialogueState.ASK_DRIVER_LICENSE_BACK: "РћС‚РїСЂР°РІСЊС‚Рµ С‡С‘С‚РєРѕРµ С„РѕС‚Рѕ РѕР±СЂР°С‚РЅРѕР№ СЃС‚РѕСЂРѕРЅС‹ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ.",
        DialogueState.ASK_ID_CARD: "РћС‚РїСЂР°РІСЊС‚Рµ С„РѕС‚Рѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ Р»РёС‡РЅРѕСЃС‚Рё.",
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC: "РћС‚РїСЂР°РІСЊС‚Рµ С„РѕС‚Рѕ С‚РµС…РїР°СЃРїРѕСЂС‚Р° РёР»Рё РЎРўРЎ Р°РІС‚РѕРјРѕР±РёР»СЏ.",
        DialogueState.ASK_SELFIE_WITH_LICENSE: "РћС‚РїСЂР°РІСЊС‚Рµ СЃРµР»С„Рё СЃ РІРѕРґРёС‚РµР»СЊСЃРєРёРј СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёРµРј РІ СЂСѓРєР°С….",
    }
    explanation = explanations.get(current_state)
    if not explanation:
        return None
    return explanation


def _validation_error_reply(current_state: DialogueState, errors: list[str]) -> str:
    if current_state == DialogueState.ASK_IIN or "invalid_iin_birth_date" in errors or "invalid_iin_length" in errors:
        return "РРРќ РІС‹РіР»СЏРґРёС‚ РЅРµРєРѕСЂСЂРµРєС‚РЅС‹Рј. РџСЂРѕРІРµСЂСЊС‚Рµ 12 С†РёС„СЂ Рё РѕС‚РїСЂР°РІСЊС‚Рµ СЂРµР°Р»СЊРЅС‹Р№ РРРќ РµС‰Рµ СЂР°Р·."
    if "driver_underage" in errors:
        return "Р”Р°С‚Р° СЂРѕР¶РґРµРЅРёСЏ СѓРєР°Р·С‹РІР°РµС‚ РЅР° РІРѕР·СЂР°СЃС‚ РјР»Р°РґС€Рµ 18 Р»РµС‚. РџСЂРѕРІРµСЂСЊС‚Рµ РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ Рё РѕС‚РїСЂР°РІСЊС‚Рµ РµРµ РµС‰Рµ СЂР°Р· РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“."
    if "birth_date_in_future" in errors:
        return "Р”Р°С‚Р° СЂРѕР¶РґРµРЅРёСЏ РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РІ Р±СѓРґСѓС‰РµРј. РћС‚РїСЂР°РІСЊС‚Рµ РєРѕСЂСЂРµРєС‚РЅСѓСЋ РґР°С‚Сѓ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“."
    if "driving_experience_too_early" in errors or "driving_experience_before_birth" in errors:
        return (
            "Р”Р°С‚Р° РЅР°С‡Р°Р»Р° СЃС‚Р°Р¶Р° РІС‹РіР»СЏРґРёС‚ РЅРµРІРѕР·РјРѕР¶РЅРѕР№. "
            "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РёР· РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ, Р° РЅРµ РґР°С‚Сѓ СЂРѕР¶РґРµРЅРёСЏ."
        )
    if "driving_experience_same_as_birth" in errors:
        return (
            "Р”Р°С‚Р° РЅР°С‡Р°Р»Р° СЃС‚Р°Р¶Р° СЃРѕРІРїР°РґР°РµС‚ СЃ РґР°С‚РѕР№ СЂРѕР¶РґРµРЅРёСЏ. "
            "РЈРєР°Р¶РёС‚Рµ, РєРѕРіРґР° РІС‹ РЅР°С‡Р°Р»Рё РІРѕРґРёС‚СЊ вЂ” РѕР±С‹С‡РЅРѕ СЌС‚Рѕ РґР°С‚Р° РёР· РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ."
        )
    if "driving_experience_in_future" in errors:
        return "Р”Р°С‚Р° РЅР°С‡Р°Р»Р° СЃС‚Р°Р¶Р° РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РІ Р±СѓРґСѓС‰РµРј. РћС‚РїСЂР°РІСЊС‚Рµ РєРѕСЂСЂРµРєС‚РЅСѓСЋ РґР°С‚Сѓ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“."
    if "license_issue_before_birth" in errors or "license_issue_too_early" in errors:
        return "Р”Р°С‚Р° РІС‹РґР°С‡Рё РїСЂР°РІ РІС‹РіР»СЏРґРёС‚ РЅРµРІРѕР·РјРѕР¶РЅРѕР№. РџСЂРѕРІРµСЂСЊС‚Рµ РґР°С‚Сѓ РІС‹РґР°С‡Рё Рё РѕС‚РїСЂР°РІСЊС‚Рµ РµРµ РµС‰Рµ СЂР°Р·."
    if "license_issue_in_future" in errors:
        return "Р”Р°С‚Р° РІС‹РґР°С‡Рё РїСЂР°РІ РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РІ Р±СѓРґСѓС‰РµРј. РћС‚РїСЂР°РІСЊС‚Рµ РєРѕСЂСЂРµРєС‚РЅСѓСЋ РґР°С‚Сѓ РІ С„РѕСЂРјР°С‚Рµ Р”Р”.РњРњ.Р“Р“Р“Р“."
    if "license_expires_before_issue" in errors:
        return "РЎСЂРѕРє РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ СЂР°РЅСЊС€Рµ РґР°С‚С‹ РІС‹РґР°С‡Рё. РћС‚РїСЂР°РІСЊС‚Рµ РєРѕСЂСЂРµРєС‚РЅСѓСЋ РґР°С‚Сѓ РѕРєРѕРЅС‡Р°РЅРёСЏ РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ."
    if "license_expired" in errors:
        return "РЎСЂРѕРє РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ СѓР¶Рµ РёСЃС‚РµРє. РџСЂРѕРІРµСЂСЊС‚Рµ РґР°С‚Сѓ Рё РѕС‚РїСЂР°РІСЊС‚Рµ Р°РєС‚СѓР°Р»СЊРЅСѓСЋ РґР°С‚Сѓ РѕРєРѕРЅС‡Р°РЅРёСЏ РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ."
    if "hired_at_in_future" in errors:
        return (
            "Р”Р°С‚Р° РїСЂРёРЅСЏС‚РёСЏ РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РІ Р±СѓРґСѓС‰РµРј. "
            "РћР±С‹С‡РЅРѕ СѓРєР°Р·С‹РІР°СЋС‚ РґР°С‚Сѓ РїРѕРґРєР»СЋС‡РµРЅРёСЏ Рє РїР°СЂРєСѓ РёР»Рё СЃРµРіРѕРґРЅСЏС€РЅСЋСЋ РґР°С‚Сѓ."
        )
    if "hired_at_same_as_license_expiry" in errors:
        return (
            "Р”Р°С‚Р° РїСЂРёРЅСЏС‚РёСЏ СЃРѕРІРїР°РґР°РµС‚ СЃРѕ СЃСЂРѕРєРѕРј РґРµР№СЃС‚РІРёСЏ РїСЂР°РІ. "
            "РЈРєР°Р¶РёС‚Рµ РґР°С‚Сѓ РїРѕРґРєР»СЋС‡РµРЅРёСЏ Рє РїР°СЂРєСѓ, Р° РЅРµ В«РґРµР№СЃС‚РІСѓРµС‚ РґРѕВ» РёР· РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ."
        )
    return _clarification_reply(current_state)


def _validate_registration_fields_for_state(
    current_state: DialogueState,
    fields: dict[str, str],
    driver: Driver,
) -> list[str]:
    state_field_map = {
        DialogueState.ASK_BIRTH_DATE: "birth_date",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "driving_experience_since",
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "driver_license_issue_date",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "driver_license_expires_at",
        DialogueState.ASK_HIRED_AT: "hired_at",
    }
    field_name = state_field_map.get(current_state)
    if not field_name or field_name not in fields:
        return []
    parsed = parse_date(fields[field_name]) or fields[field_name]
    return _validate_registration_date_field(field_name, parsed, driver)


def _validate_registration_date_field(field_name: str, parsed_date: str, driver: Driver) -> list[str]:
    if field_name == "birth_date":
        return validate_birth_date(parsed_date)
    if field_name == "driving_experience_since":
        errors = validate_driver_dates(
            birth_date=driver.birth_date,
            driving_experience_since=parsed_date,
        )
        if not errors and driver.birth_date and parsed_date == driver.birth_date:
            errors.append("driving_experience_same_as_birth")
        return errors
    if field_name == "driver_license_issue_date":
        return validate_driver_dates(
            birth_date=driver.birth_date,
            driver_license_issue_date=parsed_date,
        )
    if field_name == "driver_license_expires_at":
        return validate_driver_dates(
            birth_date=driver.birth_date,
            driver_license_issue_date=driver.driver_license_issue_date,
            driver_license_expires_at=parsed_date,
        )
    if field_name == "hired_at":
        errors = validate_hired_at(parsed_date)
        if not errors and driver.driver_license_expires_at and parsed_date == driver.driver_license_expires_at:
            errors.append("hired_at_same_as_license_expiry")
        return errors
    return []


def _looks_like_non_field_message(text: str) -> bool:
    field_part, support_parts = split_field_and_support(text)
    if field_part and support_parts:
        return False
    if looks_like_support_question(text):
        return True
    normalized = normalize_text_token(text)
    if len(normalized.split()) >= 5 and not looks_like_phone(text) and not looks_like_iin(text) and parse_date(text) is None:
        return True
    return False


def _looks_like_city_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    parts = [part for part in normalized.split() if part]
    if not (1 <= len(parts) <= 3):
        return False
    return all(part.replace("-", "").isalpha() for part in parts)


def _looks_like_address_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    has_digit = any(char.isdigit() for char in normalized)
    address_markers = ("СѓР»", "СѓР»РёС†Р°", "РїСЂ", "РїСЂРѕСЃРїРµРєС‚", "РґРѕРј", "РјРєСЂ", "РјРёРєСЂРѕСЂР°Р№РѕРЅ", "РєРІ", "СЂР°Р№РѕРЅ")
    return len(normalized) >= 5 and (has_digit or any(marker in normalized for marker in address_markers) or len(normalized.split()) >= 2)


def _looks_like_short_entity_answer(text: str) -> bool:
    normalized = normalize_text_token(text)
    parts = [part for part in normalized.split() if part]
    return 1 <= len(parts) <= 4 and len(normalized) <= 32


def _looks_like_plate_answer(text: str) -> bool:
    token = text.strip().replace(" ", "").replace("-", "")
    return 5 <= len(token) <= 10 and token.isalnum()


def _looks_like_license_number(text: str) -> bool:
    token = re.sub(r"\s+", " ", text.strip().upper())
    compact = re.sub(r"\s+", "", token)
    if len(compact) < 4 or len(compact) > 20:
        return False
    return bool(re.fullmatch(r"[A-Z0-9\s]+", token))


def _get_pending_car_model_suggestion(driver: Driver) -> str | None:
    context = driver.support_context_json or {}
    pending = context.get("pending_car_model_suggestion")
    return pending if isinstance(pending, str) and pending else None


def _looks_like_clarification_confirm(text: str) -> bool:
    normalized = normalize_text_token(text)
    return normalized in {"РґР°", "yes", "РІРµСЂРЅРѕ", "РїСЂР°РІРёР»СЊРЅРѕ", "РёРјРµРЅРЅРѕ", "Р°РіР°", "РѕРє", "ok", "СѓРіСѓ"}


def _looks_like_clarification_reject(text: str) -> bool:
    normalized = normalize_text_token(text)
    return normalized in {"РЅРµС‚", "no", "РЅРµР°"}


def _car_model_registration_result(model: str) -> AIResult:
    return AIResult(
        "",
        "registration",
        {"model": model},
        DialogueState.ASK_CAR_YEAR.value,
        0.9,
        normalized_fields={"model": model},
        reasoning_summary="registration_extract:model",
        suggested_next_action=DialogueState.ASK_CAR_YEAR.value,
        clear_suggested_clarification=True,
    )


def _car_model_clarification_result(original: str, suggested: str) -> AIResult:
    return AIResult(
        build_car_model_clarification_message(original, suggested),
        "clarification",
        {},
        DialogueState.ASK_CAR_MODEL.value,
        0.82,
        reasoning_summary="clarification:car_model_suggestion",
        suggested_next_action=DialogueState.ASK_CAR_MODEL.value,
        suggested_clarification_value=suggested,
    )


def _process_car_model_answer(text: str, driver: Driver) -> AIResult | None:
    if not _looks_like_short_entity_answer(text):
        return None
    if not looks_like_precise_car_model(text):
        return AIResult(
            _clarification_reply(DialogueState.ASK_CAR_MODEL),
            "clarification",
            {},
            DialogueState.ASK_CAR_MODEL.value,
            0.45,
            reasoning_summary="clarification:car_model",
            suggested_next_action=DialogueState.ASK_CAR_MODEL.value,
        )

    pending = _get_pending_car_model_suggestion(driver)
    if pending:
        if _looks_like_clarification_confirm(text):
            return _car_model_registration_result(pending)
        if _looks_like_clarification_reject(text):
            return AIResult(
                CAR_MODEL_PROMPT,
                "clarification",
                {},
                DialogueState.ASK_CAR_MODEL.value,
                0.75,
                reasoning_summary="clarification:car_model_rejected",
                suggested_next_action=DialogueState.ASK_CAR_MODEL.value,
                clear_suggested_clarification=True,
            )
        if normalize_text_token(text) == normalize_text_token(pending):
            return _car_model_registration_result(pending)
        if normalize_car_model(text) == pending:
            return _car_model_registration_result(pending)

    brand = driver.vehicle.brand if driver.vehicle else None
    if brand:
        model, errors = resolve_model_input(brand, text)
        if model:
            return _car_model_registration_result(model)
        suggested = detect_car_model_clarification(text, brand=brand)
        if suggested and normalize_text_token(text) != normalize_text_token(suggested):
            return _car_model_clarification_result(text, suggested)
        if errors:
            return AIResult(
                catalog_validation_error_message(errors),
                "clarification",
                {},
                DialogueState.ASK_CAR_MODEL.value,
                0.72,
                reasoning_summary="validation:car_model_catalog",
                validation_errors=errors,
                suggested_next_action=DialogueState.ASK_CAR_MODEL.value,
            )

    suggested = detect_car_model_clarification(text, brand=brand)
    if suggested and normalize_text_token(text) != normalize_text_token(suggested):
        return _car_model_clarification_result(text, suggested)

    return _car_model_registration_result(normalize_car_model(text))


def _process_driver_license_answer(text: str) -> AIResult | None:
    if not _looks_like_license_number(text):
        return None
    normalized = normalize_driver_license_number(text)
    errors = validate_driver_license_number(normalized)
    if errors:
        return AIResult(
            "РќРѕРјРµСЂ РІРѕРґРёС‚РµР»СЊСЃРєРѕРіРѕ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёСЏ РІС‹РіР»СЏРґРёС‚ РЅРµРєРѕСЂСЂРµРєС‚РЅРѕ. "
            "РќР°РїРёС€РёС‚Рµ СЃРµСЂРёСЋ Рё РЅРѕРјРµСЂ, РєР°Рє РІ РґРѕРєСѓРјРµРЅС‚Рµ вЂ” РЅР°РїСЂРёРјРµСЂ CQ 981709 РёР»Рё 374653 8475853.",
            "clarification",
            {},
            DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
            0.72,
            reasoning_summary="validation:driver_license_number",
            validation_errors=errors,
            suggested_next_action=DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
        )
    return AIResult(
        "",
        "registration",
        {"driver_license_number": normalized},
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
        0.92,
        normalized_fields={"driver_license_number": normalized},
        reasoning_summary="registration_extract:driver_license_number",
        suggested_next_action=DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
    )


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
    correction_markers = ("РёСЃРїСЂР°РІ", "РёР·РјРµРЅ", "РѕС€РёР±РєР°", "РЅРµРІРµСЂРЅРѕ", "РЅРµ РїСЂР°РІРёР»СЊРЅРѕ", "РґСЂСѓРіРѕРµ", "РїРѕРјРµРЅСЏР№")
    if not any(marker in normalized for marker in correction_markers):
        return None

    field_mapping: list[tuple[tuple[str, ...], DialogueState]] = [
        (("С„РёРѕ", "РїРѕР»РЅРѕРµ РёРјСЏ"), DialogueState.ASK_FULL_NAME),
        (("С‚РµР»РµС„РѕРЅ", "РЅРѕРјРµСЂ"), DialogueState.ASK_PHONE),
        (("РіРѕСЂРѕРґ",), DialogueState.ASK_CITY),
        (("Р°РґСЂРµСЃ",), DialogueState.ASK_ADDRESS),
        (("РёРёРЅ",), DialogueState.ASK_IIN),
        (("РґР°С‚Р° СЂРѕР¶РґРµРЅРёСЏ", "СЂРѕР¶РґРµРЅРёРµ"), DialogueState.ASK_BIRTH_DATE),
        (("СЃС‚Р°Р¶", "РѕРїС‹С‚"), DialogueState.ASK_DRIVING_EXPERIENCE_SINCE),
        (("РјР°СЂРєР°", "Р±СЂРµРЅРґ"), DialogueState.ASK_CAR_BRAND),
        (("РјРѕРґРµР»СЊ",), DialogueState.ASK_CAR_MODEL),
        (("РіРѕРґ",), DialogueState.ASK_CAR_YEAR),
        (("РіРѕСЃРЅРѕРјРµСЂ", "РЅРѕРјРµСЂ РјР°С€РёРЅС‹", "РЅРѕРјРµСЂ Р°РІС‚Рѕ", "РЅРѕРјРµСЂ Р°РІС‚РѕРјРѕР±РёР»СЏ"), DialogueState.ASK_CAR_PLATE),
        (("С†РІРµС‚",), DialogueState.ASK_CAR_COLOR),
        (("СЃС‚СЃ", "С‚РµС…РїР°СЃРїРѕСЂС‚", "СЃРІРёРґРµС‚РµР»СЊСЃС‚РІРѕ", "registration certificate"), DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE),
        (("РїСЂР°РІР°", "РІСѓ", "РЅРѕРјРµСЂ РїСЂР°РІ", "РІРѕРґРёС‚РµР»СЊСЃРєРѕРµ СѓРґРѕСЃС‚РѕРІРµСЂРµРЅРёРµ"), DialogueState.ASK_DRIVER_LICENSE_NUMBER),
        (("РґР°С‚Р° РІС‹РґР°С‡Рё", "РІС‹РґР°РЅРѕ"), DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE),
        (("СЃСЂРѕРє РґРµР№СЃС‚РІРёСЏ", "РґРµР№СЃС‚РІСѓРµС‚ РґРѕ"), DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT),
        (("СѓСЃР»РѕРІРёРµ СЂР°Р±РѕС‚С‹", "СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№", "С€С‚Р°С‚РЅС‹Р№"), DialogueState.ASK_EMPLOYMENT_TYPE),
        (("РґР°С‚Р° РїСЂРёРЅСЏС‚РёСЏ", "РїСЂРёРЅСЏС‚РёСЏ"), DialogueState.ASK_HIRED_AT),
        (("СЃР»Р°Р±РѕСЃР»С‹С€Р°С‰РёР№",), DialogueState.ASK_HEARING_IMPAIRED),
    ]
    for markers, state in field_mapping:
        if any(marker in normalized for marker in markers):
            return state

    if current_state == DialogueState.CONFIRM_DATA:
        return DialogueState.ASK_FULL_NAME
    return None


def _parse_confirm_field_edit(current_state: DialogueState, text: str, driver: Driver) -> AIResult | None:
    if current_state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
        return None

    normalized = normalize_text_token(text)
    edit_markers = ("исправ", "измен", "поменя", "замен", "неправильн", "неверн", "ошиб")
    request_markers = ("хочу", "надо", "нужно", "можно", "просьба", "прошу", "пожалуйста")
    has_edit_verb = any(marker in normalized for marker in edit_markers)
    if not has_edit_verb:
        if not (any(marker in normalized for marker in request_markers) and _resolve_field_name(normalized)):
            return None

    normalized_compact = normalized.strip()
    raw_text = text.strip()
    tail = normalized_compact
    raw_tail = raw_text

    optional_prefix = re.match(
        r"^(?:хочу|надо|нужно|можно|могу|просьба|прошу|пожалуйста|мне\s+нужно)\s+(?:\w+\s+)?(?:исправить|изменить|поменять|заменить)\s+(.*)$",
        normalized_compact,
    )
    if optional_prefix:
        tail = optional_prefix.group(1).strip()
        raw_tail = raw_text
        for marker in ("исправить", "изменить", "поменять", "заменить", "исправь", "измени", "поменяй", "замени"):
            index = raw_text.lower().find(marker)
            if index != -1:
                raw_tail = raw_text[index + len(marker) :].strip()
                break

    prefix_match = re.match(
        r"^(?:исправь|исправить|измени|изменить|поменяй|поменять|замени|заменить)\s+(.*)$",
        normalized_compact,
    )
    if prefix_match and not optional_prefix:
        tail = prefix_match.group(1).strip()
        raw_tail = raw_text.split(maxsplit=1)[1].strip() if len(raw_text.split(maxsplit=1)) > 1 else ""
    elif not optional_prefix:
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
        if target_field == "is_hearing_impaired":
            return AIResult(
                "Хорошо. Напишите одним сообщением: «слабослышащий водитель — да» или «слабослышащий водитель — нет».",
                "field_edit",
                {},
                DialogueState.CONFIRM_DATA.value,
                0.84,
                target_field=target_field,
                reasoning_summary=f"field_edit:{target_field}",
                validation_errors=["missing_new_value"],
                suggested_next_action="confirm_data",
            )
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

    normalized_fields, errors = _normalize_field_edit(target_field, raw_value, driver=driver)
    if errors:
        return AIResult(
            _field_edit_error_reply(target_field, errors),
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
    if "слабослышащ" in normalized or "не слабослышащ" in normalized or "глух" in normalized:
        return "is_hearing_impaired"
    for src, dst in (
        ("марку", "марка"),
        ("модель", "модель"),
        ("фамилию", "фамилия"),
        ("имя", "имя"),
        ("отчество", "отчество"),
        ("город", "город"),
        ("адрес", "адрес"),
        ("цвет", "цвет"),
        ("телефон", "телефон"),
        ("иин", "иин"),
        ("госномер", "госномер"),
        ("стс", "стс"),
        ("техпаспорт", "техпаспорт"),
    ):
        normalized = normalized.replace(src, dst)
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
        (("стс", "техпаспорт", "свидетельство"), "registration_certificate"),
        (("vin", "вин"), "vin"),
        (("класс", "класс авто", "тариф"), "service_class"),
    ]
    for markers, field_name in mapping:
        if any(marker in normalized for marker in markers):
            return field_name
    if any(marker in normalized for marker in ("авто", "машин", "автомобил")):
        return "vehicle_descriptor"
    return None


def _normalize_field_edit(target_field: str, raw_value: str, *, driver: Driver | None = None) -> tuple[dict[str, str], list[str]]:
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
    if target_field == "is_hearing_impaired":
        parsed = parse_yes_no(value)
        if parsed is None:
            return {}, ["invalid_yes_no"]
        return {"is_hearing_impaired": "true" if parsed else "false"}, []
    if target_field == "brand":
        brand, errors = resolve_brand_input(value)
        if brand:
            return {"brand": brand}, []
        if errors:
            return {}, errors
        return {"brand": normalize_car_brand(value)}, []
    if target_field == "model":
        brand = driver.vehicle.brand if driver and driver.vehicle else None
        if brand:
            model, errors = resolve_model_input(brand, value)
            if model:
                return {"model": model}, []
            suggested = detect_car_model_clarification(value, brand=brand)
            if suggested and normalize_text_token(value) != normalize_text_token(suggested):
                return {}, ["car_model_needs_clarification"]
            if errors:
                return {}, errors
        suggested = detect_car_model_clarification(value, brand=brand)
        if suggested and normalize_text_token(value) != normalize_text_token(suggested):
            return {}, ["car_model_needs_clarification"]
        normalized_model = normalize_car_model(value)
        if not looks_like_precise_car_model(normalized_model):
            return {}, ["invalid_model"]
        return {"model": normalized_model}, []
    if target_field == "vehicle_descriptor":
        brand, model, errors = resolve_brand_model_input(value)
        if brand and model:
            return {"brand": brand, "model": model}, []
        if errors:
            return {}, errors
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
        if driver is not None:
            errors = _validate_registration_date_field(target_field, parsed, driver)
            if errors:
                return {}, errors
        elif target_field == "birth_date":
            errors = validate_birth_date(parsed)
            if errors:
                return {}, errors
        elif target_field == "hired_at":
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
    if target_field == "registration_certificate":
        if not looks_like_registration_certificate(value):
            return {}, ["invalid_registration_certificate"]
        return {"registration_certificate": normalize_registration_certificate(value)}, []
    if target_field == "driver_license_number":
        if not _looks_like_license_number(value):
            return {}, ["invalid_license_number"]
        normalized = normalize_driver_license_number(value)
        errors = validate_driver_license_number(normalized)
        if errors:
            return {}, errors
        return {"driver_license_number": normalized}, []
    if target_field == "employment_type":
        normalized_employment = normalize_employment_type(value)
        if normalized_employment.lower() not in {"С€С‚Р°С‚РЅС‹Р№", "СЃР°РјРѕР·Р°РЅСЏС‚С‹Р№", "РёРї"} and normalized_employment == value:
            return {}, ["invalid_employment_type"]
        return {"employment_type": normalized_employment}, []
    if target_field == "is_hearing_impaired":
        parsed = parse_yes_no(value)
        if parsed is None:
            return {}, ["invalid_yes_no"]
        return {"is_hearing_impaired": str(parsed).lower()}, []
    if target_field == "service_class":
        return {"service_class": normalize_service_class(value)}, []
    return {}, ["unsupported_field"]


def _field_edit_error_reply(target_field: str, errors: list[str] | None = None) -> str:
    if errors:
        date_fields = {
            "birth_date",
            "driving_experience_since",
            "driver_license_issue_date",
            "driver_license_expires_at",
            "hired_at",
        }
        if target_field in date_fields:
            state_map = {
                "birth_date": DialogueState.ASK_BIRTH_DATE,
                "driving_experience_since": DialogueState.ASK_DRIVING_EXPERIENCE_SINCE,
                "driver_license_issue_date": DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE,
                "driver_license_expires_at": DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT,
                "hired_at": DialogueState.ASK_HIRED_AT,
            }
            return _validation_error_reply(state_map[target_field], errors)
    if target_field == "model" and errors and "car_model_needs_clarification" in errors:
        return "Укажите модель из документов без поколения и кода кузова. Например: Camry вместо Camry 35."
    examples = {
        "phone": "Например: исправь телефон на +77071234567.",
        "city": "Например: измени город на Алматы.",
        "address": "Например: исправь адрес на пр. Республики 12, Астана.",
        "iin": "Например: исправь ИИН на 070404550345.",
        "birth_date": "Например: исправь дату рождения на 04.04.2007.",
        "driver_license_issue_date": "Например: измени дату выдачи на 17.03.2015.",
        "driver_license_expires_at": "Например: измени срок действия на 17.03.2030.",
        "plate_number": "Например: исправь госномер на 004YAT03.",
        "registration_certificate": "Например: исправь номер СТС на AA12345678.",
        "brand": "Например: исправь марку на Toyota.",
        "model": "Например: измени модель авто на Camry.",
        "vehicle_descriptor": "Например: исправь авто на Mercedes-Benz S-Class.",
    }
    if target_field in {"brand", "model", "vehicle_descriptor"} and errors and any(
        error in {"car_brand_not_in_catalog", "car_model_not_in_catalog", "car_brand_model_not_in_catalog"}
        or error.startswith("invalid:car_brand_not_in_catalog")
        or error.startswith("invalid:car_model_not_in_catalog")
        for error in errors
    ):
        return catalog_validation_error_message(errors)
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
        "registration_certificate": "номер СТС",
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
        elif key == "registration_certificate":
            cleaned = normalize_registration_certificate(cleaned)
        elif key == "brand":
            cleaned = normalize_car_brand(cleaned)
        elif key == "model":
            cleaned = normalize_car_model(cleaned)
        elif key == "driver_license_number":
            cleaned = normalize_driver_license_number(cleaned)
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

