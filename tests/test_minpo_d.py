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
        self.assertEqual(23, len(data["chapters"]))
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

        self.assertEqual(184, len(question_ids))
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
        # 版番号そのものを固定すると、キャッシュを更新するたびにテストが落ちる。
        # 見たいのは「CSSとJSの版がそろっていること」なので、そちらを確かめる。
        versions = re.findall(r'(?:styles\.css|app\.js)\?v=([0-9A-Za-z-]+)', html)
        self.assertEqual(len(versions), 2, "styles.cssとapp.jsの両方に版番号が要る")
        self.assertEqual(versions[0], versions[1], "CSSとJSの版番号がそろっていない")
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

    def test_chapters_follow_civil_code_order(self) -> None:
        chapters = load_data()["chapters"]
        self.assertEqual(list(range(1, len(chapters) + 1)), [c["number"] for c in chapters])
        ids = [c["id"] for c in chapters]
        # 総則→物権→担保物権→債権総論→債権各論→親族・相続。学習日程（docs/STUDY_SCHEDULE.md）と同じ順。
        self.assertLess(ids.index("declaration-third-party"), ids.index("registration"))
        self.assertLess(ids.index("registration"), ids.index("mortgage"))
        self.assertLess(ids.index("mortgage"), ids.index("guarantee-joint"))
        self.assertLess(ids.index("guarantee-joint"), ids.index("sale-rescission"))
        self.assertLess(ids.index("sale-rescission"), ids.index("family"))
        # 2026-10-21に足した6章の位置（占有197条〜・留置権295条〜・贈与549条〜・請負632条〜・事務管理697条〜・遺言960条〜）
        self.assertLess(ids.index("good-faith-acquisition"), ids.index("possession-easement"))
        self.assertLess(ids.index("possession-easement"), ids.index("co-ownership"))
        self.assertLess(ids.index("lien-pledge"), ids.index("mortgage"))
        self.assertLess(ids.index("gift-loan"), ids.index("sale-rescission"))
        self.assertLess(ids.index("lease"), ids.index("work-mandate-deposit"))
        self.assertLess(ids.index("management-unjust"), ids.index("tort"))
        self.assertEqual(["inheritance", "will"], ids[-2:])

    def test_added_chapters_cite_actual_exam_questions(self) -> None:
        # 2026-10-20に足した12章と2026-10-21に足した6章は、行政書士試験の過去問で問われた論点だけで作った。
        # どの問題で問われたかを、年度・問・肢で必ず持つ（原文は載せない）。
        added = {
            "declaration-third-party", "limited-capacity", "prescription", "co-ownership",
            "mortgage", "guarantee-joint", "assignment-setoff", "creditor-protection",
            "sale-rescission", "lease", "tort", "family",
            "will", "gift-loan", "work-mandate-deposit", "management-unjust", "lien-pledge", "possession-easement",
        }
        pattern = re.compile(r"^(平成|令和)(元|[0-9]+)年問[0-9]+")
        seen = set()
        for chapter in load_data()["chapters"]:
            if chapter["id"] not in added:
                continue
            seen.add(chapter["id"])
            for question in chapter["questions"]:
                refs = question.get("examRefs")
                self.assertTrue(refs, question["id"])
                for ref in refs:
                    self.assertRegex(ref, pattern, question["id"])
        self.assertEqual(added, seen)

    def test_exam_refs_link_only_recent_questions_to_goukakudojyo(self) -> None:
        # 「本試験での出題」は直近10年（平成28〜令和7年度）だけ合格道場の問題ページへ結ぶ（2026-10-22）。
        # 旧10年のURLは画面へ出さない決まり（AGENTS.md §5.3）なので、対応表に入れない。
        data = load_data()
        links = data["examLinks"]
        url = re.compile(r"^https://www\.pro\.goukakudojyo\.com/worksheet2/w_main(nendo|arch)\.php\?queID=[0-9]+$")
        for key, value in links.items():
            self.assertRegex(key, r"^20(1[6-9]|2[0-5])-q[0-9]+$")
            self.assertRegex(value, url)
        pattern = re.compile(r"^(平成|令和)(元|[0-9]+)年問([0-9]+)")
        for chapter in data["chapters"]:
            for question in chapter["questions"]:
                for ref in question.get("examRefs") or []:
                    era, number, q = pattern.match(ref).groups()
                    year = (1988 if era == "平成" else 2018) + (1 if number == "元" else int(number))
                    if year >= 2016:
                        self.assertIn(f"{year}-q{q}", links, (question["id"], ref))
        js = (MINPO_D / "app.js").read_text(encoding="utf-8")
        self.assertIn("function examRefUrl(ref)", js)
        self.assertIn("examRefUrl(ref)", js)

    def test_added_chapters_legal_boundaries_do_not_regress(self) -> None:
        questions = {
            question["id"]: question
            for chapter in load_data()["chapters"]
            for question in chapter["questions"]
        }
        expected = {
            # 意思表示：詐欺の第三者は善意無過失、第三者の強迫は相手方が善意でも取り消せる
            "minpo-d-dec-002": False, "minpo-d-dec-003": False, "minpo-d-dec-004": True,
            "minpo-d-dec-007": False, "minpo-d-dec-008": True,
            # 制限行為能力：黙秘だけは詐術でない、催告の効果は相手によって追認／取消し
            "minpo-d-lc-002": False, "minpo-d-lc-005": False, "minpo-d-lc-006": True, "minpo-d-lc-007": False,
            # 時効：完成前の第三者は登記不要、完成後は登記の先後、後順位抵当権者は援用不可
            "minpo-d-pr-001": True, "minpo-d-pr-002": False, "minpo-d-pr-007": False, "minpo-d-pr-008": True,
            # 抵当権：払渡し後は物上代位不可、転貸料は原則不可、395条の6か月
            "minpo-d-mtg-002": False, "minpo-d-mtg-005": True, "minpo-d-mtg-006": False,
            # 債権譲渡・相殺：通知は譲渡人、同時到達でも拒めない、差押え後の他人債権は不可
            "minpo-d-as-002": False, "minpo-d-as-003": False, "minpo-d-as-004": True, "minpo-d-as-008": False,
            # 連帯債務：請求・免除は相対効（2020年改正後）
            "minpo-d-gj-006": True, "minpo-d-gj-007": False,
            # 賃貸借：敷金は明渡しの後、合意解除は転借人に対抗できない
            "minpo-d-ls-004": False, "minpo-d-ls-007": False, "minpo-d-ls-008": True,
            # 売買：受領遅滞後の滅失、軽微な不履行、減額は追完の催告の後
            "minpo-d-sr-002": False, "minpo-d-sr-004": False, "minpo-d-sr-006": False, "minpo-d-sr-008": True,
            # 代位・詐害：相続放棄は取消し不可、遺産分割は取消し可
            "minpo-d-cp-007": False, "minpo-d-cp-008": False,
            # 不法行為・親族・共有
            "minpo-d-tt-002": False, "minpo-d-tt-008": False,
            "minpo-d-fm-002": False, "minpo-d-fm-006": False,
            "minpo-d-co-002": False, "minpo-d-co-007": False, "minpo-d-co-008": True,
            # 遺言：成年被後見人は医師2人、カーボン複写は有効、撤回は別方式でよい、受遺者の先死亡で失効、
            # 相続させる遺言でも法定相続分を超える部分は登記が要る、他人との共有建物に配偶者居住権は成立しない
            "minpo-d-wil-001": True, "minpo-d-wil-002": False, "minpo-d-wil-003": True, "minpo-d-wil-005": True,
            "minpo-d-wil-006": False, "minpo-d-wil-007": False, "minpo-d-wil-008": True,
            # 贈与と貸し借り：履行後は解除不可、無利息が原則、返還時期なしは相当期間の催告、使用貸借の必要費は借主
            "minpo-d-gl-002": False, "minpo-d-gl-004": False, "minpo-d-gl-005": False, "minpo-d-gl-006": True,
            "minpo-d-gl-007": True, "minpo-d-gl-008": False,
            # 請負・委任・寄託：報酬は引渡しと同時、完成後は641条で解除不可、無償委任も善管注意、書面寄託は催告後に解除
            "minpo-d-wmd-001": False, "minpo-d-wmd-003": False, "minpo-d-wmd-005": False, "minpo-d-wmd-007": True,
            "minpo-d-wmd-008": False,
            # 事務管理・不当利得：報酬なし、管理中の損害は請求不可、代理権なし、既登記建物は登記まで移して給付
            "minpo-d-mu-002": False, "minpo-d-mu-003": True, "minpo-d-mu-004": False, "minpo-d-mu-005": False,
            "minpo-d-mu-006": False, "minpo-d-mu-007": True, "minpo-d-mu-008": True,
            # 担保物権：二重譲渡の損害賠償では留置不可、333条の引渡しに占有改定を含む、質権は占有改定で成立しない
            "minpo-d-lp-001": True, "minpo-d-lp-002": False, "minpo-d-lp-003": False, "minpo-d-lp-005": False,
            "minpo-d-lp-006": True, "minpo-d-lp-007": True, "minpo-d-lp-008": False,
            # 占有・地役権・相隣：引渡し前は占有の訴え不可、対抗要件なしの賃借権では請求不可、地役権は不可分、根は自分で切れる
            "minpo-d-pe-002": False, "minpo-d-pe-003": False, "minpo-d-pe-004": True, "minpo-d-pe-006": False,
            "minpo-d-pe-007": True, "minpo-d-pe-008": False,
        }
        for question_id, answer in expected.items():
            self.assertIs(answer, questions[question_id]["answer"], question_id)
        self.assertIn("当然に無効ではなく", questions["minpo-d-fm-002"]["explanation"]["summary"])
        self.assertIn("805条", questions["minpo-d-fm-002"]["explanation"]["rule"])
        self.assertIn("1年以内", questions["minpo-d-sr-008"]["statement"])

    def test_svg_marker_ids_are_unique_per_render(self) -> None:
        js = (MINPO_D / "app.js").read_text(encoding="utf-8")
        self.assertIn("state.diagramSerial += 1", js)
        self.assertRegex(js, re.compile(r"d-\$\{chapter\.id\}-\$\{question\.id\}-\$\{state\.diagramSerial\}"))
        self.assertNotIn("id: 'arrow'", js)


if __name__ == "__main__":
    unittest.main()
