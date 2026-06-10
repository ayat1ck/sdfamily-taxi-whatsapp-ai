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
    Scenario("ASK_PHONE: алло mid-flow", DialogueState.ASK_PHONE.value, "алло", expect_intent="help", must_contain=("Здравствуйте", "телефон"), must_not_contain=(office_marker(),)),
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
    Scenario("ASK_PHONE: зачем полный", DialogueState.ASK_PHONE.value, "Зачем тебе мой номер телефона?", expect_intent="help", must_contain=("Яндекс Про", "телефон"), must_not_contain=(office_marker(), "ИИН")),
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
    Scenario("ASK_CAR_MODEL: w221", DialogueState.ASK_CAR_MODEL.value, "w221", driver={"vehicle": SimpleNamespace(brand="Mercedes")}, expect_intent="clarification"),
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
    Scenario("ASK_IIN: другой вопрос", DialogueState.ASK_IIN.value, "А можно по другому вопросу?", expect_intent="help", must_contain=("условия", "офис")),
    Scenario("ASK_CAR_MODEL: Camry 35", DialogueState.ASK_CAR_MODEL.value, "Camry 35", driver={"vehicle": SimpleNamespace(brand="Toyota")}),
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
    ]
    for msg in faq_cases:
        ans = resolve_faq_replies(msg, kb, office_address="Астана, Момышулы 18/1")
        print(f"\n  Q: {msg}")
        print(f"  A: {truncate(ans or '(none)')}")

    if failed:
        print("\n" + "=" * 72)
        print(f"FAILED SCENARIOS ({len(failed)}):")
        for run in failed:
            print(f"  - {run.scenario.name}: {'; '.join(run.issues)}")
        sys.exit(1)

    print("\nAll scenarios passed.")


if __name__ == "__main__":
    passed, failed = run_all()
    print_report(passed, failed)
