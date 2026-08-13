"""正本ドキュメントに書いた枚数が、カード正本の実データと合っていることを確かめる。

2026-08-13に、次の9箇所が実データとずれているのが見つかった。

- `README.md` が商法・会社法を24枚（実際は28枚）、頻出度監査を187件・13枚未統合
  （実際は246件で一致済み）と書いていた。
- `ARCHITECTURE.md` が同じ2箇所に加え、`learningType` の内訳を暗記158／理解88
  （実際は167／79）と書いていた。2026-08-07に商法9枚を暗記ものへ直したぶんが
  反映されていなかった。
- `HANDOFF.md` の「次のAIに最初に読ませる指示」が、カード200件・行政法70…と
  2026-07-29ごろの数字のままだった。**新しいAIセッションが最初に読む文章**なので、
  ここがずれていると誤った前提で作業が始まる。
- `static/study-guide.html` が暗記158枚／理解88枚を焼き込んでいた。
  `AGENTS.md` 7.1 は「件数は手入力せず `GET /api/study-queue` から読ませている。
  数字をHTMLへ焼き込まない」と決めているのに、守られていなかった。

カードを足す・`learningType` を付け替えるたびに人が全ファイルを直すのは続かないので、
機械で見張る。**ここが落ちたら、ドキュメント側の数字を実データに合わせて直す。**
カードを減らす意図的な変更なら、期待値ではなくドキュメントを直せば通る。

HANDOFF.md の日付つき作業記録（「同日さらに、…158枚にした」など）は、その時点の
履歴として正しいので対象にしない。見るのは**現況として書かれている要約だけ**である。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "content" / "explanation_cards.json"

# 科目IDと、ドキュメントで使っている日本語名の対応。
SUBJECT_LABELS = {
    "administrative-law": "行政法",
    "general-knowledge": "基礎知識",
    "civil-law": "民法",
    "constitutional-law": "憲法",
    "commercial-law": "商法・会社法",
    "legal-foundations": "基礎法学",
}


def _load_counts() -> dict:
    items = json.loads(CARDS.read_text(encoding="utf-8"))["items"]
    subjects: dict[str, int] = {}
    learning: dict[str, int] = {}
    for card in items:
        subjects[card["subjectId"]] = subjects.get(card["subjectId"], 0) + 1
        learning[card["learningType"]] = learning.get(card["learningType"], 0) + 1
    return {"total": len(items), "subjects": subjects, "learning": learning}


class DocCardCountTest(unittest.TestCase):
    def setUp(self) -> None:
        self.counts = _load_counts()

    def _read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def _one(self, text: str, pattern: str, where: str) -> str:
        """パターンがちょうど1回だけ当たることを確かめて、その数字を返す。

        当たらなくなったら文章を書き換えたということなので、そのときは
        このテストの側も一緒に直す。黙って素通りさせない。
        """
        found = re.findall(pattern, text)
        self.assertEqual(
            len(found),
            1,
            f"{where}: 想定した書き方が見つからない（{len(found)}件）。"
            f"文章を変えたなら tests/test_doc_card_counts.py も直す。 pattern={pattern}",
        )
        return found[0]

    def test_readme_total_and_subject_breakdown(self) -> None:
        text = self._read("README.md")
        total = self._one(text, r"全6科目(\d+)件がそろいました", "README.md の総数")
        self.assertEqual(int(total), self.counts["total"])
        for subject_id, label in SUBJECT_LABELS.items():
            with self.subTest(subject=label):
                found = self._one(
                    text, rf"{re.escape(label)}(\d+)[、）]", f"README.md の{label}"
                )
                self.assertEqual(int(found), self.counts["subjects"][subject_id])

    def test_readme_learning_type_breakdown(self) -> None:
        text = self._read("README.md")
        memorize = self._one(text, r"暗記もの(\d+)枚", "README.md の暗記もの")
        understand = self._one(text, r"理解もの(\d+)枚", "README.md の理解もの")
        self.assertEqual(int(memorize), self.counts["learning"]["memorize"])
        self.assertEqual(int(understand), self.counts["learning"]["understand"])

    def test_architecture_total_and_subject_breakdown(self) -> None:
        text = self._read("ARCHITECTURE.md")
        total = self._one(
            text, r"○×学習カード(\d+)件（行政法", "ARCHITECTURE.md の現況の総数"
        )
        self.assertEqual(int(total), self.counts["total"])
        breakdown = self._one(
            text,
            r"○×学習カード\d+件（([^）]+)）",
            "ARCHITECTURE.md の科目別内訳",
        )
        for subject_id, label in SUBJECT_LABELS.items():
            with self.subTest(subject=label):
                found = self._one(
                    breakdown, rf"{re.escape(label)}(\d+)", f"内訳の{label}"
                )
                self.assertEqual(int(found), self.counts["subjects"][subject_id])

    def test_architecture_learning_type_breakdown(self) -> None:
        text = self._read("ARCHITECTURE.md")
        memorize = self._one(
            text, r"暗記もの(\d+)枚／理解もの\d+枚", "ARCHITECTURE.md の暗記もの"
        )
        understand = self._one(
            text, r"暗記もの\d+枚／理解もの(\d+)枚", "ARCHITECTURE.md の理解もの"
        )
        self.assertEqual(int(memorize), self.counts["learning"]["memorize"])
        self.assertEqual(int(understand), self.counts["learning"]["understand"])

    def test_architecture_commercial_law_sentence(self) -> None:
        """商法・会社法だけは枚数を本文で名指ししているので、そこも見る。"""
        text = self._read("ARCHITECTURE.md")
        found = self._one(
            text, r"\*\*商法・会社法\*\*は(\d+)枚を掲載している", "ARCHITECTURE.md の商法"
        )
        self.assertEqual(int(found), self.counts["subjects"]["commercial-law"])

    def test_handoff_onboarding_block(self) -> None:
        """次のAIが最初に読む指示。ここがずれると誤った前提で作業が始まる。"""
        text = self._read("HANDOFF.md")
        total = self._one(text, r"○×学習カードは(\d+)件で", "HANDOFF.md の総数")
        self.assertEqual(int(total), self.counts["total"])
        breakdown = self._one(
            text, r"全6科目がそろっています\*\*（([^）]+)）", "HANDOFF.md の科目別内訳"
        )
        # HANDOFF.md の要約だけは「商法」と短く書いている。
        labels = dict(SUBJECT_LABELS, **{"commercial-law": "商法"})
        for subject_id, label in labels.items():
            with self.subTest(subject=label):
                found = self._one(breakdown, rf"{re.escape(label)}(\d+)", f"内訳の{label}")
                self.assertEqual(int(found), self.counts["subjects"][subject_id])
        memorize = self._one(text, r"暗記もの(\d+)枚・理解もの", "HANDOFF.md の暗記もの")
        understand = self._one(text, r"・理解もの(\d+)枚", "HANDOFF.md の理解もの")
        self.assertEqual(int(memorize), self.counts["learning"]["memorize"])
        self.assertEqual(int(understand), self.counts["learning"]["understand"])

    def test_study_guide_does_not_hardcode_counts(self) -> None:
        """AGENTS.md 7.1「数字をHTMLへ焼き込まない」を守っていることを見る。

        枚数はページを開くたびに `GET /api/study-queue` から読む。焼き込むと、
        learningType を付け替えたときに案内だけが古いまま残る。
        """
        text = self._read("static/study-guide.html")
        for span_id in (
            "count-all",
            "count-memorize",
            "count-memorize-2",
            "count-memorize-3",
            "count-understand",
            "count-understand-2",
            "count-understand-3",
        ):
            with self.subTest(span=span_id):
                self.assertIn(
                    f'id="{span_id}"',
                    text,
                    f"{span_id} が無い。枚数を焼き込む形へ戻していないか確かめる。",
                )
        self.assertIn(
            "api/study-queue?limit=1",
            text,
            "枚数をAPIから読む取得が消えている。",
        )
        # 総数・暗記・理解の実数が本文へ書き戻されていないこと。
        for number in (
            self.counts["total"],
            self.counts["learning"]["memorize"],
            self.counts["learning"]["understand"],
        ):
            with self.subTest(number=number):
                self.assertNotRegex(
                    text,
                    rf"(?:暗記もの|理解もの|)\s*{number}枚",
                    f"{number}枚 がHTMLへ焼き込まれている。APIから読む形にする。",
                )


if __name__ == "__main__":
    unittest.main()
