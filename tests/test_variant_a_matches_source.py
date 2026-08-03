"""Aが⑤の肢の原文と1文字も違わないことを見る。

AGENTS.md 5章の決まり。装飾記法を外した `variants.a` が、そのカードの⑤に載っている
肢のどれかと完全に一致していなければならない。一致しないカードもあってよいが、
それは「⑤にその論点を正面から問う肢が無い」「肢がA・B・C表記で単独では読めない」
などの理由で**意図的に自作した**ものに限る。装飾を足すときに読点を1つ増やす、
言い換えてしまう、といった書き写しのミスをここで捕まえる。

⑤の出典は非公開領域にあるので、その環境が無いときは飛ばす。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unittest
from pathlib import Path

import card_edit

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "content" / "explanation_cards.json"
EVID = Path(
    os.environ.get(
        "GYOUSEI_DATA_ROOT",
        os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring"),
    )
) / "canonical" / "related_question_source.json"


class VariantAMatchesSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        if not EVID.is_file():
            self.skipTest("⑤の出典が無い環境なので飛ばす")
        self.cards = json.loads(CARDS.read_text(encoding="utf-8"))["items"]
        self.originals = {
            c["choiceId"]: c["officialOriginalText"]
            for c in json.loads(EVID.read_text(encoding="utf-8"))["choices"]
        }

    @staticmethod
    def _drop_footnote_marks(value: str) -> str:
        """原文に入っている注番号（`*` や `*4`）を落とす。

        AGENTS.md 5章の例外(3)で、注番号入りの肢はそのままAへ写せないため、
        注番号だけを外した形はミスではなく正しい扱いである。
        """
        return re.sub(r"\s*\*\d*\s*", "", value)

    def test_no_near_miss_copies(self) -> None:
        """惜しい不一致を落とす。

        完全一致でも、まったく違う自作命題でもなく、**1〜数文字だけ違う**ものは
        ほぼ確実に書き写しのミスである。自作するなら自分の言葉で書くので、
        原文と97%も似ることはない。
        """
        offenders = []
        for card in self.cards:
            plain = card_edit.strip_markup(card["variants"]["a"])
            texts = [
                self.originals[ref["choiceId"]]
                for ref in card.get("relatedPastQuestions") or []
                if ref["choiceId"] in self.originals
            ]
            if any(text == plain for text in texts):
                continue
            if any(
                self._drop_footnote_marks(text) == self._drop_footnote_marks(plain)
                for text in texts
            ):
                continue  # 注番号を外しただけ。例外(3)にあたる
            for text in texts:
                if abs(len(text) - len(plain)) > 8:
                    continue
                ratio = difflib.SequenceMatcher(None, plain, text).ratio()
                if ratio >= 0.97:
                    diff = [
                        s for s in difflib.ndiff(plain, text) if s[0] != " "
                    ]
                    offenders.append(f"{card['id']}: {''.join(diff)}")
                    break
        self.assertEqual(offenders, [], "Aが原文と数文字だけ違う。書き写しのミスの疑い")

    def test_match_rate_does_not_regress(self) -> None:
        """一致率が下がっていないことを見る。カードを足すたびに下げない。"""
        matched = sum(
            1
            for card in self.cards
            if any(
                self.originals.get(ref["choiceId"])
                == card_edit.strip_markup(card["variants"]["a"])
                for ref in card.get("relatedPastQuestions") or []
            )
        )
        self.assertGreaterEqual(
            matched / len(self.cards),
            0.70,
            f"A＝原文の一致率が70%を下回った（{matched}/{len(self.cards)}）",
        )


if __name__ == "__main__":
    unittest.main()
