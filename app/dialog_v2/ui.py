from __future__ import annotations

from app.dialog_v2.response import StructuredReply


def reply_button(button_id: str, title: str) -> dict[str, object]:
    return {"type": "reply", "reply": {"id": button_id, "title": title[:20]}}


def list_row(row_id: str, title: str, description: str) -> dict[str, object]:
    return {
        "id": row_id,
        "title": title[:24],
        "description": description[:72],
    }


def buttons_reply(
    text: str,
    buttons: list[dict[str, object] | str],
    *,
    flow: str | None = None,
    state: str | None = None,
    next_flow: str | None = None,
    flow_state: str | None = None,
    metadata: dict[str, object] | None = None,
    **kwargs,
) -> StructuredReply:
    return StructuredReply(
        type="buttons",
        text=text,
        buttons=buttons[:3],
        flow=flow,
        state=state,
        next_flow=next_flow,
        flow_state=flow_state,
        metadata=metadata or {},
        **kwargs,
    )


def list_reply(
    text: str,
    items: list[dict[str, object] | str],
    *,
    flow: str | None = None,
    state: str | None = None,
    next_flow: str | None = None,
    flow_state: str | None = None,
    metadata: dict[str, object] | None = None,
    **kwargs,
) -> StructuredReply:
    return StructuredReply(
        type="list",
        text=text,
        list_items=items,
        flow=flow,
        state=state,
        next_flow=next_flow,
        flow_state=flow_state,
        metadata=metadata or {},
        **kwargs,
    )


CONFIRM_BUTTONS = [
    reply_button("confirm", "Подтверждаю"),
    reply_button("edit", "Исправить"),
    reply_button("manager", "Менеджер"),
]

DOCUMENT_TYPE_LIST = [
    list_row("1", "ВУ", "Водительское удостоверение"),
    list_row("2", "Техпаспорт", "Техпаспорт / СТС авто"),
    list_row("3", "Селфи с ВУ", "Селфи с водительским удостоверением"),
]

EXISTING_DRIVER_MENU_LIST = [
    list_row("1", "Выплаты", "Вопросы по выплатам и балансу"),
    list_row("2", "Тарифы", "Комиссия и тарифы парка"),
    list_row("3", "Яндекс Про", "Вход и отображение парка"),
    list_row("4", "Изменить данные", "ФИО, авто, документы"),
    list_row("5", "Блокировка", "Заказы, блокировки, жалобы"),
    list_row("6", "Менеджер", "Связаться с живым менеджером"),
]

REGISTRATION_EDIT_LIST = [
    list_row("fix_full_name", "ФИО", "Исправить фамилию, имя, отчество"),
    list_row("fix_phone", "Телефон", "Исправить контактный номер"),
    list_row("fix_city", "Город", "Исправить город или адрес"),
    list_row("fix_iin", "ИИН", "Исправить ИИН"),
    list_row("fix_plate", "Госномер", "Исправить госномер авто"),
    list_row("fix_experience", "Стаж", "Исправить дату начала стажа"),
    list_row("fix_license", "ВУ", "Заменить фото водительских прав"),
    list_row("fix_vehicle", "Авто / СТС", "Заменить техпаспорт или авто"),
    list_row("fix_document", "Документ", "Заменить другой документ"),
]

PROFILE_UPDATE_LIST = [
    list_row("1", "ФИО", "Изменить фамилию, имя, отчество"),
    list_row("2", "Телефон", "Изменить контактный номер"),
    list_row("3", "Город/адрес", "Изменить город или адрес"),
    list_row("4", "Автомобиль", "Изменить марку и модель"),
    list_row("5", "Госномер", "Изменить госномер"),
    list_row("6", "СТС", "Изменить техпаспорт / СТС"),
    list_row("7", "ВУ", "Изменить водительское удостоверение"),
    list_row("8", "СМЗ", "Тип сотрудничества"),
    list_row("9", "Менеджер", "Передать запрос менеджеру"),
]

EDIT_ACTION_BY_ID = {
    "fix_full_name": "replace_full_name",
    "fix_phone": "replace_phone",
    "fix_city": "replace_city_address",
    "fix_iin": "replace_iin",
    "fix_plate": "replace_plate",
    "fix_experience": "replace_driving_experience",
    "fix_license": "replace_driver_license",
    "fix_vehicle": "replace_vehicle",
    "fix_document": "replace_document",
}


def is_confirm_choice(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"confirm", "подтверждаю", "все верно", "всё верно", "дұрыс", "ok", "ок", "да"}


def is_edit_choice(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"edit", "исправить", "изменить", "поменять"}


def is_manager_choice(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"manager", "менеджер", "оператор"}
