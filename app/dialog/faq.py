from pathlib import Path
import re

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


def _iter_message_parts(message: str) -> list[str]:
    text = message.strip()
    if not text:
        return []

    chunks = re.split(r"[?\n;]+", text)
    parts: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip(" ,.")
        if not chunk:
            continue
        subparts = re.split(r"\s+(?:и|а|but)\s+", chunk, flags=re.IGNORECASE)
        for subpart in subparts:
            cleaned = subpart.strip(" ,.")
            if len(cleaned) >= 2:
                parts.append(cleaned)

    return parts if parts else [text]


def split_field_and_support(message: str) -> tuple[str | None, list[str]]:
    text = message.strip()
    if not text:
        return None, []

    parts = _iter_message_parts(text)
    if len(parts) <= 1:
        if looks_like_support_question(text):
            return None, [text]
        return text, []

    support_parts = [part for part in parts if looks_like_support_question(part)]
    if not support_parts:
        return text, []

    field_parts = [part for part in parts if not looks_like_support_question(part)]
    field_text = " ".join(field_parts).strip() or None
    return field_text, support_parts


def split_support_questions(message: str) -> list[str]:
    text = message.strip()
    if not text:
        return []

    parts = _iter_message_parts(text)
    support_parts = [part for part in parts if looks_like_support_question(part)]

    if len(support_parts) >= 2:
        return support_parts
    if len(support_parts) == 1 and len(parts) >= 2:
        return support_parts
    return [text]


def _qa_pairs_from_content(content: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    current_question: str | None = None
    for line in content.splitlines():
        if line.startswith("Q:"):
            current_question = normalize_text_token(line[2:].strip())
            continue
        if line.startswith("A:") and current_question:
            pairs.append((current_question, line[2:].strip()))
            current_question = None
    return pairs


def _find_qa_match(lowered: str, content: str) -> str | None:
    for question, answer in _qa_pairs_from_content(content):
        if question == lowered or question in lowered or lowered in question:
            return answer
    return None


def _find_best_trigger_match(lowered: str, kb: dict[str, str]) -> str | None:
    best_score = 0
    best_answer: str | None = None

    for doc_name, triggers in FAQ_TRIGGERS.items():
        if doc_name not in kb:
            continue
        pairs = _qa_pairs_from_content(kb[doc_name])
        for trigger in triggers:
            if trigger not in lowered:
                continue
            for question, answer in pairs:
                if trigger in question:
                    score = len(trigger) * 2 + len(question)
                elif all(word in question for word in trigger.split() if len(word) >= 3):
                    score = len(trigger) + len(question)
                else:
                    continue
                if score > best_score:
                    best_score = score
                    best_answer = answer

    return best_answer


def _find_single_faq_answer(message: str, kb: dict[str, str]) -> str | None:
    lowered = normalize_text_token(message).strip(" ?!,.")
    if not lowered:
        return None

    for content in kb.values():
        answer = _find_qa_match(lowered, content)
        if answer:
            return answer

    return _find_best_trigger_match(lowered, kb)


def find_faq_answers(message: str, kb: dict[str, str]) -> list[str]:
    segments = split_support_questions(message)
    answers: list[str] = []
    seen: set[str] = set()

    for segment in segments:
        answer = _find_single_faq_answer(segment, kb)
        if answer and answer not in seen:
            seen.add(answer)
            answers.append(answer)

    if not answers:
        answer = _find_single_faq_answer(message, kb)
        if answer:
            answers.append(answer)

    return answers


def resolve_faq_replies(message: str, kb: dict[str, str], *, office_address: str | None = None) -> str | None:
    segments = split_support_questions(message)
    answers: list[str] = []
    seen: set[str] = set()
    unanswered_segments = 0

    for segment in segments:
        answer = _find_single_faq_answer(segment, kb)
        if answer and answer not in seen:
            seen.add(answer)
            answers.append(answer)
        elif looks_like_support_question(segment):
            unanswered_segments += 1

    if not answers:
        answer = _find_single_faq_answer(message, kb)
        if answer:
            return answer
        if looks_like_support_question(message) and office_address:
            return build_office_invite_reply(office_address)
        return None

    reply = "\n\n".join(answers)
    if unanswered_segments and office_address:
        reply += f"\n\nПо остальному вопросу: {build_office_invite_reply(office_address)}"
    return reply


def find_faq_answer(message: str, kb: dict[str, str]) -> str | None:
    answers = find_faq_answers(message, kb)
    if not answers:
        return None
    if len(answers) == 1:
        return answers[0]
    return "\n\n".join(answers)


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


def build_office_invite_reply(office_address: str) -> str:
    return (
        "По этому вопросу в чате нет готового ответа. "
        "Приходите в офис SD Family Taxi — менеджер подскажет на месте.\n"
        f"Адрес: {office_address}"
    )

