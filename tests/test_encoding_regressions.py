import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.dialog.engine import DialogueEngine
from app.dialog.states import DialogueState


class DummyDB:
    def add(self, obj):
        self.last_added = obj


class EncodingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = DialogueEngine.__new__(DialogueEngine)
        self.engine._record_system_trace = lambda *args, **kwargs: None
        self.engine.settings = SimpleNamespace(public_site_address="")

    def _driver(self, context=None):
        return SimpleNamespace(
            whatsapp_phone="+77001234567",
            phone="+77001234567",
            support_context_json=context,
            dialog_mode="bot_active",
            requires_attention=False,
            vehicle=None,
            full_name="Иван Иванов",
            driver_license_number="DL-123",
            iin="123456789012",
            updated_at=None,
            id=1,
        )

    def test_existing_driver_phrase_returns_clean_menu_text(self):
        driver = self._driver({"mode": "registration"})
        application = SimpleNamespace(status="collecting_data")

        with patch("app.dialog.engine.create_conversation_event"), patch("app.dialog.engine.set_application_status"):
            reply = self.engine._handle_priority_interrupts(
                DummyDB(),
                driver,
                application,
                DialogueState.ASK_FULL_NAME,
                "Я уже зарегистрирован, тех поддержка нужна",
                1,
            )

        self.assertIsInstance(reply, str)
        self.assertIn("Понял, вы уже подключены", reply)
        self.assertIn("1. Вывод денег", reply)
        self.assertIn("2. Вход в Яндекс Про", reply)
        self.assertNotIn("Р ", reply)
        self.assertNotIn("�", reply)
        self.assertEqual(driver.support_context_json["mode"], "existing_driver_support")
        self.assertEqual(driver.support_context_json["menu"], "existing_driver_main")


if __name__ == "__main__":
    unittest.main()
