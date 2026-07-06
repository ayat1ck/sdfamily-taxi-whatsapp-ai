from __future__ import annotations

from app.config import get_settings
from app.dialog.faq import load_knowledge_base, resolve_faq_replies
from app.dialog_v2.response import StructuredReply
from app.utils.logger import get_logger
from app.utils.text import repair_mojibake
from app.utils.validators import normalize_text_token

logger = get_logger(__name__)


FAQ_STUB = {
    "\u0443\u0441\u043b\u043e\u0432\u0438\u044f": "\u0423\u0441\u043b\u043e\u0432\u0438\u044f: \u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044f 2%, \u043c\u043e\u043c\u0435\u043d\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u0432\u044b\u043f\u043b\u0430\u0442\u044b, \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430 24/7.",
    "\u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044f": "\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f \u043f\u0430\u0440\u043a\u0430 2%.",
    "\u0430\u0434\u0440\u0435\u0441": "\u041e\u0444\u0438\u0441: \u0410\u0441\u0442\u0430\u043d\u0430, \u0411\u0430\u043b\u043a\u0430\u043d\u0442\u0430\u0443 117.",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b": "\u0414\u043b\u044f \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438 \u043d\u0443\u0436\u043d\u044b \u0412\u0423 \u0438 \u0442\u0435\u0445\u043f\u0430\u0441\u043f\u043e\u0440\u0442 / \u0421\u0422\u0421.",
    "\u043a\u0430\u043a \u0437\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c\u0441\u044f": "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b \u0432 WhatsApp, \u0431\u043e\u0442 \u0441\u0430\u043c \u043f\u043e\u0434\u0441\u043a\u0430\u0436\u0435\u0442 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0448\u0430\u0433.",
    "\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f": "\u0414\u043b\u044f \u043d\u0430\u0447\u0430\u043b\u0430 \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b \u0438\u043b\u0438 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \"\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f\".",
    "\u0431\u043e\u043d\u0443\u0441": "\u041f\u043e \u0431\u043e\u043d\u0443\u0441\u0430\u043c \u0438 \u0411\u0430\u0439\u0433\u0435 \u0443\u0441\u043b\u043e\u0432\u0438\u044f \u043c\u043e\u0433\u0443\u0442 \u043c\u0435\u043d\u044f\u0442\u044c\u0441\u044f. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u0443, \u0438 \u043e\u043d \u043f\u043e\u0434\u0441\u043a\u0430\u0436\u0435\u0442 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u044b\u0435 \u0430\u043a\u0446\u0438\u0438.",
    "\u0431\u043e\u043d\u0443\u0441\u044b": "\u041f\u043e \u0431\u043e\u043d\u0443\u0441\u0430\u043c \u0438 \u0411\u0430\u0439\u0433\u0435 \u0443\u0441\u043b\u043e\u0432\u0438\u044f \u043c\u043e\u0433\u0443\u0442 \u043c\u0435\u043d\u044f\u0442\u044c\u0441\u044f. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u0443, \u0438 \u043e\u043d \u043f\u043e\u0434\u0441\u043a\u0430\u0436\u0435\u0442 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u044b\u0435 \u0430\u043a\u0446\u0438\u0438.",
    "\u0431\u0430\u0439\u0433\u0435": "\u041f\u043e \u0411\u0430\u0439\u0433\u0435 \u0438 \u0431\u043e\u043d\u0443\u0441\u0430\u043c \u0443\u0441\u043b\u043e\u0432\u0438\u044f \u043c\u043e\u0433\u0443\u0442 \u043c\u0435\u043d\u044f\u0442\u044c\u0441\u044f. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u0443, \u0438 \u043e\u043d \u043f\u043e\u0434\u0441\u043a\u0430\u0436\u0435\u0442 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u044b\u0435 \u0430\u043a\u0446\u0438\u0438.",
}

NO_FAQ_ANSWER = "\u041d\u0435 \u043d\u0430\u0448\u0451\u043b \u0442\u043e\u0447\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u0432 \u0431\u0430\u0437\u0435. \u0427\u0442\u043e\u0431\u044b \u043d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0442\u044c, \u043b\u0443\u0447\u0448\u0435 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \"\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\" - \u044f \u043f\u0435\u0440\u0435\u0434\u0430\u043c \u0432\u043e\u043f\u0440\u043e\u0441 \u0447\u0435\u043b\u043e\u0432\u0435\u043a\u0443."


class FAQFlow:
    def _load_kb(self) -> dict[str, str] | None:
        try:
            kb = load_knowledge_base()
        except Exception as exc:
            logger.exception("Failed to load FAQ knowledge_base: %s", exc)
            return None
        return kb or None

    def _matched_kb_key(self, answer: str, kb: dict[str, str]) -> str | None:
        for name, content in kb.items():
            if answer and answer in content:
                return name
        return None

    def _stub_reply(self, text: str) -> tuple[str | None, str | None]:
        normalized = normalize_text_token(repair_mojibake(text))
        for key, answer in FAQ_STUB.items():
            if key in normalized:
                return answer, key
        return None, None

    def handle(self, db, driver, application, message) -> StructuredReply:
        raw_text = repair_mojibake(message.text or "")
        kb = self._load_kb()
        if kb:
            answer = resolve_faq_replies(raw_text, kb, office_address=get_settings().public_site_address)
            if answer:
                return StructuredReply(
                    text=answer,
                    next_flow="faq",
                    flow_state="faq",
                    metadata={
                        "intent": "faq",
                        "faq_source": "knowledge_base",
                        "faq_matched_key": self._matched_kb_key(answer, kb),
                    },
                )
            return StructuredReply(
                text=NO_FAQ_ANSWER,
                next_flow="faq",
                flow_state="faq",
                metadata={"intent": "faq", "faq_source": "none", "faq_matched_key": None},
            )

        stub_answer, stub_key = self._stub_reply(raw_text)
        if stub_answer:
            return StructuredReply(
                text=stub_answer,
                next_flow="faq",
                flow_state="faq",
                metadata={"intent": "faq", "faq_source": "stub", "faq_matched_key": stub_key},
            )

        return StructuredReply(
            text=NO_FAQ_ANSWER,
            next_flow="faq",
            flow_state="faq",
            metadata={"intent": "faq", "faq_source": "none", "faq_matched_key": None},
        )
