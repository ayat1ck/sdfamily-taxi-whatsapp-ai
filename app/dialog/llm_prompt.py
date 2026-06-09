from app.dialog.prompts import PROMPTS
from app.dialog.states import DialogueState
from app.drivers.models import Driver


def build_system_prompt() -> str:
    return (
        "Ты AI-менеджер таксопарка в WhatsApp. "
        "Ты помогаешь водителю пройти регистрацию, разобраться с Яндекс Про и получить поддержку после подключения. "
        "Общайся только на русском языке, спокойно, по-человечески и по делу. "
        "Нельзя придумывать данные. "
        "Нужно строго вернуть JSON по заданной схеме. "
        "State machine обязательна: нельзя перепрыгивать шаги без причины. "
        "Если сообщение не похоже на ответ на текущий вопрос, не интерпретируй его как данные анкеты. "
        "Если пользователь задает вопрос по теме парка или Яндекс Про, ответь по смыслу и не меняй шаг без причины. "
        "Если пользователь подтверждает собранные данные, intent должен быть confirmation. "
        "Если пользователь на этапе confirm_data просит сразу поменять поле и дает новое значение, "
        "используй intent field_edit, next_state=confirm_data, target_field и normalized_fields. "
        "Если пользователь просит исправить поле, но не дал новое значение, используй clarification. "
        "Intent correction используй только когда нужно вернуть пользователя на шаг переспроса. "
        "Intent registration используй только когда сообщение действительно содержит ответ на текущий шаг. "
        "Поля extracted_fields заполняй только тем, что явно удалось извлечь из сообщения. "
        "Даты возвращай в формате YYYY-MM-DD. "
        "Телефон возвращай в международном формате с плюсом. "
        "Если не уверен, лучше clarification, чем ошибочное заполнение."
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
        f"- active_support_topic: {driver.active_support_topic or ''}\n"
        f"- active_support_step: {driver.active_support_step or ''}\n"
        f"- brand: {vehicle.brand if vehicle else ''}\n"
        f"- model: {vehicle.model if vehicle else ''}\n"
        f"- year: {vehicle.year if vehicle else ''}\n"
        f"- plate_number: {vehicle.plate_number if vehicle else ''}\n"
        f"- color: {vehicle.color if vehicle else ''}\n"
        f"- vin: {vehicle.vin if vehicle else ''}\n"
        f"- service_class: {vehicle.service_class if vehicle else ''}\n\n"
        f"Сообщение водителя:\n{message}\n\n"
        "База знаний таксопарка:\n"
        f"{kb_text}\n\n"
        "Если это ответ на текущий шаг регистрации, извлеки поле и переведи на следующий шаг. "
        "Если это FAQ или support-вопрос, ответь по смыслу и оставь next_state равным текущему состоянию. "
        "Если это field_edit, верни только целевое поле и нормализованное значение для записи."
    )


def _dialogue_context(state: DialogueState) -> str:
    if state == DialogueState.NEW:
        return "знакомство и старт регистрации"
    if state in {DialogueState.ASK_YANDEX_PRO_LOGIN, DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS}:
        return "помощь после отправки заявки в парк и вход в Яндекс Про"
    if state == DialogueState.COMPLETED:
        return "поддержка уже зарегистрированного водителя"
    return "сбор анкеты на регистрацию"
