from pathlib import Path

from app.utils.validators import normalize_text_token


KB_DIR = Path(__file__).resolve().parents[2] / "knowledge_base"


FAQ_TRIGGERS: dict[str, tuple[str, ...]] = {
    "documents": (
        "какие документы",
        "документы",
        "что нужно из документов",
        "что отправить",
        "что нужно отправить",
    ),
    "yandex_pro": (
        "яндекс про",
        "yandex pro",
        "yandexpro",
        "как войти",
        "зайти в яндекс про",
        "скачать яндекс про",
        "выход на линию",
        "на линию",
        "линия",
        "онлайн",
        "статус в про",
        "запуск про",
    ),
    "yandex_pro_login_errors": (
        "не могу войти",
        "не входит",
        "ошибка входа",
        "логин не проходит",
        "не заходит",
        "не пускает",
    ),
    "yandex_pro_sms_issues": (
        "смс не приходит",
        "не приходит код",
        "код не приходит",
        "не пришел код",
        "sms не приходит",
    ),
    "yandex_pro_account_inactive": (
        "аккаунт не активен",
        "неактивен",
        "не активен",
        "профиль не активен",
        "не дает выйти на линию",
    ),
    "yandex_pro_go_online_steps": (
        "как выйти на линию",
        "выйти на линию",
        "как выйти онлайн",
        "как начать заказы",
        "не знаю как выйти на линию",
    ),
    "car_requirements": (
        "без своего авто",
        "без авто",
        "какие авто",
        "какая машина",
        "требования к авто",
    ),
    "park_info": (
        "кто вы",
        "кто вы такие",
        "что за парк",
        "что за компания",
        "о вас",
        "понятно что вы таксопарк",
        "вы таксопарк",
        "какие условия",
        "условия парка",
        "условия работы",
        "комиссия",
        "выплаты",
        "байге",
        "подарок",
        "подарочный бокс",
        "сухой туман",
        "офис",
        "где офис",
        "где находится офис",
        "адрес офиса",
        "ваш офис",
        "балкантау",
        "балкантау 117",
        "вода",
        "поддержка",
    ),
    "registration": (
        "статус заявки",
        "статус",
        "сколько занимает",
        "сколько времени",
        "как подключиться",
        "как зарегистрироваться",
        "как проходит регистрация",
        "повторная регистрация",
        "перезапуск",
        "зачем иин",
        "зачем нужен иин",
        "почему иин",
        "для чего иин",
        "можно по другому",
        "другой вопрос",
        "можно задать",
    ),
    "registered_driver_support": (
        "после регистрации",
        "после подключения",
        "как работать дальше",
        "что дальше",
        "подарочный бокс",
        "бокс",
        "сухой туман",
        "моментальные выплаты",
    ),
}


def load_knowledge_base() -> dict[str, str]:
    data: dict[str, str] = {}
    if not KB_DIR.exists():
        return data
    for file_path in KB_DIR.glob("*.md"):
        data[file_path.stem] = file_path.read_text(encoding="utf-8")
    return data


def find_faq_answer(message: str, kb: dict[str, str]) -> str | None:
    lowered = normalize_text_token(message).strip(" ?!.,")

    for _, content in kb.items():
        lines = content.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("Q:"):
                continue
            question = normalize_text_token(line[2:].strip())
            if question and (question == lowered or question in lowered or lowered in question):
                for answer_line in lines[index + 1 :]:
                    if answer_line.startswith("A:"):
                        return answer_line[2:].strip()
                return content

    for doc_name, triggers in FAQ_TRIGGERS.items():
        if doc_name not in kb:
            continue
        if any(trigger in lowered for trigger in triggers):
            lines = kb[doc_name].splitlines()
            for line in lines:
                if line.startswith("A:"):
                    return line[2:].strip()
            return kb[doc_name]

    return None


def looks_like_support_question(message: str) -> bool:
    normalized = normalize_text_token(message).strip(" ?!.,")
    if not normalized:
        return False
    if "?" in message:
        return True

    greeting_markers = ("ало", "алло", "привет", "здравствуйте", "салам", "добрый день", "добрый вечер", "hi", "hello")
    if normalized in greeting_markers or any(normalized.startswith(marker) for marker in ("ало", "алло")):
        return True

    help_markers = (
        "зачем",
        "почему",
        "для чего",
        "что такое",
        "объясни",
        "объясните",
        "поясни",
        "не понял",
        "не понимаю",
        "помогите",
        "помоги",
        "help",
        "можно по другому",
        "другой вопрос",
        "не про это",
    )
    if any(marker in normalized for marker in help_markers):
        return True

    question_starters = (
        "где ",
        "как ",
        "какие ",
        "какой ",
        "что ",
        "кто ",
        "сколько ",
        "когда ",
        "а где ",
        "а как ",
        "а какие ",
    )
    if normalized.startswith(question_starters):
        return True
    if any(fragment in normalized for fragment in (" условия", " офис", " комиссия", " документы", " яндекс про")):
        return True
    return False
