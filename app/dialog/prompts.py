from app.dialog.states import DialogueState


OFFICE_HOURS = "🕘 Ежедневно с 09:00 до 18:00"

WELCOME_GREETING = (
    "Здравствуйте! 👋\n\n"
    "SD Family Taxi — надежный таксопарк с выгодными условиями:\n"
    "✅ Комиссия парка — 2%\n"
    "✅ Моментальные выплаты 24/7\n"
    "✅ Круглосуточная поддержка\n"
    "✅ Бонусы для водителей\n"
    "✅ Бесплатный сухой туман для постоянных водителей"
)

REGISTRATION_START_CTA = "✍️ Для подключения напишите ваше ФИО полностью."

DOCUMENT_PHOTO_QUALITY_HINT = (
    "💡 Для фото: положите документ на ровную поверхность, снимайте при ярком освещении — "
    "весь документ в кадре, без бликов и засветов."
)

MANUAL_ENTRY_HINT = (
    "✍️ Отправьте фото или PDF документа — бот заполнит данные автоматически."
)

CAR_MODEL_PROMPT = (
    "🚘 Напишите модель автомобиля, как в документах или техпаспорте "
    "(Camry, Rio, S-Class, X5 и т.п.). "
    "Указывайте название модели, а не код кузова (w221, e90 и подобное)."
)


PROMPTS: dict[DialogueState, str] = {
    DialogueState.NEW: f"{WELCOME_GREETING}\n\n{REGISTRATION_START_CTA}",
    DialogueState.ASK_FULL_NAME: "✍️ Напишите ваше ФИО полностью.",
    DialogueState.ASK_EXECUTOR_TYPE: "📋 Переходим к анкете. Укажите контактный номер телефона.",
    DialogueState.ASK_PHONE: "📱 Укажите ваш контактный номер телефона.",
    DialogueState.ASK_CITY: "🏙 Напишите город, в котором будете работать.",
    DialogueState.ASK_ADDRESS: "📍 Укажите адрес проживания или регистрации (например: пр. Республики 12, Астана).",
    DialogueState.ASK_IIN: "🪪 Укажите ИИН из 12 цифр.",
    DialogueState.ASK_BIRTH_DATE: "📅 Укажите дату рождения в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: (
        "📅 Укажите дату начала водительского стажа в формате ДД.ММ.ГГГГ. "
        "Это не дата рождения — возьмите дату из водительского удостоверения."
    ),
    DialogueState.ASK_HAS_CAR: "🚘 Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER: "🚘 Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_CAR_BRAND: "🚘 Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_CAR_MODEL: CAR_MODEL_PROMPT,
    DialogueState.ASK_CAR_YEAR: "📅 Укажите год выпуска автомобиля.",
    DialogueState.ASK_CAR_PLATE: "🔢 Укажите госномер автомобиля.",
    DialogueState.ASK_CAR_COLOR: "🎨 Укажите цвет автомобиля.",
    DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "📄 Укажите номер техпаспорта (СТС) автомобиля, как в документе.",
    DialogueState.ASK_DRIVER_LICENSE_NUMBER: (
        "🪪 Напишите серию и номер водительского удостоверения, как в документе "
        "(например CQ 981709). Серию и номер можно через пробел."
    ),
    DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "📅 Укажите дату выдачи водительского удостоверения в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "📅 Укажите срок действия водительского удостоверения до даты в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_EMPLOYMENT_TYPE: "💼 Укажите условие работы: штатный, самозанятый или другое согласованное с парком условие.",
    DialogueState.ASK_HIRED_AT: (
        "📅 Укажите дату принятия в парк в формате ДД.ММ.ГГГГ. "
        "Обычно это сегодняшняя дата или день подключения — не путайте со сроком действия прав."
    ),
    DialogueState.ASK_HEARING_IMPAIRED: "❓ Вы являетесь слабослышащим водителем? Ответьте: да или нет.",
    DialogueState.ASK_DRIVER_LICENSE_FRONT: (
        "📸 Отправьте водительское удостоверение:\n"
        "• PDF из eGov или Kaspi (обе стороны на одной странице) — один файл\n"
        "• или 2 фото: лицевая и обратная стороны\n\n"
        f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
        f"{MANUAL_ENTRY_HINT}\n\n"
        "Бот распознает ФИО, ИИН, даты и номер прав."
    ),
    DialogueState.ASK_DRIVER_LICENSE_BACK: (
        "📸 Отправьте обратную сторону водительского удостоверения.\n"
        "Или PDF из eGov/Kaspi с обеими сторонами на одной странице.\n\n"
        f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
        f"{MANUAL_ENTRY_HINT}"
    ),
    DialogueState.ASK_ID_CARD: (
        "📸 Отправьте удостоверение личности — фото или PDF из eGov/Kaspi.\n\n"
        f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
        f"{MANUAL_ENTRY_HINT}"
    ),
    DialogueState.ASK_VEHICLE_REGISTRATION_DOC: (
        "📸 Отправьте техпаспорт или СТС — фото или PDF из eGov/Kaspi.\n"
        "Распознаем марку, модель, госномер и номер СТС.\n\n"
        f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
        f"{MANUAL_ENTRY_HINT}"
    ),
    DialogueState.ASK_SELFIE_WITH_LICENSE: (
        "📸 Отправьте селфи с водительским удостоверением в руке.\n"
        "Лицо и документ должны быть четко видны, без бликов."
    ),
    DialogueState.ASK_RENT_OR_POWER_OF_ATTORNEY: "✅ Принял. Проверьте введенные данные.",
    DialogueState.CONFIRM_DATA: (
        "📋 Проверьте собранные данные. "
        "Если всё верно — напишите «Подтверждаю». "
        "Если нужно исправить — напишите, что изменить."
    ),
    DialogueState.READY_TO_SEND_YANDEX: "✅ Спасибо, данные собраны. Отправляю заявку на регистрацию в таксопарк…",
    DialogueState.SENT_TO_YANDEX: "✅ Готово! Заявка отправлена в систему таксопарка.",
    DialogueState.ASK_YANDEX_PRO_LOGIN: "📱 Теперь нужно завершить вход в Яндекс Про.",
    DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS: "❓ Опишите, что именно не получается при входе в Яндекс Про.",
    DialogueState.YANDEX_ERROR: (
        "⚠️ Не удалось автоматически отправить заявку. Данные сохранены. "
        "Если ошибка исправлена — напишите «Подтверждаю» для повторной отправки."
    ),
    DialogueState.COMPLETED: (
        "🎉 Регистрация завершена! Пишите сюда, если нужна помощь.\n"
        "🎁 После регистрации можно приехать в офис и забрать приветственный бонус.\n"
        "В бокс входят: зарядка 3 в 1, держатель для телефона, салфетка и тряпка.\n"
        "Для бизнес-класса дополнительно выдаем блок воды.\n"
        "📍 Офис: Астана, Балкантау 117\n"
        f"{OFFICE_HOURS}"
    ),
}


DOCUMENT_STATE_MAP = {
    DialogueState.ASK_DRIVER_LICENSE_FRONT: "driver_license_front",
    DialogueState.ASK_DRIVER_LICENSE_BACK: "driver_license_back",
    DialogueState.ASK_ID_CARD: "id_card",
    DialogueState.ASK_VEHICLE_REGISTRATION_DOC: "vehicle_registration_doc",
    DialogueState.ASK_SELFIE_WITH_LICENSE: "selfie_with_license",
}


STATUS_REPLIES = {
    "waiting_documents": "📸 Заявка ждет документы. Отправьте следующий запрошенный файл.",
    "confirming_data": "📋 Заявка собрана — ждем вашего подтверждения.",
    "ready_to_send_yandex": "✅ Заявка готова к отправке в парк.",
    "sending_to_yandex": "⏳ Заявка отправляется в систему парка…",
    "sent_to_yandex": (
        "✅ Заявка отправлена в парк!\n"
        "📱 Завершите вход в Яндекс Про.\n"
        "Вошли — напишите: Вошел\n"
        "Ошибка — напишите: Ошибка"
    ),
    "completed": PROMPTS[DialogueState.COMPLETED],
    "awaiting_manager_review": "👨‍💼 Сообщение передано менеджеру. Опишите проблему текстом — поможем.",
    "duplicate_rejected": "⛔ Повторная регистрация остановлена — заявка уже существует.",
    "deletion_requested": "📝 Запрос на удаление зафиксирован. Менеджер обработает вручную.",
    "yandex_error": "⚠️ Ошибка при отправке заявки. Менеджер проверит и поможет.",
}


STATUS_COLLECTING_DATA_TEMPLATE = "📋 Заявка заполняется. Текущий шаг: {state}."
STATUS_FALLBACK_TEMPLATE = "📋 Статус заявки: {status}."


YANDEX_PRO_START_TEMPLATE = (
    "✅ Заявка принята в парк!\n\n"
    "📱 Завершите вход в Яндекс Про:\n"
    "1️⃣ Скачайте приложение из App Store или Google Play\n"
    "2️⃣ Войдите по номеру {phone}\n"
    "3️⃣ Подтвердите SMS и завершите шаги в приложении\n\n"
    "Вошли — напишите: Вошел\n"
    "Не скачал — напишите: Не скачал\n"
    "Ошибка — напишите: Ошибка"
)


YANDEX_PRO_INSTALL_TEMPLATE = (
    "📱 Чтобы закончить подключение:\n"
    "1️⃣ Скачайте Яндекс Про из официального магазина\n"
    "2️⃣ Войдите по номеру {phone}\n"
    "3️⃣ Подтвердите SMS и завершите вход\n\n"
    "Готово — напишите: Вошел\n"
    "Ошибка — напишите: Ошибка"
)


def format_in_flow_reply(answer: str, state: DialogueState) -> str:
    step_prompt = PROMPTS.get(state, "").strip()
    cleaned = answer.strip()
    if not step_prompt:
        return cleaned
    if not cleaned or cleaned == step_prompt:
        return f"📋 Следующий шаг:\n{step_prompt}"
    if "Следующий шаг:" in cleaned or "Текущий шаг регистрации:" in cleaned:
        return cleaned
    return f"{cleaned}\n\n📋 Следующий шаг:\n{step_prompt}"
OFFICE_HOURS = "🕘 Ежедневно с 09:00 до 18:00"
WELCOME_GREETING = (
    "Здравствуйте! 👋\n\n"
    "SD Family Taxi — надёжный таксопарк с выгодными условиями:\n"
    "✅ Комиссия парка — 2%\n"
    "✅ Моментальные выплаты 24/7\n"
    "✅ Круглосуточная поддержка\n"
    "✅ Бонусы для водителей\n"
    "✅ Бесплатный сухой туман для постоянных водителей"
)
REGISTRATION_START_CTA = "✍️ Для подключения напишите ваше ФИО полностью."
DOCUMENT_PHOTO_QUALITY_HINT = (
    "💡 Для фото: положите документ на ровную поверхность, снимайте при ярком освещении — "
    "весь документ в кадре, без бликов и засветов."
)
MANUAL_ENTRY_HINT = (
    "✍️ Отправьте фото или PDF документа — бот заполнит данные автоматически."
)
CAR_MODEL_PROMPT = (
    "🚘 Напишите модель автомобиля, как в документах или техпаспорте "
    "(Camry, Rio, S-Class, X5 и т.п.). "
    "Указывайте название модели, а не код кузова (w221, e90 и подобное)."
)

PROMPTS.update(
    {
        DialogueState.NEW: f"{WELCOME_GREETING}\n\n{REGISTRATION_START_CTA}",
        DialogueState.ASK_FULL_NAME: "✍️ Напишите ваше ФИО полностью.",
        DialogueState.ASK_EXECUTOR_TYPE: "📋 Укажите контактный номер телефона.",
        DialogueState.ASK_PHONE: "📱 Укажите ваш контактный номер телефона.",
        DialogueState.ASK_CITY: "🏙 Напишите город, в котором будете работать.",
        DialogueState.ASK_ADDRESS: "📍 Укажите адрес проживания или регистрации (например: пр. Республики 12, Астана).",
        DialogueState.ASK_IIN: "🪪 Укажите ИИН из 12 цифр.",
        DialogueState.ASK_BIRTH_DATE: "📅 Укажите дату рождения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: (
            "📅 Укажите дату начала водительского стажа в формате ДД.ММ.ГГГГ. "
            "Это не дата рождения — возьмите дату из водительского удостоверения."
        ),
        DialogueState.ASK_HAS_CAR: "🚘 Напишите марку автомобиля, например Toyota.",
        DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER: "🚘 Напишите марку автомобиля, например Toyota.",
        DialogueState.ASK_CAR_BRAND: "🚘 Напишите марку автомобиля, например Toyota.",
        DialogueState.ASK_CAR_MODEL: CAR_MODEL_PROMPT,
        DialogueState.ASK_CAR_YEAR: "📅 Укажите год выпуска автомобиля.",
        DialogueState.ASK_CAR_PLATE: "🔢 Укажите госномер автомобиля.",
        DialogueState.ASK_CAR_COLOR: "🎨 Укажите цвет автомобиля.",
        DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "📄 Укажите номер техпаспорта (СТС) автомобиля, как в документе.",
        DialogueState.ASK_DRIVER_LICENSE_NUMBER: (
            "🪪 Напишите серию и номер водительского удостоверения, как в документе "
            "(например CQ 981709). Серия и номер могут быть через пробел."
        ),
        DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "📅 Укажите дату выдачи водительского удостоверения в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "📅 Укажите срок действия водительского удостоверения до даты в формате ДД.ММ.ГГГГ.",
        DialogueState.ASK_EMPLOYMENT_TYPE: "💼 Укажите условие работы: штатный, самозанятый или другое согласованное с парком условие.",
        DialogueState.ASK_HIRED_AT: (
            "📅 Укажите дату принятия в парк в формате ДД.ММ.ГГГГ. "
            "Обычно это сегодняшняя дата или день подключения — не путайте со сроком действия прав."
        ),
        DialogueState.ASK_HEARING_IMPAIRED: "❓ Вы являетесь слабослышащим водителем? Ответьте: да или нет.",
        DialogueState.ASK_DRIVER_LICENSE_FRONT: (
            "📷 Отправьте водительское удостоверение:\n"
            "• PDF из eGov или Kaspi (обе стороны на одной странице) — один файл\n"
            "• или 2 фото: лицевая и обратная стороны\n\n"
            f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
            f"{MANUAL_ENTRY_HINT}\n\n"
            "Бот распознает ФИО, ИИН, даты и номер прав."
        ),
        DialogueState.ASK_DRIVER_LICENSE_BACK: (
            "📷 Отправьте обратную сторону водительского удостоверения.\n"
            "Или PDF из eGov/Kaspi с обеими сторонами на одной странице.\n\n"
            f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
            f"{MANUAL_ENTRY_HINT}"
        ),
        DialogueState.ASK_ID_CARD: (
            "📷 Отправьте удостоверение личности — фото или PDF из eGov/Kaspi.\n\n"
            f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
            f"{MANUAL_ENTRY_HINT}"
        ),
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC: (
            "📷 Отправьте техпаспорт или СТС — фото или PDF из eGov/Kaspi.\n"
            "Распознаем марку, модель, госномер и номер СТС.\n\n"
            f"{DOCUMENT_PHOTO_QUALITY_HINT}\n\n"
            f"{MANUAL_ENTRY_HINT}"
        ),
        DialogueState.ASK_SELFIE_WITH_LICENSE: (
            "📷 Отправьте селфи с водительским удостоверением в руке.\n"
            "Лицо и документ должны быть чётко видны, без бликов."
        ),
        DialogueState.ASK_RENT_OR_POWER_OF_ATTORNEY: "✅ Принято. Проверьте введённые данные.",
        DialogueState.CONFIRM_DATA: (
            "📋 Проверьте собранные данные. "
            "Если всё верно — напишите «Подтверждаю». "
            "Если нужно исправить — напишите, что изменить."
        ),
        DialogueState.READY_TO_SEND_YANDEX: "✅ Спасибо, данные собраны. Отправляю заявку на регистрацию в таксопарк…",
        DialogueState.SENT_TO_YANDEX: "✅ Готово! Заявка отправлена в систему таксопарка.",
        DialogueState.ASK_YANDEX_PRO_LOGIN: "📱 Теперь нужно завершить вход в Яндекс Про.",
        DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS: "❓ Опишите, что именно не получается при входе в Яндекс Про.",
        DialogueState.YANDEX_ERROR: (
            "⚠️ Не удалось автоматически отправить заявку. Данные сохранены. "
            "Если ошибка исправлена — напишите «Подтверждаю» для повторной отправки."
        ),
        DialogueState.COMPLETED: (
            "🎉 Регистрация завершена! Пишите сюда, если нужна помощь.\n"
            "🎁 После регистрации можно приехать в офис и забрать приветственный бонус.\n"
            "В бокс входят: зарядка 3 в 1, держатель для телефона, салфетка и тряпка.\n"
            "Для бизнес-класса дополнительно выдаём блок воды.\n"
            "📍 Офис: Астана, Балкантау 117\n"
            f"{OFFICE_HOURS}"
        ),
    }
)

STATUS_REPLIES.update(
    {
        "waiting_documents": "📷 Заявка ждёт документы. Отправьте следующий запрошенный файл.",
        "confirming_data": "📋 Заявка собрана — ждём вашего подтверждения.",
        "ready_to_send_yandex": "✅ Заявка готова к отправке в парк.",
        "sending_to_yandex": "⏳ Заявка отправляется в систему парка…",
        "sent_to_yandex": (
            "✅ Заявка отправлена в парк!\n"
            "📱 Завершите вход в Яндекс Про.\n"
            "Вошли — напишите: Вошел\n"
            "Ошибка — напишите: Ошибка"
        ),
        "awaiting_manager_review": "👨‍💼 Сообщение передано менеджеру. Опишите проблему текстом — поможем.",
        "duplicate_rejected": "⛔ Повторная регистрация остановлена — заявка уже существует.",
        "deletion_requested": "📝 Запрос на удаление зафиксирован. Менеджер обработает вручную.",
        "yandex_error": "⚠️ Ошибка при отправке заявки. Менеджер проверит и поможет.",
    }
)
