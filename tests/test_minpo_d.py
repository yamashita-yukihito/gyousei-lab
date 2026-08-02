from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINPO_D = ROOT / "static" / "minpo-d"


def load_data() -> dict:
    return json.loads((MINPO_D / "chapters.json").read_text(encoding="utf-8"))


class MinpoDTest(unittest.TestCase):
    def test_files_schema_ids_and_required_content(self) -> None:
        for name in ("index.html", "styles.css", "app.js", "chapters.json"):
            self.assertTrue((MINPO_D / name).is_file(), name)

        data = load_data()
        self.assertEqual("minpo-d@1", data["schemaVersion"])
        self.assertEqual("2026-04-01", data["asOf"])
        self.assertEqual(5, len(data["chapters"]))
        self.assertEqual(6, len(data["cast"]))

        chapter_ids = [chapter["id"] for chapter in data["chapters"]]
        self.assertEqual(len(chapter_ids), len(set(chapter_ids)))
        question_ids: list[str] = []
        for chapter in data["chapters"]:
            self.assertEqual(8, len(chapter["questions"]), chapter["id"])
            self.assertTrue(chapter["baseScenario"])
            self.assertTrue(chapter["goal"])
            self.assertTrue(chapter["remedyGuide"])
            self.assertTrue(chapter["summaryTable"]["rows"])
            for question in chapter["questions"]:
                question_ids.append(question["id"])
                self.assertIn(question["kind"], {"straight", "trap"})
                self.assertIsInstance(question["answer"], bool)
                self.assertGreaterEqual(len(question["currentScenario"]), 24)
                self.assertGreaterEqual(len(question["statement"]), 18)
                self.assertEqual(
                    {"before", "now", "same"},
                    set(question["delta"]) - {"transition"},
                    question["id"],
                )
                if "transition" in question["delta"]:
                    self.assertEqual("reset", question["delta"]["transition"])
                for field in (
                    "summary", "fact", "rule", "conclusion", "protectWho",
                    "why", "ifOpposite", "legalSense", "minimum", "next",
                ):
                    self.assertTrue(question["explanation"].get(field), (question["id"], field))
                self.assertTrue(question["legalBasis"])
                for basis in question["legalBasis"]:
                    self.assertTrue(basis["url"].startswith("https://"))
                    self.assertNotEqual("https://www.courts.go.jp/hanrei/", basis["url"])
                    if "e-gov.go.jp" in basis["url"]:
                        self.assertIn("occasion_date=20260401", basis["url"])

        self.assertEqual(40, len(question_ids))
        self.assertEqual(len(question_ids), len(set(question_ids)))

    def test_every_chapter_and_label_mix_true_and_false(self) -> None:
        for chapter in load_data()["chapters"]:
            answers = [question["answer"] for question in chapter["questions"]]
            self.assertEqual(4, sum(answers), chapter["id"])
            self.assertEqual(4, len(answers) - sum(answers), chapter["id"])
            for kind in ("straight", "trap"):
                kind_answers = [
                    question["answer"]
                    for question in chapter["questions"]
                    if question["kind"] == kind
                ]
                self.assertIn(True, kind_answers, (chapter["id"], kind))
                self.assertIn(False, kind_answers, (chapter["id"], kind))

    def test_each_question_owns_a_complete_diagram(self) -> None:
        cast_ids = {item["id"] for item in load_data()["cast"]}
        for chapter in load_data()["chapters"]:
            base_nodes = {node["id"] for node in chapter["diagram"]["nodes"]}
            self.assertTrue(base_nodes)
            for node in chapter["diagram"]["nodes"]:
                if node.get("castId"):
                    self.assertIn(node["castId"], cast_ids)
            for question in chapter["questions"]:
                diagram = question["diagram"]
                self.assertTrue(diagram["edges"], question["id"])
                self.assertTrue(question["answerEdges"], question["id"])
                hidden = set(diagram.get("hiddenNodes", []))
                self.assertLessEqual(hidden, base_nodes)
                extra = {node["id"] for node in diagram.get("extraNodes", [])}
                visible = (base_nodes - hidden) | extra
                self.assertTrue(set(diagram.get("nodeLabels", {})) <= base_nodes | extra)
                for edge in diagram["edges"] + question["answerEdges"]:
                    self.assertIn(edge["from"], visible, (question["id"], edge))
                    self.assertIn(edge["to"], visible, (question["id"], edge))

    def test_card_links_only_use_existing_public_card_ids(self) -> None:
        cards = json.loads(
            (ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8")
        )["items"]
        existing = {card["id"] for card in cards}
        linked = {
            question["cardId"]
            for chapter in load_data()["chapters"]
            for question in chapter["questions"]
            if question.get("cardId")
        }
        self.assertTrue(linked)
        self.assertLessEqual(linked, existing)
        self.assertIn("min-property-good-faith-acquisition-001", linked)
        self.assertIn("min-property-registration-bad-faith-001", linked)
        self.assertIn("min-security-statutory-superficies-same-owner-001", linked)

    def test_read_solve_exam_paths_and_safe_card_link_are_present(self) -> None:
        html = (MINPO_D / "index.html").read_text(encoding="utf-8")
        js = (MINPO_D / "app.js").read_text(encoding="utf-8")
        self.assertIn("styles.css?v=20260802-7", html)
        self.assertIn("app.js?v=20260802-7", html)
        self.assertIn("minpoD:v1", html)
        self.assertIn("const STORE_KEY = 'minpoD:v1'", js)
        self.assertIn("chapter.questions.forEach", js)
        self.assertIn("state.mode !== 'exam'", js)
        self.assertLess(
            js.index("if (state.mode !== 'exam') {\n    badges.appendChild(el('span', 'badge topic', question.topic))"),
            js.index("badges.appendChild(el('span', 'badge topic', '本番変換'))"),
        )
        self.assertIn("question.currentScenario", js)
        self.assertIn("question.answerEdges", js)
        self.assertIn("encodeURIComponent(question.cardId)", js)
        self.assertIn("fetch('chapters.json'", js)
        self.assertIn("const changed = state.mode !== mode", js)
        self.assertIn("chapterRecord(chapter.id).current[mode].index", js)
        self.assertIn("'未回答の問題へ →'", js)
        self.assertIn("moveToQuestion(firstUnanswered)", js)
        self.assertIn("summary: params.get('summary') === '1'", js)
        self.assertIn("function savedSummary(chapterId, mode)", js)
        self.assertIn("state.mode === 'exam' ? '#1d2925'", js)
        self.assertIn("state.mode === 'exam' ? castAliasesMap.get(node.castId)", js)
        self.assertIn("document.getElementById('storage-warning')", js)

    def test_exam_mode_keeps_base_and_current_facts_visible(self) -> None:
        js = (MINPO_D / "app.js").read_text(encoding="utf-8")
        base_at = js.index("appendRichText(document.getElementById('base-scenario'), chapter.baseScenario")
        scenario_at = js.index("appendRichText(scenario, question.currentScenario")
        delta_at = js.index("if (state.mode !== 'exam') card.appendChild(renderDelta")
        statement_at = js.index("appendRichText(statement, question.statement")
        self.assertLess(base_at, scenario_at)
        self.assertLess(scenario_at, delta_at)
        self.assertLess(delta_at, statement_at)

        # C案で成立しなかった盗品問題の再発防止。差分を隠しても必要事実が本文にある。
        question = next(
            question
            for chapter in load_data()["chapters"]
            for question in chapter["questions"]
            if question["id"] == "minpo-d-gfa-007"
        )
        self.assertIn("盗難から1年", question["currentScenario"])
        self.assertIn("時計店", question["currentScenario"])
        self.assertIn("代金30万円", question["currentScenario"])
        self.assertIn("知らず", question["currentScenario"])
        self.assertIn("注意しても分からない", question["currentScenario"])

    def test_reviewed_legal_boundaries_do_not_regress(self) -> None:
        questions = {
            question["id"]: question
            for chapter in load_data()["chapters"]
            for question in chapter["questions"]
        }
        self.assertTrue(questions["minpo-d-ua-006"]["answer"])
        self.assertIn("悪意でも", questions["minpo-d-ua-006"]["statement"])
        self.assertFalse(questions["minpo-d-gfa-003"]["answer"])
        self.assertIn("占有改定", questions["minpo-d-gfa-003"]["statement"])
        self.assertEqual("reset", questions["minpo-d-gfa-003"]["delta"]["transition"])
        self.assertIn("善意無過失", questions["minpo-d-gfa-006"]["currentScenario"])
        for question_id in ("minpo-d-gfa-007", "minpo-d-gfa-008"):
            diagram = questions[question_id]["diagram"]
            self.assertIn("gian", diagram["hiddenNodes"])
            self.assertEqual(["shop"], [node["id"] for node in diagram["extraNodes"]])
            self.assertNotIn("gian", diagram.get("nodeLabels", {}))
        self.assertNotIn(
            "腕時計を返還",
            [edge["label"] for edge in questions["minpo-d-gfa-008"]["diagram"]["edges"]],
        )
        self.assertFalse(questions["minpo-d-reg-002"]["answer"])
        self.assertTrue(questions["minpo-d-reg-003"]["answer"])
        self.assertIn("二重譲渡していません", questions["minpo-d-reg-006"]["currentScenario"])
        self.assertIn("二重譲渡していません", questions["minpo-d-reg-007"]["currentScenario"])
        self.assertIn("nobita", questions["minpo-d-sup-001"]["diagram"]["hiddenNodes"])
        self.assertNotIn(
            "nobita",
            {
                endpoint
                for edge in questions["minpo-d-sup-001"]["diagram"]["edges"]
                for endpoint in (edge["from"], edge["to"])
            },
        )
        self.assertIn("設定した時は更地", questions["minpo-d-sup-002"]["currentScenario"])
        self.assertFalse(questions["minpo-d-sup-002"]["answer"])
        self.assertTrue(questions["minpo-d-sup-007"]["answer"])
        self.assertIn("出木杉", questions["minpo-d-inh-002"]["currentScenario"])
        self.assertFalse(questions["minpo-d-inh-006"]["answer"])
        self.assertIn("全財産をドラえもんへ遺贈", questions["minpo-d-inh-007"]["currentScenario"])
        self.assertIn("請求期間はまだ過ぎていません", questions["minpo-d-inh-007"]["currentScenario"])
        self.assertFalse(questions["minpo-d-inh-008"]["answer"])
        self.assertNotIn("代襲者", json.dumps(questions["minpo-d-inh-003"]["diagram"], ensure_ascii=False))
        self.assertNotIn("個別遺留分1/8", json.dumps(questions["minpo-d-inh-007"]["diagram"], ensure_ascii=False))

    def test_svg_marker_ids_are_unique_per_render(self) -> None:
        js = (MINPO_D / "app.js").read_text(encoding="utf-8")
        self.assertIn("state.diagramSerial += 1", js)
        self.assertRegex(js, re.compile(r"d-\$\{chapter\.id\}-\$\{question\.id\}-\$\{state\.diagramSerial\}"))
        self.assertNotIn("id: 'arrow'", js)


if __name__ == "__main__":
    unittest.main()
