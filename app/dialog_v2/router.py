from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified

from app.dialog_v2.context import DialogContext
from app.dialog_v2.fallback import FallbackPolicy
from app.dialog_v2.flows.existing_driver import ExistingDriverFlow
from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.flows.profile_update import ProfileUpdateFlow
from app.dialog_v2.flows.registration import RegistrationFlow
from app.dialog_v2.flows.support import SupportFlow
from app.dialog_v2.global_intents import GlobalIntentRouter
from app.dialog_v2.intent import (
    looks_like_existing_driver,
    looks_like_faq,
    looks_like_frustration,
    looks_like_profile_update,
    looks_like_support_escalation,
    normalize_intent_text,
)
from app.dialog_v2.states import DialogV2State
from app.dialog_v2.ui import MANAGER_TRIAGE_BUTTONS, buttons_reply
from app.messages.service import create_message
from app.whatsapp.parser import ParsedWhatsAppMessage

PROFILE_MENU_CHOICES = {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
EXISTING_MENU_CHOICES = {"1", "2", "3", "4", "5", "6"}

# Message types the bot cannot extract content from.
NON_PROCESSABLE_TYPES = {"audio", "voice", "sticker", "unsupported", "location", "contacts", "reaction"}

MENU_COMMANDS = {"меню", "menu", "главное меню", "мәзір", "мазір", "мазир"}

MENU_TEXT = "Чем помочь? Выберите вариант или напишите своими словами:"

REGISTRATION_STATES = {
    DialogV2State.REGISTRATION_DOCUMENT_COLLECTION,
    DialogV2State.REGISTRATION_MISSING_FIELDS,
    DialogV2State.REGISTRATION_CONFIRMATION,
    DialogV2State.READY_TO_SEND_YANDEX,
}

FALLBACK_MENU_CHOICES = {
    "mgr_register": "register",
    "1": "register",
    "регистрация": "register",
    "mgr_help": "existing",
    "2": "existing",
    "уже водитель": "existing",
    "mgr_human": "manager",
    "3": "manager",
    "менеджер": "manager",
    "менеджеру": "manager",
}


class Router:
    def __init__(self) -> None:
        self.registration = RegistrationFlow()
        self.existing_driver = ExistingDriverFlow()
        self.profile_update = ProfileUpdateFlow()
        self.support = SupportFlow()
        self.faq = FAQFlow()
        self.manager = ManagerHandoffFlow()
        self.global_intents = GlobalIntentRouter()
        self.fallback = FallbackPolicy()

    def _store_incoming(self, db, driver, message: ParsedWhatsAppMessage) -> None:
        if db is None:
            return
        try:
            create_message(
                db,
                driver=driver,
                direction="incoming",
                sender_type="customer",
                message_type=message.message_type,
                text=message.text,
                provider_message_id=message.provider_message_id,
                media_url=message.media_id,
                mime_type=message.mime_type,
                raw_payload=message.raw_payload,
                delivery_status="received",
            )
        except Exception:
            pass

    def _clear_pending_menu(self, driver) -> None:
        context = dict(driver.support_context_json or {})
        if "pending_menu" in context:
            context.pop("pending_menu", None)
            driver.support_context_json = context
            try:
                flag_modified(driver, "support_context_json")
            except Exception:
                pass

    def _fallback_menu_choice(self, db, driver, application, message: ParsedWhatsAppMessage):
        choice = FALLBACK_MENU_CHOICES.get(normalize_intent_text(message.text))
        if choice is None:
            self._clear_pending_menu(driver)
            return None
        self._clear_pending_menu(driver)
        self.fallback.reset_misses(driver)
        if choice == "register":
            return self.registration.start(db, driver, application)
        if choice == "existing":
            return self.existing_driver.handle(db, driver, application, message)
        return self.manager.handle(db, driver, application, message, reason="human_requested", skip_triage=True)

    def _show_main_menu(self, driver):
        context = dict(driver.support_context_json or {})
        context["pending_menu"] = "fallback_menu"
        driver.support_context_json = context
        try:
            flag_modified(driver, "support_context_json")
        except Exception:
            pass
        return buttons_reply(
            MENU_TEXT,
            MANAGER_TRIAGE_BUTTONS,
            flow="menu",
            state=driver.state,
            metadata={"intent": "main_menu"},
        )

    def _pending_menu(self, db, driver, application, message: ParsedWhatsAppMessage):
        context = dict(driver.support_context_json or {})
        pending_menu = context.get("pending_menu")
        text = (message.text or "").strip()
        if pending_menu == "existing_driver_main":
            if text in EXISTING_MENU_CHOICES:
                return self.existing_driver.handle(db, driver, application, message)
            if (
                looks_like_profile_update(message.text)
                or looks_like_faq(message.text)
                or looks_like_support_escalation(message.text)
                or looks_like_existing_driver(message.text)
            ):
                context.pop("pending_menu", None)
                driver.support_context_json = context
                flag_modified(driver, "support_context_json")
                return None
            return self.existing_driver.handle(db, driver, application, message)
        if pending_menu == "profile_update_menu":
            if text in PROFILE_MENU_CHOICES:
                return self.profile_update.handle(db, driver, application, message)
            context.pop("pending_menu", None)
            driver.support_context_json = context
            flag_modified(driver, "support_context_json")
            return None
        if pending_menu == "manager_triage":
            return self.manager.handle_triage_choice(db, driver, application, message)
        if pending_menu == "fallback_menu":
            return self._fallback_menu_choice(db, driver, application, message)
        if pending_menu == "confirm_document_type":
            return self.registration.handle_text(db, driver, application, message)
        if pending_menu == "registration_edit_fields":
            return self.global_intents.handle(db, driver, application, message, registration_flow=self.registration)
        return None

    def route(self, db, driver, application, message: ParsedWhatsAppMessage) -> DialogContext:
        self.fallback.touch_session(driver)
        context = self._route(db, driver, application, message)
        reply = context.structured_reply
        guarded = self.fallback.guard_repeat(db, driver, application, message, reply)
        if guarded is not reply:
            return DialogContext(flow="manager", stage="manager", intent="manager", structured_reply=guarded)
        return context

    def _route(self, db, driver, application, message: ParsedWhatsAppMessage) -> DialogContext:
        global_reply = self.global_intents.handle(db, driver, application, message, registration_flow=self.registration)
        if global_reply is not None:
            self.fallback.reset_misses(driver)
            return DialogContext(flow=global_reply.flow or "global", stage=global_reply.state or driver.state, intent=global_reply.metadata.get("intent", "global"), structured_reply=global_reply)

        if message.message_type in {"image", "document"}:
            self.fallback.reset_misses(driver)
            reply = self.registration.handle_document(db, driver, application, message)
            return DialogContext(flow="registration", stage=driver.state, intent=reply.metadata.get("intent", "registration"), structured_reply=reply)

        if message.message_type in NON_PROCESSABLE_TYPES or message.message_type == "video":
            self._store_incoming(db, driver, message)
            kind = message.message_type if message.message_type in {"audio", "video", "sticker"} else "unsupported"
            reply = self.fallback.handle_miss(db, driver, application, message, kind=kind)
            return DialogContext(flow="fallback", stage=driver.state, intent=reply.metadata.get("intent", "fallback"), structured_reply=reply)

        if looks_like_frustration(message.text):
            self._store_incoming(db, driver, message)
            self._clear_pending_menu(driver)
            reply = self.manager.handle(db, driver, application, message, reason="frustration", skip_triage=True)
            return DialogContext(flow="manager", stage="manager", intent="manager", structured_reply=reply)

        if normalize_intent_text(message.text) in MENU_COMMANDS:
            self._store_incoming(db, driver, message)
            self.fallback.reset_misses(driver)
            reply = self._show_main_menu(driver)
            return DialogContext(flow="menu", stage=driver.state, intent="main_menu", structured_reply=reply)

        pending_reply = self._pending_menu(db, driver, application, message)
        if pending_reply is not None:
            self.fallback.reset_misses(driver)
            return DialogContext(flow=pending_reply.flow or pending_reply.next_flow or "pending_menu", stage=pending_reply.state or pending_reply.flow_state or driver.state, intent=pending_reply.metadata.get("intent", "pending_menu"), structured_reply=pending_reply)

        if self.manager.should_handoff(driver, message):
            self.fallback.reset_misses(driver)
            reply = self.manager.offer_triage(driver, reason=(message.text or "").strip() or "human_requested")
            return DialogContext(flow="manager", stage="manager_triage", intent="manager_triage", structured_reply=reply)

        if looks_like_existing_driver(message.text):
            self.fallback.reset_misses(driver)
            reply = self.existing_driver.handle(db, driver, application, message)
            return DialogContext(flow="existing_driver", stage=driver.state, intent="existing_driver", structured_reply=reply)

        if looks_like_profile_update(message.text):
            self.fallback.reset_misses(driver)
            reply = self.profile_update.handle(db, driver, application, message)
            return DialogContext(flow="profile_update", stage="requested", intent="profile_update", structured_reply=reply)

        if looks_like_support_escalation(message.text):
            self.fallback.reset_misses(driver)
            reply = self.support.handle(db, driver, application, message)
            return DialogContext(flow="support", stage="requested", intent=reply.metadata.get("intent", "support"), structured_reply=reply)

        if looks_like_faq(message.text):
            self.fallback.reset_misses(driver)
            reply = self.faq.handle(db, driver, application, message)
            return DialogContext(flow="faq", stage="answered", intent="faq", structured_reply=reply)

        if driver.state == DialogV2State.NEW or driver.state in REGISTRATION_STATES:
            reply = self.registration.handle_text(db, driver, application, message)
            return DialogContext(flow="registration", stage=driver.state, intent=reply.metadata.get("intent", "registration"), structured_reply=reply)

        # Driver is outside registration (completed, manager handoff, etc.) and
        # nothing matched — do not push documents at them, clarify instead.
        self._store_incoming(db, driver, message)
        reply = self.fallback.handle_miss(db, driver, application, message, kind="unclear_text")
        return DialogContext(flow="fallback", stage=driver.state, intent=reply.metadata.get("intent", "fallback"), structured_reply=reply)
