import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from app.dialog.engine import DialogueEngine
    from app.dialog.states import DialogueState
    from app.dialog.faq import classify_dialog_intent
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal runtimes
    DialogueEngine = None
    DialogueState = None
    classify_dialog_intent = None


class DummyDB:
    def add(self, obj):
        self.last_added = obj


class StatefulSupportMenuTests(unittest.TestCase):
    def setUp(self):
        if DialogueEngine is None:
            self.skipTest("runtime dependencies unavailable")
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
            vehicle=SimpleNamespace(id=7, brand="Toyota", model="Camry", year="2020", plate_number="123ABC"),
            full_name="Иван Иванов",
            driver_license_number="DL-123",
            iin="123456789012",
            updated_at=None,
            id=1,
        )

    def test_existing_driver_phrase_opens_stateful_menu_in_registration(self):
        driver = self._driver({"mode": "registration"})
        application = SimpleNamespace(status="collecting_data")
        with patch("app.dialog.engine.create_conversation_event"), patch("app.dialog.engine.set_application_status"):
            reply = self.engine._handle_priority_interrupts(
                DummyDB(),
                driver,
                application,
                DialogueState.ASK_FULL_NAME,
                "я уже подключен",
                1,
            )
        self.assertIn("Вывод денег", reply)
        self.assertEqual(driver.support_context_json["mode"], "existing_driver_support")
        self.assertEqual(driver.support_context_json["menu"], "existing_driver_main")

    def test_existing_driver_menu_option_four_requests_profile_update(self):
        driver = self._driver(
            {
                "mode": "existing_driver_support",
                "menu": "existing_driver_main",
                "created_at": "2026-01-01T00:00:00",
                "expires_at": "2099-01-01T00:30:00",
            }
        )
        application = SimpleNamespace(status="collecting_data")
        with patch("app.dialog.engine.find_driver_by_whatsapp_phone", return_value=driver), patch(
            "app.dialog.engine.create_conversation_event"
        ):
            reply = self.engine._handle_stateful_support_menu(
                DummyDB(),
                driver,
                application,
                DialogueState.ASK_FULL_NAME,
                "4",
                2,
            )
        self.assertIn("Нашёл ваш профиль", reply)
        self.assertIn("Что хотите изменить?", reply)
        self.assertEqual(driver.support_context_json["mode"], "driver_profile_update")
        self.assertEqual(driver.support_context_json["menu"], "profile_update_menu")

    def test_direct_update_phrase_routes_to_driver_update_request(self):
        phrases = [
            "поменять машину",
            "поменять авто",
            "изменить госномер",
            "обновить СТС",
            "поменять права",
            "заменить водительское удостоверение",
            "изменить номер телефона",
            "поменять ФИО",
            "исправить имя",
            "исправить данные",
            "поменять город",
            "изменить адрес",
            "обновить документы",
            "сменить данные",
            "данные неправильно",
            "ошибка в данных",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(classify_dialog_intent(phrase), "driver_update_request")

    def test_driver_lookup_found_returns_profile_card(self):
        driver = self._driver(
            {
                "mode": "driver_lookup",
                "reason": "driver_update_request",
                "created_at": "2026-01-01T00:00:00",
                "expires_at": "2099-01-01T00:30:00",
            }
        )
        application = SimpleNamespace(status="collecting_data")
        profile = self._driver()
        with patch("app.dialog.engine.find_driver_by_phone", return_value=profile), patch(
            "app.dialog.engine.find_driver_by_whatsapp_phone", return_value=profile
        ), patch("app.dialog.engine.find_driver_by_iin", return_value=profile), patch(
            "app.dialog.engine.create_conversation_event"
        ):
            reply = self.engine._handle_stateful_support_menu(
                DummyDB(),
                driver,
                application,
                DialogueState.ASK_FULL_NAME,
                "77001234567",
                3,
            )
        self.assertIn("Нашёл ваш профиль", reply)
        self.assertIn("Что хотите изменить?", reply)
        self.assertEqual(driver.support_context_json["mode"], "driver_profile_update")

    def test_driver_lookup_missing_asks_for_iin_or_phone(self):
        driver = self._driver(
            {
                "mode": "driver_lookup",
                "reason": "driver_update_request",
                "created_at": "2026-01-01T00:00:00",
                "expires_at": "2099-01-01T00:30:00",
            }
        )
        application = SimpleNamespace(status="collecting_data")
        with patch("app.dialog.engine.find_driver_by_phone", return_value=None), patch(
            "app.dialog.engine.find_driver_by_whatsapp_phone", return_value=None
        ), patch("app.dialog.engine.find_driver_by_iin", return_value=None), patch(
            "app.dialog.engine.create_conversation_event"
        ), patch("app.dialog.engine.set_application_status"):
            reply = self.engine._handle_stateful_support_menu(
                DummyDB(),
                driver,
                application,
                DialogueState.ASK_FULL_NAME,
                "77001234567",
                4,
            )
        self.assertIn("Не нашёл профиль", reply)
        self.assertEqual(driver.support_context_json["mode"], "manual")

    def test_profile_update_context_handles_documents_as_correction(self):
        driver = self._driver(
            {
                "mode": "driver_profile_update",
                "menu": "profile_update_menu",
                "driver_id": 1,
                "vehicle_id": 7,
                "created_at": "2026-01-01T00:00:00",
                "expires_at": "2099-01-01T00:30:00",
                "field": "registration_certificate",
            }
        )
        application = SimpleNamespace(status="collecting_data")
        incoming = SimpleNamespace(message_type="image", mime_type="image/jpeg", filename="photo.jpg", media_id="m1")
        with patch("app.dialog.engine.create_conversation_event"):
            reply = self.engine._handle_document(DummyDB(), driver, application, incoming, 5)
        self.assertIn("обновления данных профиля", reply)


if __name__ == "__main__":
    unittest.main()
