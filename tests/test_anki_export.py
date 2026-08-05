"""Ankiへの書き出しの回帰テスト。

固定したいのは3つ。

- **表に答えを出さない。** ラボと同じで、A・B1・B2・Cが表、正誤と解説は裏である。
- **装飾記法を持ち込まない。** `%%` `__` `**` `==` `!!` `@@` を1つも残さない。
- 列がずれない（タブと改行が本文に混ざらない）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "authoring" / "tools"))

import anki_export  # noqa: E402

MARKS = ("%%", "__", "**", "==", "!!", "@@")


def sample_card() -> dict:
    return {
        "id": "gyo-sample-001",
        "subjectId": "administrative-law",
        "topic": "行政手続法",
        "correct": False,
        "variants": {
            "a": "%%行政庁%%は、__理由__を示さなければ**ならない**。",
            "b": "やさしい言い換え。\n2行目もある。",
            "bCasual": "別の角度からの言い換え。",
            "bCasualStyle": "用語からほどく",
            "c": "==ひとこと==",
        },
        "correction": "正しくは==こう==です@@（14条）@@。",
        "memoryPoint": "!!分かれ目!!はここ。",
        "explanations": {
            "normal": "普通の解説。",
            "deepDive": {
                "background": "背景。", "trap": "ひっかけ。", "example": "場面。",
            },
            "commonSense": "常識力。",
        },
        "frequency": {"label": "頻出"},
        "relatedPastQuestions": [{"choiceId": "src:1:choice:3"}],
    }


EVIDENCE = {
    "src:1:choice:3": {
        "choiceId": "src:1:choice:3",
        "choiceLabel": "3",
        "eraYear": "令和7年",
        "questionNumber": 11,
    }
}


class AnkiExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.card = sample_card()
        self.rows = anki_export.rows_for([self.card], EVIDENCE)
        self.row = self.rows[0]

    def test_front_never_contains_the_answer(self) -> None:
        front = self.row[0]
        self.assertNotIn("答え:", front)
        self.assertNotIn("正しくは", front)
        self.assertNotIn("普通の解説", front)
        self.assertIn("行政庁は、理由を示さなければならない。", front)

    def test_back_carries_the_verdict_and_the_explanations(self) -> None:
        back = self.row[1]
        self.assertIn("答え: ×（誤り）", back)
        self.assertIn("正しくはこうです（14条）。", back)
        self.assertIn("分かれ目はここ。", back)
        self.assertIn("普通の解説。", back)
        self.assertIn("常識力。", back)

    def test_no_markup_survives(self) -> None:
        for column in self.row:
            for mark in MARKS:
                self.assertNotIn(mark, column, f"{mark} が残っている: {column[:60]}")

    def test_columns_never_shift(self) -> None:
        for column in self.row:
            self.assertNotIn("\t", column)
            self.assertNotIn("\n", column)
        self.assertEqual(len(self.row), 6)

    def test_tags_and_source_columns(self) -> None:
        self.assertIn("administrative-law", self.row[2])
        self.assertIn("gyo-sample-001", self.row[2])
        self.assertEqual(self.row[3], "gyo-sample-001")
        self.assertEqual(self.row[4], "頻出")
        self.assertEqual(self.row[5], "令和7年問11肢3")

    def test_html_special_characters_are_escaped(self) -> None:
        """#html:true を宣言しているので、本文の < > & はタグとして解釈される。"""
        card = sample_card()
        card["variants"]["a"] = "条文は<b>太字</b>、A & B、5<10 とする。"
        card["correction"] = "正しくは <script>alert(1)</script> です。"
        row = anki_export.rows_for([card], EVIDENCE)[0]
        self.assertIn("&lt;b&gt;", row[0])
        self.assertNotIn("<b>", row[0])
        self.assertIn("&amp;", row[0])
        self.assertIn("5&lt;10", row[0])
        self.assertIn("&lt;script&gt;", row[1])
        self.assertNotIn("<script>", row[1])
        # 段落の区切りに使う <br> だけは、こちらが足したものとして残る。
        self.assertIn("<br>", row[0])

    def test_every_column_is_sanitized_not_only_the_body(self) -> None:
        """タグ・カードID・頻出度・出典にタブや改行が入っても列がずれない。"""
        card = sample_card()
        card["topic"] = "行政\t法"
        card["frequency"] = {"label": "頻出\n出題"}
        card["id"] = "t\r1"
        row = anki_export.rows_for([card], EVIDENCE)[0]
        self.assertEqual(len(row), 6)
        for column in row:
            self.assertNotIn("\t", column)
            self.assertNotIn("\n", column)
            self.assertNotIn("\r", column)
        self.assertEqual(row[4], "頻出 出題")

    def test_control_characters_are_removed(self) -> None:
        card = sample_card()
        card["variants"]["c"] = "制御\x00文字\x1f入り"
        row = anki_export.rows_for([card], EVIDENCE)[0]
        self.assertNotIn("\x00", row[0])
        self.assertNotIn("\x1f", row[0])

    def test_render_starts_with_the_anki_header(self) -> None:
        text = anki_export.render([self.card], EVIDENCE)
        lines = text.splitlines()
        self.assertEqual(lines[0], "#separator:tab")
        self.assertIn("#tags column:3", lines)
        self.assertEqual(len([l for l in lines if not l.startswith("#")]), 1)


class AnkiExportRealCardsTest(unittest.TestCase):
    """正本の全カードを通しても、装飾が残らず列がずれないことを見る。"""

    def test_every_card_exports_cleanly(self) -> None:
        import json

        source = ROOT / "content" / "explanation_cards.json"
        cards = json.loads(source.read_text(encoding="utf-8"))["items"]
        rows = anki_export.rows_for(cards, {})
        self.assertEqual(len(rows), len(cards))
        for row, card in zip(rows, cards):
            self.assertEqual(len(row), 6)
            for column in row:
                self.assertNotIn("\t", column)
                self.assertNotIn("\n", column)
                for mark in MARKS:
                    self.assertNotIn(mark, column, f"{card['id']} に {mark} が残る")
            self.assertNotIn("答え:", row[0])


if __name__ == "__main__":
    unittest.main()
