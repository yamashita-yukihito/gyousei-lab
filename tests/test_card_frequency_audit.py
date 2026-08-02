"""カードと頻出度監査正本のIDが一致していることを確かめる。

2026-08-02までの間、画面のカードは200枚なのに監査正本は187件だった。
7月31日に足した13枚が、カード側のfrequencyだけを持ち、その表示の根拠が
正本に無い状態になっていた。画面に頻出度を出す以上、その数がどの問題から
来たのかは正本に残っていなければならない。

監査正本は非公開の編集データ配下にあるので、その環境が無いときは飛ばす。
Gitに入っているのはカードだけである。
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "content" / "explanation_cards.json"
AUDIT = Path(
    os.environ.get(
        "GYOUSEI_DATA_ROOT",
        os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring"),
    )
) / "curation" / "card_frequency_2006_2025.json"

LABEL_RANGES = (
    ("重要論点", 1, 2),
    ("繰り返し出題", 3, 5),
    ("頻出", 6, 9),
    ("最頻出", 10, 10**6),
)


class CardFrequencyAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        if not AUDIT.is_file():
            self.skipTest("頻出度監査正本が無い環境なので飛ばす")
        self.cards = json.loads(CARDS.read_text(encoding="utf-8"))["items"]
        self.audit = json.loads(AUDIT.read_text(encoding="utf-8"))["cards"]

    def test_every_card_has_an_audit_entry(self) -> None:
        card_ids = {c["id"] for c in self.cards}
        audit_ids = {a["cardId"] for a in self.audit}
        self.assertEqual(
            card_ids - audit_ids, set(), "監査正本に根拠が無いまま頻出度を表示しているカード"
        )
        self.assertEqual(
            audit_ids - card_ids, set(), "カードが無いのに監査正本だけ残っているID"
        )

    def test_label_matches_the_counted_questions(self) -> None:
        """ラベルは問題数で機械的に決まる。手で書き換えられていないことを見る。"""
        by_card = {a["cardId"]: a for a in self.audit}
        for card in self.cards:
            entry = by_card[card["id"]]
            count = entry["combined"]["questionCount"]
            expected = next(
                name for name, low, high in LABEL_RANGES if low <= count <= high
            )
            with self.subTest(card=card["id"]):
                self.assertEqual(entry["combined"]["label"], expected)
                self.assertEqual(card["frequency"]["label"], expected)
                self.assertEqual(card["frequency"]["occurrences"], count)


if __name__ == "__main__":
    unittest.main()
