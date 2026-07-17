from __future__ import annotations

import re

from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


def normalize_intent_text(text: str | None) -> str:
    return normalize_text_token(repair_mojibake(text or ""))


def _has_token(normalized: str, tokens: tuple[str, ...]) -> bool:
    """Match tokens at word starts (stems), multi-word tokens as substrings."""
    if not normalized:
        return False
    for token in tokens:
        if " " in token:
            if token in normalized:
                return True
        elif re.search(rf"(?:^|[^\w]){re.escape(token)}", normalized):
            return True
    return False


EXISTING_DRIVER_TOKENS = (
    "я уже водитель",
    "я уже подключен",
    "я уже подключён",
    "men tirkelgenmin",
    "уже подключен",
    "уже водитель",
    "мен тіркелгенмін",
    "тіркелгенмін",
    "тіркелген",
    "тиркелген",
)

PROFILE_UPDATE_TOKENS = (
    "поменять машину",
    "изменить данные",
    "поменять номер",
    "изменить авто",
    "заменить стс",
    "заменить права",
    "сменить смз",
    "хочу поменять",
    "изменить фио",
)

SUPPORT_TOKENS = (
    "не могу вывести",
    "не выводятся",
    "не снимается",
    "не снимают",
    "снять ден",
    "снять деньг",
    "снять средств",
    "вывести ден",
    "вывести деньг",
    "вывод средств",
    "вывод ден",
    "оператор",
    "менеджер",
    "блокировк",
    "жалоб",
    "смз",
    "тариф",
    "яндекс",
    "деньги",
    "денги",
    "ақша",
    "акша",
    "төлем",
    "толем",
)

FAQ_TOKENS = (
    "кто вы",
    "кто ты",
    "кто такие",
    "что за парк",
    "что за компания",
    "о вас",
    "вы таксопарк",
    "таксопарк",
    "какие условия",
    "условия",
    "комиссия",
    "процент",
    "бонус",
    "бонусы",
    "байге",
    "адрес",
    "офис",
    "документы",
    "как зарегистрироваться",
    "регистрация",
    "аренда",
    "аренд",
    "свои машины",
    "своя машина",
    "своего авто",
    "своим авто",
    "без авто",
    "без машин",
    "взять машин",
    "есть ли машин",
    "машины или",
    "какие авто",
    "какое авто",
    "какие машины",
    "туман",
    "тұман",
)

FRUSTRATION_TOKENS = (
    "не работает",
    "не понимаю",
    "не понял",
    "не поняла",
    "непонятно",
    "ничего не понятно",
    "вы бот",
    "ты бот",
    "тупой бот",
    "робот",
    "сколько можно",
    "надоело",
    "достали",
    "достал",
    "бесит",
    "ерунд",
    "бред",
    "не то отвеча",
    "отвечаете не то",
    "не помогает",
    "не помогло",
    "одно и то же",
    "опять то же",
    "по кругу",
    "хватит",
    "türsinbedim",
    "түсінбедім",
    "тусінбедім",
    "тусинбедим",
    "түсінбеймін",
    "тусинбеймин",
    "жауап бермейд",
    "неге жауап",
)


def looks_like_existing_driver(text: str | None) -> bool:
    return _has_token(normalize_intent_text(text), EXISTING_DRIVER_TOKENS)


def looks_like_profile_update(text: str | None) -> bool:
    return _has_token(normalize_intent_text(text), PROFILE_UPDATE_TOKENS)


def looks_like_support_escalation(text: str | None) -> bool:
    return _has_token(normalize_intent_text(text), SUPPORT_TOKENS)


def looks_like_faq(text: str | None) -> bool:
    return _has_token(normalize_intent_text(text), FAQ_TOKENS)


def looks_like_frustration(text: str | None) -> bool:
    return _has_token(normalize_intent_text(text), FRUSTRATION_TOKENS)
