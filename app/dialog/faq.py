from pathlib import Path
import re
from dataclasses import dataclass

from app.utils.validators import normalize_text_token


KB_DIR = Path(__file__).resolve().parents[2] / "knowledge_base"

_FAQ_STOPWORDS = frozenset(
    {
        "что",
        "как",
        "где",
        "когда",
        "кто",
        "какие",
        "какой",
        "какая",
        "какое",
        "каков",
        "можно",
        "нужно",
        "ли",
        "это",
        "вас",
        "ваш",
        "вашей",
        "вашего",
        "есть",
        "или",
        "для",
        "про",
        "нас",
        "нам",
        "мне",
        "меня",
        "если",
        "еще",
        "ещё",
        "у",
        "в",
        "на",
        "за",
        "по",
        "из",
        "от",
        "до",
        "не",
        "да",
        "а",
        "и",
        "the",
        "you",
        "your",
    }
)

TOKEN_SYNONYMS: dict[str, frozenset[str]] = {
    "бонус": frozenset({"бонус", "бонусы", "бонусов", "приз", "призы", "премия", "премии", "награда", "награды", "байге", "акция", "акции"}),
    "получ": frozenset({"получу", "получить", "получает", "получите", "получаю", "дают", "дадут", "даете", "получаете", "положено", "полагается"}),
    "дают": frozenset({"дают", "дадут", "даете", "получу", "получить", "подарок", "подарочный"}),
    "стаж": frozenset({"стаж", "стажа", "опыт", "работаю", "работать", "водителем"}),
    "комисс": frozenset({"комиссия", "комиссии", "процент", "проц", "проценты", "удерж", "заработок", "заработать", "зарабатываю"}),
    "выплат": frozenset({"выплаты", "выплат", "вывод", "выводить", "моментальн"}),
    "услов": frozenset({"условия", "условие", "условиях", "работы", "работать", "парк", "парка"}),
    "офис": frozenset({"офис", "офиса", "адрес", "адреса", "находитесь", "находитесь", "приехать", "прийти", "локация", "балкантау"}),
    "регистрац": frozenset({"регистрация", "регистрацию", "регистрации", "подключение", "подключиться", "подключится", "оформить", "устроиться"}),
    "документ": frozenset({"документ", "документы", "документов", "фото", "фотограф", "скан", "копия", "справк"}),
    "яндекс": frozenset({"яндекс", "yandex", "про", "yandexpro"}),
    "войти": frozenset({"войти", "вход", "входа", "зайти", "заход", "логин", "авториз", "вошел", "вошёл"}),
    "линия": frozenset({"линия", "линию", "онлайн", "заказ", "заказы", "работать", "выйти"}),
    "машин": frozenset({"машина", "машину", "машины", "авто", "автомобил", "тачка", "тачку"}),
    "поддерж": frozenset({"поддержка", "поддержку", "помощь", "помогите", "менеджер", "оператор"}),
    "туман": frozenset({"туман", "тумана", "сухой"}),
    "вод": frozenset({"вода", "воды", "блок"}),
    "статус": frozenset({"статус", "статуса", "заявк", "заявка", "этап", "этапе"}),
    "иин": frozenset({"иин", "инн"}),
    "скач": frozenset({"скачать", "скачал", "установ", "install", "download"}),
    "смс": frozenset({"смс", "sms", "код", "кода"}),
}


@dataclass(frozen=True)
class FaqIntentRoute:
    keywords: tuple[str, ...]
    doc: str
    question: str
    min_hits: int = 1


FAQ_INTENT_ROUTES: tuple[FaqIntentRoute, ...] = (
    FaqIntentRoute(("получ", "стаж"), "park_info", "какие бонусы", 2),
    FaqIntentRoute(("получ", "бонус"), "park_info", "какие бонусы", 2),
    FaqIntentRoute(("получ", "приз"), "park_info", "какие бонусы", 2),
    FaqIntentRoute(("дают", "стаж"), "park_info", "какие бонусы", 2),
    FaqIntentRoute(("получ",), "park_info", "какие бонусы", 1),
    FaqIntentRoute(("бонус", "приз", "премия", "награда", "байге", "акци"), "park_info", "какие бонусы"),
    FaqIntentRoute(("подароч", "бокс"), "park_info", "что дают за регистрацию"),
    FaqIntentRoute(("комисс", "процент", "проц", "заработ"), "park_info", "какая комиссия"),
    FaqIntentRoute(("выплат", "вывод"), "park_info", "какие условия"),
    FaqIntentRoute(("услов",), "park_info", "какие условия"),
    FaqIntentRoute(("офис", "адрес", "балкантау", "приехать", "прийти", "локац"), "park_info", "где находится офис"),
    FaqIntentRoute(("туман",), "park_info", "сухой туман"),
    FaqIntentRoute(("поддерж",), "park_info", "есть ли поддержка"),
    FaqIntentRoute(("кто вы", "что за парк", "о вас", "таксопарк"), "park_info", "кто вы такие"),
    FaqIntentRoute(("регистрац", "подключ", "устроиться", "оформить"), "registration", "как подключиться"),
    FaqIntentRoute(("сколько времен", "сколько занимает", "долго регист"), "registration", "сколько занимает"),
    FaqIntentRoute(("статус", "заявк"), "registration", "как узнать статус"),
    FaqIntentRoute(("иин",), "registration", "зачем нужен иин"),
    FaqIntentRoute(("документ", "фото", "скан"), "documents", "какие документы"),
    FaqIntentRoute(("яндекс", "yandex"), "yandex_pro", "как войти"),
    FaqIntentRoute(("скач", "установ"), "yandex_pro", "как скачать"),
    FaqIntentRoute(("линия", "онлайн", "заказ"), "yandex_pro", "как выйти на линию"),
    FaqIntentRoute(("смс", "код не"), "yandex_pro_sms_issues", "смс не приходит"),
    FaqIntentRoute(("не могу войти", "не входит", "не заходит", "не пускает"), "yandex_pro_login_errors", "не могу войти"),
    FaqIntentRoute(("неактив", "аккаунт не"), "yandex_pro_account_inactive", "аккаунт не активен"),
    FaqIntentRoute(("без авто", "без машин", "свой машин"), "car_requirements", "можно ли работать без"),
    FaqIntentRoute(("машин", "авто", "kia", "toyota", "camry", "rio"), "car_requirements", "какие авто"),
    FaqIntentRoute(("после регистрац", "после подключ"), "registered_driver_support", "что делать после регистрации"),
)

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
        "бонус",
        "бонусы",
        "какие бонусы",
        "призы",
        "премии",
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
        "бонус",
        "бонусы",
        "какие бонусы",
        "призы",
        "премии",
        "получу",
        "получить",
        "дадут",
        "стаж",
        "акци",
        "подарок",
        "заработ",
        "процент",
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


def _tokenize_meaningful(text: str) -> set[str]:
    normalized = normalize_text_token(text)
    tokens = re.findall(r"[a-z0-9а-яё]+", normalized, flags=re.IGNORECASE)
    return {token for token in tokens if len(token) >= 3 and token not in _FAQ_STOPWORDS}


def _expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        for root, aliases in TOKEN_SYNONYMS.items():
            if token.startswith(root) or root.startswith(token[: min(4, len(token))]) or token in aliases:
                expanded |= set(aliases)
    return expanded


def _answer_for_question(kb: dict[str, str], doc: str, question_hint: str) -> str | None:
    content = kb.get(doc)
    if not content:
        return None
    hint = normalize_text_token(question_hint)
    for question, answer in _qa_pairs_from_content(content):
        if question == hint or hint in question or question in hint:
            return answer
    return None


def _find_intent_route_match(lowered: str, kb: dict[str, str]) -> str | None:
    best_score = 0
    best_answer: str | None = None

    for route in FAQ_INTENT_ROUTES:
        hits = sum(1 for keyword in route.keywords if keyword in lowered)
        if hits < route.min_hits:
            continue
        answer = _answer_for_question(kb, route.doc, route.question)
        if not answer:
            continue
        score = hits * 10 + sum(len(keyword) for keyword in route.keywords if keyword in lowered)
        if score > best_score:
            best_score = score
            best_answer = answer

    return best_answer


def _find_fuzzy_qa_match(lowered: str, kb: dict[str, str]) -> str | None:
    user_tokens = _expand_tokens(_tokenize_meaningful(lowered))
    if not user_tokens:
        return None

    best_score = 0.0
    best_answer: str | None = None

    for content in kb.values():
        for question, answer in _qa_pairs_from_content(content):
            question_tokens = _expand_tokens(_tokenize_meaningful(question))
            if not question_tokens:
                continue
            overlap = user_tokens & question_tokens
            if not overlap:
                continue
            score = (len(overlap) ** 2) + sum(len(token) for token in overlap)
            if score > best_score:
                best_score = score
                best_answer = answer

    return best_answer if best_score >= 2.0 else None


def _find_qa_match(lowered: str, content: str) -> str | None:
    for question, answer in _qa_pairs_from_content(content):
        if question == lowered:
            return answer
        if question in lowered:
            return answer
        if lowered in question and len(lowered) >= max(12, len(question) // 2):
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


def _looks_like_registration_field_edit(message: str) -> bool:
    lowered = normalize_text_token(message)
    if not any(marker in lowered for marker in ("исправ", "измен", "поменя", "замен")):
        return False
    field_markers = (
        "модель",
        "марка",
        "госномер",
        "номер",
        "город",
        "адрес",
        "иин",
        "фио",
        "фамил",
        "имя",
        "отчеств",
        "год",
        "цвет",
        "стс",
        "техпаспорт",
        "vin",
        "вин",
        "права",
        "стаж",
        "телефон",
    )
    return any(marker in lowered for marker in field_markers)


def _find_single_faq_answer(message: str, kb: dict[str, str]) -> str | None:
    lowered = normalize_text_token(message).strip(" ?!,.")
    if not lowered:
        return None
    if _looks_like_registration_field_edit(message):
        return None

    for content in kb.values():
        answer = _find_qa_match(lowered, content)
        if answer:
            return answer

    trigger_answer = _find_best_trigger_match(lowered, kb)
    if trigger_answer:
        return trigger_answer

    intent_answer = _find_intent_route_match(lowered, kb)
    if intent_answer:
        return intent_answer

    return _find_fuzzy_qa_match(lowered, kb)


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
            if looks_like_greeting(message):
                return None
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


def looks_like_greeting(message: str) -> bool:
    normalized = normalize_text_token(message).strip(" ?!,.")
    if not normalized:
        return False

    greeting_markers = (
        "ало",
        "алло",
        "привет",
        "здравствуйте",
        "салам",
        "добрый день",
        "добрый вечер",
        "доброе утро",
        "hi",
        "hello",
        "hey",
    )
    if normalized in greeting_markers:
        return True

    for marker in ("ало", "алло"):
        if normalized.startswith(marker):
            remainder = normalized[len(marker) :].strip(" ?!,.")
            if not remainder or len(remainder.split()) <= 2:
                return True

    return False


def looks_like_support_question(message: str) -> bool:
    normalized = normalize_text_token(message).strip(" ?!.,")
    if not normalized:
        return False
    if looks_like_greeting(message):
        return False
    if "?" in message:
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
    if any(fragment in normalized for fragment in (
        " условия", " офис", " комиссия", " документы", " яндекс про",
        " бонус", " бонусы", " приз", " стаж", " выплат", " акци",
    )):
        return True
    return False


def build_office_invite_reply(office_address: str) -> str:
    return (
        "По этому вопросу в чате нет готового ответа. "
        "Приходите в офис SD Family Taxi — менеджер подскажет на месте.\n"
        f"Адрес: {office_address}"
    )

