from __future__ import annotations

from dataclasses import dataclass

from app.dialog_v2.response import StructuredReply


@dataclass(slots=True)
class DialogContext:
    flow: str = "router"
    stage: str = "start"
    intent: str = "unknown"
    structured_reply: StructuredReply | None = None
