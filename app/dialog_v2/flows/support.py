from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


MANAGER_TOPICS = (
    "\u0432\u044b\u043f\u043b\u0430\u0442",
    "\u0432\u044b\u0432\u043e\u0434",
    "\u0434\u0435\u043d\u044c\u0433\u0438",
    "\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432",
    "\u0442\u0430\u0440\u0438\u0444",
    "\u043f\u0430\u0440\u043a \u043d\u0435",
    "\u043f\u0430\u0440\u043a \u043d\u0435 \u043e\u0442\u043e\u0431\u0440\u0430\u0436",
    "\u043d\u0435 \u043e\u0442\u043e\u0431\u0440\u0430\u0436\u0430\u0435\u0442\u0441\u044f \u043f\u0430\u0440\u043a",
    "\u044f\u043d\u0434\u0435\u043a\u0441",
    "yandex",
    "\u043f\u0440\u043e \u043e\u0448\u0438\u0431\u043a\u0430",
    "\u0441\u043c\u0437",
    "\u0441\u0430\u043c\u043e\u0437\u0430\u043d",
    "\u0436\u0430\u043b\u043e\u0431",
    "\u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440",
    "\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440",
)


class SupportFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.faq = FAQFlow()
        self.manager = ManagerHandoffFlow()

    def handle(self, db, driver, application, message) -> StructuredReply:
        text = normalize_text_token(repair_mojibake(message.text or ""))
        if any(token in text for token in MANAGER_TOPICS):
            reply = self.manager.handle(db, driver, application, message, reason=(message.text or "").strip() or "support")
            reply.metadata["intent"] = "support"
            return reply
        return self.faq.handle(db, driver, application, message)
