from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "legal-doctrines"
FIELD = re.compile(r"^\s*(?:-\s+)?(法理|別名|正確|やさしく|根拠|過去問|カード)\s*::\s*(.+)$")
EXAM = re.compile(r"^(平成|令和)(\d+|元)年問\d+$")
# 過去問に出ていないのに載せてよいのは、行政書士法の令和7年改正の新しい規定だけ（AGENTS.md §5.3 の例外）。
NOT_YET_ASKED = {"両罰規定"}
EXACT_MAX = 110


def parse() -> list[dict[str, str]]:
    text = re.sub(r"<!--.*?-->", "", (PAGE / "content.md").read_text(encoding="utf-8"), flags=re.S)
    items: list[dict[str, str]] = []
    group = None
    item = None
    for line in text.splitlines():
        if not line.strip():
            continue
        head = re.match(r"^##\s+(.+)$", line)
        if head:
            group, item = head.group(1), None
            continue
        field = FIELD.match(line)
        if not field or group is None:
            raise AssertionError(f"読めない行: {line}")
        if field.group(1) == "法理":
            item = {"区分": group}
            items.append(item)
        item[field.group(1)] = field.group(2)
    return items


class LegalDoctrinesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.items = parse()

    def test_every_doctrine_has_the_required_parts(self) -> None:
        self.assertGreater(len(self.items), 0)
        names = [item["法理"] for item in self.items]
        self.assertEqual(len(names), len(set(names)), "同じ法理を2回書かない")
        for item in self.items:
            for key in ("正確", "やさしく", "根拠"):
                self.assertTrue(item.get(key), f"{item['法理']}: {key}")
            if item["法理"] not in NOT_YET_ASKED:
                self.assertTrue(item.get("過去問"), f"{item['法理']}: 過去問")

    def test_exact_text_is_short(self) -> None:
        for item in self.items:
            self.assertLessEqual(len(item["正確"]), EXACT_MAX, item["法理"])

    def test_covers_requested_doctrine_and_all_groups(self) -> None:
        self.assertIn("権利外観法理", {item["法理"] for item in self.items})
        want = {"法の一般原則（全科目）", "民法", "行政法", "憲法", "商法・会社法", "基礎法学", "基礎知識"}
        self.assertEqual(want, {item["区分"] for item in self.items})

    def test_exam_references_resolve_to_links(self) -> None:
        links = json.loads((ROOT / "static" / "simple-rules" / "exam-links.json").read_text(encoding="utf-8"))
        for item in self.items:
            for token in item.get("過去問", "").split():
                self.assertRegex(token, EXAM, item["法理"])
                self.assertIn(token, links, item["法理"])

    def test_card_ids_exist(self) -> None:
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        known = {card["id"] for card in cards}
        for item in self.items:
            ids = item.get("カード", "").split()
            self.assertEqual(len(ids), len(set(ids)), item["法理"])
            for card_id in ids:
                self.assertIn(card_id, known, item["法理"])

    def test_page_is_linked_and_does_not_write(self) -> None:
        self.assertIn('href="legal-doctrines/"', (ROOT / "static" / "index.html").read_text(encoding="utf-8"))
        self.assertIn('href="legal-doctrines/"', (ROOT / "static" / "architecture.html").read_text(encoding="utf-8"))
        self.assertIn('href="../legal-doctrines/"', (ROOT / "static" / "simple-rules" / "index.html").read_text(encoding="utf-8"))
        script = (PAGE / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("POST", script)
        self.assertNotIn("localStorage", script)


if __name__ == "__main__":
    unittest.main()
