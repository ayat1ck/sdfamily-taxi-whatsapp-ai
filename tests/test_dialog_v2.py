import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.applications.models import Application
from app.database.base import Base
from app.dialog_v2 import handle_message_v2
from app.dialog_v2.document_types import DocumentTypeResolver
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.router import Router
from app.dialog_v2.summary_builder import SummaryBuilder
from app.documents.extraction import DocumentExtractionResult
from app.drivers.service import get_or_create_driver
from app.messages.models import Message
from app.whatsapp.parser import ParsedWhatsAppMessage

import app.audit.models  # noqa: F401
import app.conversation_events.models  # noqa: F401
import app.documents.models  # noqa: F401
import app.integration_jobs.models  # noqa: F401
import app.unknown_intents.models  # noqa: F401
import app.vehicles.models  # noqa: F401


def fake_extraction(**fields):
    return DocumentExtractionResult(
        document_type=fields.pop("document_type", "driver_license"),
        confidence=fields.pop("confidence", 0.95),
        **fields,
    )


class DialogV2Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(prefix="dialog-v2-", suffix=".db", delete=False)
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

    def test_ocr_text_hooks_resolve_driver_license(self):
        resolver = DocumentTypeResolver()
        result = resolver.resolve(
            current_flow="registration_document_collection",
            current_state="registration_document_collection",
            mime_type="image/jpeg",
            filename="vu.jpg",
            extracted_fields={},
            ocr_text="Водительское удостоверение. Дата выдачи. Действует до. License number.",
            confidence=0.31,
        )
        self.assertEqual(result.document_type, "driver_license")

    def test_ocr_text_hooks_resolve_id_card(self):
        resolver = DocumentTypeResolver()
        result = resolver.resolve(
            current_flow="registration_document_collection",
            current_state="registration_document_collection",
            mime_type="image/jpeg",
            filename="id.jpg",
            extracted_fields={},
            ocr_text="Удостоверение личности. ИИН. Дата рождения. Место рождения.",
            confidence=0.31,
        )
        self.assertEqual(result.document_type, "id_card")

    def test_ocr_text_hooks_resolve_vehicle_doc(self):
        resolver = DocumentTypeResolver()
        result = resolver.resolve(
            current_flow="registration_document_collection",
            current_state="registration_document_collection",
            mime_type="image/jpeg",
            filename="sts.jpg",
            extracted_fields={},
            ocr_text="Свидетельство о регистрации ТС. Марка. Модель. Госномер. VIN. Кузов. Цвет.",
            confidence=0.31,
        )
        self.assertEqual(result.document_type, "vehicle_registration_doc")

    def test_summary_builder_shows_filled_and_missing_fields(self):
        builder = SummaryBuilder()
        draft = {
            "driver": {
                "full_name": "Иванов Иван",
                "iin": "070404550345",
                "birth_date": "1990-01-01",
                "phone": "+77001112233",
                "city": None,
                "address": None,
                "driving_experience_since": "2015-01-01",
                "driver_license_number": "CQ 981709",
                "driver_license_issue_date": "2015-01-01",
                "driver_license_expires_at": "2030-01-01",
            },
            "vehicle": {
                "brand": "Toyota",
                "model": "Camry",
                "plate_number": "123ABC01",
                "registration_certificate": "AA12345678",
                "color": None,
            },
            "documents": {
                "driver_license": {"file_name": "vu.pdf"},
                "id_card": None,
                "vehicle_registration_doc": {"file_name": "sts.pdf"},
                "selfie_with_license": None,
            },
        }
        text = builder.build_final_summary(draft)
        self.assertIn("ФИО: Иванов Иван", text)
        self.assertIn("Город: —", text)
        self.assertIn("Документы:", text)
        self.assertIn("удостоверение личности: нет", text.lower())

    def test_pdf_first_message_starts_registration(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(
                document_type="driver_license",
                full_name="Иванов Иван",
                driver_license_number="CQ 981709",
                driver_license_issue_date="2015-01-01",
                driver_license_expires_at="2030-01-01",
                confidence=0.92,
            )
            driver = get_or_create_driver(db, "+77000000001")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="document",
                text=None,
                provider_message_id="msg-1",
                media_id="media-1",
                mime_type="application/pdf",
                filename="rights.pdf",
            )
            db.commit()

            application = db.scalar(select(Application).where(Application.driver_id == driver.id))
            self.assertIsNotNone(application)
            self.assertEqual(driver.state, "registration_confirmation")
            self.assertEqual(reply.next_flow, "registration_confirmation")
            self.assertIn("Документ получил: водительское удостоверение", reply.text)
            self.assertIn("ФИО: Иванов Иван", reply.text)
            self.assertNotIn("номер ВУ", reply.text)

    def test_image_first_message_starts_registration(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(
                document_type="id_card",
                full_name="Иванов Иван",
                iin="070404550345",
                birth_date="1990-01-01",
                confidence=0.93,
            )
            driver = get_or_create_driver(db, "+77000000002")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="image",
                text=None,
                provider_message_id="msg-2",
                media_id="media-2",
                mime_type="image/jpeg",
                filename="id.jpg",
            )
            db.commit()

            self.assertEqual(driver.state, "registration_confirmation")
            self.assertIn("Документ получил: удостоверение личности", reply.text)
            self.assertIn("ИИН: 070404550345", reply.text)

    def test_vu_does_not_ask_for_vu_number_again(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(
                document_type="driver_license",
                full_name="Иванов Иван",
                driver_license_number="CQ 981709",
                driver_license_issue_date="2015-01-01",
                driver_license_expires_at="2030-01-01",
                confidence=0.96,
            )
            driver = get_or_create_driver(db, "+77000000003")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="document",
                text=None,
                provider_message_id="msg-3",
                media_id="media-3",
                mime_type="application/pdf",
                filename="vu.pdf",
            )
            db.commit()

            self.assertNotIn("номер ву", reply.text.lower())
            draft = (driver.support_context_json or {}).get("registration_draft", {})
            self.assertEqual(draft.get("driver", {}).get("driver_license_number"), "CQ 981709")
            self.assertEqual(draft.get("driver", {}).get("driver_license_issue_date"), "2015-01-01")

    def test_only_missing_city_and_address_are_requested(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000004")
            driver.support_context_json = {
                "registration_draft": {
                    "driver": {
                        "full_name": "Иванов Иван",
                        "phone": "+77001112233",
                        "iin": "070404550345",
                        "birth_date": "1990-01-01",
                        "driving_experience_since": "2015-01-01",
                        "driver_license_number": "CQ 981709",
                        "driver_license_issue_date": "2015-01-01",
                        "driver_license_expires_at": "2030-01-01",
                        "employment_type": "самозанятый",
                        "hired_at": "2026-01-01",
                        "is_hearing_impaired": "false",
                        "city": None,
                        "address": None,
                    },
                    "vehicle": {
                        "brand": "Toyota",
                        "model": "Camry",
                        "year": "2020",
                        "plate_number": "123ABC01",
                        "color": "white",
                        "registration_certificate": "AA12345678",
                        "vin": "VIN123",
                    },
                    "documents": {
                        "driver_license": {"file_name": "vu.pdf"},
                        "id_card": {"file_name": "id.pdf"},
                        "vehicle_registration_doc": {"file_name": "sts.pdf"},
                        "selfie_with_license": {"file_name": "selfie.jpg"},
                    },
                    "missing_fields": [],
                    "confidence_by_field": {},
                }
            }
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="Астана",
                provider_message_id="msg-4",
            )
            db.commit()

            self.assertIn("Город", reply.text)
            self.assertIn("Адрес", reply.text)
            self.assertNotIn("ИИН", reply.text)

    def test_after_all_data_shows_summary(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000005")
            driver.support_context_json = {
                "registration_draft": {
                    "driver": {
                        "full_name": "Иванов Иван",
                        "phone": "+77001112233",
                        "city": "Астана",
                        "address": "Республика 12",
                        "iin": "070404550345",
                        "birth_date": "1990-01-01",
                        "driving_experience_since": "2015-01-01",
                        "driver_license_number": "CQ 981709",
                        "driver_license_issue_date": "2015-01-01",
                        "driver_license_expires_at": "2030-01-01",
                        "employment_type": "самозанятый",
                        "hired_at": "2026-01-01",
                        "is_hearing_impaired": "false",
                    },
                    "vehicle": {
                        "brand": "Toyota",
                        "model": "Camry",
                        "year": "2020",
                        "plate_number": "123ABC01",
                        "color": "white",
                        "registration_certificate": "AA12345678",
                        "vin": "VIN123",
                    },
                    "documents": {
                        "driver_license": {"file_name": "vu.pdf"},
                        "id_card": {"file_name": "id.pdf"},
                        "vehicle_registration_doc": {"file_name": "sts.pdf"},
                        "selfie_with_license": {"file_name": "selfie.jpg"},
                    },
                    "missing_fields": [],
                    "confidence_by_field": {},
                }
            }
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="любое сообщение",
                provider_message_id="msg-5",
            )
            db.commit()

            self.assertEqual(reply.next_flow, "registration_confirmation")
            self.assertIn("Проверьте данные", reply.text)
            self.assertIn("Если всё верно", reply.text)

    def test_confirmation_moves_to_ready_to_send_yandex(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000006")
            driver.state = "registration_confirmation"
            driver.support_context_json = {
                "registration_draft": {
                    "driver": {
                        "full_name": "Иванов Иван",
                        "phone": "+77001112233",
                        "city": "Астана",
                        "address": "Республика 12",
                        "iin": "070404550345",
                        "birth_date": "1990-01-01",
                        "driving_experience_since": "2015-01-01",
                        "driver_license_number": "CQ 981709",
                        "driver_license_issue_date": "2015-01-01",
                        "driver_license_expires_at": "2030-01-01",
                        "employment_type": "самозанятый",
                        "hired_at": "2026-01-01",
                        "is_hearing_impaired": "false",
                    },
                    "vehicle": {
                        "brand": "Toyota",
                        "model": "Camry",
                        "year": "2020",
                        "plate_number": "123ABC01",
                        "color": "white",
                        "registration_certificate": "AA12345678",
                        "vin": "VIN123",
                    },
                    "documents": {
                        "driver_license": {"file_name": "vu.pdf"},
                        "id_card": {"file_name": "id.pdf"},
                        "vehicle_registration_doc": {"file_name": "sts.pdf"},
                        "selfie_with_license": {"file_name": "selfie.jpg"},
                    },
                    "missing_fields": [],
                    "confidence_by_field": {},
                }
            }
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="подтверждаю",
                provider_message_id="msg-6",
            )
            db.commit()

            application = db.scalar(select(Application).where(Application.driver_id == driver.id))
            self.assertEqual(reply.next_flow, "ready_to_send_yandex")
            self.assertEqual(driver.state, "ready_to_send_yandex")
            self.assertEqual(application.status, "ready_to_send_yandex")

    def test_unknown_document_asks_for_type(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(document_type="unknown", confidence=0.2)
            driver = get_or_create_driver(db, "+77000000007")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="image",
                text=None,
                provider_message_id="msg-7",
                media_id="media-7",
                mime_type="image/jpeg",
                filename="file.jpg",
            )
            db.commit()
            self.assertIn("Получил файл, но не уверен", reply.text)
            self.assertEqual(driver.support_context_json["registration_draft"]["pending_action"], "confirm_document_type")

    def test_unknown_then_answer_two_applies_id_card(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(document_type="unknown", confidence=0.2)
            driver = get_or_create_driver(db, "+77000000008")
            db.commit()
            self._send(
                db,
                driver,
                message_type="image",
                text=None,
                provider_message_id="msg-8a",
                media_id="media-8",
                mime_type="image/jpeg",
                filename="file.jpg",
            )
            db.commit()
            extract.return_value = fake_extraction(
                document_type="id_card",
                full_name="Иванов Иван",
                iin="070404550345",
                birth_date="1990-01-01",
                confidence=0.93,
            )
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="2",
                provider_message_id="msg-8b",
            )
            db.commit()
            self.assertIn("удостоверение личности", reply.text.lower())
            self.assertEqual(driver.support_context_json["registration_draft"]["documents"]["id_card"]["received"], True)

    def test_incoming_messages_are_persisted_once(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(document_type="id_card", confidence=0.9)
            driver = get_or_create_driver(db, "+77000000009")
            db.commit()
            self._send(
                db,
                driver,
                message_type="image",
                text=None,
                provider_message_id="msg-9",
                media_id="media-9",
                mime_type="image/jpeg",
                filename="id.jpg",
            )
            db.commit()
            messages = db.scalars(select(Message).where(Message.driver_id == driver.id)).all()
            self.assertEqual(len(messages), 1)

    def test_existing_driver_routes_to_existing_driver_flow(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000010")
            driver.full_name = "Иванов Иван"
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="я уже подключен",
                provider_message_id="msg-10",
            )
            db.commit()
            self.assertEqual(reply.next_flow, "existing_driver")
            self.assertIn("Выплаты", reply.text)

    def test_profile_update_routes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000011")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="хочу поменять машину",
                provider_message_id="msg-11",
            )
            db.commit()
            self.assertEqual(reply.next_flow, "manager")
            self.assertTrue(reply.requires_manager)

    def test_manager_flow_for_money_goes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000012")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="не могу вывести деньги",
                provider_message_id="msg-12",
            )
            db.commit()
            self.assertEqual(reply.next_flow, "manager")
            self.assertTrue(reply.requires_manager)

    def test_faq_flow_routes_questions(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000013")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="какие условия",
                provider_message_id="msg-13",
            )
            db.commit()
            self.assertEqual(reply.next_flow, "faq")
            self.assertIn("комиссия", reply.text.lower())

    def test_operator_routes_to_manager(self):
        with self.SessionLocal() as db:
            driver = get_or_create_driver(db, "+77000000014")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="text",
                text="оператор",
                provider_message_id="msg-14",
            )
            db.commit()
            self.assertEqual(reply.next_flow, "manager")
            self.assertTrue(reply.requires_manager)

    def test_pdf_first_message_routes_to_registration(self):
        with self.SessionLocal() as db, patch("app.dialog_v2.flows.registration.DocumentExtractionService.extract") as extract:
            extract.return_value = fake_extraction(
                document_type="driver_license",
                full_name="Иванов Иван",
                driver_license_number="CQ 981709",
                driver_license_issue_date="2015-01-01",
                driver_license_expires_at="2030-01-01",
                confidence=0.91,
            )
            driver = get_or_create_driver(db, "+77000000015")
            db.commit()
            reply = self._send(
                db,
                driver,
                message_type="document",
                text=None,
                provider_message_id="msg-15",
                media_id="media-15",
                mime_type="application/pdf",
                filename="rights.pdf",
            )
            db.commit()
            self.assertIn("Документ получил: водительское удостоверение", reply.text)
            self.assertEqual(reply.next_flow, "registration_confirmation")


if __name__ == "__main__":
    unittest.main()
