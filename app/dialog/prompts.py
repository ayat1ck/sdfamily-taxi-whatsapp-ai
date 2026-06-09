from app.dialog.states import DialogueState


PROMPTS: dict[DialogueState, str] = {
    DialogueState.NEW: "Здравствуйте. Я помогу зарегистрироваться в таксопарке. Напишите ваше ФИО.",
    DialogueState.ASK_FULL_NAME: "Напишите ваше ФИО полностью.",
    DialogueState.ASK_EXECUTOR_TYPE: "Переходим к анкете. Укажите ваш контактный номер телефона.",
    DialogueState.ASK_PHONE: "Укажите ваш контактный номер телефона.",
    DialogueState.ASK_CITY: "Напишите город, в котором будете работать.",
    DialogueState.ASK_ADDRESS: "Укажите адрес проживания или регистрации.",
    DialogueState.ASK_IIN: "Укажите ИИН из 12 цифр.",
    DialogueState.ASK_BIRTH_DATE: "Укажите дату рождения в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "Укажите дату начала водительского стажа в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_HAS_CAR: "Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER: "Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_CAR_BRAND: "Напишите марку автомобиля, например Toyota.",
    DialogueState.ASK_CAR_MODEL: "Теперь напишите модель автомобиля, например Camry.",
    DialogueState.ASK_CAR_YEAR: "Укажите год выпуска автомобиля.",
    DialogueState.ASK_CAR_PLATE: "Укажите госномер автомобиля.",
    DialogueState.ASK_CAR_COLOR: "Укажите цвет автомобиля.",
    DialogueState.ASK_DRIVER_LICENSE_NUMBER: "Напишите серию и номер водительского удостоверения.",
    DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "Укажите дату выдачи водительского удостоверения в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "Укажите срок действия водительского удостоверения до даты в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_EMPLOYMENT_TYPE: "Укажите условие работы: штатный, самозанятый или другое согласованное с парком условие.",
    DialogueState.ASK_HIRED_AT: "Укажите дату принятия в формате ДД.ММ.ГГГГ.",
    DialogueState.ASK_HEARING_IMPAIRED: "Вы являетесь слабослышащим водителем? Ответьте: да или нет.",
    DialogueState.ASK_DRIVER_LICENSE_FRONT: "Отправьте фото водительского удостоверения: лицевая сторона.",
    DialogueState.ASK_DRIVER_LICENSE_BACK: "Принял. Теперь отправьте фото водительского удостоверения: обратная сторона.",
    DialogueState.ASK_ID_CARD: "Принял. Теперь отправьте фото удостоверения личности.",
    DialogueState.ASK_VEHICLE_REGISTRATION_DOC: "Принял. Теперь отправьте фото техпаспорта или СТС автомобиля.",
    DialogueState.ASK_SELFIE_WITH_LICENSE: "Принял. Теперь отправьте селфи с водительским удостоверением.",
    DialogueState.ASK_RENT_OR_POWER_OF_ATTORNEY: "Принял. Проверьте введенные данные.",
    DialogueState.CONFIRM_DATA: "Проверьте собранные данные. Если все верно, напишите 'Подтверждаю'. Если нужно исправить, напишите, что изменить.",
    DialogueState.READY_TO_SEND_YANDEX: "Спасибо, данные собраны. Отправляю заявку на регистрацию в таксопарк.",
    DialogueState.SENT_TO_YANDEX: "Готово. Ваша заявка отправлена в систему таксопарка.",
    DialogueState.ASK_YANDEX_PRO_LOGIN: "Теперь нужно завершить вход в Яндекс Про.",
    DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS: "Если у вас ошибка во входе в Яндекс Про, напишите, что именно не получается.",
    DialogueState.YANDEX_ERROR: "Не удалось автоматически отправить заявку. Данные сохранены, попробуем обработать повторно.",
    DialogueState.COMPLETED: "Регистрация завершена. Если нужно что-то уточнить, напишите сюда. Адрес офиса: Балкантау 117.",
}


DOCUMENT_STATE_MAP = {
    DialogueState.ASK_DRIVER_LICENSE_FRONT: "driver_license_front",
    DialogueState.ASK_DRIVER_LICENSE_BACK: "driver_license_back",
    DialogueState.ASK_ID_CARD: "id_card",
    DialogueState.ASK_VEHICLE_REGISTRATION_DOC: "vehicle_registration_doc",
    DialogueState.ASK_SELFIE_WITH_LICENSE: "selfie_with_license",
}


STATUS_REPLIES = {
    "waiting_documents": "Заявка ждет документы. Отправьте следующий запрошенный документ.",
    "confirming_data": "Заявка собрана и ждет вашего подтверждения.",
    "ready_to_send_yandex": "Заявка готова к отправке в парк.",
    "sending_to_yandex": "Заявка сейчас отправляется в систему парка.",
    "sent_to_yandex": "Заявка отправлена в парк. Теперь нужно завершить вход в Яндекс Про. Если уже вошли, напишите: Вошел. Если есть ошибка, напишите: Ошибка.",
    "completed": "Регистрация завершена. Если нужно что-то уточнить, напишите сюда. Адрес офиса: Балкантау 117.",
    "awaiting_manager_review": "Ваше сообщение передано менеджеру. Опишите проблему текстом, если нужна помощь.",
    "duplicate_rejected": "Повторная регистрация остановлена, так как заявка уже существует.",
    "deletion_requested": "Запрос на удаление аккаунта уже зафиксирован и ожидает ручной обработки менеджером.",
    "yandex_error": "При отправке заявки возникла ошибка. Менеджер должен проверить заявку.",
}


STATUS_COLLECTING_DATA_TEMPLATE = "Заявка еще заполняется. Текущий шаг: {state}."
STATUS_FALLBACK_TEMPLATE = "Текущий статус заявки: {status}."


YANDEX_PRO_START_TEMPLATE = (
    "Заявка отправлена в парк.\n\n"
    "Теперь нужно завершить вход в Яндекс Про:\n"
    "1. Установите приложение Яндекс Про из App Store или Google Play.\n"
    "2. Войдите по номеру {phone}.\n"
    "3. Подтвердите вход по SMS и завершите шаги в приложении.\n\n"
    "Когда войдете, напишите: Вошел.\n"
    "Если приложение еще не установлено, напишите: Не скачал.\n"
    "Если есть ошибка или что-то не получается, напишите: Ошибка, и я помогу дальше."
)


YANDEX_PRO_INSTALL_TEMPLATE = (
    "Чтобы закончить подключение:\n"
    "1. Скачайте Яндекс Про из официального магазина приложений.\n"
    "2. Войдите по номеру {phone}.\n"
    "3. Подтвердите SMS и завершите вход.\n\n"
    "Когда все получится, напишите: Вошел.\n"
    "Если появится ошибка, напишите: Ошибка."
)
