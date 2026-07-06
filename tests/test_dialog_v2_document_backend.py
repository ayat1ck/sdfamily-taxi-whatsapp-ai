import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.dialog_v2.document_types import DocumentTypeResolver
from app.documents.extraction import DocumentExtractionResult, DocumentExtractionService


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
