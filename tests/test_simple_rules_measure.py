"""scripts/simple_rules/measure.py の数え方を、作り物の過去問で確かめる。

本物の過去問は非公開編集データにあり、ここでは読まない。語の正規表現・正誤の求め方・
期間ごとの平均・本表に出す条件・正答率の計算だけを見る。
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("simple_rules_measure", ROOT / "scripts" / "simple_rules" / "measure.py")
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)
RULES = {rule[0]: rule for rule in measure.RULES + measure.NO_EFFECT}


def row(text: str, truth: bool, base: float, year: int = 2020, subject: str = "行政法") -> dict:
    return {"year": year, "era": "令和2年", "number": 1, "label": "1", "text": text, "subject": subject, "truth": truth, "base": base}


def question(number: int, stem: str, choices: list[str], answer: str, statements: list[tuple[str, str]] | None = None) -> dict:
    text = stem
    if statements:
        text += " " + " ".join(f"{label}．{body}" for label, body in statements)
    return {
        "examYear": 2020, "eraYear": "令和2年", "questionNumber": number, "subjectLabel": "行政法",
        "questionText": text, "instructionText": "", "isWithdrawn": False,
        "answer": {"kind": "option", "value": answer},
        "choices": [{"label": str(index + 1), "text": value} for index, value in enumerate(choices)],
    }


class WordPatternTest(unittest.TestCase):
    def assertMatch(self, rule_id: str, subject: str, text: str, want: bool) -> None:
        self.assertEqual(want, measure.matches(RULES[rule_id], {"subject": subject, "text": text}), f"{rule_id}: {text}")

    def test_negated_forms_are_not_counted_as_affirmative(self) -> None:
        # 「を認める」の語で「を認めない」まで数えると、肯定の語として測れない。
        self.assertMatch("ke-allow", "憲法", "外国人の政治活動の自由を認めない。", False)
        self.assertMatch("ke-allow", "憲法", "その権利を認められない。", False)
        self.assertMatch("ke-allow", "憲法", "権利の行使を認めることはできない。", False)
        self.assertMatch("ke-allow", "憲法", "判例は、外国人にも政治活動の自由を認めている。", True)

    def test_constitutional_wording_meaning_constitutional_is_not_counted_as_unconstitutional(self) -> None:
        # 「違反するものではない」は合憲の結論。違憲の側で数えず、合憲の側で拾う。
        for text in ("憲法21条1項に違反するものではない。", "憲法14条1項に反するとはいえない。", "憲法14条1項に反しない。"):
            self.assertMatch("ke-unconst", "憲法", text, False)
            self.assertMatch("ke-noviolate", "憲法", text, True)
        self.assertMatch("ke-unconst", "憲法", "憲法14条1項に違反する。", True)
        self.assertMatch("ke-noviolate", "憲法", "憲法14条1項に違反する。", False)

    def test_reason_words_skip_purpose_and_numeric_threshold(self) -> None:
        self.assertMatch("all-reason", "民法", "総株主の議決権の3分の2以上、賛成が必要である。", False)
        self.assertMatch("all-reason", "民法", "公益を確保するため、規制できる。", False)
        self.assertMatch("all-reason", "民法", "契約が成立した以上、解除できない。", True)
        self.assertMatch("all-reason", "民法", "登記がないため、対抗できない。", True)

    def test_no_effect_words_skip_opposite_meanings(self) -> None:
        # 「必ずしも」は「すべて」「必ず」と逆の向き（正しい寄り）なので、断定の語に混ぜない。
        self.assertMatch("no-absolute", "民法", "必ずしも無効とはならない。", False)
        self.assertMatch("no-absolute", "民法", "必ず書面でしなければならない。", True)
        self.assertMatch("no-only", "民法", "できるだけ早く通知する。", False)
        self.assertMatch("no-only", "民法", "本人だけでなく代理人も請求できる。", False)
        self.assertMatch("no-only", "民法", "本人だけが請求できる。", True)


class LoadRowsTest(unittest.TestCase):
    def test_truth_comes_from_the_stem_and_the_answer(self) -> None:
        long = "行政庁は、申請がその事務所に到達したときは審査を開始する（{}）。"
        questions = [
            question(1, "正しいものはどれか。", [long.format(i) for i in range(5)], "3"),
            question(2, "誤っているものはどれか。", [long.format(i) for i in range(5)], "2"),
            question(3, "妥当なものの組合せはどれか。", ["ア・イ", "ア・ウ", "イ・エ", "ウ・オ", "エ・オ"], "2",
                     [(label, long.format(label)) for label in "アイウエオ"]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "all_subjects" / "recent" / "extracted" / "administrative"
            folder.mkdir(parents=True)
            for item in questions:
                (folder / f"q{item['questionNumber']}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            rows = measure.load_rows(Path(tmp) / "all_subjects")
        by_question: dict[int, list[dict]] = {}
        for value in rows:
            by_question.setdefault(value["number"], []).append(value)
        self.assertEqual([False, False, True, False, False], [r["truth"] for r in by_question[1]])
        self.assertEqual([True, False, True, True, True], [r["truth"] for r in by_question[2]])
        self.assertEqual({"ア": True, "イ": False, "ウ": True, "エ": False, "オ": False}, {r["label"]: r["truth"] for r in by_question[3]})
        self.assertAlmostEqual(0.2, by_question[1][0]["base"])
        self.assertAlmostEqual(0.8, by_question[2][0]["base"])
        self.assertAlmostEqual(0.4, by_question[3][0]["base"])


class RuleStatsTest(unittest.TestCase):
    def test_each_period_is_compared_with_its_own_base(self) -> None:
        # 旧10年は「正しいもの」問題（平均で誤り80%）、新10年は「誤っているもの」問題（平均で誤り20%）。
        rows = [row("申請は認められない。", False, 0.2, year=2010) for _ in range(10)]
        rows += [row("申請は認められない。", index == 0, 0.8, year=2020) for index in range(10)]
        stats = measure.rule_stats(RULES["gy-block"], rows)
        self.assertEqual((100, 80), (stats["early"], stats["earlyBase"]))
        self.assertEqual((90, 20), (stats["late"], stats["lateBase"]))
        self.assertTrue(stats["stable"])
        self.assertEqual("", stats["unstableReason"])

    def test_a_lean_in_only_one_period_goes_to_reference(self) -> None:
        rows = [row("申請は認められない。", False, 0.2, year=2010) for _ in range(10)]
        # 新10年は平均どおり（誤り20%）で、語が効いていない。
        rows += [row("申請は認められない。", index >= 2, 0.8, year=2020) for index in range(10)]
        stats = measure.rule_stats(RULES["gy-block"], rows)
        self.assertEqual((20, 20), (stats["late"], stats["lateBase"]))
        self.assertFalse(stats["stable"])
        self.assertIn("新10年", stats["unstableReason"])

    def test_few_choices_go_to_reference(self) -> None:
        rows = [row("申請は認められない。", False, 0.2, year=2010 + index) for index in range(measure.MIN_COUNT - 1)]
        stats = measure.rule_stats(RULES["gy-block"], rows)
        self.assertFalse(stats["stable"])
        self.assertIn(f"{measure.MIN_COUNT - 1}肢しかない", stats["unstableReason"])


class SimulateTest(unittest.TestCase):
    def test_same_direction_words_count_once(self) -> None:
        # 「ことがある」は全科目の語と基礎知識の語の両方に当たる。2点にしない。
        self.assertEqual(1, measure.score(row("例外とされることがある。", True, 0.5, subject="基礎知識")))
        self.assertEqual(0, measure.score(row("例外とされることがあるため、認められない。", True, 0.5, subject="基礎知識")))

    def test_accuracy_and_pairs(self) -> None:
        rows = []
        # 問1：正解の肢だけに正しい寄りの語がある。問2：どの肢にも語がない（5肢で引き分け）。
        for number, answer_text in ((1, "例外とされることがある。"), (2, "例外とされる。")):
            for index in range(5):
                text = answer_text if index == 0 else "例外とされる。"
                rows.append({**row(text, index == 0, 0.2), "number": number, "label": str(index + 1)})
        result = measure.simulate(rows)
        self.assertEqual(2, result["questions"])
        self.assertEqual(60, result["accuracy"])  # (1 + 1/5) / 2
        self.assertEqual(50, result["withSignal"])
        self.assertEqual({"pairs": 8, "decided": 50, "accuracy": 100}, result["pairs"])


class WordSignalsFileTest(unittest.TestCase):
    def test_json_was_written_from_the_same_rules(self) -> None:
        # 語を足したり消したりしたら、measure.py --write で JSON を作り直す。
        data = json.loads((ROOT / "static" / "technique" / "word-signals.json").read_text(encoding="utf-8"))
        self.assertEqual([rule[0] for rule in measure.RULES], [rule["id"] for rule in data["rules"]])
        if "noEffect" in data:
            self.assertEqual([rule[0] for rule in measure.NO_EFFECT], [rule["id"] for rule in data["noEffect"]])
        if "criteria" in data:
            self.assertEqual({"splitYear": measure.SPLIT_YEAR, "minCount": measure.MIN_COUNT, "minLift": measure.MIN_LIFT}, data["criteria"])


if __name__ == "__main__":
    unittest.main()
