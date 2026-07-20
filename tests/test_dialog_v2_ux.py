import unittest

from app.dialog_v2.summary_builder import SummaryBuilder
from app.dialog_v2.ui import CONFIRM_BUTTONS, DOCUMENT_TYPE_LIST, is_confirm_choice
from app.integrations.yandex.messages import format_yandex_error_for_user, manager_phone
from app.whatsapp.parser import parse_whatsapp_payload


class DialogV2UxHelpersTests(unittest.TestCase):
    def test_parser_reads_button_reply_as_text(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid-btn",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {"id": "confirm", "title": "Подтверждаю"},
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        parsed = parse_whatsapp_payload(payload)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].message_type, "text")
        self.assertEqual(parsed[0].text, "confirm")

    def test_parser_reads_list_reply_as_text(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid-list",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "list_reply",
                                            "list_reply": {"id": "fix_full_name", "title": "ФИО"},
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        parsed = parse_whatsapp_payload(payload)
        self.assertEqual(parsed[0].text, "fix_full_name")

    def test_document_progress_and_next_step(self):
        builder = SummaryBuilder()
        draft = {
            "documents": {
                "driver_license": {"file_name": "vu.jpg"},
                "id_card": None,
                "vehicle_registration_doc": None,
                "selfie_with_license": None,
            },
            "missing_fields": ["vehicle_registration_doc", "city"],
        }
        received, total, missing = builder.document_progress(draft)
        self.assertEqual(received, 1)
        self.assertEqual(total, 2)
        self.assertEqual(missing[0], "vehicle_registration_doc")
        text = builder.build_document_reply(
            "driver_license",
            {"full_name": "Иванов Иван"},
            draft["missing_fields"],
            draft,
        )
        self.assertIn("Документы: 1 из 2", text)
        self.assertIn("Следующий шаг:", text)
        self.assertIn("техпаспорт", text.lower())
        self.assertNotIn("удостоверения личности", text.lower())

    def test_document_reply_hides_split_name_parts(self):
        builder = SummaryBuilder()
        draft = {
            "documents": {"driver_license": {"received": True}, "vehicle_registration_doc": None},
            "missing_fields": ["vehicle_registration_doc"],
        }
        text = builder.build_document_reply(
            "driver_license",
            {
                "full_name": "БЕКСУЛТАНОВ МИРАС ҚҰРМАНҒАЗЫҰЛЫ",
                "last_name": "БЕКСУЛТАНОВ",
                "first_name": "МИРАС",
                "middle_name": "ҚҰРМАНҒАЗЫҰЛЫ",
                "iin": "970429300638",
            },
            draft["missing_fields"],
            draft,
        )
        self.assertIn("ФИО: БЕКСУЛТАНОВ МИРАС ҚҰРМАНҒАЗЫҰЛЫ", text)
        self.assertIn("ИИН: 970429300638", text)
        self.assertNotIn("last_name", text)
        self.assertNotIn("first_name", text)
        self.assertNotIn("middle_name", text)

    def test_confirm_helpers_and_buttons(self):
        self.assertTrue(is_confirm_choice("confirm"))
        self.assertTrue(is_confirm_choice("Подтверждаю"))
        self.assertEqual(len(CONFIRM_BUTTONS), 3)
        self.assertEqual(len(DOCUMENT_TYPE_LIST), 3)

    def test_yandex_error_includes_manager_phone(self):
        message = format_yandex_error_for_user(
            "Yandex API error 400: code=duplicate_driver_license, message=duplicate_driver_license"
        )
        self.assertIn(manager_phone(), message)
        self.assertNotIn("Yandex API error", message)


if __name__ == "__main__":
    unittest.main()
