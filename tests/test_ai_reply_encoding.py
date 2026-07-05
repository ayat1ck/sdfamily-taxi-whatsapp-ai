import unittest

from app.dialog.ai import CASUAL_SMALLTALK_REPLY, SHORT_SUPPORT_REPLY, _clarification_reply
from app.dialog.states import DialogueState


class AIReplyEncodingTests(unittest.TestCase):
    def test_casual_smalltalk_reply_is_clean(self):
        self.assertIn("Здравствуйте", CASUAL_SMALLTALK_REPLY)
        self.assertIn("Регистрация", CASUAL_SMALLTALK_REPLY)
        self.assertNotIn("Р В ", CASUAL_SMALLTALK_REPLY)
        self.assertNotIn("пїЅ", CASUAL_SMALLTALK_REPLY)

    def test_short_support_reply_is_clean(self):
        self.assertIn("Уточните", SHORT_SUPPORT_REPLY)
        self.assertNotIn("Р В ", SHORT_SUPPORT_REPLY)
        self.assertNotIn("пїЅ", SHORT_SUPPORT_REPLY)

    def test_clarification_reply_is_clean(self):
        reply = _clarification_reply(DialogueState.ASK_CITY)
        self.assertIn("город", reply.lower())
        self.assertNotIn("Р В ", reply)
        self.assertNotIn("пїЅ", reply)


if __name__ == "__main__":
    unittest.main()
