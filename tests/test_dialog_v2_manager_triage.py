import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.dialog_v2.flows.manager import ManagerHandoffFlow
from app.dialog_v2.router import Router
from app.whatsapp.parser import ParsedWhatsAppMessage


class ManagerTriageTests(unittest.TestCase):
    def _driver(self):
        return SimpleNamespace(
            id=1,
            phone="+77001112233",
            whatsapp_phone="+77001112233",
            full_name=None,
            state="new",
            support_context_json={},
            dialog_mode="bot_active",
            requires_attention=False,
        )

    def test_manager_request_shows_triage_menu(self):
        driver = self._driver()
        message = ParsedWhatsAppMessage(sender_phone=driver.whatsapp_phone, message_type="text", text="менеджер")
        context = Router().route(None, driver, None, message)
        self.assertEqual(context.structured_reply.metadata.get("intent"), "manager_triage")
        self.assertEqual(driver.support_context_json.get("pending_menu"), "manager_triage")
        self.assertEqual(context.structured_reply.type, "buttons")
        self.assertIn("Регистрация", context.structured_reply.text)

    def test_triage_registration_starts_docs(self):
        driver = self._driver()
        driver.support_context_json = {"pending_menu": "manager_triage", "manager_triage_reason": "human_requested"}
        message = ParsedWhatsAppMessage(sender_phone=driver.whatsapp_phone, message_type="text", text="mgr_register")
        application = MagicMock()
        with patch("app.dialog_v2.flows.registration.set_application_status"), patch(
            "app.dialog_v2.flows.registration.flag_modified"
        ):
            reply = ManagerHandoffFlow().handle_triage_choice(None, driver, application, message)
        self.assertEqual(reply.metadata.get("intent"), "registration")
        self.assertIn("водительское удостоверение", reply.text.lower())
        self.assertNotIn("pending_menu", driver.support_context_json)

    def test_triage_human_creates_manager_ticket(self):
        driver = self._driver()
        driver.support_context_json = {"pending_menu": "manager_triage", "manager_triage_reason": "human_requested"}
        message = ParsedWhatsAppMessage(sender_phone=driver.whatsapp_phone, message_type="text", text="mgr_human")
        reply = ManagerHandoffFlow().handle_triage_choice(None, driver, None, message)
        self.assertTrue(reply.requires_manager)
        self.assertEqual(reply.metadata.get("intent"), "manager")
        self.assertTrue(driver.requires_attention)

    def test_payout_support_skips_triage(self):
        driver = self._driver()
        message = ParsedWhatsAppMessage(sender_phone=driver.whatsapp_phone, message_type="text", text="не могу вывести деньги")
        with patch("app.dialog_v2.flows.manager.ManagerHandoffFlow._last_messages", return_value=[]):
            context = Router().route(None, driver, None, message)
        self.assertEqual(context.flow, "support")
        self.assertTrue(context.structured_reply.requires_manager)
        self.assertNotEqual(driver.support_context_json.get("pending_menu"), "manager_triage")


if __name__ == "__main__":
    unittest.main()
