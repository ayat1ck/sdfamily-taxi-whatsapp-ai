from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.response import StructuredReply


class SupportFlow:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.faq = FAQFlow()

    def handle(self, db, driver, application, message) -> StructuredReply:
        text = (message.text or "").lower()
        if any(token in text for token in ("деньги", "тариф", "блокировк", "смз", "жалоб", "яндекс", "оператор", "менеджер")):
            context = dict(driver.support_context_json or {})
            context["support_reason"] = message.text
            driver.support_context_json = context
            driver.requires_attention = True
            self.bus.emit(db, driver, "support_ticket_created", {"reason": message.text})
            return StructuredReply(
                text="Передал вопрос менеджеру.",
                next_flow="manager",
                requires_manager=True,
                flow_state="manager",
                metadata={"intent": "support"},
            )
        return self.faq.handle(db, driver, application, message)
