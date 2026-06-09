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
    "car_requirements": (
        "без своего авто",
        "без авто",
        "какие авто",
        "какая машина",
        "требования к авто",
    ),
    "registration": (
        "статус заявки",
        "статус",
        "сколько занимает",
        "сколько времени",
        "как подключиться",
        "как регистрироваться",
        "как проходит регистрация",
        "повторная регистрация",
        "перезапуск",
    ),
    "park_info": (
        "кто вы",
        "кто вы такие",
        "что за парк",
        "что за компания",
        "о вас",
        "какие условия",
        "условия парка",
        "комиссия",
        "выплаты",
        "байге",
        "подарок",
        "подарочный бокс",
        "сухой туман",
        "офис",
        "балкантау 117",
        "вода",
        "поддержка",
    ),
    "registered_driver_support": (
        "после регистрации",
        "после подключения",
        "выйти на линию",
        "как выйти на линию",
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
    lowered = normalize_text_token(message)

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
