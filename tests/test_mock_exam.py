from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "mock-exam"
NODE = "/opt/homebrew/bin/node"

# app.js を画面なしで読み込み、採点に使う純粋な関数だけを呼ぶ。
# 末尾の init() は画面を組み立てるので外し、DOMとlocalStorageは空の代用品で足りる。
# node -e に渡した引数は process.argv[1] から始まる。
HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/\ninit\(\);\s*$/, '\n');
const chapters = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const store = {};
const context = {
  console,
  localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } },
  document: { getElementById: () => ({ hidden: false }) },
  window: {},
};
vm.createContext(context);
const api = vm.runInContext(source + `
;({ sectionOf, maxPoints, formatOf, yearLabel, officialPdfUrl, eraToYear, buildDrillIndex, recommendedPlan, PASS, YEARS, PLANNED })`, context);
const slots = [];
for (let n = 1; n <= 60; n += 1) slots.push({ number: n, question: null });
const sum = (filter) => slots.filter(filter).reduce((total, slot) => total + api.maxPoints(slot), 0);
const drill = api.buildDrillIndex(chapters);
console.log(JSON.stringify({
  total: sum(() => true),
  legal: sum((slot) => api.sectionOf(slot.number).group === 'legal'),
  general: sum((slot) => api.sectionOf(slot.number).group === 'general'),
  written: sum((slot) => api.formatOf(slot) === 'written'),
  blank: sum((slot) => api.formatOf(slot) === 'multiple_blank'),
  pass: api.PASS,
  years: api.YEARS,
  planned: api.PLANNED,
  labels: [2016, 2018, 2019, 2021, 2025].map(api.yearLabel),
  eras: ['平成28年', '令和元年', '令和3年', '令和7年', 'x'].map(api.eraToYear),
  pdf: [2017, 2018, 2019, 2021].map(api.officialPdfUrl),
  drillR3Q29: Array.from((drill.get('2021:29') || new Map()).keys()),
  drillKeys: drill.size,
  planOn1022: api.recommendedPlan(new Date(2026, 9, 22, 9)),
  planOn1026: api.recommendedPlan(new Date(2026, 9, 26, 9)),
  planOn1102: api.recommendedPlan(new Date(2026, 10, 2, 9)),
}));
"""


def run_harness() -> dict:
    result = subprocess.run(
        [NODE, "-e", HARNESS, str(PAGE / "app.js"), str(ROOT / "static" / "minpo-d" / "chapters.json")],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class MockExamScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = run_harness()

    def test_points_follow_the_real_exam(self) -> None:
        # 法令等: 択一40問×4＋多肢3問×8＋記述3問×20＝244、基礎知識14問×4＝56。
        self.assertEqual(self.data["total"], 300)
        self.assertEqual(self.data["legal"], 244)
        self.assertEqual(self.data["general"], 56)
        self.assertEqual(self.data["written"], 60)
        self.assertEqual(self.data["blank"], 24)

    def test_pass_lines(self) -> None:
        self.assertEqual(
            self.data["pass"],
            {"legal": 122, "legalMax": 244, "general": 24, "generalMax": 56, "total": 180, "totalMax": 300},
        )

    def test_year_and_era_conversion(self) -> None:
        self.assertEqual(self.data["labels"], ["平成28年度", "平成30年度", "令和元年度", "令和3年度", "令和7年度"])
        self.assertEqual(self.data["eras"], [2016, 2019, 2021, 2025, None])
        self.assertEqual(self.data["years"], list(range(2025, 2015, -1)))

    def test_official_pdf_only_from_heisei30(self) -> None:
        self.assertEqual(
            self.data["pdf"],
            [
                None,
                "https://gyosei-shiken.or.jp/pdf/h30_mondai.pdf",
                "https://gyosei-shiken.or.jp/pdf/r1_mondai.pdf",
                "https://gyosei-shiken.or.jp/pdf/r3_mondai.pdf",
            ],
        )

    def test_drill_chapters_are_found_from_exam_refs(self) -> None:
        # 令和3年問29は抵当権の章に出題箇所として書いてある。
        self.assertIn("mortgage", self.data["drillR3Q29"])
        self.assertGreater(self.data["drillKeys"], 40)

    def test_planned_years_match_study_schedule(self) -> None:
        schedule = (ROOT / "docs" / "STUDY_SCHEDULE.md").read_text(encoding="utf-8")
        planned = {item["date"]: item["year"] for item in self.data["planned"]}
        self.assertEqual(planned, {"2026-10-25": 2021, "2026-11-01": 2020})
        self.assertRegex(schedule, r"10/25 日 \| \*\*通し演習1\*\* \| 13:00〜16:00 令和3年度")
        self.assertRegex(schedule, r"11/1 日 \| \*\*通し演習2\*\* \| 13:00〜16:00 令和2年度")
        self.assertEqual(self.data["planOn1022"]["year"], 2021)
        self.assertEqual(self.data["planOn1026"]["year"], 2020)
        self.assertIsNone(self.data["planOn1102"])


class MockExamContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PAGE / "app.js").read_text(encoding="utf-8")
        self.html = (PAGE / "index.html").read_text(encoding="utf-8")

    def test_asset_versions_stay_in_sync(self) -> None:
        version = re.search(r"const APP_VERSION = 'mock-([^']+)';", self.source).group(1)
        self.assertIn(f"app.js?v={version}", self.html)
        self.assertIn(f"styles.css?v={version}", self.html)

    def test_answers_go_only_to_past_question_history(self) -> None:
        # 回答は過去問の回答履歴へ mode=mock_exam で送る。学習カードの記録（FSRSの期日）には触れない。
        self.assertIn("API + '/attempts'", self.source)
        self.assertIn("mode: 'mock_exam'", self.source)
        self.assertNotIn("card-attempts", self.source)
        self.assertNotIn("card-marks", self.source)
        self.assertIn("const API = '../api';", self.source)

    def test_event_ids_are_fixed_per_exam_and_question(self) -> None:
        # 保存のやり直しで二重に数えないよう、eventId は通し演習と問題番号から決める。
        self.assertIn("eventId: 'mock-' + active.id + '-q' + slot.number", self.source)
        self.assertIn("active.attempts = buildAttempts();", self.source)

    def test_progress_stays_in_its_own_storage_key(self) -> None:
        self.assertIn("const STORE_KEY = 'gyouseiMock:v1';", self.source)

    def test_entry_links(self) -> None:
        index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="mock-exam/"', index)
        self.assertIn('href="../"', self.html)


class MockExamReasonsAndPickedCardsTest(unittest.TestCase):
    def test_reasons_are_local_and_go_into_summary(self) -> None:
        # 間違えた理由は通し演習の端末内の記録だけに持ち、サーバーへは送らない。コピー用の文と履歴に入れる。
        source = (PAGE / "app.js").read_text(encoding="utf-8")
        self.assertIn("const REASONS = [", source)
        for label in ("知らなかった", "覚え違い", "2択まで絞って外した", "問題文・肢の読み違い", "時間不足・焦り"):
            self.assertIn(label, source)
        start = source.index("function buildAttempts()")
        end = source.index("async function postAttempts()", start)
        self.assertNotIn("reason", source[start:end])
        self.assertIn("lines.push('間違えた理由：'", source)
        self.assertIn("row.reasons = ", source)

    def test_mock_exam_links_wrong_cards_as_a_picked_set(self) -> None:
        source = (PAGE / "app.js").read_text(encoding="utf-8")
        self.assertIn("'../?cardIds=' + ids.map(encodeURIComponent).join(',') + '&from=mock#study'", source)

    def test_picked_view_accepts_only_loaded_card_ids(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<option value="picked" hidden disabled>', html)
        start = source.index("  function requestedPickedCardIds(")
        end = source.index("  function openRequestedPickedCards(", start)
        block = source[start:end]
        self.assertIn("state.studyById.has(id)", block)
        self.assertIn("slice(0, 200)", block)
        # 指定したカードの回答は、ふつうの回答として記録する（期日を動かさない rapid とは分ける）。
        self.assertIn('isPickedStudyView() ? "picked"', source)
        study_queue = (ROOT / "study_queue.py").read_text(encoding="utf-8")
        self.assertIn('MODES_OUTSIDE_SCHEDULE = ("rapid",)', study_queue)
        # 指定ビューは絞り込みを通さず、渡された順に出す。
        start = source.index("  function getFilteredStudyCards()")
        self.assertIn("if (isPickedStudyView()) return pickedStudyCards();", source[start:start + 200])


class QuizRelatedCardsTest(unittest.TestCase):
    def test_quiz_result_links_back_to_cards(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        start = source.index("  function showQuizResult(")
        end = source.index("\n  }\n", start)
        self.assertIn("renderQuizRelatedCards(question);", source[start:end])
        self.assertIn('id="quiz-related-cards"', html)
        # カードを開くのは既存の openRelatedStudyCard だけ。URLやHTMLへ値を流さない。
        start = source.index("  function renderQuizRelatedCards(")
        end = source.index("  function cardsForQuestion(", start)
        self.assertIn("openRelatedStudyCard(studyCardId(card))", source[start:end])
        self.assertNotIn("innerHTML", source[start:end])


if __name__ == "__main__":
    unittest.main()
