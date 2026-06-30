import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.applications.models import Application
from app.database.base import Base
from app.dialog.ai import get_ai_service
from app.dialog.engine import DialogueEngine
from app.drivers.models import Driver
from app.drivers.service import get_or_create_driver
from app.messages.models import Message
from app.whatsapp.parser import ParsedWhatsAppMessage

import app.ai_traces.models  # noqa: F401
import app.audit.models  # noqa: F401
import app.conversation_events.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.integration_jobs.models  # noqa: F401
import app.unknown_intents.models  # noqa: F401
import app.vehicles.models  # noqa: F401


FIXTURE_DIR = Path(__file__).with_name("fixtures")
FIXTURE_PATHS = (
    FIXTURE_DIR / "chat_cases.json",
    FIXTURE_DIR / "chat_cases_export.json",
)


class DummySheets:
    def sync_application(self, driver, application):
        return None


class DummyYandex:
    def validate_driver(self, driver):
        return {"errors": []}

    def submit_driver(self, db, driver, application):
        return {"status": "ok"}


class ChatSimulationCaseTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="chat-sim-", suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.engine_db = create_engine(f"sqlite:///{tmp.name}", future=True, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine_db)
        self.SessionLocal = sessionmaker(bind=self.engine_db, autoflush=False, autocommit=False, future=True)

        self.engine = DialogueEngine.__new__(DialogueEngine)
        self.engine.settings = SimpleNamespace(
            public_site_address="Астана, Балкантау 117",
            google_sheets_id=None,
            get_google_service_account_info=lambda: None,
        )
        self.engine.ai = get_ai_service()
        self.engine.drive = SimpleNamespace()
        self.engine.sheets = DummySheets()
        self.engine.yandex = DummyYandex()
        self.engine.media = SimpleNamespace()
        self.engine.document_extractor = SimpleNamespace()

    def tearDown(self):
        self.engine_db.dispose()
        self.db_path.unlink(missing_ok=True)

    def _simulate_chat(self, phone: str, messages: list[str]):
        transcript = []
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, phone)
            db.commit()
            for idx, text in enumerate(messages, start=1):
                reply = self.engine.handle_message(
                    db,
                    driver,
                    ParsedWhatsAppMessage(
                        sender_phone=phone,
                        message_type="text",
                        text=text,
                        provider_message_id=f"fixture-{idx}",
                        raw_payload={"source": "chat-fixture", "text": text},
                    ),
                )
                db.commit()
                transcript.append({"user": text, "bot": reply, "state": driver.state})
            driver = db.scalar(select(Driver).where(Driver.whatsapp_phone == driver.whatsapp_phone))
            application = db.scalar(select(Application).where(Application.driver_id == driver.id))
            messages_in_db = db.scalars(select(Message).where(Message.driver_id == driver.id).order_by(Message.id)).all()
            return transcript, driver, application, messages_in_db

    def _run_case(self, case: dict):
        transcript, driver, application, messages_in_db = self._simulate_chat(case["phone"], case["messages"])
        expected = case["expected"]

        if "final_state" in expected:
            self.assertEqual(driver.state, expected["final_state"], msg=transcript)
        if "application_status" in expected:
            self.assertIsNotNone(application, msg=transcript)
            self.assertEqual(application.status, expected["application_status"], msg=transcript)
        if "requires_attention" in expected:
            self.assertEqual(driver.requires_attention, expected["requires_attention"], msg=transcript)
        if "message_count" in expected:
            self.assertEqual(len(messages_in_db), expected["message_count"], msg=transcript)

        for rule in expected.get("state_at_step", []):
            self.assertEqual(transcript[rule["step"]]["state"], rule["state"], msg=transcript)
        for rule in expected.get("reply_contains", []):
            self.assertIn(rule["text"], transcript[rule["step"]]["bot"], msg=transcript)
        for rule in expected.get("reply_not_contains", []):
            self.assertNotIn(rule["text"], transcript[rule["step"]]["bot"], msg=transcript)

    def test_fixture_chat_cases(self):
        cases = []
        for fixture_path in FIXTURE_PATHS:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            cases.extend(payload["cases"])
        expected_failures = []

        for case in cases:
            with self.subTest(case=case["name"]):
                if case.get("expected_failure"):
                    try:
                        self._run_case(case)
                    except AssertionError:
                        expected_failures.append(case["name"])
                    else:
                        self.fail(f"Case {case['name']} is marked expected_failure but now passes")
                else:
                    self._run_case(case)

        self.assertEqual(
            sorted(expected_failures),
            sorted(case["name"] for case in cases if case.get("expected_failure")),
        )


if __name__ == "__main__":
    unittest.main()
