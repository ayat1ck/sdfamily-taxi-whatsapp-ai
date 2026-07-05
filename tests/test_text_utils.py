import unittest

from app.utils.text import looks_like_mojibake, repair_mojibake


class TextUtilsTests(unittest.TestCase):
    def test_repair_mojibake_repairs_existing_driver_menu_line(self):
        broken = "Р’С‹РІРѕРґ РґРµРЅРµРі"
        self.assertEqual(repair_mojibake(broken), "Вывод денег")

    def test_repair_mojibake_repairs_full_sentence(self):
        broken = "РџРѕРЅСЏР», РІС‹ СѓР¶Рµ РїРѕРґРєР»СЋС‡РµРЅС‹. Р§С‚Рѕ РЅСѓР¶РЅРѕ СЃРґРµР»Р°С‚СЊ?"
        self.assertEqual(repair_mojibake(broken), "Понял, вы уже подключены. Что нужно сделать?")

    def test_repair_mojibake_keeps_clean_text_unchanged(self):
        clean = "Понял, вы уже подключены. Что нужно сделать?"
        self.assertEqual(repair_mojibake(clean), clean)

    def test_looks_like_mojibake_detects_broken_emoji_prefix(self):
        broken = "рџ“Ќ РЈРєР°Р¶РёС‚Рµ Р°РґСЂРµСЃ"
        self.assertTrue(looks_like_mojibake(broken))


if __name__ == "__main__":
    unittest.main()
