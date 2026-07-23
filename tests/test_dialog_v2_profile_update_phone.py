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


class ProfileUpdatePhoneTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-phone-", suffix=".db", delete=False)
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

    def test_phone_number_accepted_after_menu_choice(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77077502705")
            driver.state = "completed"
            db.commit()

            menu = self._send(db, driver, "p1", message_type="text", text="поменять номер")
            self.assertEqual(menu.metadata.get("intent"), "profile_update")

            ask = self._send(db, driver, "p2", message_type="text", text="2")
            self.assertIn("телефон", ask.text.lower())
            self.assertEqual((driver.support_context_json or {}).get("pending_menu"), "profile_update_value")
            self.assertFalse(ask.requires_manager)

            done = self._send(db, driver, "p3", message_type="text", text="87753105152")
            db.commit()

            self.assertNotIn("не совсем понял", done.text.lower())
            self.assertNotEqual(done.metadata.get("intent"), "fallback")
            self.assertIn("77753105152", done.text.replace("+", "").replace(" ", ""))
            ticket = (driver.support_context_json or {}).get("manager_ticket") or {}
            self.assertEqual(ticket.get("field"), "phone")
            self.assertEqual(ticket.get("new_value"), "+77753105152")
            self.assertEqual(ticket.get("status"), "open")
            self.assertTrue(done.requires_manager)
            self.assertTrue(driver.requires_attention)
            self.assertNotIn("pending_menu", driver.support_context_json or {})


if __name__ == "__main__":
    unittest.main()
