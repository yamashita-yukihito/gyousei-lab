"""学習画面の絞り込み「集中が切れた時」の決まりを固定する。"""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "static" / "app.js").read_text(encoding="utf-8")
HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")


class EasyReadTest(unittest.TestCase):
    def test_excluded_ids_exist(self):
        block = re.search(r"EASY_READ_EXCLUDE = new Set\(\[(.*?)\]\)", APP, re.S).group(1)
        ids = re.findall(r'"([a-z0-9-]+)"', block)
        self.assertTrue(ids)
        cards = json.loads((REPO / "content" / "explanation_cards.json").read_text(encoding="utf-8"))
        known = {card["id"] for card in cards["items"]}
        self.assertEqual(set(ids) - known, set())

    def test_filter_is_applied_to_the_card_pool(self):
        self.assertIn("matchesStudyEasy(item) &&", APP)
        self.assertIn('entry.prio === "重点" && isEasyReadCard(item)', APP)

    def test_off_by_default_and_not_persisted(self):
        self.assertIn('<select id="study-easy"><option value="off">使わない</option>', HTML)
        self.assertNotRegex(APP, r"setItem\([^)]*study-easy|setItem\(STUDY_EASY")
        self.assertLess(APP.index('$("study-easy").value = "off";'), APP.index('$("study-easy").addEventListener'))

    def test_not_combined_with_today_queue(self):
        self.assertIn("""querySelector('option[value="today"]').disabled = on""", APP)

    def test_related_card_link_clears_the_filter(self):
        body = APP[APP.index("function openRelatedStudyCard"):]
        self.assertLess(body.index('$("study-easy").value = "off"'), body.index("refreshStudyPool"))


if __name__ == "__main__":
    unittest.main()
