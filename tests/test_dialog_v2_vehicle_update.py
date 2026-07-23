import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.applications.service import get_or_create_application
from app.database.base import Base
from app.dialog_v2 import handle_message_v2
from app.drivers.service import get_or_create_driver
from app.integrations.yandex.client import YandexFleetClient
from app.integrations.yandex.schemas import YandexDriverPayload
from app.vehicles.service import get_or_create_vehicle
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


class VehicleUpdateAndExistingDriverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-vehicle-", suffix=".db", delete=False)
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

    def test_update_vehicle_client_uses_put(self):
        client = YandexFleetClient()
        payload = YandexDriverPayload(
            full_name="Test",
            last_name="Test",
            first_name="Driver",
            middle_name=None,
            phone="+77001112233",
            city="Astana",
            address="Astana",
            iin="900101300123",
            birth_date="1990-01-01",
            driving_experience_since="2015-01-01",
            driver_license_number="AB123456",
            driver_license_issue_date="2015-01-01",
            driver_license_expires_at="2030-01-01",
            executor_type=None,
            employment_type="самозанятый",
            hired_at="2026-01-01",
            existing_vehicle_lookup=None,
            has_personal_car="true",
            is_hearing_impaired="false",
            car_brand="Toyota",
            car_model="Camry",
            car_year="2018",
            plate_number="123ABC01",
            color="белый",
            service_class="econom,comfort",
            registration_certificate="AA12345678",
            vin=None,
        )

        class FakeResponse:
            is_success = True

            def json(self):
                return {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.put_calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def put(self, path, headers=None, params=None, json=None):
                self.put_calls.append({"path": path, "params": params, "json": json})
                return FakeResponse()

        fake = FakeClient()
        with patch.object(client, "_validate_config"), patch.object(client, "_build_headers", return_value={}), patch(
            "app.integrations.yandex.client.httpx.Client", return_value=fake
        ):
            result = client.update_vehicle("veh-1", payload)

        self.assertEqual(result["status"], "updated_in_yandex")
        self.assertEqual(fake.put_calls[0]["path"], "/v2/parks/vehicles/car")
        self.assertEqual(fake.put_calls[0]["params"]["vehicle_id"], "veh-1")
        self.assertIn("econom", fake.put_calls[0]["json"]["park_profile"]["categories"])

    def test_plate_update_calls_yandex_put(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77030000001")
            driver.full_name = "Тест Водитель"
            driver.state = "completed"
            application = get_or_create_application(db, driver)
            application.yandex_driver_id = "drv-1"
            application.yandex_vehicle_id = "veh-1"
            vehicle = get_or_create_vehicle(db, driver)
            vehicle.brand = "Toyota"
            vehicle.model = "Camry"
            vehicle.year = "2018"
            vehicle.plate_number = "OLD111"
            vehicle.color = "белый"
            vehicle.registration_certificate = "AA111"
            db.commit()

            self._send(db, driver, "m1", message_type="text", text="поменять номер")
            # profile update may open from looks_like_profile_update; force menu choice path
            driver.support_context_json = {
                **(driver.support_context_json or {}),
                "pending_menu": "profile_update_menu",
                "manager_ticket": {"reason": "profile_update", "status": "collecting"},
            }
            db.commit()
            ask = self._send(db, driver, "m2", message_type="text", text="5")
            self.assertEqual((driver.support_context_json or {}).get("pending_menu"), "profile_update_value")

            with patch(
                "app.dialog_v2.flows.profile_update.YandexSubmissionService.update_vehicle_in_yandex",
                return_value={"status": "updated_in_yandex", "yandex_vehicle_id": "veh-1"},
            ) as mocked:
                done = self._send(db, driver, "m3", message_type="text", text="123ABC01")
                db.commit()
                mocked.assert_called_once()

            self.assertIn("обновил", done.text.lower())
            self.assertFalse(done.requires_manager)
            self.assertEqual(driver.vehicle.plate_number, "123ABC01")

    def test_new_vehicle_creates_and_binds(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77030000002")
            driver.full_name = "Тест Водитель"
            driver.state = "completed"
            application = get_or_create_application(db, driver)
            application.yandex_driver_id = "drv-2"
            db.commit()

            driver.support_context_json = {
                "pending_menu": "profile_update_value",
                "manager_ticket": {"reason": "profile_update", "field": "new_vehicle", "status": "collecting"},
            }
            db.commit()

            with patch(
                "app.dialog_v2.flows.profile_update.YandexSubmissionService.add_vehicle_and_bind",
                return_value={"status": "sent_to_yandex", "yandex_driver_id": "drv-2", "yandex_vehicle_id": "veh-new"},
            ) as mocked:
                text = "Toyota Camry 2018\nгосномер 555AAA01\nцвет белый\nСТС BB99999999"
                done = self._send(db, driver, "n1", message_type="text", text=text)
                db.commit()
                mocked.assert_called_once()

            self.assertIn("новая машина", done.text.lower())
            self.assertFalse(done.requires_manager)
            self.assertEqual(driver.vehicle.plate_number, "555AAA01")

    def test_phone_still_goes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77030000003")
            driver.full_name = "Тест"
            driver.state = "completed"
            db.commit()
            driver.support_context_json = {
                "pending_menu": "profile_update_value",
                "manager_ticket": {"reason": "profile_update", "field": "phone", "status": "collecting"},
            }
            db.commit()
            done = self._send(db, driver, "p1", message_type="text", text="87753105152")
            db.commit()
            self.assertTrue(done.requires_manager)
            self.assertEqual((driver.support_context_json or {}).get("manager_ticket", {}).get("new_value"), "+77753105152")

    def test_empty_existing_driver_asks_lookup_then_syncs(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77030000004")
            db.commit()

            ask = self._send(db, driver, "e1", message_type="text", text="я уже водитель")
            self.assertIn("иин", ask.text.lower())

            with patch(
                "app.dialog_v2.flows.existing_driver.YandexSubmissionService.find_and_sync_existing_driver",
                side_effect=lambda db, d, lookup: (
                    setattr(d, "full_name", "СИНХРОН ТЕСТ")
                    or setattr(d, "iin", "970429300638")
                    or setattr(d, "state", "completed")
                    or d
                ),
            ):
                menu = self._send(db, driver, "e2", message_type="text", text="970429300638")
                db.commit()

            self.assertIn("профиль найден", menu.text.lower())
            self.assertIn("синхрон", menu.text.lower())


if __name__ == "__main__":
    unittest.main()
