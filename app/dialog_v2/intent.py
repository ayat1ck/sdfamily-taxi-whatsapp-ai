from __future__ import annotations

from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


def normalize_intent_text(text: str | None) -> str:
    return normalize_text_token(repair_mojibake(text or ""))


def looks_like_existing_driver(text: str | None) -> bool:
    normalized = normalize_intent_text(text)
    return any(
        token in normalized
        for token in (
            "я уже водитель",
            "я уже подключен",
            "я уже водитель",
            "я уже подключен",
            "я уже подключён",
            "men tirkelgenmin",
            "men tirkelgenmin",
            "уже подключен",
            "уже водитель",
        )
    )


def looks_like_profile_update(text: str | None) -> bool:
    normalized = normalize_intent_text(text)
    return any(
        token in normalized
        for token in (
            "поменять машину",
            "изменить данные",
            "поменять номер",
            "изменить авто",
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
    )


def looks_like_support_escalation(text: str | None) -> bool:
    normalized = normalize_intent_text(text)
    return any(
        token in normalized
        for token in (
            "не могу вывести деньги",
            "оператор",
            "менеджер",
            "не могу вывести деньги",
            "не выводятся деньги",
            "оператор",
            "менеджер",
            "блокировк",
            "жалоб",
            "смз",
            "тариф",
            "яндекс",
            "деньги",
        )
    )


def looks_like_faq(text: str | None) -> bool:
    normalized = normalize_intent_text(text)
    return any(
        token in normalized
        for token in (
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
        )
    )
