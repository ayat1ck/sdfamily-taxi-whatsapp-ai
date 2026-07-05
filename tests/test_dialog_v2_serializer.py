import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.serializer import serialize_reply
from app.dialog_v2.trace import build_v2_trace
from app.drivers.service import get_or_create_driver
from app.whatsapp.webhook import receive_webhook

import app.audit.models  # noqa: F401
import app.applications.models  # noqa: F401
import app.conversation_events.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.integration_jobs.models  # noqa: F401
import app.messages.models  # noqa: F401
import app.unknown_intents.models  # noqa: F401
import app.vehicles.models  # noqa: F401


class DialogV2SerializerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-serializer-", suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.engine = create_engine(f"sqlite:///{tmp.name}", future=True, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self):
        self.engine.dispose()
        self.db_path.unlink(missing_ok=True)

    def test_serialize_text(self):
        payload = serialize_reply(StructuredReply(text="Привет"), "+77001112233")
        self.assertEqual(payload["type"], "text")
        self.assertEqual(payload["text"]["body"], "Привет")
        self.assertEqual(payload["to"], "77001112233")

    def test_serialize_buttons(self):
        reply = StructuredReply(type="buttons", text="Выберите", buttons=["Да", "Нет"])
        payload = serialize_reply(reply, "+77001112233")
        self.assertEqual(payload["type"], "interactive")
        self.assertEqual(payload["interactive"]["type"], "button")
        self.assertEqual(payload["interactive"]["action"]["buttons"][0]["reply"]["title"], "Да")

    def test_serialize_list(self):
        reply = StructuredReply(type="list", text="Выберите", list_items=[{"id": "1", "title": "Один", "description": "Тест"}])
        payload = serialize_reply(reply, "+77001112233")
        self.assertEqual(payload["type"], "interactive")
        self.assertEqual(payload["interactive"]["type"], "list")
        self.assertEqual(payload["interactive"]["action"]["sections"][0]["rows"][0]["title"], "Один")

    def test_invalid_buttons_fall_back_to_text(self):
        reply = StructuredReply(type="buttons", text="Выберите", buttons=["Да", "Нет", "Может быть", "Лишняя"])
        payload = serialize_reply(reply, "+77001112233")
        self.assertEqual(payload["type"], "text")
        self.assertIn("1. Да", payload["text"]["body"])
        self.assertIn("4. Лишняя", payload["text"]["body"])

    def test_v2_webhook_serializes_structured_reply_and_saves_manager_alert(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid-1",
                                        "type": "text",
                                        "text": {"body": "оператор"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        with self.SessionLocal() as db, \
            patch("app.whatsapp.webhook.get_settings") as get_settings_mock, \
            patch("app.whatsapp.webhook.parse_whatsapp_payload") as parse_mock, \
            patch("app.whatsapp.webhook.handle_message_v2") as handle_mock, \
            patch("app.whatsapp.webhook.sender") as sender_mock, \
            patch("app.whatsapp.webhook.create_integration_job") as create_job_mock, \
            patch("app.whatsapp.webhook.finish_integration_job") as finish_job_mock:
            settings = SimpleNamespace(use_dialog_v2=True, whatsapp_access_token="token", whatsapp_phone_number_id="123")
            get_settings_mock.return_value = settings
            parse_mock.return_value = [SimpleNamespace(sender_phone="+77001112233", message_type="text", text="оператор", provider_message_id="wamid-1", raw_payload=payload)]
            handle_mock.return_value = StructuredReply(
                text="Понял, передал менеджеру.",
                requires_manager=True,
                flow="manager",
                state="manager",
                manager_alert={"phone": "+77001112233", "name": "Иван", "reason": "human_requested", "last_messages": [], "admin_url": "http://localhost/admin/chats/1"},
                events=[{"type": "manager_handoff", "payload": {"reason": "human_requested"}}],
            )
            sender_mock.send_payload.return_value = {"messages": [{"id": "wamid-out-1"}]}
            create_job_mock.return_value = SimpleNamespace(id=1)
            async def _json():
                return payload

            request = SimpleNamespace(json=_json)

            driver = get_or_create_driver(db, "+77001112233")
            db.commit()
            response = self._run_async(receive_webhook(request, db=db))
            db.commit()

            self.assertEqual(response["status"], "ok")
            handle_mock.assert_called_once()
            sender_mock.send_payload.assert_called_once()
            self.assertTrue(driver.support_context_json["manager_notification_pending"])
            messages = db.scalars(select(app.messages.models.Message).where(app.messages.models.Message.driver_id == driver.id)).all()
            self.assertTrue(any(m.direction == "outgoing" for m in messages))
            finish_job_mock.assert_called()

    def test_webhook_falls_back_to_text_when_interactive_send_fails(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid-2",
                                        "type": "text",
                                        "text": {"body": "оператор"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        with self.SessionLocal() as db, \
            patch("app.whatsapp.webhook.get_settings") as get_settings_mock, \
            patch("app.whatsapp.webhook.parse_whatsapp_payload") as parse_mock, \
            patch("app.whatsapp.webhook.handle_message_v2") as handle_mock, \
            patch("app.whatsapp.webhook.sender") as sender_mock, \
            patch("app.whatsapp.webhook.create_integration_job") as create_job_mock, \
            patch("app.whatsapp.webhook.finish_integration_job") as finish_job_mock:
            settings = SimpleNamespace(use_dialog_v2=True, whatsapp_access_token="token", whatsapp_phone_number_id="123")
            get_settings_mock.return_value = settings
            parse_mock.return_value = [SimpleNamespace(sender_phone="+77001112233", message_type="text", text="оператор", provider_message_id="wamid-2", raw_payload=payload)]
            handle_mock.return_value = StructuredReply(
                type="buttons",
                text="Выберите",
                buttons=["Да", "Нет"],
                flow="manager",
                state="manager",
                requires_manager=True,
                manager_alert={"phone": "+77001112233", "name": "Иван", "reason": "human_requested", "last_messages": [], "admin_url": "http://localhost/admin/chats/1"},
            )
            sender_mock.send_payload.side_effect = [RuntimeError("interactive failed"), {"messages": [{"id": "wamid-fallback"}]}]
            create_job_mock.return_value = SimpleNamespace(id=1)
            async def _json():
                return payload
            request = SimpleNamespace(json=_json)

            driver = get_or_create_driver(db, "+77001112233")
            db.commit()
            response = self._run_async(receive_webhook(request, db=db))
            db.commit()

            self.assertEqual(response["status"], "ok")
            self.assertEqual(sender_mock.send_payload.call_count, 2)
            self.assertTrue(any(m.delivery_status == "error" or m.delivery_status == "sent" for m in db.scalars(select(app.messages.models.Message).where(app.messages.models.Message.driver_id == driver.id)).all()))

    def test_build_v2_trace_contains_expected_fields(self):
        reply = StructuredReply(
            type="text",
            text="Привет",
            flow="registration",
            state="registration_document_collection",
            requires_manager=False,
            events=[{"type": "registration_started"}],
        )
        trace = build_v2_trace(
            phone="+77001112233",
            message_type="text",
            text="Привет",
            flow="registration",
            intent="registration",
            state_before="new",
            state_after="registration_document_collection",
            pending_menu_before=None,
            pending_menu_after=None,
            reply=reply,
            duration_ms=12,
        )
        self.assertEqual(trace["phone"], "77001112233")
        self.assertEqual(trace["flow"], "registration")
        self.assertEqual(trace["state_before"], "new")
        self.assertEqual(trace["state_after"], "registration_document_collection")
        self.assertEqual(trace["reply_type"], "text")
        self.assertEqual(trace["events"], ["registration_started"])

    def test_webhook_does_not_crash_on_v2_handle_error_and_saves_trace(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid-3",
                                        "type": "text",
                                        "text": {"body": "Привет"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        with self.SessionLocal() as db, \
            patch("app.whatsapp.webhook.get_settings") as get_settings_mock, \
            patch("app.whatsapp.webhook.parse_whatsapp_payload") as parse_mock, \
            patch("app.whatsapp.webhook.handle_message_v2", side_effect=RuntimeError("boom")) as handle_mock, \
            patch("app.whatsapp.webhook.sender") as sender_mock, \
            patch("app.whatsapp.webhook.create_integration_job") as create_job_mock, \
            patch("app.whatsapp.webhook.finish_integration_job") as finish_job_mock:
            settings = SimpleNamespace(use_dialog_v2=True, use_dialog_v2_phone_allowlist="77001112233", whatsapp_access_token="token", whatsapp_phone_number_id="123")
            get_settings_mock.return_value = settings
            parse_mock.return_value = [SimpleNamespace(sender_phone="+77001112233", message_type="text", text="Привет", provider_message_id="wamid-3", raw_payload=payload)]
            sender_mock.send_payload.side_effect = RuntimeError("should not send")
            create_job_mock.return_value = SimpleNamespace(id=1)

            async def _json():
                return payload

            request = SimpleNamespace(json=_json)
            driver = get_or_create_driver(db, "+77001112233")
            db.commit()
            response = self._run_async(receive_webhook(request, db=db))
            db.commit()

            self.assertEqual(response["status"], "ok")
            handle_mock.assert_called_once()
            trace_messages = db.scalars(select(app.messages.models.Message).where(app.messages.models.Message.driver_id == driver.id)).all()
            self.assertTrue(any((m.raw_payload or {}).get("v2_trace") for m in trace_messages))
            finish_job_mock.assert_not_called()

    def _run_async(self, coro):
        import asyncio

        return asyncio.get_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    unittest.main()
