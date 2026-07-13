from __future__ import annotations

import re
from dataclasses import dataclass

from app.messages.models import Message

MEDIA_PAYLOAD_KEYS = ("image", "document", "video", "audio", "sticker")


@dataclass(slots=True)
class MessageMediaInfo:
    available: bool
    media_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    kind: str = "unknown"
    preview_url: str | None = None


def _payload_media(raw_payload: dict | None) -> tuple[str | None, dict]:
    if not isinstance(raw_payload, dict):
        return None, {}
    for key in MEDIA_PAYLOAD_KEYS:
        media = raw_payload.get(key)
        if isinstance(media, dict) and media.get("id"):
            return key, media
    return None, {}


def resolve_message_media_id(message: Message) -> str | None:
    if message.media_url and not str(message.media_url).startswith(("http://", "https://", "/")):
        return str(message.media_url).strip() or None
    _, media = _payload_media(message.raw_payload)
    media_id = media.get("id")
    return str(media_id) if media_id else None


def message_media_info(message: Message) -> MessageMediaInfo:
    key, media = _payload_media(message.raw_payload)
    media_id = resolve_message_media_id(message)
    mime_type = message.mime_type or media.get("mime_type")
    filename = media.get("filename")
    kind = key or (message.message_type if message.message_type in MEDIA_PAYLOAD_KEYS else None)
    if not kind and mime_type:
        if str(mime_type).startswith("image/"):
            kind = "image"
        elif str(mime_type).startswith("video/"):
            kind = "video"
        elif str(mime_type).startswith("audio/"):
            kind = "audio"
        elif str(mime_type) == "application/pdf":
            kind = "document"
    kind = kind or "unknown"
    if not media_id and not (message.media_url and str(message.media_url).startswith(("http://", "https://", "/"))):
        return MessageMediaInfo(available=False, mime_type=mime_type, filename=filename, kind=kind)
    preview_url = f"/admin/api/messages/{message.id}/media"
    return MessageMediaInfo(
        available=True,
        media_id=media_id,
        mime_type=mime_type,
        filename=filename,
        kind=kind,
        preview_url=preview_url,
    )


def whatsapp_chat_url(phone: str | None) -> str | None:
    digits = re.sub(r"\D+", "", phone or "")
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    elif len(digits) == 10:
        digits = f"7{digits}"
    return f"https://wa.me/{digits}"
