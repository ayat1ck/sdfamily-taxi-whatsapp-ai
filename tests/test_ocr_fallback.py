import unittest
from types import SimpleNamespace

try:
    from app.dialog.engine import DialogueEngine
    from app.documents.extraction import DocumentExtractionResult
except ModuleNotFoundError:  # pragma: no cover
    DialogueEngine = None
    DocumentExtractionResult = None


class OCRFallbackTests(unittest.TestCase):
    def setUp(self):
        if DialogueEngine is None or DocumentExtractionResult is None:
            self.skipTest("runtime dependencies unavailable")
        self.engine = DialogueEngine.__new__(DialogueEngine)

    def _driver(self):
        return SimpleNamespace(support_context_json=None, updated_at=None)

    def test_empty_ocr_once_increments_counter(self):
        driver = self._driver()
        self.engine._increment_ocr_failure_counter(driver)
        self.assertEqual(self.engine._ocr_failure_count(driver), 1)

    def test_empty_ocr_twice_can_enable_manual_mode(self):
        driver = self._driver()
        self.engine._increment_ocr_failure_counter(driver)
        self.engine._increment_ocr_failure_counter(driver)
        self.engine._set_manual_data_entry_enabled(driver, True)
        self.assertEqual(self.engine._ocr_failure_count(driver), 2)
        self.assertTrue(driver.support_context_json["manual_data_entry"])

    def test_successful_ocr_resets_counter(self):
        driver = self._driver()
        self.engine._increment_ocr_failure_counter(driver)
        self.engine._increment_ocr_failure_counter(driver)
        self.engine._reset_ocr_failure_counter(driver)
        self.assertEqual(self.engine._ocr_failure_count(driver), 0)

    def test_document_extraction_result_can_represent_empty_outcome(self):
        result = DocumentExtractionResult(
            provider_status="empty",
            provider_chain=["gemini_empty", "openai_empty"],
            failure_reason="gemini_empty;openai_empty",
        )
        self.assertEqual(result.provider_status, "empty")
        self.assertEqual(result.provider_chain[-1], "openai_empty")
        self.assertIn("openai_empty", result.failure_reason)


if __name__ == "__main__":
    unittest.main()
