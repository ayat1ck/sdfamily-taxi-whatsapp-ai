import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.dialog_v2.document_types import DocumentTypeResolver
from app.dialog_v2.draft_merger import DraftMerger
from app.dialog_v2.missing_fields import MissingFieldsCalculator
from app.documents.extraction import DocumentExtractionResult, DocumentExtractionService, normalize_extracted_fields


class DialogV2DocumentBackendTests(unittest.TestCase):
    def test_empty_image_does_not_default_to_selfie(self):
        resolver = DocumentTypeResolver()
        result = resolver.resolve(
            current_flow="registration_document_collection",
            current_state="registration_document_collection",
            mime_type="image/jpeg",
            filename="photo.jpg",
            extracted_fields={},
            ocr_text="",
            confidence=0.0,
        )
        self.assertEqual(result.document_type, "unknown")

    def test_driver_license_fields_win_over_id_card_fields(self):
        resolver = DocumentTypeResolver()
        result = resolver.resolve(
            current_flow="registration_document_collection",
            current_state="registration_document_collection",
            mime_type="image/jpeg",
            filename="photo.jpg",
            extracted_fields={
                "full_name": "ХАЛИЕВ ОМАР",
                "iin": "041204501406",
                "birth_date": "2004-12-04",
                "driver_license_number": "XT 164890",
                "driver_license_issue_date": "2023-05-12",
                "driver_license_expires_at": "2033-05-11",
            },
            ocr_text="DRIVING LICENCE ИИН дата рождения",
            confidence=0.9,
        )
        self.assertEqual(result.document_type, "driver_license")

    def test_id_card_is_not_required_for_yandex_ready_mvp(self):
        draft = {
            "driver": {
                "full_name": "Test Driver",
                "iin": "041204501406",
                "birth_date": "2004-12-04",
                "driver_license_number": "XT 164890",
                "driver_license_issue_date": "2023-05-12",
                "driver_license_expires_at": "2033-05-11",
                "driving_experience_since": "2023-05-12",
                "city": "Astana",
                "employment_type": "самозанятый",
            },
            "vehicle": {
                "brand": "Lada",
                "model": "21703",
                "year": "2013",
                "plate_number": "311ARP17",
                "color": "WHITE",
                "registration_certificate": "YA99788458",
            },
            "documents": {
                "driver_license": {"received": True},
                "id_card": None,
                "vehicle_registration_doc": {"received": True},
            },
        }

        missing = MissingFieldsCalculator().calculate(draft)

        self.assertEqual(missing, [])
        self.assertTrue(draft["is_registration_complete"])
        self.assertTrue(draft["ready_for_yandex"])

    def test_driver_license_issue_date_fills_experience_since(self):
        draft = {
            "driver": {
                "driver_license_number": None,
                "driver_license_issue_date": None,
                "driving_experience_since": None,
            },
            "vehicle": {},
            "documents": {"driver_license": None},
            "confidence_by_field": {},
        }

        result = DraftMerger().merge(
            current_draft=draft,
            document_type="driver_license",
            extracted_fields={
                "driver_license_number": "XT 164890",
                "driver_license_issue_date": "2023-05-12",
            },
            confidence=0.9,
        )

        self.assertEqual(result.draft["driver"]["driving_experience_since"], "2023-05-12")
        self.assertIn("driving_experience_since", result.updated_fields)

    def test_sts_owner_name_is_not_applied_to_driver(self):
        draft = {
            "driver": {"full_name": "Водитель Правильный"},
            "vehicle": {},
            "documents": {"vehicle_registration_doc": None},
            "confidence_by_field": {"full_name": 0.5},
        }
        result = DraftMerger().merge(
            current_draft=draft,
            document_type="vehicle_registration_doc",
            extracted_fields={
                "full_name": "Владелец Чужой",
                "iin": "900101300123",
                "brand": "Toyota",
                "model": "Camry",
                "plate_number": "123ABC01",
            },
            confidence=0.99,
        )
        self.assertEqual(result.draft["driver"]["full_name"], "Водитель Правильный")
        self.assertIsNone(result.draft["driver"].get("iin"))
        self.assertEqual(result.draft["vehicle"]["brand"], "Toyota")
        self.assertEqual(result.draft["vehicle"]["plate_number"], "123ABC01")
        self.assertNotIn("full_name", result.updated_fields)

    def test_normalize_strips_owner_identity_from_sts(self):
        result = DocumentExtractionResult(
            document_type="vehicle_registration_doc",
            full_name="Владелец Чужой",
            iin="900101300123",
            brand="Toyota",
            model="Camry",
            plate_number="123ABC01",
            confidence=0.95,
        )
        fields, recognized = normalize_extracted_fields(result, document_type="vehicle_registration_doc")
        self.assertNotIn("full_name", fields)
        self.assertNotIn("iin", fields)
        self.assertEqual(fields["brand"], "Toyota")
        self.assertNotIn("full_name", recognized)

    def test_openai_provider_is_first_when_configured(self):
        service = DocumentExtractionService()
        settings = SimpleNamespace(
            document_extraction_enabled=True,
            ai_provider="openai",
            openai_api_key="openai-key",
            gemini_api_key="gemini-key",
        )
        with patch("app.documents.extraction.get_settings", return_value=settings), \
            patch.object(service, "_extract_with_openai") as openai_extract, \
            patch.object(service, "_extract_with_gemini") as gemini_extract:
            openai_extract.return_value = DocumentExtractionResult(
                document_type="driver_license_front",
                driver_license_number="ABC123",
                confidence=0.9,
            )
            gemini_extract.return_value = DocumentExtractionResult(
                document_type="selfie_with_license",
                confidence=0.7,
            )

            result = service.extract(b"image-bytes", mime_type="image/jpeg", expected_document_type="unknown")

            self.assertEqual(result.document_type, "driver_license_front")
            openai_extract.assert_called_once()
            gemini_extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
