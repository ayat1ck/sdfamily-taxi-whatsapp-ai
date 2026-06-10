import re
from datetime import datetime


PHONE_PATTERN = re.compile(r"\+?\d{10,15}")
IIN_PATTERN = re.compile(r"\b\d{12}\b")
YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
PLATE_PATTERN = re.compile(r"\b[A-ZА-Я0-9]{5,10}\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(\d{2})[.\-/](\d{2})[.\-/](\d{4})\b")

CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

CAR_BRAND_ALIASES = {
    "мерседес": "Mercedes-Benz",
    "мерс": "Mercedes-Benz",
    "мерседес бенц": "Mercedes-Benz",
    "мерседес-бенц": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "бмв": "BMW",
    "bmw": "BMW",
    "тойота": "Toyota",
    "toyota": "Toyota",
    "хундай": "Hyundai",
    "хендай": "Hyundai",
    "hyundai": "Hyundai",
    "киа": "Kia",
    "kia": "Kia",
    "лада": "Lada",
    "ваз": "Lada",
    "lada": "Lada",
    "лексус": "Lexus",
    "lexus": "Lexus",
    "ниссан": "Nissan",
    "nissan": "Nissan",
    "шевроле": "Chevrolet",
    "chevrolet": "Chevrolet",
    "шкода": "Skoda",
    "skoda": "Skoda",
    "фольксваген": "Volkswagen",
    "volkswagen": "Volkswagen",
    "ауди": "Audi",
    "audi": "Audi",
    "хонда": "Honda",
    "honda": "Honda",
    "мазда": "Mazda",
    "mazda": "Mazda",
    "рено": "Renault",
    "renault": "Renault",
    "джили": "Geely",
    "geely": "Geely",
    "хавал": "Haval",
    "haval": "Haval",
    "чанган": "Changan",
    "changan": "Changan",
    "черри": "Chery",
    "chery": "Chery",
    "омода": "Omoda",
    "omoda": "Omoda",
    "джейку": "Jaecoo",
    "jaecoo": "Jaecoo",
    "эксид": "Exeed",
    "exeed": "Exeed",
    "джетур": "Jetour",
    "jetour": "Jetour",
    "танк": "Tank",
    "tank": "Tank",
    "лисян": "Li Auto",
    "li auto": "Li Auto",
    "бид": "BYD",
    "byd": "BYD",
    "зикр": "Zeekr",
    "zeekr": "Zeekr",
    "гак": "GAC",
    "gac": "GAC",
    "джак": "JAC",
    "jac": "JAC",
    "донгфенг": "Dongfeng",
    "dongfeng": "Dongfeng",
    "войя": "Voyah",
    "voyah": "Voyah",
    "хончи": "Hongqi",
    "hongqi": "Hongqi",
    "фау": "FAW",
    "faw": "FAW",
    "грейт вол": "Great Wall",
    "great wall": "Great Wall",
    "эм джи": "MG",
    "mg": "MG",
    "равон": "Ravon",
    "ravon": "Ravon",
    "инфинити": "Infiniti",
    "infiniti": "Infiniti",
    "порше": "Porsche",
    "porsche": "Porsche",
    "вольво": "Volvo",
    "volvo": "Volvo",
    "ленд ровер": "Land Rover",
    "land rover": "Land Rover",
    "рэндж ровер": "Range Rover",
    "range rover": "Range Rover",
    "субару": "Subaru",
    "subaru": "Subaru",
    "сузуки": "Suzuki",
    "suzuki": "Suzuki",
    "митсубиси": "Mitsubishi",
    "mitsubishi": "Mitsubishi",
}

CAR_MODEL_ALIASES = {
    "с класс": "S-Class",
    "ц класс": "C-Class",
    "с-класс": "S-Class",
    "ц-класс": "C-Class",
    "c class": "C-Class",
    "c-class": "C-Class",
    "е класс": "E-Class",
    "е-класс": "E-Class",
    "e class": "E-Class",
    "e-class": "E-Class",
    "эс класс": "S-Class",
    "эс-класс": "S-Class",
    "s class": "S-Class",
    "s-class": "S-Class",
    "s klasse": "S-Class",
    "s-klasse": "S-Class",
    "эска": "S-Class",
    "г класс": "G-Class",
    "г-класс": "G-Class",
    "g class": "G-Class",
    "g-class": "G-Class",
    "гелик": "G-Class",
    "камри": "Camry",
    "camry": "Camry",
    "королла": "Corolla",
    "corolla": "Corolla",
    "авенсис": "Avensis",
    "avensis": "Avensis",
    "элантра": "Elantra",
    "elantra": "Elantra",
    "солярис": "Solaris",
    "solaris": "Solaris",
    "соната": "Sonata",
    "sonata": "Sonata",
    "к5": "K5",
    "k5": "K5",
    "рио": "Rio",
    "rio": "Rio",
    "церато": "Cerato",
    "cerato": "Cerato",
    "приора": "Priora",
    "гранта": "Granta",
    "granta": "Granta",
    "веста": "Vesta",
    "vesta": "Vesta",
    "джолион": "Jolion",
    "jolion": "Jolion",
    "дарго": "Dargo",
    "dargo": "Dargo",
    "f7": "F7",
    "f7x": "F7x",
    "h5": "H5",
    "h6": "H6",
    "uni k": "Uni-K",
    "uni-k": "Uni-K",
    "уни кей": "Uni-K",
    "uni t": "Uni-T",
    "uni-t": "Uni-T",
    "уни ти": "Uni-T",
    "alsvin": "Alsvin",
    "алсвин": "Alsvin",
    "cs35": "CS35",
    "cs55": "CS55",
    "cs55 plus": "CS55 Plus",
    "cs75": "CS75",
    "тигго 7": "Tiggo 7",
    "тигго 8": "Tiggo 8",
    "tiggo 7": "Tiggo 7",
    "tiggo 8": "Tiggo 8",
    "arrizo 8": "Arrizo 8",
    "арризо 8": "Arrizo 8",
    "c5": "C5",
    "s5": "S5",
    "j7": "J7",
    "t2": "T2",
    "txl": "TXL",
    "lx": "LX",
    "rx": "RX",
    "монжаро": "Monjaro",
    "monjaro": "Monjaro",
    "кулрей": "Coolray",
    "coolray": "Coolray",
    "эмгранд": "Emgrand",
    "emgrand": "Emgrand",
    "атлас": "Atlas",
    "atlas": "Atlas",
    "okavango": "Okavango",
    "окаванго": "Okavango",
}

# Mercedes-Benz chassis codes (W/V) → catalog model names accepted by Yandex Fleet.
CAR_CHASSIS_ALIASES = {
    "w140": "S-Class",
    "w220": "S-Class",
    "w221": "S-Class",
    "w222": "S-Class",
    "w223": "S-Class",
    "w204": "C-Class",
    "w205": "C-Class",
    "w206": "C-Class",
    "w210": "E-Class",
    "w211": "E-Class",
    "w212": "E-Class",
    "w213": "E-Class",
    "w214": "E-Class",
    "w245": "B-Class",
    "w246": "B-Class",
    "w247": "B-Class",
    "w176": "A-Class",
    "w177": "A-Class",
    "w463": "G-Class",
    "w464": "G-Class",
    "w461": "G-Class",
    "w164": "GLE",
    "w166": "GLE",
    "w167": "GLE",
    "w253": "GLC",
    "x253": "GLC",
    "w251": "R-Class",
    "v447": "V-Class",
    "vito": "Vito",
    "sprinter": "Sprinter",
}


def _normalize_chassis_key(value: str) -> str:
    return re.sub(r"[\s._-]+", "", normalize_text_token(value).lower())


def resolve_car_chassis_model(value: str) -> str | None:
    key = _normalize_chassis_key(value)
    if not key:
        return None
    if key in CAR_CHASSIS_ALIASES:
        return CAR_CHASSIS_ALIASES[key]
    match = re.fullmatch(r"([wvx]\d{3})[a-z]?", key)
    if match:
        return CAR_CHASSIS_ALIASES.get(match.group(1))
    return None


def _car_model_suffix_needs_clarification(parts: list[str]) -> bool:
    for part in parts:
        if resolve_car_chassis_model(part):
            return True
        if re.fullmatch(r"[wvx]\d{3}[a-z]?", part):
            return True
        if re.fullmatch(r"\d{1,3}[a-z]?", part):
            return True
    return False


def detect_car_model_clarification(value: str) -> str | None:
    cleaned = normalize_text_token(value)
    if not cleaned:
        return None
    if cleaned in CAR_MODEL_ALIASES:
        return None

    chassis = resolve_car_chassis_model(cleaned)
    if chassis and normalize_text_token(chassis) != cleaned:
        return chassis

    parts = cleaned.split()
    if len(parts) >= 2:
        if len(parts) == 2 and resolve_car_chassis_model(parts[1]):
            return resolve_car_chassis_model(parts[1])

        for prefix_len in range(len(parts), 0, -1):
            prefix = " ".join(parts[:prefix_len])
            suffix = parts[prefix_len:]
            if prefix not in CAR_MODEL_ALIASES:
                continue
            if suffix and _car_model_suffix_needs_clarification(suffix):
                return CAR_MODEL_ALIASES[prefix]
            return None

        first = parts[0]
        if first in CAR_MODEL_ALIASES and _car_model_suffix_needs_clarification(parts[1:]):
            return CAR_MODEL_ALIASES[first]

    return None


def build_car_model_clarification_message(original: str, suggested: str) -> str:
    return (
        f"Похоже, вы указали «{original.strip()}». "
        f"Для регистрации нужна модель из документов — без кода кузова или поколения. "
        f"Вы имели в виду {suggested}? Напишите «{suggested}» или «да»."
    )


def normalize_driver_license_number(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip().upper())
    if not text:
        return ""

    compact = re.sub(r"\s+", "", text)
    letter_match = re.fullmatch(r"([A-Z]{2})(\d{6,8})", compact)
    if letter_match:
        return f"{letter_match.group(1)} {letter_match.group(2)}"

    parts = text.split()
    if len(parts) == 2:
        left, right = parts
        if re.fullmatch(r"[A-Z]{2}", left) and re.fullmatch(r"\d{6,8}", right):
            return f"{left} {right}"
        if re.fullmatch(r"\d{4,6}", left) and re.fullmatch(r"\d{6,8}", right):
            return f"{left} {right}"

    return compact


def validate_driver_license_number(value: str) -> list[str]:
    normalized = normalize_driver_license_number(value)
    if not normalized:
        return ["invalid_license_number"]

    compact = normalized.replace(" ", "")
    if re.fullmatch(r"[A-Z]{2}\d{6,8}", compact):
        return []

    parts = normalized.split()
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        if 10 <= sum(len(part) for part in parts) <= 14:
            return []

    if 4 <= len(compact) <= 20 and compact.isalnum():
        return []

    return ["invalid_license_number_format"]


def normalize_text_token(value: str) -> str:
    normalized = value.strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", normalized)


def transliterate_cyrillic_to_latin(value: str) -> str:
    result: list[str] = []
    for char in value:
        lower = char.lower()
        if lower in CYRILLIC_TO_LATIN:
            latin = CYRILLIC_TO_LATIN[lower]
            if char.isupper() and latin:
                result.append(latin[0].upper() + latin[1:])
            else:
                result.append(latin)
        else:
            result.append(char)
    return re.sub(r"\s+", " ", "".join(result)).strip()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value)
    if not digits:
        return value.strip()
    return f"+{digits}"


def normalize_plate_number(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.strip()).upper()


REGISTRATION_CERTIFICATE_PATTERN = re.compile(r"^[A-Z0-9А-ЯЁ]{5,20}$", re.IGNORECASE)


def normalize_registration_certificate(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.strip()).upper()


def looks_like_registration_certificate(value: str) -> bool:
    cleaned = normalize_registration_certificate(value)
    return bool(REGISTRATION_CERTIFICATE_PATTERN.match(cleaned))


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


def parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


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
        "ип": "ип",
        "individual entrepreneur": "ип",
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


def normalize_car_brand(value: str) -> str:
    cleaned = normalize_text_token(value)
    alias = CAR_BRAND_ALIASES.get(cleaned)
    if alias:
        return alias
    transliterated = transliterate_cyrillic_to_latin(value)
    if not transliterated:
        return value.strip()
    return " ".join(part.capitalize() if not part.isupper() else part for part in transliterated.split())


def normalize_car_model(value: str) -> str:
    cleaned = normalize_text_token(value)
    for key in sorted(CAR_BRAND_ALIASES.keys(), key=len, reverse=True):
        if cleaned.startswith(f"{key} "):
            cleaned = cleaned[len(key) + 1 :].strip()
            break
    chassis = resolve_car_chassis_model(cleaned)
    if chassis:
        return chassis
    alias = CAR_MODEL_ALIASES.get(cleaned)
    if alias:
        return alias
    transliterated = transliterate_cyrillic_to_latin(cleaned)
    if not transliterated:
        return value.strip()
    normalized = transliterated.replace(" Class", "-Class").replace(" class", "-Class")
    return " ".join(
        part.capitalize() if re.search(r"[A-Za-z]", part) and not part.isupper() and not re.search(r"\d", part) else part
        for part in normalized.split()
    )


def extract_known_car_brand(value: str) -> str | None:
    cleaned = normalize_text_token(value)
    if cleaned in CAR_BRAND_ALIASES:
        return CAR_BRAND_ALIASES[cleaned]
    for key, brand in sorted(CAR_BRAND_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if cleaned == key:
            return brand
        if cleaned.startswith(f"{key} ") or cleaned.endswith(f" {key}") or f" {key} " in cleaned:
            return brand
    return None


def looks_like_precise_car_model(value: str) -> bool:
    cleaned = normalize_text_token(value)
    for key in sorted(CAR_BRAND_ALIASES.keys(), key=len, reverse=True):
        if cleaned.startswith(f"{key} "):
            cleaned = cleaned[len(key) + 1 :].strip()
            break
    if cleaned in CAR_MODEL_ALIASES:
        return True
    if resolve_car_chassis_model(cleaned):
        return True
    if len(cleaned) < 2:
        return False
    if len(cleaned.split()) >= 5:
        return False
    return bool(re.search(r"[a-zA-Zа-яА-Я]", value))


def validate_kz_iin(value: str) -> list[str]:
    digits = re.sub(r"\D+", "", value)
    if len(digits) != 12:
        return ["invalid_iin_length"]
    try:
        datetime.strptime(digits[:6], "%y%m%d")
    except ValueError:
        return ["invalid_iin_birth_date"]
    return []


def validate_birth_date(value: str) -> list[str]:
    parsed = parse_iso_date(value)
    if not parsed:
        return ["invalid_birth_date"]
    now = datetime.utcnow()
    if parsed > now:
        return ["birth_date_in_future"]
    age = now.year - parsed.year - ((now.month, now.day) < (parsed.month, parsed.day))
    if age < 18:
        return ["driver_underage"]
    if age > 80:
        return ["driver_age_too_high"]
    return []


def validate_hired_at(value: str) -> list[str]:
    parsed = parse_iso_date(value)
    if not parsed:
        return ["invalid_hired_at"]
    if parsed > datetime.utcnow():
        return ["hired_at_in_future"]
    return []


def validate_driver_dates(
    *,
    birth_date: str | None = None,
    driving_experience_since: str | None = None,
    driver_license_issue_date: str | None = None,
    driver_license_expires_at: str | None = None,
) -> list[str]:
    errors: list[str] = []
    birth = parse_iso_date(birth_date)
    experience = parse_iso_date(driving_experience_since)
    issue = parse_iso_date(driver_license_issue_date)
    expires = parse_iso_date(driver_license_expires_at)
    now = datetime.utcnow()

    if experience:
        if experience > now:
            errors.append("driving_experience_in_future")
        if birth and experience < birth:
            errors.append("driving_experience_before_birth")
        if birth and (experience.year - birth.year - ((experience.month, experience.day) < (birth.month, birth.day))) < 16:
            errors.append("driving_experience_too_early")

    if issue:
        if issue > now:
            errors.append("license_issue_in_future")
        if birth and issue < birth:
            errors.append("license_issue_before_birth")
        if birth and (issue.year - birth.year - ((issue.month, issue.day) < (birth.month, birth.day))) < 16:
            errors.append("license_issue_too_early")

    if expires:
        if issue and expires <= issue:
            errors.append("license_expires_before_issue")
        if expires < now:
            errors.append("license_expired")

    return errors
