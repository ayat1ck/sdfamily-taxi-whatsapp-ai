from __future__ import annotations

from app.conversation_events.service import create_conversation_event
from app.dialog_v2.response import StructuredReply


class EventBus:
    def emit(self, db, driver, event_type: str, payload: dict | None = None, reply: StructuredReply | None = None):
        event = {"type": event_type, "payload": payload or {}}
        if reply is not None:
            reply.append_event(event)
        if db is None or driver is None:
            return event
        create_conversation_event(db, driver, event_type, payload)
        return event
