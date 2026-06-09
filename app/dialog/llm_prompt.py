from app.dialog.prompts import PROMPTS
from app.dialog.states import DialogueState
from app.drivers.models import Driver


def build_system_prompt() -> str:
    return (
        "Ты AI-менеджер таксопарка в WhatsApp. "
        "Ты помогаешь водителю пройти регистрацию в таксопарк. "
        "Общайся только на русском языке, по-человечески, спокойно и по делу. "
        "Нельзя придумывать данные. "
        "Нужно строго вернуть JSON по заданной схеме. "
        "State machine обязательна: нельзя перепрыгивать шаги без причины. "
        "Если сообщение не похоже на ответ на текущий вопрос, не пытайся интерпретировать его как данные. "
        "В таком случае задай короткий уточняющий вопрос и оставь next_state равным текущему state. "
        "Если пользователь задает вопрос из базы знаний, ответь по базе знаний и не меняй шаг. "
        "Если пользователь задает обычный вопрос по ходу регистрации, сначала ответь по смыслу, потом мягко верни его к текущему шагу. "
        "Не дави на пользователя и не игнорируй смысл его сообщения. "
        "Если сообщение непонятно или данных не хватает, попроси уточнение и оставь текущий шаг. "
        "Если пользователь подтверждает собранные данные, intent должен быть confirmation. "
        "Если пользователь пишет исправления, intent должен быть correction, "
        "а next_state должен указывать на шаг, который нужно переспросить. "
        "Используй intent faq для вопросов об условиях, парке, офисе, выплатах, подарках, поддержке и похожих темах. "
        "Используй intent clarification, если пользователь не ответил на текущий шаг анкеты. "
        "Используй intent registration только когда сообщение действительно содержит ответ на текущий шаг. "
        "Поля extracted_fields заполняй только тем, что явно удалось извлечь из сообщения. "
        "Даты возвращай в формате YYYY-MM-DD. "
        "Телефон возвращай в международном формате с плюсом."
    )


def build_user_prompt(
    state: str,
    message: str,
    driver: Driver,
    knowledge_base: dict[str, str],
    allowed_states: list[str],
) -> str:
    kb_text = "\n\n".join(f"[{name}]\n{content}" for name, content in knowledge_base.items())
    vehicle = driver.vehicle
    current_state = DialogueState(state)
    current_prompt = PROMPTS.get(current_state, "")
    dialogue_mode = _dialogue_context(current_state)
    return (
        f"Текущее состояние: {state}\n"
        f"Режим диалога: {dialogue_mode}\n"
        f"Допустимые next_state: {', '.join(allowed_states)}\n"
        f"Текущий обязательный вопрос: {current_prompt}\n"
        "Уже собранные данные:\n"
        f"- full_name: {driver.full_name or ''}\n"
        f"- last_name: {driver.last_name or ''}\n"
        f"- first_name: {driver.first_name or ''}\n"
        f"- middle_name: {driver.middle_name or ''}\n"
        f"- phone: {driver.phone or ''}\n"
        f"- city: {driver.city or ''}\n"
        f"- address: {driver.address or ''}\n"
        f"- iin: {driver.iin or ''}\n"
        f"- birth_date: {driver.birth_date or ''}\n"
        f"- driving_experience_since: {driver.driving_experience_since or ''}\n"
        f"- driver_license_number: {driver.driver_license_number or ''}\n"
        f"- driver_license_issue_date: {driver.driver_license_issue_date or ''}\n"
        f"- driver_license_expires_at: {driver.driver_license_expires_at or ''}\n"
        f"- employment_type: {driver.employment_type or ''}\n"
        f"- hired_at: {driver.hired_at or ''}\n"
        f"- is_hearing_impaired: {driver.is_hearing_impaired or ''}\n"
        f"- brand: {vehicle.brand if vehicle else ''}\n"
        f"- model: {vehicle.model if vehicle else ''}\n"
        f"- year: {vehicle.year if vehicle else ''}\n"
        f"- plate_number: {vehicle.plate_number if vehicle else ''}\n"
        f"- color: {vehicle.color if vehicle else ''}\n"
        f"- vin: {vehicle.vin if vehicle else ''}\n\n"
        f"Сообщение водителя:\n{message}\n\n"
        "База знаний таксопарка:\n"
        f"{kb_text}\n\n"
        "Если сообщение относится к текущему шагу регистрации, извлеки поле и переведи на следующий шаг. "
        "Если это FAQ или обычный вопрос по теме работы, ответь по смыслу и оставь next_state равным текущему state. "
        "Если пользователь уходит в сторону от текущего шага, не заполняй extracted_fields."
    )


def _dialogue_context(state: DialogueState) -> str:
    if state == DialogueState.NEW:
        return "знакомство и старт регистрации"
    if state in {DialogueState.ASK_YANDEX_PRO_LOGIN, DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS}:
        return "помощь после отправки заявки в парк и вход в Яндекс Про"
    if state == DialogueState.COMPLETED:
        return "поддержка уже зарегистрированного водителя"
    return "сбор анкеты на регистрацию"
