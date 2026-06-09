from dataclasses import dataclass, field
from functools import lru_cache
import json

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
            model = _coerce_model_response(parsed, state)
        else:
            raw_text = getattr(response, "text", "") or ""
            if not raw_text:
                raise RuntimeError("Gemini returned no structured output")
            try:
                model = _coerce_model_response(json.loads(raw_text), state)
            except json.JSONDecodeError:
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

        if not text:
            return AIResult(_clarification_reply(current_state), "clarification", {}, state, 0.4)

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
                )
            if _looks_like_onboarding_intent(text):
                return AIResult(
                    PROMPTS[DialogueState.NEW],
                    "clarification",
                    {},
                    DialogueState.ASK_FULL_NAME.value,
                    0.75,
                )
            return AIResult(
                "Здравствуйте. Я могу рассказать об условиях парка и помочь пройти регистрацию. Если хотите начать, напишите ваше ФИО полностью.",
                "clarification",
                {},
                DialogueState.NEW.value,
                0.55,
            )

        correction_state = _detect_correction_state(current_state, text)
        if correction_state is not None:
            return AIResult(
                f"Хорошо, исправим этот пункт. {PROMPTS[correction_state]}",
                "correction",
                {},
                correction_state.value,
                0.85,
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
                return AIResult("", "registration", extracted, DialogueState.ASK_PHONE.value, 0.9)
            return AIResult(_clarification_reply(current_state), "clarification", {}, state, 0.45)

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

        extracted = _extract_safe_field_answer(current_state, text)
        if extracted:
            return AIResult("", "registration", extracted, _default_next_state(current_state).value, 0.8)

        return AIResult(_clarification_reply(current_state), "clarification", {}, state, 0.4)


def _normalize_llm_result(result: AIResult, current_state: DialogueState, fallback: AIResult) -> AIResult:
    try:
        next_state = DialogueState(result.next_state or current_state.value)
    except ValueError:
        return fallback
    if next_state.value not in set(_allowed_next_states(current_state)):
        return fallback
    if not result.reply and result.intent not in {"registration", "confirmation", "correction"}:
        return fallback
    if result.intent == "registration" and not _is_safe_registration_result(result, current_state):
        return fallback
    return result


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
    if len(parts[0]) < 2 or len(parts[1]) < 2:
        return False
    return True


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
        return {"brand": text.strip()}
    if current_state == DialogueState.ASK_CAR_MODEL and _looks_like_short_entity_answer(text):
        return {"model": text.strip()}
    if current_state == DialogueState.ASK_CAR_PLATE and _looks_like_plate_answer(text):
        return {"plate_number": text.strip()}
    if current_state == DialogueState.ASK_CAR_COLOR and _looks_like_short_entity_answer(text):
        return {"color": text.strip()}
    if current_state == DialogueState.ASK_DRIVER_LICENSE_NUMBER and _looks_like_license_number(text):
        return {"driver_license_number": text.strip()}
    if current_state == DialogueState.ASK_EMPLOYMENT_TYPE:
        normalized = normalize_employment_type(text)
        if normalized in {"штатный", "самозанятый"} or normalized != text.strip():
            return {"employment_type": normalized}
    return {}


def _clarification_reply(current_state: DialogueState) -> str:
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
        DialogueState.ASK_EMPLOYMENT_TYPE: "Укажите условие работы: штатный или самозанятый.",
        DialogueState.ASK_HIRED_AT: "Укажите дату принятия в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_HEARING_IMPAIRED: "Ответьте коротко: да или нет.",
    }
    return custom.get(current_state, PROMPTS.get(current_state, "Пожалуйста, ответьте на текущий вопрос."))


def _looks_like_non_field_message(text: str) -> bool:
    normalized = normalize_text_token(text)
    if "?" in text:
        return True
    if len(normalized.split()) >= 5 and not looks_like_phone(text) and not looks_like_iin(text) and parse_date(text) is None:
        return True
    question_markers = (
        "кто",
        "что",
        "какие условия",
        "сколько",
        "как",
        "почему",
        "где",
        "можно ли",
        "помоги",
        "объясни",
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
    if not (1 <= len(parts) <= 4):
        return False
    return len(normalized) <= 32


def _looks_like_plate_answer(text: str) -> bool:
    token = text.strip().replace(" ", "").replace("-", "")
    return 5 <= len(token) <= 10 and token.isalnum()


def _looks_like_license_number(text: str) -> bool:
    token = text.strip().replace(" ", "")
    return 4 <= len(token) <= 20 and token.isalnum()


def _coerce_model_response(payload: dict[str, object], current_state: str) -> AIModelResponse:
    normalized = dict(payload)
    normalized.setdefault("reply", "")
    normalized.setdefault("intent", "clarification")
    normalized.setdefault("extracted_fields", {})
    normalized.setdefault("next_state", current_state)
    normalized.setdefault("confidence", 0.6)
    return AIModelResponse.model_validate(normalized)


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


@lru_cache
def get_ai_service() -> AIService:
    return AIService()
