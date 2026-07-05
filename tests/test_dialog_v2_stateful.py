import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.dialog_v2 import handle_message_v2
from app.dialog_v2.document_types import DocumentTypeResolver
from app.dialog_v2.summary_builder import SummaryBuilder
from app.drivers.service import get_or_create_driver
from app.whatsapp.parser import ParsedWhatsAppMessage

import app.audit.models  # noqa: F401
import app.conversation_events.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.integration_jobs.models  # noqa: F401
import app.unknown_intents.models  # noqa: F401
import app.vehicles.models  # noqa: F401


class DialogV2StatefulTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-stateful-", suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.engine = create_engine(f"sqlite:///{tmp.name}", future=True, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self):
        self.engine.dispose()
        self.db_path.unlink(missing_ok=True)

    def _send(self, db, driver, **kwargs):
        return handle_message_v2(
            db,
            driver,
            ParsedWhatsAppMessage(sender_phone=driver.whatsapp_phone, raw_payload={}, **kwargs),
        )

    def test_existing_driver_menu_four_opens_profile_update(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000001")
            driver.full_name = "Иванов Иван"
            driver.support_context_json = {
                "existing_driver_target_id": driver.id,
                "pending_menu": "existing_driver_main",
                "menu": "existing_driver_main",
            }
            db.commit()
            reply = self._send(db, driver, message_type="text", text="4", provider_message_id="menu-4")
            db.commit()
            self.assertIsNotNone(reply)
            self.assertEqual(reply.flow, "profile_update")
            self.assertEqual(reply.state, "profile_update")
            self.assertIn("Что нужно изменить?", reply.text)
            self.assertEqual(driver.support_context_json["manager_ticket"]["reason"], "profile_update")
            self.assertEqual(driver.support_context_json["manager_ticket"]["field"], "full_name")

    def test_existing_driver_menu_one_goes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000002")
            driver.full_name = "Иванов Иван"
            driver.support_context_json = {
                "existing_driver_target_id": driver.id,
                "pending_menu": "existing_driver_main",
                "menu": "existing_driver_main",
            }
            db.commit()
            reply = self._send(db, driver, message_type="text", text="1", provider_message_id="menu-1")
            db.commit()
            self.assertIsNotNone(reply)
            self.assertEqual(reply.flow, "manager")
            self.assertTrue(reply.requires_manager)
            self.assertIsNotNone(reply.manager_alert)
            self.assertEqual(driver.support_context_json["manager_ticket"]["reason"], "payout_issue")
            self.assertIn("manager_alert", driver.support_context_json)
            self.assertTrue(driver.support_context_json["manager_alert"]["admin_url"])

    def test_profile_update_menu_lists_fields(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000003")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="хочу поменять машину",
                provider_message_id="profile-update",
            )
            db.commit()
            self.assertIsNotNone(reply)
            self.assertEqual(reply.flow, "profile_update")
            self.assertIn("ФИО", reply.text)
            self.assertIn("Менеджер", reply.text)
            self.assertEqual(driver.support_context_json["manager_ticket"]["reason"], "profile_update")
            self.assertEqual(driver.support_context_json["manager_ticket"]["status"], "collecting")

    def test_manager_flow_persists_alert_payload(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000004")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="не могу вывести деньги",
                provider_message_id="manager-flow",
            )
            db.commit()
            self.assertIsNotNone(reply)
            self.assertEqual(reply.flow, "manager")
            self.assertTrue(reply.requires_manager)
            self.assertEqual(driver.support_context_json["manager_ticket"]["reason"], "не могу вывести деньги")
            self.assertEqual(driver.support_context_json["manager_alert"]["reason"], "не могу вывести деньги")

    def test_every_flow_returns_structured_reply(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000005")
            db.commit()
            cases = [
                ("я уже подключен", "existing_driver"),
                ("хочу поменять машину", "profile_update"),
                ("не могу вывести деньги", "manager"),
                ("какие условия", "faq"),
            ]
            for idx, (text, expected_flow) in enumerate(cases, start=1):
                reply = self._send(db, driver, message_type="text", text=text, provider_message_id=f"case-{idx}")
                db.commit()
                self.assertIsNotNone(reply)
                self.assertEqual(reply.flow or reply.next_flow, expected_flow)
                self.assertIsInstance(reply.to_dict(), dict)

    def test_unknown_menu_choice_repeats_menu(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77001000006")
            driver.full_name = "Иванов Иван"
            driver.support_context_json = {
                "existing_driver_target_id": driver.id,
                "pending_menu": "existing_driver_main",
                "menu": "existing_driver_main",
            }
            db.commit()
            reply = self._send(db, driver, message_type="text", text="7", provider_message_id="menu-7")
            db.commit()
            self.assertEqual(reply.flow, "existing_driver")
            self.assertIn("Что нужно?", reply.text)

    def test_document_type_resolver_uses_ocr_markers(self):
        resolver = DocumentTypeResolver()
        self.assertEqual(
            resolver.resolve(
                current_flow="registration_document_collection",
                current_state="registration_document_collection",
                mime_type="image/jpeg",
                filename="vu.jpg",
                extracted_fields={},
                ocr_text="водительское удостоверение",
                confidence=0.2,
            ).document_type,
            "driver_license",
        )

    def test_summary_builder_lists_missing_items(self):
        builder = SummaryBuilder()
        text = builder.build_final_summary(
            {
                "driver": {"full_name": "Иванов Иван", "phone": "+7700"},
                "vehicle": {"brand": "Toyota"},
                "documents": {"driver_license": {"file_name": "vu.pdf"}},
                "missing_fields": ["city", "address", "vehicle.model"],
            }
        )
        self.assertIn("Проверьте данные", text)
        self.assertIn("Город:", text)


if __name__ == "__main__":
    unittest.main()
