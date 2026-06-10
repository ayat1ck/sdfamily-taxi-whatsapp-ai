# -*- coding: utf-8 -*-
"""Simulate bot responses across states and message types (deterministic + AIService layer)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.dialog.ai import AIService, AIResult
from app.dialog.faq import load_knowledge_base, resolve_faq_replies
from app.dialog.prompts import format_in_flow_reply
from app.dialog.states import DialogueState
from app.documents.registration_flow import is_expecting_data_document, next_registration_state
from app.utils.validators import looks_like_manual_data_entry


# Valid test IIN (yy=88 mm=01 dd=01)
TEST_IIN = "880101300123"


@dataclass
class Scenario:
    name: str
    state: str
    message: str
    driver: dict[str, Any] = field(default_factory=dict)
    expect_intent: str | None = None
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    expect_fields: tuple[str, ...] = ()
    expect_target_field: str | None = None


def make_driver(**kwargs: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": 1,
        "whatsapp_phone": "+77001112233",
        "state": DialogueState.NEW.value,
        "support_context_json": None,
        "birth_date": None,
        "driver_license_issue_date": None,
        "driver_license_expires_at": None,
        "iin": None,
        "vehicle": None,
        "documents": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def truncate(text: str, limit: int = 280) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def office_marker() -> str:
    return "захотите"


SCENARIOS: list[Scenario] = [
    # --- NEW / greetings & FAQ ---
    Scenario("NEW: привет", DialogueState.NEW.value, "Привет", expect_intent="help", must_contain=("Здравствуйте",), must_not_contain=(office_marker(),)),
    Scenario("NEW: алло", DialogueState.NEW.value, "алло", expect_intent="help", must_contain=("Здравствуйте",), must_not_contain=(office_marker(),)),
    Scenario("NEW: здравствуйте", DialogueState.NEW.value, "Здравствуйте", expect_intent="help", must_not_contain=(office_marker(),)),
    Scenario("NEW: где офис", DialogueState.NEW.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",), must_not_contain=(office_marker(),)),
    Scenario("NEW: условия", DialogueState.NEW.value, "Какие условия?", expect_intent="faq", must_contain=("2%",), must_not_contain=(office_marker(), "Кто вы такие")),
    Scenario("NEW: два вопроса", DialogueState.NEW.value, "Где офис и какие условия?", expect_intent="faq", must_contain=("Момышулы", "2%")),
    Scenario("NEW: кто вы", DialogueState.NEW.value, "Кто вы такие?", expect_intent="faq", must_contain=("SD Family Taxi",), must_not_contain=(office_marker(),)),
    Scenario("NEW: документы", DialogueState.NEW.value, "Какие документы нужны?", expect_intent="faq", must_not_contain=(office_marker(),)),
    Scenario("NEW: яндекс про", DialogueState.NEW.value, "Как войти в Яндекс Про?", expect_intent="faq", must_not_contain=(office_marker(),)),
    Scenario("NEW: неизвестный вопрос", DialogueState.NEW.value, "Можно ли работать на Kia Rio?", expect_intent="faq", must_contain=("авто",), must_not_contain=(office_marker(),)),
    Scenario("NEW: хочу подключиться", DialogueState.NEW.value, "Хочу подключиться", expect_intent="clarification", must_contain=("ФИО",), must_not_contain=("Начинаем регистрацию",)),
    Scenario("NEW: ФИО", DialogueState.NEW.value, "Абай Аят Жаныбекулы", expect_intent="registration", expect_fields=("full_name",), must_contain=("регистрац",)),
    # --- Registration flow ---
    Scenario("ASK_PHONE: номер", DialogueState.ASK_PHONE.value, "+77071234567", expect_intent="registration", expect_fields=("phone",)),
    Scenario("ASK_PHONE: алло mid-flow", DialogueState.ASK_PHONE.value, "алло", expect_intent="help", must_contain=("Здравствуйте",), must_not_contain=(office_marker(),)),
    Scenario("ASK_PHONE: где офис", DialogueState.ASK_PHONE.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario(
        "ASK_IIN: mixed IIN + office",
        DialogueState.ASK_IIN.value,
        f"ИИН {TEST_IIN} и где офис?",
        expect_intent="registration",
        expect_fields=("iin",),
        must_contain=("Момышулы", "дату рождения"),
    ),
    Scenario("ASK_IIN: только ИИН", DialogueState.ASK_IIN.value, TEST_IIN, expect_intent="registration", expect_fields=("iin",)),
    Scenario("ASK_IIN: bad IIN", DialogueState.ASK_IIN.value, "123", expect_intent="clarification", must_contain=("ИИН",)),
    Scenario("ASK_PHONE: зачем полный", DialogueState.ASK_PHONE.value, "Зачем тебе мой номер телефона?", expect_intent="help", must_contain=("Яндекс Про", "контактный"), must_not_contain=(office_marker(), "ИИН")),
    Scenario("ASK_PHONE: зачем короткий", DialogueState.ASK_PHONE.value, "Зачем?", expect_intent="help", must_contain=("Яндекс Про",), must_not_contain=(office_marker(), "ИИН")),
    Scenario("ASK_IIN: зачем короткий", DialogueState.ASK_IIN.value, "Зачем?", expect_intent="help", must_contain=("ИИН",), must_not_contain=(office_marker(),)),
    Scenario(
        "ASK_BIRTH_DATE: дата",
        DialogueState.ASK_BIRTH_DATE.value,
        "01.01.1988",
        driver={"birth_date": None, "iin": TEST_IIN},
        expect_intent="registration",
        expect_fields=("birth_date",),
    ),
    Scenario(
        "ASK_BIRTH_DATE: будущая дата",
        DialogueState.ASK_BIRTH_DATE.value,
        "01.01.2030",
        expect_intent="clarification",
    ),
    Scenario("ASK_CAR_BRAND: Toyota", DialogueState.ASK_CAR_BRAND.value, "Toyota", expect_intent="registration", expect_fields=("brand",)),
    Scenario("ASK_CAR_MODEL: Toyota и Camry", DialogueState.ASK_CAR_MODEL.value, "Toyota и Camry", driver={"vehicle": SimpleNamespace(brand="Toyota")}, expect_intent="registration", expect_fields=("model",), must_not_contain=(office_marker(), "I Camry")),
    Scenario("ASK_CAR_MODEL: w221", DialogueState.ASK_CAR_MODEL.value, "w221", driver={"vehicle": SimpleNamespace(brand="Mercedes")}, expect_intent="registration", expect_fields=("model",)),
    Scenario("ASK_CAR_MODEL: Camry", DialogueState.ASK_CAR_MODEL.value, "Camry", driver={"vehicle": SimpleNamespace(brand="Toyota")}, expect_intent="registration", expect_fields=("model",)),
    Scenario("ASK_CAR_YEAR: год", DialogueState.ASK_CAR_YEAR.value, "2018", expect_intent="registration", expect_fields=("year",)),
    Scenario("ASK_CAR_PLATE: номер", DialogueState.ASK_CAR_PLATE.value, "123ABC01", expect_intent="registration", expect_fields=("plate_number",)),
    Scenario("ASK_EMPLOYMENT_TYPE: штатный", DialogueState.ASK_EMPLOYMENT_TYPE.value, "штатный", expect_intent="registration", expect_fields=("employment_type",)),
    Scenario(
        "ASK_DRIVER_LICENSE: номер",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
        "CQ 981709",
        expect_intent="registration",
        expect_fields=("driver_license_number",),
    ),
    Scenario("CONFIRM: подтверждаю", DialogueState.CONFIRM_DATA.value, "Подтверждаю", expect_intent="confirmation"),
    Scenario("CONFIRM: где офис", DialogueState.CONFIRM_DATA.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    # --- Edge / noise ---
    Scenario("ASK_CITY: город", DialogueState.ASK_CITY.value, "Астана", expect_intent="registration", expect_fields=("city",)),
    Scenario("ASK_IIN: ало без вопроса", DialogueState.ASK_IIN.value, "ало", expect_intent="help", must_not_contain=(office_marker(),)),
    Scenario("NEW: привет где офис", DialogueState.NEW.value, "Привет, где офис?", must_contain=("Момышулы",), must_not_contain=(office_marker(),)),
    Scenario("NEW: комиссия", DialogueState.NEW.value, "Какая комиссия?", expect_intent="faq", must_contain=("2%",)),
    Scenario("NEW: бонусы", DialogueState.NEW.value, "Какие бонусы?", expect_intent="faq", must_contain=("Байге",), must_not_contain=(office_marker(),)),
    Scenario("NEW: бонусы перефраз", DialogueState.NEW.value, "Что получу за стаж?", expect_intent="faq", must_contain=("Байге",), must_not_contain=(office_marker(),)),
    Scenario("NEW: комиссия перефраз", DialogueState.NEW.value, "Сколько процентов берете?", expect_intent="faq", must_contain=("2%",), must_not_contain=(office_marker(),)),
    Scenario("NEW: офис перефраз", DialogueState.NEW.value, "Куда приехать?", expect_intent="faq", must_contain=("Момышулы",), must_not_contain=(office_marker(),)),
    Scenario("NEW: акции", DialogueState.NEW.value, "Есть акции?", expect_intent="faq", must_contain=("Байге",), must_not_contain=(office_marker(),)),
    Scenario("ASK_ADDRESS: адрес", DialogueState.ASK_ADDRESS.value, "пр. Республики 12, Астана", expect_intent="registration", expect_fields=("address",)),
    Scenario("ASK_IIN: смешанный bad+office", DialogueState.ASK_IIN.value, "123 и где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario("ASK_IIN: где офис mid-flow", DialogueState.ASK_IIN.value, "Где ваш офис находится?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario("ASK_IIN: условия mid-flow", DialogueState.ASK_IIN.value, "А какие у вас условия?", expect_intent="faq", must_contain=("2%",)),
    Scenario("ASK_IIN: другой вопрос", DialogueState.ASK_IIN.value, "А можно по другому вопросу?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario("ASK_CAR_MODEL: Camry 35", DialogueState.ASK_CAR_MODEL.value, "Camry 35", driver={"vehicle": SimpleNamespace(brand="Toyota")}, expect_intent="registration", expect_fields=("model",)),
    # --- Field edit on confirm / yandex_error ---
    Scenario(
        "CONFIRM: хочу изменить модель",
        DialogueState.CONFIRM_DATA.value,
        "Хочу изменить модель",
        expect_intent="field_edit",
        expect_target_field="model",
        must_contain=("модель",),
    ),
    Scenario(
        "CONFIRM: исправь модель на 5er",
        DialogueState.CONFIRM_DATA.value,
        "Исправь модель на 5er",
        driver={"vehicle": SimpleNamespace(brand="BMW", model="525i")},
        expect_intent="field_edit",
        expect_target_field="model",
        expect_fields=("model",),
    ),
    Scenario(
        "CONFIRM: просьба изменить модель",
        DialogueState.CONFIRM_DATA.value,
        "Просьба изменить модель на 5er",
        driver={"vehicle": SimpleNamespace(brand="BMW", model="525i")},
        expect_intent="field_edit",
        expect_target_field="model",
        expect_fields=("model",),
    ),
    Scenario(
        "CONFIRM: поменять город",
        DialogueState.CONFIRM_DATA.value,
        "Поменять город на Алматы",
        expect_intent="field_edit",
        expect_target_field="city",
        expect_fields=("city",),
    ),
    Scenario(
        "CONFIRM: модель изменить suffix",
        DialogueState.CONFIRM_DATA.value,
        "модель изменить",
        expect_intent="field_edit",
        expect_target_field="model",
        must_contain=("модель",),
    ),
    Scenario(
        "YANDEX_ERROR: изменить госномер",
        DialogueState.YANDEX_ERROR.value,
        "Нужно изменить госномер на 123ABC01",
        expect_intent="field_edit",
        expect_target_field="plate_number",
        expect_fields=("plate_number",),
    ),
    Scenario(
        "CONFIRM: подтверждаю typo",
        DialogueState.CONFIRM_DATA.value,
        "Потверждаю",
        expect_intent="confirmation",
    ),
    # --- FAQ paraphrases ---
    Scenario("NEW: pdf документы", DialogueState.NEW.value, "Можно PDF из eGov?", expect_intent="faq", must_contain=("PDF",)),
    Scenario("NEW: как сфоткать", DialogueState.NEW.value, "Как правильно сфотографировать документ?", expect_intent="faq", must_contain=("поверхность",)),
    Scenario("ASK_CAR_BRAND: faq не перехватывает", DialogueState.ASK_CAR_BRAND.value, "Skoda", expect_intent="registration", expect_fields=("brand",)),
    # --- Extended FAQ phrasings ---
    Scenario("NEW: салам", DialogueState.NEW.value, "Салам", expect_intent="help", must_contain=("Здравствуйте",)),
    Scenario("NEW: добрый день", DialogueState.NEW.value, "Добрый день", expect_intent="help"),
    Scenario("NEW: сколько времени регистрация", DialogueState.NEW.value, "Сколько времени занимает регистрация?", expect_intent="faq", must_contain=("10",)),
    Scenario("NEW: статус заявки", DialogueState.NEW.value, "Как узнать статус заявки?", expect_intent="faq", must_contain=("статус",)),
    Scenario("NEW: смс не приходит", DialogueState.NEW.value, "СМС не приходит", expect_intent="faq", must_contain=("SMS",)),
    Scenario("NEW: не могу войти яндекс", DialogueState.NEW.value, "Не могу войти в Яндекс Про", expect_intent="faq"),
    Scenario("NEW: поддержка", DialogueState.NEW.value, "Есть поддержка?", expect_intent="faq", must_contain=("поддерж",)),
    Scenario("NEW: сухой туман", DialogueState.NEW.value, "Что за сухой туман?", expect_intent="faq", must_contain=("туман",)),
    Scenario("NEW: выплаты", DialogueState.NEW.value, "Как выводить деньги?", expect_intent="faq", must_contain=("выплат",)),
    Scenario("NEW: зачем иин", DialogueState.NEW.value, "Зачем нужен ИИН?", expect_intent="faq", must_contain=("ИИН",)),
    Scenario("NEW: kaspi pdf", DialogueState.NEW.value, "Можно PDF из Kaspi?", expect_intent="faq", must_contain=("PDF",)),
    Scenario("NEW: три вопроса", DialogueState.NEW.value, "Где офис, какая комиссия и какие документы?", expect_intent="faq", must_contain=("Момышулы", "2%", "удостоверение")),
    Scenario("NEW: подарок регистрация", DialogueState.NEW.value, "Что дают за регистрацию?", expect_intent="faq", must_contain=("подар",)),
    Scenario("NEW: работать без авто", DialogueState.NEW.value, "Можно работать без своей машины?", expect_intent="faq"),
    Scenario("NEW: после регистрации", DialogueState.NEW.value, "Что делать после регистрации?", expect_intent="faq"),
    Scenario("NEW: скачать яндекс", DialogueState.NEW.value, "Где скачать Яндекс Про?", expect_intent="faq", must_contain=("Установ",)),
    Scenario("NEW: выйти на линию", DialogueState.NEW.value, "Как выйти на линию?", expect_intent="faq"),
    Scenario("NEW: аккаунт неактивен", DialogueState.NEW.value, "Аккаунт не активен в Про", expect_intent="faq", must_contain=("актив",)),
    # --- Registration variants ---
    Scenario("ASK_PHONE: без плюса", DialogueState.ASK_PHONE.value, "87071234567", expect_intent="registration", expect_fields=("phone",)),
    Scenario("ASK_PHONE: с пробелами", DialogueState.ASK_PHONE.value, "8 707 123 45 67", expect_intent="registration", expect_fields=("phone",)),
    Scenario("ASK_CITY: астана lower", DialogueState.ASK_CITY.value, "астана", expect_intent="registration", expect_fields=("city",)),
    Scenario("ASK_CITY: офис mid-flow", DialogueState.ASK_CITY.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario("ASK_CAR_BRAND: бмв", DialogueState.ASK_CAR_BRAND.value, "бмв", expect_intent="registration", expect_fields=("brand",)),
    Scenario("ASK_CAR_BRAND: Mercedes-Benz", DialogueState.ASK_CAR_BRAND.value, "Mercedes-Benz", expect_intent="registration", expect_fields=("brand",)),
    Scenario(
        "ASK_CAR_MODEL: BMW 525i",
        DialogueState.ASK_CAR_MODEL.value,
        "525i",
        driver={"vehicle": SimpleNamespace(brand="BMW")},
        expect_intent="registration",
        expect_fields=("model",),
    ),
    Scenario(
        "ASK_CAR_MODEL: Passat TDI",
        DialogueState.ASK_CAR_MODEL.value,
        "Passat 2.0 TDI",
        driver={"vehicle": SimpleNamespace(brand="Volkswagen")},
        expect_intent="registration",
        expect_fields=("model",),
    ),
    Scenario("ASK_CAR_COLOR: белый", DialogueState.ASK_CAR_COLOR.value, "белый", expect_intent="registration", expect_fields=("color",)),
    Scenario("ASK_CAR_COLOR: черный", DialogueState.ASK_CAR_COLOR.value, "черный", expect_intent="registration", expect_fields=("color",)),
    Scenario("ASK_EMPLOYMENT_TYPE: самозанятый", DialogueState.ASK_EMPLOYMENT_TYPE.value, "самозанятый", expect_intent="registration", expect_fields=("employment_type",)),
    Scenario("ASK_EMPLOYMENT_TYPE: ип", DialogueState.ASK_EMPLOYMENT_TYPE.value, "ИП", expect_intent="registration", expect_fields=("employment_type",)),
    Scenario("ASK_HEARING_IMPAIRED: нет", DialogueState.ASK_HEARING_IMPAIRED.value, "нет", expect_intent="registration", expect_fields=("is_hearing_impaired",)),
    Scenario("ASK_HEARING_IMPAIRED: да", DialogueState.ASK_HEARING_IMPAIRED.value, "да", expect_intent="registration", expect_fields=("is_hearing_impaired",)),
    Scenario("ASK_HIRED_AT: дата", DialogueState.ASK_HIRED_AT.value, "10.06.2026", expect_intent="registration", expect_fields=("hired_at",)),
    Scenario(
        "ASK_DRIVING_EXPERIENCE: дата",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE.value,
        "01.06.2010",
        expect_intent="registration",
        expect_fields=("driving_experience_since",),
    ),
    Scenario(
        "ASK_DRIVER_LICENSE_ISSUE: дата",
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
        "15.03.2020",
        expect_intent="registration",
        expect_fields=("driver_license_issue_date",),
    ),
    Scenario(
        "ASK_DRIVER_LICENSE_EXPIRES: дата",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
        "15.03.2030",
        expect_intent="registration",
        expect_fields=("driver_license_expires_at",),
    ),
    Scenario(
        "ASK_CAR_REGISTRATION_CERT: номер",
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE.value,
        "AA12345678",
        expect_intent="registration",
        expect_fields=("registration_certificate",),
    ),
    Scenario(
        "ASK_CAR_PLATE: lower",
        DialogueState.ASK_CAR_PLATE.value,
        "123abc01",
        expect_intent="registration",
        expect_fields=("plate_number",),
    ),
    Scenario(
        "ASK_CAR_PLATE: условия faq",
        DialogueState.ASK_CAR_PLATE.value,
        "Какие условия у парка?",
        expect_intent="faq",
        must_contain=("2%",),
    ),
    # --- Confirm variants ---
    Scenario("CONFIRM: все верно", DialogueState.CONFIRM_DATA.value, "Всё верно", expect_intent="confirmation"),
    Scenario("CONFIRM: ok", DialogueState.CONFIRM_DATA.value, "ok", expect_intent="confirmation"),
    Scenario("CONFIRM: подтверждаю bang", DialogueState.CONFIRM_DATA.value, "Подтверждаю!", expect_intent="confirmation"),
    Scenario("YANDEX_ERROR: подтверждаю retry", DialogueState.YANDEX_ERROR.value, "Подтверждаю", expect_intent="confirmation"),
    Scenario("YANDEX_ERROR: офис faq", DialogueState.YANDEX_ERROR.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario(
        "CONFIRM: faq не мешает правке",
        DialogueState.CONFIRM_DATA.value,
        "Какая комиссия? Хочу изменить модель",
        expect_intent="field_edit",
        expect_target_field="model",
    ),
    # --- Extended field edits ---
    Scenario(
        "CONFIRM: замени иин",
        DialogueState.CONFIRM_DATA.value,
        f"Замени ИИН на {TEST_IIN}",
        expect_intent="field_edit",
        expect_target_field="iin",
        expect_fields=("iin",),
    ),
    Scenario(
        "CONFIRM: исправьте фио",
        DialogueState.CONFIRM_DATA.value,
        "Исправьте ФИО на Касымов Али Бекович",
        expect_intent="field_edit",
        expect_target_field="full_name",
        expect_fields=("full_name",),
    ),
    Scenario(
        "CONFIRM: поменяй марку",
        DialogueState.CONFIRM_DATA.value,
        "Поменяй марку на BMW",
        expect_intent="field_edit",
        expect_target_field="brand",
        expect_fields=("brand",),
    ),
    Scenario(
        "CONFIRM: заменить цвет",
        DialogueState.CONFIRM_DATA.value,
        "Заменить цвет на белый",
        expect_intent="field_edit",
        expect_target_field="color",
        expect_fields=("color",),
    ),
    Scenario(
        "CONFIRM: адрес изменить",
        DialogueState.CONFIRM_DATA.value,
        "адрес изменить на пр. Абая 10",
        expect_intent="field_edit",
        expect_target_field="address",
        expect_fields=("address",),
    ),
    Scenario(
        "CONFIRM: прошу поменять телефон",
        DialogueState.CONFIRM_DATA.value,
        "Прошу поменять телефон",
        expect_intent="field_edit",
        expect_target_field="phone",
        must_contain=("телефон",),
    ),
    Scenario(
        "CONFIRM: модель неверная",
        DialogueState.CONFIRM_DATA.value,
        "Модель неверная, исправь на 5er",
        driver={"vehicle": SimpleNamespace(brand="BMW", model="525i")},
        expect_intent="field_edit",
        expect_target_field="model",
        expect_fields=("model",),
    ),
    Scenario(
        "YANDEX_ERROR: хочу изменить модель",
        DialogueState.YANDEX_ERROR.value,
        "Хочу изменить модель",
        expect_intent="field_edit",
        expect_target_field="model",
    ),
    Scenario(
        "CONFIRM: не поле а faq",
        DialogueState.CONFIRM_DATA.value,
        "Сколько процентов комиссия?",
        expect_intent="faq",
        must_contain=("2%",),
    ),
    # --- Onboarding phrasings ---
    Scenario("NEW: хочу работать", DialogueState.NEW.value, "Хочу работать в такси", expect_intent="clarification", must_contain=("ФИО",)),
    Scenario("NEW: как подключиться", DialogueState.NEW.value, "Как подключиться?", expect_intent="faq", must_contain=("ФИО",)),
    Scenario("NEW: как устроиться", DialogueState.NEW.value, "Как устроиться?", expect_intent="faq", must_contain=("ФИО",)),
    # --- Noise / edge ---
    Scenario("ASK_IIN: иин с пробелами", DialogueState.ASK_IIN.value, f"ИИН {TEST_IIN[:6]} {TEST_IIN[6:]}", expect_intent="registration", expect_fields=("iin",)),
    Scenario("ASK_FULL_NAME: faq офис", DialogueState.ASK_FULL_NAME.value, "Где офис?", expect_intent="faq", must_contain=("Момышулы",)),
    Scenario(
        "ASK_FULL_NAME: фио",
        DialogueState.ASK_FULL_NAME.value,
        "Нурланов Бекжан Серикович",
        expect_intent="registration",
        expect_fields=("full_name",),
    ),
    Scenario(
        "ASK_CAR_MODEL: x5",
        DialogueState.ASK_CAR_MODEL.value,
        "X5",
        driver={"vehicle": SimpleNamespace(brand="BMW")},
        expect_intent="registration",
        expect_fields=("model",),
    ),
    Scenario(
        "ASK_CAR_MODEL: Rio",
        DialogueState.ASK_CAR_MODEL.value,
        "Rio",
        driver={"vehicle": SimpleNamespace(brand="Kia")},
        expect_intent="registration",
        expect_fields=("model",),
    ),
    Scenario(
        "ASK_EMPLOYMENT_TYPE: самозанятый not manual",
        DialogueState.ASK_EMPLOYMENT_TYPE.value,
        "самозанятый",
        expect_intent="registration",
        expect_fields=("employment_type",),
    ),
]


@dataclass
class ManualMarkerCase:
    message: str
    expect: bool
    label: str = ""


MANUAL_MARKER_CASES: list[ManualMarkerCase] = [
    ManualMarkerCase("хочу вручную", True, "хочу вручную"),
    ManualMarkerCase("заполню сам", True, "заполню сам"),
    ManualMarkerCase("без фото", True, "без фото"),
    ManualMarkerCase("напишу сама данные", True, "напишу сама"),
    ManualMarkerCase("лучше вручную", True, "лучше вручную"),
    ManualMarkerCase("самозанятый", False, "самозанятый"),
    ManualMarkerCase("Астана", False, "город"),
]


@dataclass
class ManualFlowCase:
    name: str
    driver: dict[str, Any]
    expect_next: str
    expect_expecting_doc: bool | None = None
    state: str = DialogueState.ASK_DRIVER_LICENSE_FRONT.value


MANUAL_FLOW_CASES: list[ManualFlowCase] = [
    ManualFlowCase(
        "license front expects data doc",
        driver={"documents": [], "full_name": "Абай Аят", "phone": "+77001112233", "city": "Астана"},
        expect_next=DialogueState.ASK_DRIVER_LICENSE_FRONT.value,
        expect_expecting_doc=True,
    ),
    ManualFlowCase(
        "manual skip -> first empty text field",
        driver={
            "support_context_json": {"manual_data_entry": True},
            "documents": [
                SimpleNamespace(document_type="driver_license_front", status="skipped_manual"),
                SimpleNamespace(document_type="driver_license_back", status="skipped_manual"),
                SimpleNamespace(document_type="id_card", status="skipped_manual"),
                SimpleNamespace(document_type="vehicle_registration_doc", status="skipped_manual"),
            ],
            "full_name": "Абай Аят",
            "phone": "+77001112233",
            "city": "Астана",
        },
        expect_next=DialogueState.ASK_ADDRESS.value,
        expect_expecting_doc=False,
    ),
    ManualFlowCase(
        "manual all text filled -> selfie",
        driver={
            "support_context_json": {"manual_data_entry": True},
            "documents": [
                SimpleNamespace(document_type=t, status="skipped_manual")
                for t in (
                    "driver_license_front",
                    "driver_license_back",
                    "id_card",
                    "vehicle_registration_doc",
                )
            ],
            "full_name": "Абай Аят Жаныбекулы",
            "phone": "+77001112233",
            "city": "Астана",
            "address": "ул. Тест 1",
            "iin": TEST_IIN,
            "birth_date": "1988-01-01",
            "driving_experience_since": "2010-01-01",
            "driver_license_number": "CQ981709",
            "driver_license_issue_date": "2020-01-01",
            "driver_license_expires_at": "2030-01-01",
            "employment_type": "park_employee",
            "hired_at": "2026-01-01",
            "is_hearing_impaired": "false",
            "vehicle": SimpleNamespace(
                brand="Toyota",
                model="Camry",
                year=2018,
                plate_number="123ABC01",
                color="белый",
                registration_certificate="AB12345678",
            ),
        },
        expect_next=DialogueState.ASK_SELFIE_WITH_LICENSE.value,
        expect_expecting_doc=False,
    ),
]


@dataclass
class RunResult:
    scenario: Scenario
    result: AIResult
    issues: list[str]


def user_facing_reply(state: str, result: AIResult) -> str:
    try:
        dialogue_state = DialogueState(state)
    except ValueError:
        return result.reply or ""
    if dialogue_state.value.startswith("ask_") and result.intent in {"faq", "help", "smalltalk", "clarification"}:
        return format_in_flow_reply(result.reply or "", dialogue_state)
    return result.reply or ""


def check_scenario(scenario: Scenario, result: AIResult) -> list[str]:
    issues: list[str] = []
    reply = result.reply or ""

    if scenario.expect_intent and result.intent != scenario.expect_intent:
        issues.append(f"intent: expected {scenario.expect_intent!r}, got {result.intent!r}")

    for field_name in scenario.expect_fields:
        if field_name not in result.extracted_fields:
            issues.append(f"missing field {field_name!r} in {result.extracted_fields}")

    if scenario.expect_target_field and result.target_field != scenario.expect_target_field:
        issues.append(f"target_field: expected {scenario.expect_target_field!r}, got {result.target_field!r}")

    for needle in scenario.must_contain:
        if needle.lower() not in reply.lower():
            issues.append(f"reply missing {needle!r}")

    for needle in scenario.must_not_contain:
        if needle.lower() in reply.lower():
            issues.append(f"reply must not contain {needle!r}")

    if result.intent in {"faq", "help"} and not reply.strip():
        issues.append("empty reply for faq/help")

    if result.intent == "clarification" and not reply.strip() and not result.extracted_fields:
        issues.append("empty clarification reply")

    return issues


def run_manual_entry_checks() -> list[str]:
    issues: list[str] = []

    for case in MANUAL_MARKER_CASES:
        got = looks_like_manual_data_entry(case.message)
        if got != case.expect:
            label = case.label or case.message
            issues.append(f"manual marker {label!r}: expected {case.expect}, got {got}")

    for case in MANUAL_FLOW_CASES:
        driver = make_driver(state=case.state, **case.driver)
        dialogue_state = DialogueState(case.state)
        next_state = next_registration_state(driver, driver.vehicle)
        if next_state.value != case.expect_next:
            issues.append(
                f"{case.name}: next_state expected {case.expect_next!r}, got {next_state.value!r}"
            )
        if case.expect_expecting_doc is not None:
            expecting = is_expecting_data_document(driver, dialogue_state)
            if expecting != case.expect_expecting_doc:
                issues.append(
                    f"{case.name}: is_expecting_data_document expected {case.expect_expecting_doc}, got {expecting}"
                )

    return issues


def run_all() -> tuple[list[RunResult], list[RunResult]]:
    ai = AIService()
    passed: list[RunResult] = []
    failed: list[RunResult] = []

    for scenario in SCENARIOS:
        driver = make_driver(state=scenario.state, **scenario.driver)
        result = ai.respond(scenario.state, scenario.message, driver)
        issues = check_scenario(scenario, result)
        run = RunResult(scenario=scenario, result=result, issues=issues)
        if issues:
            failed.append(run)
        else:
            passed.append(run)

    return passed, failed


def print_report(passed: list[RunResult], failed: list[RunResult]) -> None:
    kb = load_knowledge_base()
    print("=" * 72)
    print("DIALOG SIMULATION REPORT")
    print(f"Total: {len(passed) + len(failed)} | Passed: {len(passed)} | Failed: {len(failed)}")
    print("=" * 72)

    for run in passed + failed:
        s = run.scenario
        r = run.result
        status = "OK" if not run.issues else "FAIL"
        print(f"\n[{status}] {s.name}")
        print(f"  state={s.state} | message={s.message!r}")
        print(f"  intent={r.intent} | next={r.next_state} | fields={json.dumps(r.extracted_fields, ensure_ascii=False)}")
        print(f"  summary={r.reasoning_summary}")
        if r.reply.strip():
            print(f"  reply: {truncate(r.reply)}")
            formatted = user_facing_reply(s.state, r)
            if formatted.strip() and formatted.strip() != r.reply.strip():
                print(f"  user sees: {truncate(formatted)}")
        else:
            print("  reply: (empty — engine uses next step prompt)")
        if run.issues:
            for issue in run.issues:
                print(f"  ! {issue}")

    print("\n" + "=" * 72)
    print("FAQ SPOT CHECKS (resolve_faq_replies)")
    faq_cases = [
        "Где офис и какие условия?",
        "880101300123 и где офис?",
        "Toyota и Camry",
        "Привет",
        "Можно PDF из eGov?",
        "Как правильно сфотографировать документ?",
        "Сколько процентов берете?",
        "Что получу за стаж?",
        "Хочу изменить модель",
    ]
    for msg in faq_cases:
        ans = resolve_faq_replies(msg, kb, office_address="Астана, Момышулы 18/1")
        print(f"\n  Q: {msg}")
        print(f"  A: {truncate(ans or '(none)')}")

    manual_issues = run_manual_entry_checks()
    print("\n" + "=" * 72)
    print("MANUAL DATA ENTRY CHECKS")
    print(f"Marker cases: {len(MANUAL_MARKER_CASES)} | Flow cases: {len(MANUAL_FLOW_CASES)}")
    if manual_issues:
        print(f"FAILED ({len(manual_issues)}):")
        for issue in manual_issues:
            print(f"  - {issue}")
    else:
        print("All manual entry checks passed.")

    if failed or manual_issues:
        print("\n" + "=" * 72)
        if failed:
            print(f"FAILED SCENARIOS ({len(failed)}):")
            for run in failed:
                print(f"  - {run.scenario.name}: {'; '.join(run.issues)}")
        sys.exit(1)

    print("\nAll scenarios passed.")


if __name__ == "__main__":
    passed, failed = run_all()
    print_report(passed, failed)
