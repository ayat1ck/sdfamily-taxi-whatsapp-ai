import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.dialog_v2 import handle_message_v2
from app.dialog_v2.missing_fields import MissingFieldsCalculator
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


def _almost_ready_draft(*, employment_type=None):
    return {
        "driver": {
            "full_name": "БЕКСУЛТАНОВ МИРАС",
            "iin": "970429300638",
            "birth_date": "1997-04-29",
            "driving_experience_since": "2024-04-01",
            "driver_license_number": "VA 981155",
            "driver_license_issue_date": "2024-04-01",
            "driver_license_expires_at": "2034-03-31",
            "city": "Алматы",
            "employment_type": employment_type,
        },
        "vehicle": {
            "brand": "Toyota",
            "model": "Camry",
            "year": "2018",
            "plate_number": "123ABC01",
            "color": "белый",
            "registration_certificate": "AA12345678",
        },
        "documents": {
            "driver_license": {"received": True},
            "vehicle_registration_doc": {"received": True},
        },
        "missing_fields": [],
        "confidence_by_field": {},
    }


class EmploymentTypeMenuTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-emp-", suffix=".db", delete=False)
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

    def test_employment_type_required_before_yandex_ready(self):
        draft = _almost_ready_draft(employment_type=None)
        missing = MissingFieldsCalculator().calculate(draft)
        self.assertIn("employment_type", missing)
        self.assertFalse(draft["ready_for_yandex"])

    def test_asks_smz_or_park_with_buttons(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77020000001")
            driver.state = "registration_missing_fields"
            driver.support_context_json = {"registration_draft": _almost_ready_draft()}
            db.commit()

            reply = self._send(db, driver, "e1", message_type="text", text="что ещё нужно")
            db.commit()

            # Global "что ещё нужно" may answer missing text; force via free text continue:
            reply = self._send(db, driver, "e2", message_type="text", text="дальше")
            db.commit()
            self.assertEqual(reply.metadata.get("intent"), "employment_type")
            self.assertEqual(reply.type, "buttons")
            self.assertIn("СМЗ", reply.text)
            self.assertEqual((driver.support_context_json or {}).get("pending_menu"), "employment_type")

    def test_park_choice_goes_to_confirmation(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77020000002")
            driver.state = "registration_missing_fields"
            draft = _almost_ready_draft()
            draft["pending_action"] = "choose_employment_type"
            driver.support_context_json = {
                "registration_draft": draft,
                "pending_menu": "employment_type",
            }
            db.commit()

            reply = self._send(db, driver, "e3", message_type="text", text="emp_park")
            db.commit()

            saved = driver.support_context_json["registration_draft"]
            self.assertEqual(saved["driver"]["employment_type"], "штатный")
            self.assertEqual(reply.metadata.get("intent"), "summary")
            self.assertIn("парковый", reply.text.lower())
            self.assertNotIn("pending_menu", driver.support_context_json)

    def test_smz_choice_goes_to_confirmation(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77020000003")
            driver.state = "registration_missing_fields"
            draft = _almost_ready_draft()
            draft["pending_action"] = "choose_employment_type"
            driver.support_context_json = {
                "registration_draft": draft,
                "pending_menu": "employment_type",
            }
            db.commit()

            reply = self._send(db, driver, "e4", message_type="text", text="emp_smz")
            db.commit()

            saved = driver.support_context_json["registration_draft"]
            self.assertEqual(saved["driver"]["employment_type"], "самозанятый")
            self.assertIn("СМЗ", reply.text)


if __name__ == "__main__":
    unittest.main()
