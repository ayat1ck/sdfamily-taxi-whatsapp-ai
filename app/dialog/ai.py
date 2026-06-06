from dataclasses import dataclass, field
from functools import lru_cache

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dialog.faq import load_knowledge_base
from app.dialog.llm_prompt import build_system_prompt, build_user_prompt
from app.dialog.states import DialogueState
from app.drivers.models import Driver
from app.utils.logger import get_logger
from app.utils.validators import (
    looks_like_iin,
    looks_like_phone,
    normalize_employment_type,
    normalize_phone,
    normalize_text_token,
    parse_confirmation,
    parse_date,
    parse_year,
    parse_yes_no,
    split_full_name,
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


class AIModelResponse(BaseModel):
    reply: str
    intent: str
    extracted_fields: dict[str, str] = Field(default_factory=dict)
    next_state: str
    confidence: float = Field(ge=0.0, le=1.0)


class AIService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.knowledge_base = load_knowledge_base()
        self.deterministic = DeterministicAIProvider(self.knowledge_base)
        self.llm = self._build_llm_provider()

    def respond(self, state: str, message: str, driver: Driver) -> AIResult:
        fallback = self.deterministic.respond(state, message)
        if self.llm is None:
            return fallback
        try:
            current_state = DialogueState(state)
            llm_result = self.llm.respond(state, message, driver, self.knowledge_base)
            return _normalize_llm_result(llm_result, current_state, fallback)
        except Exception as exc:
            logger.exception("AI provider failed for state %s: %s", state, exc)
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
        return AIResult(parsed.reply, parsed.intent, dict(parsed.extracted_fields), parsed.next_state, parsed.confidence)


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
            model = parsed
        elif isinstance(parsed, dict):
            model = AIModelResponse.model_validate(parsed)
        else:
            raw_text = getattr(response, "text", "") or ""
            if not raw_text:
                raise RuntimeError("Gemini returned no structured output")
            model = AIModelResponse.model_validate_json(raw_text)
        return AIResult(model.reply, model.intent, dict(model.extracted_fields), model.next_state, model.confidence)


class DeterministicAIProvider:
    def __init__(self, knowledge_base: dict[str, str]) -> None:
        self.knowledge_base = knowledge_base

    def respond(self, state: str, message: str) -> AIResult:
        faq_answer = _match_faq(message, self.knowledge_base)
        if faq_answer:
            return AIResult(faq_answer, "faq", {}, state, 0.9)

        current_state = DialogueState(state)
        text = message.strip()

        if current_state == DialogueState.ASK_PHONE and looks_like_phone(text):
            return AIResult("", "registration", {"phone": normalize_phone(text)}, DialogueState.ASK_CITY.value, 0.95)
        if current_state == DialogueState.ASK_IIN and looks_like_iin(text):
            return AIResult("", "registration", {"iin": text}, DialogueState.ASK_BIRTH_DATE.value, 0.95)
        if current_state == DialogueState.ASK_CAR_YEAR:
            year = parse_year(text)
            if year:
                return AIResult("", "registration", {"year": str(year)}, DialogueState.ASK_CAR_PLATE.value, 0.9)

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
                return AIResult("", "registration", {field_name: parsed_date}, next_state, 0.9)

        if current_state == DialogueState.ASK_HEARING_IMPAIRED:
            parsed = parse_yes_no(text)
            if parsed is not None:
                return AIResult(
                    "",
                    "registration",
                    {"is_hearing_impaired": str(parsed).lower()},
                    DialogueState.ASK_DRIVER_LICENSE_FRONT.value,
                    0.9,
                )

        if current_state == DialogueState.CONFIRM_DATA and parse_confirmation(text):
            return AIResult("", "confirmation", {}, DialogueState.READY_TO_SEND_YANDEX.value, 0.99)

        fallback_field_map = {
            DialogueState.ASK_FULL_NAME: "full_name",
            DialogueState.ASK_CITY: "city",
            DialogueState.ASK_ADDRESS: "address",
            DialogueState.ASK_CAR_BRAND: "brand",
            DialogueState.ASK_CAR_MODEL: "model",
            DialogueState.ASK_CAR_PLATE: "plate_number",
            DialogueState.ASK_CAR_COLOR: "color",
            DialogueState.ASK_DRIVER_LICENSE_NUMBER: "driver_license_number",
            DialogueState.ASK_EMPLOYMENT_TYPE: "employment_type",
        }
        if current_state in fallback_field_map and text:
            extracted = {fallback_field_map[current_state]: text}
            if current_state == DialogueState.ASK_FULL_NAME:
                last_name, first_name, middle_name = split_full_name(text)
                if last_name:
                    extracted["last_name"] = last_name
                if first_name:
                    extracted["first_name"] = first_name
                if middle_name:
                    extracted["middle_name"] = middle_name
            if current_state == DialogueState.ASK_EMPLOYMENT_TYPE:
                extracted["employment_type"] = normalize_employment_type(text)
            return AIResult("", "registration", extracted, _default_next_state(current_state).value, 0.8)

        return AIResult(
            "Не совсем понял сообщение. Пожалуйста, ответьте еще раз коротко.",
            "clarification",
            {},
            state,
            0.4,
        )


def _normalize_llm_result(result: AIResult, current_state: DialogueState, fallback: AIResult) -> AIResult:
    try:
        next_state = DialogueState(result.next_state or current_state.value)
    except ValueError:
        return fallback
    if next_state.value not in set(_allowed_next_states(current_state)):
        return fallback
    if not result.reply and result.intent not in {"registration", "confirmation", "correction"}:
        return fallback
    return result


def _allowed_next_states(current_state: DialogueState) -> list[str]:
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


def _match_faq(message: str, knowledge_base: dict[str, str]) -> str | None:
    lowered = normalize_text_token(message)
    keyword_map = {
        "какие документы": "documents",
        "kakie dokumenty": "documents",
        "яндекс про": "yandex_pro",
        "yandex pro": "yandex_pro",
        "без своего авто": "car_requirements",
        "bez svoego avto": "car_requirements",
        "какие авто": "car_requirements",
        "kakie avto": "car_requirements",
        "статус заявки": "registration",
        "status zayavki": "registration",
        "сколько занимает": "registration",
        "skolko zanimaet": "registration",
        "как подключиться": "registration",
        "kak podklyuchitsya": "registration",
    }
    for keyword, doc_name in keyword_map.items():
        if keyword in lowered and doc_name in knowledge_base:
            return knowledge_base[doc_name]
    return None


@lru_cache
def get_ai_service() -> AIService:
    return AIService()
