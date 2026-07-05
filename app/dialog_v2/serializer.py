from __future__ import annotations

from app.dialog_v2.response import StructuredReply


def _button_title(button: dict[str, object] | str) -> str:
    if isinstance(button, str):
        return button
    reply = button.get("reply")
    if isinstance(reply, dict):
        title = reply.get("title")
        if isinstance(title, str):
            return title
    title = button.get("title")
    return title if isinstance(title, str) else ""


def _list_row_title(item: dict[str, object] | str) -> str:
    if isinstance(item, str):
        return item
    title = item.get("title")
    return title if isinstance(title, str) else ""


def _list_row_description(item: dict[str, object] | str) -> str:
    if isinstance(item, str):
        return ""
    description = item.get("description")
    return description if isinstance(description, str) else ""


def _normalize_button(item: dict[str, object] | str) -> dict[str, object]:
    if isinstance(item, str):
        return {"type": "reply", "reply": {"id": item, "title": item}}
    return item


def _normalize_list_item(item: dict[str, object] | str) -> dict[str, object]:
    if isinstance(item, str):
        return {"id": item, "title": item, "description": ""}
    return item


def build_text_fallback(reply: StructuredReply) -> str:
    lines: list[str] = []
    if reply.text.strip():
        lines.append(reply.text.strip())
    if reply.type == "buttons":
        for index, button in enumerate(reply.buttons, start=1):
            title = _button_title(button)
            if title:
                lines.append(f"{index}. {title}")
    elif reply.type == "list":
        for index, item in enumerate(reply.list_items, start=1):
            title = _list_row_title(item)
            description = _list_row_description(item)
            if title:
                suffix = f" - {description}" if description else ""
                lines.append(f"{index}. {title}{suffix}")
    return "\n".join(lines).strip()


def validate_reply_for_interactive(reply: StructuredReply) -> list[str]:
    errors: list[str] = []
    if reply.type == "buttons":
        if len(reply.buttons) > 3:
            errors.append("too_many_buttons")
        for button in reply.buttons:
            title = _button_title(button)
            if not title or len(title) > 20:
                errors.append("invalid_button_title")
                break
    elif reply.type == "list":
        for item in reply.list_items:
            title = _list_row_title(item)
            description = _list_row_description(item)
            if not title or not description:
                errors.append("invalid_list_row")
                break
    return errors


def serialize_reply(reply: StructuredReply, to_phone: str) -> dict[str, object]:
    payload_type = reply.type or "text"
    to = to_phone.lstrip("+")
    if payload_type in {"buttons", "list"}:
        errors = validate_reply_for_interactive(reply)
        if errors:
            return {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": build_text_fallback(reply)},
                "fallback_reason": errors,
            }
    if payload_type == "buttons":
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": reply.text},
                "action": {"buttons": [_normalize_button(btn) for btn in reply.buttons[:3]]},
            },
        }
    if payload_type == "list":
        sections = [
            {
                "title": "Выберите вариант",
                "rows": [_normalize_list_item(item) for item in reply.list_items],
            }
        ]
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": reply.text},
                "action": {
                    "button": "Выбрать",
                    "sections": sections,
                },
            },
        }
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": reply.text},
    }
