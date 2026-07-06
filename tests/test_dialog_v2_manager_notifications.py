import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.dialog_v2.hooks import notify_manager_stub


class DialogV2ManagerNotificationTests(unittest.TestCase):
    def test_notify_manager_sends_telegram_and_keeps_context(self):
        driver = SimpleNamespace(id=1, support_context_json={})
        settings = SimpleNamespace(
            telegram_bot_token="token",
            telegram_manager_chat_id="12345",
            telegram_api_base_url="https://api.telegram.org",
        )
        response = SimpleNamespace(raise_for_status=lambda: None)

        with patch("app.dialog_v2.hooks.get_settings", return_value=settings), \
            patch("app.dialog_v2.hooks.httpx.post", return_value=response) as post:
            notify_manager_stub(
                db=object(),
                driver=driver,
                manager_alert={
                    "phone": "+77001112233",
                    "name": "Test Driver",
                    "reason": "human_requested",
                    "last_messages": ["оператор"],
                    "admin_url": "https://example.com/admin/chats/1",
                },
            )

        post.assert_called_once()
        self.assertTrue(driver.support_context_json["manager_notification_pending"])
        self.assertTrue(driver.support_context_json["manager_notification_sent"])
        self.assertEqual(driver.support_context_json["manager_notification_channel"], "telegram")


if __name__ == "__main__":
    unittest.main()
