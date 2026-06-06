import re
from datetime import datetime


PHONE_PATTERN = re.compile(r"\+?\d{10,15}")
IIN_PATTERN = re.compile(r"\b\d{12}\b")
YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
PLATE_PATTERN = re.compile(r"\b[A-ZА-Я0-9]{5,10}\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(\d{2})[.\-/](\d{2})[.\-/](\d{4})\b")


def normalize_text_token(value: str) -> str:
    normalized = value.strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", normalized)


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value)
    if not digits:
        return value.strip()
    return f"+{digits}"


def normalize_plate_number(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.strip()).upper()


def looks_like_phone(value: str) -> bool:
    return bool(PHONE_PATTERN.search(value))


def looks_like_iin(value: str) -> bool:
    return bool(IIN_PATTERN.search(value))


def parse_year(value: str) -> int | None:
    match = YEAR_PATTERN.search(value)
    if not match:
        return None
    year = int(match.group(0))
    current_year = datetime.utcnow().year + 1
    if 1980 <= year <= current_year:
        return year
    return None


def parse_date(value: str) -> str | None:
    match = DATE_PATTERN.search(value)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        parsed = datetime(int(year), int(month), int(day))
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def parse_yes_no(value: str) -> bool | None:
    normalized = normalize_text_token(value)
    yes_values = {
        "да",
        "yes",
        "y",
        "ага",
        "ок",
        "ok",
        "конечно",
        "есть",
        "имеется",
        "da",
        "aga",
        "konechno",
        "est",
        "imeetsya",
        "true",
    }
    no_values = {
        "нет",
        "no",
        "n",
        "неа",
        "отсутствует",
        "net",
        "nea",
        "false",
    }
    if normalized in yes_values:
        return True
    if normalized in no_values:
        return False
    return None


def parse_confirmation(value: str) -> bool:
    normalized = normalize_text_token(value)
    return normalized in {
        "подтверждаю",
        "подтверждаю данные",
        "все верно",
        "всё верно",
        "ok",
        "confirm",
        "confirmed",
        "podtverzhdayu",
        "podtverjdau",
        "podtverzhdaiu",
        "vse verno",
        "vsyo verno",
    }


def normalize_employment_type(value: str) -> str:
    normalized = normalize_text_token(value)
    mapping = {
        "штатный": "штатный",
        "shtatnyi": "штатный",
        "shtatniy": "штатный",
        "shtatnyy": "штатный",
        "shtatny": "штатный",
        "staff": "штатный",
        "employee": "штатный",
        "самозанятый": "самозанятый",
        "самозанятый водитель": "самозанятый",
        "samozanyatyi": "самозанятый",
        "samozanyatiy": "самозанятый",
        "samozanyatyy": "самозанятый",
        "self employed": "самозанятый",
        "self-employed": "самозанятый",
    }
    return mapping.get(normalized, value.strip())


def normalize_work_rule_id(value: str | None) -> str | None:
    if not value:
        return value
    return value.strip().split("?", 1)[0]


def split_full_name(value: str) -> tuple[str | None, str | None, str | None]:
    parts = [part for part in value.strip().split() if part]
    if not parts:
        return None, None, None
    last_name = parts[0] if len(parts) > 0 else None
    first_name = parts[1] if len(parts) > 1 else None
    middle_name = " ".join(parts[2:]) if len(parts) > 2 else None
    return last_name, first_name, middle_name
