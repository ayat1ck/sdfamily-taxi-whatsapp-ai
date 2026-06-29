import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

try:
    from app.dialog.engine import DialogueEngine
except ModuleNotFoundError:  # pragma: no cover
    DialogueEngine = None


class SupportContextTTLTests(unittest.TestCase):
    def setUp(self):
        if DialogueEngine is None:
            self.skipTest("runtime dependencies unavailable")
        self.engine = DialogueEngine.__new__(DialogueEngine)

    def _driver(self, context):
        return SimpleNamespace(
            support_context_json=context,
            updated_at=None,
            active_support_topic="yandex_login",
            active_support_step="1",
        )

    def test_support_context_after_two_hours_still_active(self):
        context = {
            "mode": "existing_driver_support",
            "created_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
            "last_updated": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
        }
        self.assertFalse(self.engine._support_context_is_expired(context))
        self.assertFalse(self.engine._support_context_is_stale(context))

    def test_support_context_after_twenty_four_hours_resets(self):
        context = {
            "mode": "existing_driver_support",
            "created_at": (datetime.utcnow() - timedelta(hours=25)).isoformat(),
            "last_updated": (datetime.utcnow() - timedelta(hours=25)).isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
        }
        driver = self._driver(context)
        self.engine._reset_stale_support_context(driver)
        self.assertIsNone(driver.support_context_json)
        self.assertIsNone(driver.active_support_topic)
        self.assertIsNone(driver.active_support_step)

    def test_registration_state_without_support_context_is_untouched(self):
        driver = self._driver(None)
        self.engine._reset_stale_support_context(driver)
        self.assertIsNone(driver.support_context_json)
        self.assertEqual(driver.active_support_topic, "yandex_login")
        self.assertEqual(driver.active_support_step, "1")


if __name__ == "__main__":
    unittest.main()
