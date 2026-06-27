from __future__ import annotations

USER_ERROR_MESSAGES: dict[str, str] = {
    "missing_last_name": "Не указана фамилия.",
    "missing_first_name": "Не указано имя.",
    "missing_phone": "Не указан контактный телефон.",
    "missing_address": "Не указан адрес.",
    "missing_iin": "Не указан ИИН.",
    "missing_birth_date": "Не указана дата рождения.",
    "missing_driving_experience_since": "Не указана дата начала водительского стажа.",
    "missing_driver_license_number": "Не указан номер водительского удостоверения.",
    "missing_driver_license_issue_date": "Не указана дата выдачи водительского удостоверения.",
    "missing_driver_license_expires_at": "Не указан срок действия водительского удостоверения.",
    "missing_employment_type": "Не указано условие работы.",
    "missing_hired_at": "Не указана дата принятия в парк.",
    "missing_car_brand": "Не указана марка автомобиля.",
    "missing_car_model": "Не указана модель автомобиля.",
    "missing_car_year": "Не указан год выпуска автомобиля.",
    "missing_plate_number": "Не указан госномер автомобиля.",
    "missing_color": "Не указан цвет автомобиля.",
    "missing_registration_certificate": "Не указан номер СТС.",
    "invalid_iin_length": "ИИН должен содержать 12 цифр.",
    "invalid_iin_birth_date": "ИИН содержит некорректную дату рождения.",
    "invalid_birth_date": "Дата рождения указана некорректно.",
    "birth_date_in_future": "Дата рождения не может быть в будущем.",
    "driver_underage": "Возраст по дате рождения меньше 18 лет.",
    "driver_age_too_high": "Проверьте дату рождения — возраст выглядит некорректно.",
    "driving_experience_before_birth_date": "Дата начала стажа не может быть раньше даты рождения.",
    "driving_experience_before_birth": "Дата начала стажа не может быть раньше даты рождения.",
    "driving_experience_too_early": "Дата начала стажа слишком ранняя. Укажите, когда вы реально начали водить, а не дату рождения.",
    "driving_experience_same_as_birth": "Дата начала стажа совпадает с датой рождения. Укажите дату из водительского удостоверения.",
    "driving_experience_in_future": "Дата начала стажа не может быть в будущем.",
    "license_issue_before_birth": "Дата выдачи прав не может быть раньше даты рождения.",
    "license_issue_too_early": "Дата выдачи прав выглядит слишком ранней.",
    "license_issue_in_future": "Дата выдачи прав не может быть в будущем.",
    "license_expires_before_issue": "Срок действия прав не может быть раньше даты выдачи.",
    "license_expired": "Срок действия прав уже истёк.",
    "driver_license_expires_at_before_issue_date": "Срок действия прав не может быть раньше даты выдачи.",
    "hired_at_in_future": "Дата принятия не может быть в будущем. Обычно указывают дату подключения к парку или сегодняшнюю дату.",
    "hired_at_same_as_license_expiry": "Дата принятия совпадает со сроком действия прав. Укажите дату подключения к парку, а не «действует до».",
    "invalid_hired_at": "Дата принятия указана некорректно.",
    "invalid_license_number_format": "Номер водительского удостоверения указан некорректно. Проверьте серию и номер, как в документе.",
    "car_brand_not_in_catalog": "Марка автомобиля не найдена в справочнике парка.",
    "car_model_not_in_catalog": "Модель автомобиля не найдена в справочнике парка. Укажите модель из документов, например Camry или S-Class.",
    "car_brand_model_not_in_catalog": "Марка и модель автомобиля не найдены в справочнике парка.",
    "duplicate_phone": "Этот номер телефона уже зарегистрирован в парке. Если вы уже подключались раньше, напишите менеджеру — поможем восстановить доступ.",
    "invalid_driver_license": "Номер водительского удостоверения не принят системой парка. Проверьте серию и номер, как в документе.",
    "invalid_car_brand": "Марка автомобиля не принята системой парка. Укажите марку как в Яндекс Про или документах, например LADA, Toyota или Hyundai.",
    "invalid_car_model": "Модель автомобиля не принята системой парка. Укажите модель из документов без лишних цифр и кодов кузова.",
}


def _normalize_error_code(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("invalid:"):
        return cleaned.removeprefix("invalid:")
    if cleaned.startswith("missing:"):
        return f"missing_{cleaned.removeprefix('missing:')}"
    return cleaned


def format_validation_error_code(code: str) -> str:
    normalized = _normalize_error_code(code)
    if normalized in USER_ERROR_MESSAGES:
        return USER_ERROR_MESSAGES[normalized]
    if normalized.startswith("invalid:car_model_not_in_catalog"):
        return USER_ERROR_MESSAGES["car_model_not_in_catalog"]
    if normalized.startswith("invalid:car_brand_not_in_catalog"):
        return USER_ERROR_MESSAGES["car_brand_not_in_catalog"]
    return code


def format_validation_errors_for_user(errors: list[str]) -> str:
    if not errors:
        return ""
    unique: list[str] = []
    seen: set[str] = set()
    for error in errors:
        message = format_validation_error_code(error)
        if message not in seen:
            seen.add(message)
            unique.append(message)
    return "\n".join(f"• {message}" for message in unique)


def format_yandex_error_for_user(raw_error: str | None) -> str:
    if not raw_error:
        return "При отправке заявки возникла техническая ошибка. Менеджер проверит заявку."

    lower = raw_error.lower()
    if "yandex payload validation failed:" in lower:
        tail = raw_error.split("failed:", 1)[-1]
        codes = [code.strip() for code in tail.split(";") if code.strip()]
        if codes:
            return format_validation_errors_for_user(codes)

    for marker, message in USER_ERROR_MESSAGES.items():
        if marker.replace("_", " ") in lower or marker in lower:
            return message

    if "duplicate_phone" in lower or "phone already exists" in lower:
        return USER_ERROR_MESSAGES["duplicate_phone"]
    if "invalid_car_brand" in lower or "brand" in lower and "does not exist" in lower:
        return USER_ERROR_MESSAGES["invalid_car_brand"]
    if "invalid_car_model" in lower or "model" in lower and "does not exist" in lower:
        return USER_ERROR_MESSAGES["invalid_car_model"]
    if "invalid_driver_license" in lower:
        return USER_ERROR_MESSAGES["invalid_driver_license"]

    return (
        "При отправке заявки возникла ошибка на стороне парка. "
        "Проверьте данные или напишите менеджеру, если проблема повторяется."
    )


def build_yandex_error_reply(raw_error: str | None) -> str:
    details = format_yandex_error_for_user(raw_error)
    return (
        "Не удалось автоматически отправить заявку. Данные сохранены.\n\n"
        f"{details}\n\n"
        "Исправьте данные и напишите «Подтверждаю» для повторной отправки."
    )
