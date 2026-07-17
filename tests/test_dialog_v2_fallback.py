import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.dialog_v2 import handle_message_v2
from app.drivers.service import get_or_create_driver
from app.whatsapp.parser import ParsedWhatsAppMessage

import app.applications.models  # noqa: F401
import app.audit.models  # noqa: F401
import app.ai_traces.models  # noqa: F401
import app.conversation_events.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.integration_jobs.models  # noqa: F401
import app.messages.models  # noqa: F401
import app.unknown_intents.models  # noqa: F401
import app.vehicles.models  # noqa: F401


class DialogV2FallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-fallback-", suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.engine = create_engine(f"sqlite:///{tmp.name}", future=True, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self):
        self.engine.dispose()
        self.db_path.unlink(missing_ok=True)

    def _send(self, db, driver, msg_id, **kwargs):
        return handle_message_v2(
            db,
            driver,
            ParsedWhatsAppMessage(
                sender_phone=driver.whatsapp_phone,
                provider_message_id=msg_id,
                raw_payload={},
                **kwargs,
            ),
        )

    def test_audio_message_gets_menu_not_document_prompt(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000001")
            db.commit()
            reply = self._send(db, driver, "a1", message_type="audio", media_id="voice-1", mime_type="audio/ogg")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "fallback")
            self.assertIn("голосов", reply.text.lower())
            self.assertNotIn("документы: 0", reply.text.lower())
            self.assertEqual(reply.type, "buttons")

    def test_sticker_message_gets_menu(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000002")
            db.commit()
            reply = self._send(db, driver, "s1", message_type="sticker", media_id="stk-1", mime_type="image/webp")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "fallback")
            self.assertIn("стикер", reply.text.lower())

    def test_three_audio_messages_escalate_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000003")
            driver.state = "completed"
            db.commit()
            self._send(db, driver, "a1", message_type="audio", media_id="v1", mime_type="audio/ogg")
            self._send(db, driver, "a2", message_type="audio", media_id="v2", mime_type="audio/ogg")
            reply = self._send(db, driver, "a3", message_type="audio", media_id="v3", mime_type="audio/ogg")
            db.commit()
            self.assertTrue(reply.requires_manager)
            self.assertTrue(driver.requires_attention)

    def test_fallback_menu_choice_starts_registration(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000004")
            driver.state = "completed"
            db.commit()
            self._send(db, driver, "a1", message_type="audio", media_id="v1", mime_type="audio/ogg")
            reply = self._send(db, driver, "t1", message_type="text", text="mgr_register")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "registration")
            self.assertIn("водительское удостоверение", reply.text.lower())

    def test_repeated_identical_replies_escalate_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000005")
            driver.state = "registration_document_collection"
            db.commit()
            first = self._send(db, driver, "t1", message_type="text", text="19бала")
            second = self._send(db, driver, "t2", message_type="text", text="19бала")
            third = self._send(db, driver, "t3", message_type="text", text="19бала")
            db.commit()
            self.assertFalse(first.requires_manager)
            self.assertIn("менеджер", second.text.lower())
            self.assertTrue(third.requires_manager)
            self.assertTrue(driver.requires_attention)

    def test_frustration_goes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000006")
            db.commit()
            reply = self._send(db, driver, "t1", message_type="text", text="вы бот? ничего не работает")
            db.commit()
            self.assertTrue(reply.requires_manager)
            self.assertTrue(driver.requires_attention)

    def test_menu_command_shows_main_menu(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000007")
            driver.state = "registration_document_collection"
            db.commit()
            reply = self._send(db, driver, "t1", message_type="text", text="меню")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "main_menu")
            self.assertEqual(reply.type, "buttons")
            self.assertEqual((driver.support_context_json or {}).get("pending_menu"), "fallback_menu")

    def test_completed_driver_unclear_text_not_asked_for_documents(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77010000008")
            driver.state = "completed"
            db.commit()
            reply = self._send(db, driver, "t1", message_type="text", text="19бала")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "fallback")
            self.assertNotIn("документ", reply.text.lower())

    def test_stale_pending_menu_expires(self):
        with self.SessionLocal() as db:
            from datetime import datetime, timedelta

            driver = get_or_create_driver(db, "+77010000009")
            driver.support_context_json = {
                "pending_menu": "manager_triage",
                "last_seen_at": (datetime.utcnow() - timedelta(hours=12)).isoformat(),
            }
            db.commit()
            reply = self._send(db, driver, "t1", message_type="text", text="какие условия")
            db.commit()
            # Menu expired, message routed as a fresh FAQ instead of a triage choice.
            self.assertEqual(reply.flow or reply.next_flow, "faq")


if __name__ == "__main__":
    unittest.main()
