"""問番号ごとの出題傾向（static/question-slots/）のデータと書き出しの整合。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "question_slots"))

import build  # noqa: E402
import slots_data  # noqa: E402


class QuestionSlotsTest(unittest.TestCase):
    def test_all_sixty_numbers_with_ten_recent_years(self):
        self.assertEqual(sorted(slots_data.S), list(range(1, 61)))
        groups = {g[1] for g in slots_data.GROUPS}
        for n, s in slots_data.S.items():
            self.assertIn(s["group"], groups, n)
            if n <= 57:
                self.assertEqual(len(s["years"].split("／")), 10, n)
            self.assertTrue((s["p"] is None) != (s["ev"] is None), n)
            if s["ev"] is not None:
                self.assertLessEqual(s["ev"], s["max"], n)

    def test_points_add_up_to_the_exam(self):
        total = sum(s["max"] for s in slots_data.S.values())
        general = sum(s["max"] for s in slots_data.S.values() if s["group"] == "chishiki")
        self.assertEqual(total, 300)
        self.assertEqual(general, 56)

    def test_card_links_use_existing_card_ids(self):
        items = json.loads((ROOT / "content" / "explanation_cards.json").read_text())["items"]
        ids = {c["id"] for c in items}
        cards = json.loads((ROOT / "scripts" / "question_slots" / "cards_by_slot.json").read_text())
        for n, cids in cards.items():
            self.assertTrue(1 <= int(n) <= 60)
            self.assertLessEqual(len(cids), 200)
            for cid in cids:
                self.assertIn(cid, ids, n)

    def test_written_html_is_up_to_date(self):
        current = (ROOT / "static" / "question-slots" / "index.html").read_text()
        self.assertEqual(current, build.build(), "build.py --write で書き出し直す")


if __name__ == "__main__":
    unittest.main()
