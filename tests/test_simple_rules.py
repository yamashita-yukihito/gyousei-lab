from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "simple-rules"
FIELD = re.compile(r"^\s*(?:-\s+)?(概念|解説|具体例|過去問|カード|実測|目安|理由|使い方)\s*::\s*(.+)$")
TABLES = ("sense", "ok", "ng")
# static/simple-rules/app.js の EXAM と同じ形。
EXAM = re.compile(r"^(.+?年問\d+)(?:肢([^○×]+))?([○×])?$")


def parse() -> dict[str, list[dict[str, str]]]:
    text = re.sub(r"<!--.*?-->", "", (PAGE / "content.md").read_text(encoding="utf-8"), flags=re.S)
    tables: dict[str, list[dict[str, str]]] = {name: [] for name in TABLES}
    table = None
    subject = None
    item = None
    for line in text.splitlines():
        if not line.strip():
            continue
        top = re.match(r"^#\s+(sense|ok|ng)\s*$", line)
        if top:
            table, subject, item = top.group(1), None, None
            continue
        sub = re.match(r"^##\s+(.+)$", line)
        if sub:
            subject, item = sub.group(1), None
            continue
        field = FIELD.match(line)
        if not field or table is None or subject is None:
            raise AssertionError(f"読めない行: {line}")
        if field.group(1) == "概念":
            item = {"科目": subject}
            tables[table].append(item)
        item[field.group(1)] = field.group(2)
    return tables


class SimpleRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tables = parse()

    def test_every_item_has_the_three_columns_and_an_exam(self) -> None:
        for name in ("ok", "ng"):
            self.assertGreater(len(self.tables[name]), 0, name)
            for item in self.tables[name]:
                for key in ("概念", "解説", "具体例", "過去問"):
                    self.assertTrue(item.get(key), f"{item['概念']}: {key}")

    def test_every_sense_item_has_evidence_and_how_to_use(self) -> None:
        measured = json.loads((PAGE / "measured.json").read_text(encoding="utf-8"))
        self.assertGreater(len(self.tables["sense"]), 0)
        for item in self.tables["sense"]:
            for key in ("理由", "使い方"):
                self.assertTrue(item.get(key), f"{item['概念']}: {key}")
            # 割合は measured.json から出す。本文の「目安」に手で割合を書かない。
            self.assertTrue(item.get("実測") or item.get("目安"), item["概念"])
            if item.get("実測"):
                self.assertIn(item["実測"], measured, item["概念"])
            self.assertNotRegex(item.get("目安", ""), r"\d+%", item["概念"])

    def test_measured_numbers_are_percentages(self) -> None:
        measured = json.loads((PAGE / "measured.json").read_text(encoding="utf-8"))
        for rule_id, value in measured.items():
            self.assertIn(value["direction"], {"true", "false"}, rule_id)
            self.assertTrue(0 <= value["hit"] <= 100, rule_id)
            self.assertTrue(0 <= value["base"] <= 100, rule_id)
            self.assertGreater(value["count"], 0, rule_id)

    def test_exam_references_resolve_to_links(self) -> None:
        links = json.loads((PAGE / "exam-links.json").read_text(encoding="utf-8"))
        for name in TABLES:
            for item in self.tables[name]:
                for token in item.get("過去問", "").split():
                    found = EXAM.match(token)
                    self.assertIsNotNone(found, token)
                    self.assertIn(found.group(1), links, token)
                    self.assertTrue(links[found.group(1)].startswith("https://www.pro.goukakudojyo.com/"), token)

    def test_card_ids_exist(self) -> None:
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        known = {card["id"] for card in cards}
        for name in TABLES:
            for item in self.tables[name]:
                for card_id in item.get("カード", "").split():
                    self.assertIn(card_id, known, item["概念"])

    def test_covers_all_six_subjects(self) -> None:
        want = {"行政法", "民法", "憲法", "商法・会社法", "基礎知識", "基礎法学"}
        for name in ("ok", "ng"):
            self.assertEqual(want, {item["科目"] for item in self.tables[name]}, name)
        self.assertEqual(want | {"試験全体"}, {item["科目"] for item in self.tables["sense"]})

    def test_page_is_linked_and_does_not_write(self) -> None:
        self.assertIn('href="simple-rules/"', (ROOT / "static" / "index.html").read_text(encoding="utf-8"))
        self.assertIn('href="simple-rules/"', (ROOT / "static" / "architecture.html").read_text(encoding="utf-8"))
        script = (PAGE / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("POST", script)
        self.assertNotIn("localStorage", script)


if __name__ == "__main__":
    unittest.main()
