from dataclasses import dataclass


@dataclass
class ParsedWhatsAppMessage:
    sender_phone: str
    message_type: str
    text: str | None = None
    provider_message_id: str | None = None
    media_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    raw_payload: dict | None = None


def parse_whatsapp_payload(payload: dict) -> list[ParsedWhatsAppMessage]:
    parsed: list[ParsedWhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                sender = message.get("from", "")
                message_type = message.get("type", "unsupported")
                if message_type == "text":
                    parsed.append(
                        ParsedWhatsAppMessage(
                            sender_phone=sender,
                            message_type="text",
                            text=message.get("text", {}).get("body"),
                            provider_message_id=message.get("id"),
                            raw_payload=message,
                        )
                    )
                elif message_type in {"image", "document"}:
                    media_payload = message.get(message_type, {})
                    parsed.append(
                        ParsedWhatsAppMessage(
                            sender_phone=sender,
                            message_type=message_type,
                            provider_message_id=message.get("id"),
                            media_id=media_payload.get("id"),
                            mime_type=media_payload.get("mime_type"),
                            filename=media_payload.get("filename") or f"{message_type}.bin",
                            raw_payload=message,
                        )
                    )
                elif message_type == "interactive":
                    interactive = message.get("interactive") or {}
                    interactive_type = interactive.get("type")
                    choice_text = None
                    if interactive_type == "button_reply":
                        button_reply = interactive.get("button_reply") or {}
                        choice_text = button_reply.get("id") or button_reply.get("title")
                    elif interactive_type == "list_reply":
                        list_reply = interactive.get("list_reply") or {}
                        choice_text = list_reply.get("id") or list_reply.get("title")
                    if choice_text:
                        parsed.append(
                            ParsedWhatsAppMessage(
                                sender_phone=sender,
                                message_type="text",
                                text=str(choice_text),
                                provider_message_id=message.get("id"),
                                raw_payload=message,
                            )
                        )
                    else:
                        parsed.append(
                            ParsedWhatsAppMessage(
                                sender_phone=sender,
                                message_type="unsupported",
                                provider_message_id=message.get("id"),
                                raw_payload=message,
                            )
                        )
                else:
                    parsed.append(
                        ParsedWhatsAppMessage(
                            sender_phone=sender,
                            message_type="unsupported",
                            provider_message_id=message.get("id"),
                            raw_payload=message,
                        )
                    )
    return parsed
