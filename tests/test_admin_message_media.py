import unittest
from types import SimpleNamespace

from app.admin.message_media import message_media_info, resolve_message_media_id
from app.whatsapp.parser import parse_whatsapp_payload


class MessageMediaTests(unittest.TestCase):
    def test_resolve_media_id_from_raw_image_payload(self):
        message = SimpleNamespace(
            id=12,
            media_url=None,
            mime_type="image/jpeg",
            message_type="image",
            raw_payload={"type": "image", "image": {"id": "media-123", "mime_type": "image/jpeg"}},
        )
        info = message_media_info(message)
        self.assertTrue(info.available)
        self.assertEqual(info.media_id, "media-123")
        self.assertEqual(info.kind, "image")
        self.assertEqual(info.preview_url, "/admin/api/messages/12/media")

    def test_resolve_media_id_from_media_url_field(self):
        message = SimpleNamespace(
            id=7,
            media_url="wa-media-9",
            mime_type="application/pdf",
            message_type="document",
            raw_payload=None,
        )
        self.assertEqual(resolve_message_media_id(message), "wa-media-9")
        info = message_media_info(message)
        self.assertTrue(info.available)
        self.assertEqual(info.kind, "document")

    def test_parser_accepts_video_and_sticker(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "77001112233",
                                        "id": "wamid.1",
                                        "type": "video",
                                        "video": {"id": "vid-1", "mime_type": "video/mp4"},
                                    },
                                    {
                                        "from": "77001112233",
                                        "id": "wamid.2",
                                        "type": "sticker",
                                        "sticker": {"id": "stk-1", "mime_type": "image/webp"},
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        parsed = parse_whatsapp_payload(payload)
        self.assertEqual([item.message_type for item in parsed], ["video", "sticker"])
        self.assertEqual(parsed[0].media_id, "vid-1")
        self.assertEqual(parsed[1].media_id, "stk-1")

    def test_whatsapp_chat_url_normalizes_kz_phone(self):
        from app.admin.message_media import whatsapp_chat_url

        self.assertEqual(whatsapp_chat_url("+7 708 405 21 07"), "https://wa.me/77084052107")
        self.assertEqual(whatsapp_chat_url("87084052107"), "https://wa.me/77084052107")
        self.assertIsNone(whatsapp_chat_url(""))


if __name__ == "__main__":
    unittest.main()
