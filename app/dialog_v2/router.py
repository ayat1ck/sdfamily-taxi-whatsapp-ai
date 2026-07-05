from __future__ import annotations

from app.dialog_v2.context import DialogContext
from app.dialog_v2.flows.existing_driver import ExistingDriverFlow
from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.flows.profile_update import ProfileUpdateFlow
from app.dialog_v2.flows.registration import RegistrationFlow
from app.dialog_v2.flows.support import SupportFlow
from app.dialog_v2.intent import (
    looks_like_existing_driver,
    looks_like_faq,
    looks_like_profile_update,
    looks_like_support_escalation,
)
from app.whatsapp.parser import ParsedWhatsAppMessage


class Router:
    def __init__(self) -> None:
        self.registration = RegistrationFlow()
        self.existing_driver = ExistingDriverFlow()
        self.profile_update = ProfileUpdateFlow()
        self.support = SupportFlow()
        self.faq = FAQFlow()
        self.manager = ManagerHandoffFlow()

    def _pending_menu(self, db, driver, application, message: ParsedWhatsAppMessage):
        context = dict(driver.support_context_json or {})
        pending_menu = context.get("pending_menu")
        if pending_menu == "existing_driver_main":
            return self.existing_driver.handle(db, driver, application, message)
        if pending_menu == "profile_update_menu":
            return self.profile_update.handle(db, driver, application, message)
        if pending_menu == "confirm_document_type":
            return self.registration.handle_text(db, driver, application, message)
        return None

    def route(self, db, driver, application, message: ParsedWhatsAppMessage) -> DialogContext:
        if message.message_type in {"image", "document"}:
            reply = self.registration.handle_document(db, driver, application, message)
            return DialogContext(flow="registration", stage=driver.state, intent=reply.metadata.get("intent", "registration"), structured_reply=reply)

        pending_reply = self._pending_menu(db, driver, application, message)
        if pending_reply is not None:
            return DialogContext(flow=pending_reply.flow or pending_reply.next_flow or "pending_menu", stage=pending_reply.state or pending_reply.flow_state or driver.state, intent=pending_reply.metadata.get("intent", "pending_menu"), structured_reply=pending_reply)

        if self.manager.should_handoff(driver, message):
            reply = self.manager.handle(db, driver, application, message)
            return DialogContext(flow="manager", stage="manual", intent="manager", structured_reply=reply)

        if looks_like_existing_driver(message.text):
            reply = self.existing_driver.handle(db, driver, application, message)
            return DialogContext(flow="existing_driver", stage=driver.state, intent="existing_driver", structured_reply=reply)

        if looks_like_profile_update(message.text):
            reply = self.profile_update.handle(db, driver, application, message)
            return DialogContext(flow="profile_update", stage="requested", intent="profile_update", structured_reply=reply)

        if looks_like_support_escalation(message.text):
            reply = self.support.handle(db, driver, application, message)
            return DialogContext(flow="support", stage="requested", intent=reply.metadata.get("intent", "support"), structured_reply=reply)

        if looks_like_faq(message.text):
            reply = self.faq.handle(db, driver, application, message)
            return DialogContext(flow="faq", stage="answered", intent="faq", structured_reply=reply)

        reply = self.registration.handle_text(db, driver, application, message)
        return DialogContext(flow="registration", stage=driver.state, intent=reply.metadata.get("intent", "registration"), structured_reply=reply)
