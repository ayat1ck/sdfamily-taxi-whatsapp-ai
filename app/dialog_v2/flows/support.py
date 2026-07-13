from __future__ import annotations

from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.response import StructuredReply
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token


MANAGER_TOPICS = (
    "выплат",
    "вывод",
    "деньги",
    "денги",
    "снят",
    "ақша",
    "акша",
    "төлем",
    "толем",
    "блокиров",
    "тариф",
    "парк не",
    "парк не отобр",
    "не отображается парк",
    "яндекс",
    "yandex",
    "про ошибка",
    "смз",
    "самозан",
    "жалоб",
    "оператор",
    "менеджер",
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
