from __future__ import annotations

from app.dialog_v2.response import StructuredReply


FAQ_KB = {
    "условия": "Условия: комиссия 2%, моментальные выплаты, поддержка 24/7.",
    "комиссия": "Комиссия парка 2%.",
    "адрес": "Офис: Астана, Балкантау 117.",
    "документы": "Для регистрации нужны ВУ, удостоверение личности и техпаспорт / СТС.",
    "как зарегистрироваться": "Отправьте документы в WhatsApp, бот сам подскажет следующий шаг.",
    "регистрация": "Для начала регистрации отправьте документы или напишите «Регистрация».",
}


class FAQFlow:
    def handle(self, db, driver, application, message) -> StructuredReply:
        text = (message.text or "").lower()
        for key, answer in FAQ_KB.items():
            if key in text:
                return StructuredReply(text=answer, next_flow="faq", flow_state="faq", metadata={"intent": "faq"})
        return StructuredReply(
            text="Если хотите, помогу с регистрацией или передам вопрос менеджеру.",
            next_flow="faq",
            flow_state="faq",
            metadata={"intent": "faq"},
        )
