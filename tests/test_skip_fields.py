"""勉強しない3分野の「形だけで解く」数字と画面（AGENTS.md §5.2.3）。"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static" / "technique"


class SkipFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads((STATIC / "skip-fields.json").read_text(encoding="utf-8"))

    def test_three_fields_only(self) -> None:
        self.assertEqual({"local", "family", "company"}, set(self.data["fields"]))

    def test_words_on_the_page_lean_the_same_way_in_both_decades(self) -> None:
        criteria = self.data["criteria"]
        for name, field in self.data["fields"].items():
            for word in field["words"]:
                self.assertGreaterEqual(word["count"], criteria["minCount"], (name, word["label"]))
                self.assertGreaterEqual(word["early"] - word["earlyBase"], criteria["minLift"] - 1, (name, word["label"]))
                self.assertGreaterEqual(word["late"] - word["lateBase"], criteria["minLift"] - 1, (name, word["label"]))
                self.assertTrue(word["note"], (name, word["label"]))

    def test_no_question_text_leaks(self) -> None:
        # 集計だけを置く。肢の文章が紛れていないことを、長い文字列が無いことで見る。
        text = (STATIC / "skip-fields.json").read_text(encoding="utf-8")
        for value in re.findall(r'"((?:[^"\\]|\\.)*)"', text):
            self.assertLess(len(value), 60, value)

    def test_page_has_the_tab_and_reads_the_json(self) -> None:
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="skip-tab"', page)
        self.assertIn('id="skip-section"', page)
        self.assertIn('"skip"', script)
        self.assertIn("skip-fields.json", script)

    def test_company_longest_rule_keeps_the_direction(self) -> None:
        # 商法の最も長い肢は「誤っているもの」の問題だけで効く。向きを混ぜて書かない。
        company = self.data["fields"]["company"]
        self.assertGreater(company["longestFalse"]["rate"], 20)
        self.assertLess(company["longestTrue"]["rate"], 20)


if __name__ == "__main__":
    unittest.main()
