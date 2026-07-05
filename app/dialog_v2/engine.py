from __future__ import annotations

from datetime import datetime

from app.applications.service import get_or_create_application
from app.config import get_settings
from app.conversation_events.service import create_conversation_event
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.router import Router
from app.whatsapp.parser import ParsedWhatsAppMessage


class DialogV2Engine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.router = Router()

    def handle_message(self, db, driver, parsed: ParsedWhatsAppMessage) -> StructuredReply:
        application = get_or_create_application(db, driver)
        driver.last_message_at = datetime.utcnow()
        context = self.router.route(db, driver, application, parsed)
        reply = context.structured_reply or StructuredReply(text="")
        if reply.requires_manager:
            create_conversation_event(db, driver, "manager_handoff_requested")
        return reply


def handle_message_v2(db, driver, parsed: ParsedWhatsAppMessage) -> StructuredReply:
    return DialogV2Engine().handle_message(db, driver, parsed)
