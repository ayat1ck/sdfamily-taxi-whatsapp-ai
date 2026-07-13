import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.dialog_v2.flows.faq import FAQFlow
from app.dialog_v2.flows.support import SupportFlow


CONDITIONS_TEXT = "\u043a\u0430\u043a\u0438\u0435 \u0443\u0441\u043b\u043e\u0432\u0438\u044f"
ADDRESS_TEXT = "\u0430\u0434\u0440\u0435\u0441"
OFFICE_ANSWER = "\u041e\u0444\u0438\u0441: \u0411\u0430\u043b\u043a\u0430\u043d\u0442\u0430\u0443"
PAYOUT_TEXT = "\u043d\u0435 \u043c\u043e\u0433\u0443 \u0432\u044b\u0432\u0435\u0441\u0442\u0438 \u0434\u0435\u043d\u044c\u0433\u0438"
CONDITIONS_KEY = "\u0443\u0441\u043b\u043e\u0432\u0438\u044f"


class DialogV2FAQFlowTests(unittest.TestCase):
    def test_conditions_answer_comes_from_knowledge_base(self):
        with patch("app.dialog_v2.flows.faq.load_knowledge_base", return_value={"park_info": "kb"}), patch(
            "app.dialog_v2.flows.faq.resolve_faq_replies", return_value="KB conditions answer"
        ):
            reply = FAQFlow().handle(None, None, None, SimpleNamespace(text=CONDITIONS_TEXT))

        self.assertEqual(reply.text, "KB conditions answer")
        self.assertEqual(reply.metadata["faq_source"], "knowledge_base")

    def test_address_answer_comes_from_knowledge_base(self):
        with patch("app.dialog_v2.flows.faq.load_knowledge_base", return_value={"park_info": OFFICE_ANSWER}), patch(
            "app.dialog_v2.flows.faq.resolve_faq_replies", return_value=OFFICE_ANSWER
        ):
            reply = FAQFlow().handle(None, None, None, SimpleNamespace(text=ADDRESS_TEXT))

        self.assertEqual(reply.text, OFFICE_ANSWER)
        self.assertEqual(reply.metadata["faq_source"], "knowledge_base")
        self.assertEqual(reply.metadata["faq_matched_key"], "park_info")

    def test_payout_problem_goes_to_manager_not_faq(self):
        driver = SimpleNamespace(
            id=1,
            phone="+77001112233",
            whatsapp_phone="+77001112233",
            full_name=None,
            support_context_json={},
            dialog_mode="bot_active",
            requires_attention=False,
        )
        message = SimpleNamespace(text=PAYOUT_TEXT)
        with patch("app.dialog_v2.flows.manager.ManagerHandoffFlow._last_messages", return_value=[]):
            reply = SupportFlow().handle(None, driver, None, message)

        self.assertEqual(reply.flow, "manager")
        self.assertTrue(reply.requires_manager)
        self.assertIsNotNone(reply.manager_alert)

    def test_stub_fallback_when_knowledge_base_unavailable(self):
        with patch("app.dialog_v2.flows.faq.load_knowledge_base", side_effect=RuntimeError("no kb")):
            reply = FAQFlow().handle(None, None, None, SimpleNamespace(text=CONDITIONS_TEXT))

        self.assertEqual(reply.metadata["faq_source"], "stub")
        self.assertEqual(reply.metadata["faq_matched_key"], CONDITIONS_KEY)

    def test_rental_question_routes_to_faq_not_registration(self):
        from app.dialog_v2.intent import looks_like_faq
        from app.dialog_v2.router import Router
        from app.whatsapp.parser import ParsedWhatsAppMessage

        question = "Есть ли свои машины или аренда?"
        self.assertTrue(looks_like_faq(question))

        driver = SimpleNamespace(
            id=1,
            phone="+77001112233",
            whatsapp_phone="+77001112233",
            full_name=None,
            state="new",
            support_context_json={},
            dialog_mode="bot_active",
            requires_attention=False,
        )
        message = ParsedWhatsAppMessage(sender_phone="+77001112233", message_type="text", text=question)
        with patch("app.dialog_v2.flows.faq.load_knowledge_base", return_value={"car_requirements": "kb"}), patch(
            "app.dialog_v2.flows.faq.resolve_faq_replies",
            return_value="Пока что аренды машин у таксопарка нет. Сейчас подключаем только водителей со своими автомобилями.",
        ):
            context = Router().route(None, driver, None, message)

        self.assertEqual(context.flow, "faq")
        self.assertIn("аренды", context.structured_reply.text.lower())
        self.assertNotIn("документы для регистрации", context.structured_reply.text.lower())

    def test_who_are_you_routes_to_faq_not_registration(self):
        from app.dialog_v2.intent import looks_like_faq
        from app.dialog_v2.router import Router
        from app.whatsapp.parser import ParsedWhatsAppMessage

        question = "Кто вы?"
        self.assertTrue(looks_like_faq(question))

        driver = SimpleNamespace(
            id=1,
            phone="+77001112233",
            whatsapp_phone="+77001112233",
            full_name=None,
            state="new",
            support_context_json={},
            dialog_mode="bot_active",
            requires_attention=False,
        )
        message = ParsedWhatsAppMessage(sender_phone="+77001112233", message_type="text", text=question)
        with patch("app.dialog_v2.flows.faq.load_knowledge_base", return_value={"park_info": "kb"}), patch(
            "app.dialog_v2.flows.faq.resolve_faq_replies",
            return_value="Мы таксопарк SD Family Taxi. Подключаем водителей, помогаем с регистрацией.",
        ):
            context = Router().route(None, driver, None, message)

        self.assertEqual(context.flow, "faq")
        self.assertIn("SD Family Taxi", context.structured_reply.text)
        self.assertNotIn("документы для регистрации", context.structured_reply.text.lower())

    def test_payout_typo_routes_to_support_not_registration(self):
        from app.dialog_v2.intent import looks_like_faq, looks_like_support_escalation
        from app.dialog_v2.router import Router
        from app.whatsapp.parser import ParsedWhatsAppMessage

        question = "Хотел снять денги со счета"
        self.assertTrue(looks_like_support_escalation(question))
        self.assertFalse(looks_like_faq(question))

        driver = SimpleNamespace(
            id=1,
            phone="+77001112233",
            whatsapp_phone="+77001112233",
            full_name=None,
            state="new",
            support_context_json={},
            dialog_mode="bot_active",
            requires_attention=False,
        )
        message = ParsedWhatsAppMessage(sender_phone="+77001112233", message_type="text", text=question)
        with patch("app.dialog_v2.flows.manager.ManagerHandoffFlow._last_messages", return_value=[]):
            context = Router().route(None, driver, None, message)

        self.assertEqual(context.flow, "support")
        self.assertNotIn("регистрац", context.structured_reply.text.lower())

    def test_dry_fog_routes_to_faq(self):
        from app.dialog_v2.intent import looks_like_faq

        self.assertTrue(looks_like_faq("Сухой туманды колдансак болама"))


if __name__ == "__main__":
    unittest.main()
