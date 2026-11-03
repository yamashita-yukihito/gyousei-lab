"""本試験形式の練習（static/exam-practice/）と、過去問原文タブの練習セット入口を見る。

練習セットの正本は sets.json。問題IDは合格道場の問題ページ（exam-links.json）と食い違わないこと、
カードIDは実在すること、ねらい（解く前に見せる）は短いこと、各肢の理由の先頭の○×が問いの向きと合うこと、
絞り方に書いた常識・誤りの作り方の名前が実在することを確かめる。
問題文は公開リポジトリに入れない（ページは ../api/questions から読む）。稼働中bundleがあれば、
各肢の○×が正解と合うことと、問題文がそのまま入っていないことも確かめる。
練習ページはサーバーへ書き込まず、記録はlocalStorageだけに持つ。「解く前に答えを表示する」は既定でオン。
過去問原文タブは ?questionIds= で渡された問題だけを、渡された順に、習得済みも外さずに出す。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "exam-practice"
LINKS = ROOT / "static" / "simple-rules" / "exam-links.json"
CARDS = ROOT / "content" / "explanation_cards.json"
RULES = ROOT / "static" / "simple-rules" / "content.md"
HINTS = ROOT / "static" / "assets" / "card-hints.json"
BUNDLE = Path.home() / ".local/share/yuki-services/gyousei-lab/gyousei-production.json"
SKIP_FIELDS = ("地方自治法", "親族", "相続", "家族法", "一般知識", "政治", "経済", "社会")
QUESTION_TEXT_KEYS = {"question", "questions_text", "instruction", "choices", "text", "statementText", "content"}


def quoted_names(text, head):
    """常識「「何のための制度か」から結論を出す」のように、入れ子のかぎ括弧を含む名前も取り出す。"""
    names = []
    start = 0
    while True:
        i = text.find(head + "「", start)
        if i < 0:
            return names
        k = i + len(head) + 1
        depth = 1
        while k < len(text) and depth:
            depth += {"「": 1, "」": -1}.get(text[k], 0)
            k += 1
        names.append(text[i + len(head) + 1:k - 1])
        start = k


def load_sets():
    return json.loads((PAGE / "sets.json").read_text(encoding="utf-8"))


def all_questions(data):
    return [question for set_ in data["sets"] for question in set_["questions"]]


class ExamPracticeSetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_sets()
        cls.links = json.loads(LINKS.read_text(encoding="utf-8"))
        cards = json.loads(CARDS.read_text(encoding="utf-8"))
        items = cards["items"] if isinstance(cards, dict) else cards
        cls.card_ids = {card["id"] for card in items}

    def questions(self):
        return all_questions(self.data)

    def test_each_question_is_the_listed_past_question(self):
        self.assertGreaterEqual(len(self.questions()), 30)
        for question in self.questions():
            self.assertRegex(question["questionId"], r"^goukakudojyo:\d+$")
            self.assertIn(question["exam"], self.links, question["exam"])
            self.assertEqual(question["url"], self.links[question["exam"]], question["exam"])
            number = question["questionId"].split(":")[1]
            self.assertTrue(question["url"].endswith("queID=" + number), question["exam"])

    def test_no_question_appears_twice(self):
        ids = [question["questionId"] for question in self.questions()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_sets_fit_in_one_sitting(self):
        for set_ in self.data["sets"]:
            self.assertTrue(set_["id"])
            self.assertTrue(5 <= len(set_["questions"]) <= 12, set_["id"])

    def test_cards_exist(self):
        for question in self.questions():
            self.assertTrue(question["cards"], question["exam"])
            for card in question["cards"]:
                self.assertIn(card["id"], self.card_ids, question["exam"])
                self.assertTrue(card["label"], question["exam"])
            for key in ("links", "near"):
                for label, ids in question[key].items():
                    self.assertIn(label, question["notes"], question["exam"])
                    self.assertTrue(ids, question["exam"])
                    for card_id in ids:
                        self.assertIn(card_id, self.card_ids, question["exam"])
            # 近い論点のカードは、正面から扱うカードの無い肢にだけ付ける
            self.assertFalse(set(question["links"]) & set(question["near"]), question["exam"])

    def test_aim_is_short_and_route_is_written(self):
        for question in self.questions():
            self.assertTrue(question["aim"], question["exam"])
            self.assertLessEqual(len(question["aim"]), 80, question["exam"])
            self.assertGreaterEqual(len(question["route"]), 40, question["exam"])
            self.assertLessEqual(len(question["route"]), 420, question["exam"])

    def test_notes_cover_every_choice_and_match_the_direction(self):
        for question in self.questions():
            notes = question["notes"]
            combo = "組合せ" in question["format"]
            if combo:
                self.assertTrue(set(notes) <= set("アイウエオ"), question["exam"])
                self.assertGreaterEqual(len(notes), 4, question["exam"])
            else:
                self.assertEqual(sorted(notes), ["1", "2", "3", "4", "5"], question["exam"])
            marks = [text[:1] for text in notes.values()]
            self.assertTrue(set(marks) <= {"○", "×"}, question["exam"])
            for text in notes.values():
                self.assertLessEqual(len(text), 120, question["exam"])
            asked = "×" if question["asksFalse"] else "○"
            # 1つ選ぶ問題は問われている側の肢がちょうど1つ、2肢の組合せは2つ
            self.assertEqual(marks.count(asked), 2 if combo else 1, question["exam"])

    def test_route_names_exist(self):
        rule_names = set(re.findall(r"概念\s*::\s*(.+)", RULES.read_text(encoding="utf-8")))
        kinds = {kind["label"] for kind in json.loads(HINTS.read_text(encoding="utf-8"))["kinds"].values()}
        for question in self.questions():
            for name in quoted_names(question["route"], "常識"):
                self.assertIn(name.strip(), rule_names, question["exam"])
            for name in quoted_names(question["route"], "誤りの作り方"):
                self.assertIn(name, kinds, question["exam"])

    def test_no_question_text_keys(self):
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, QUESTION_TEXT_KEYS)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(self.data)

    def test_direction_tag_matches_format(self):
        for question in self.questions():
            self.assertIsInstance(question["asksFalse"], bool)
            self.assertIsInstance(question["amended"], bool)
            self.assertEqual(question["asksFalse"], "誤っている" in question["format"], question["exam"])

    def test_skip_areas_are_left_out(self):
        for question in self.questions():
            for word in SKIP_FIELDS:
                self.assertNotIn(word, question["field"], question["exam"])


@unittest.skipUnless(BUNDLE.exists(), "稼働中bundleが無い環境では照合しない")
class ExamPracticeAgainstBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_sets()
        cls.raw = (PAGE / "sets.json").read_text(encoding="utf-8")
        bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
        cls.bundle_questions = {question["id"]: question for question in bundle["questions"]}

    def test_marks_match_the_answer(self):
        for item in all_questions(self.data):
            question = self.bundle_questions[item["questionId"]]
            answer = str(question["answer"]["value"])
            choices = {str(choice["label"]): choice["text"].strip() for choice in question["content"]["choices"]}
            asked = "×" if item["asksFalse"] else "○"
            other = "○" if asked == "×" else "×"
            if "組合せ" in item["format"]:
                picked = set(choices[answer].split("・"))
                expected = {label: asked if label in picked else other for label in item["notes"]}
            else:
                expected = {label: asked if label == answer else other for label in choices}
            actual = {label: text[:1] for label, text in item["notes"].items()}
            self.assertEqual(actual, expected, item["exam"])

    def test_question_text_is_not_copied(self):
        for item in all_questions(self.data):
            question = self.bundle_questions[item["questionId"]]
            content = question["content"]
            pieces = [choice["text"] for choice in content["choices"]]
            pieces += re.split(r"\s[ア-オ]．|\n", content.get("question") or "")
            for piece in pieces:
                piece = piece.strip()
                if len(piece) >= 25:
                    self.assertNotIn(piece, self.raw, item["exam"])


class PracticePageTest(unittest.TestCase):
    def setUp(self):
        self.html = (PAGE / "index.html").read_text(encoding="utf-8")
        self.script = (PAGE / "app.js").read_text(encoding="utf-8")

    def test_page_loads_its_script_with_a_version(self):
        self.assertRegex(self.html, r'<script src="app\.js\?v=\d{8}-\d+"></script>')
        self.assertRegex(self.html, r'href="styles\.css\?v=\d{8}-\d+"')
        self.assertIn("../assets/text-marks.js", self.html)

    def test_reads_questions_from_the_api_and_sets_from_json(self):
        self.assertIn('fetchJson("sets.json")', self.script)
        self.assertIn('"../api/questions?questionId=" + encodeURIComponent(item.questionId)', self.script)
        self.assertIn('fetchJson("../api/cards")', self.script)

    def test_never_writes_to_the_server(self):
        for word in ("POST", "api/attempts", "api/card-attempts", "api/card-marks", "innerHTML"):
            self.assertNotIn(word, self.script)
        self.assertIn('const STORE_KEY = "examPractice:v1";', self.script)

    def test_answer_is_shown_first_by_default(self):
        self.assertIn('<input type="checkbox" id="show-answer" checked>', self.html)
        self.assertIn('return window.localStorage.getItem(SHOW_KEY) !== "0";', self.script)

    def test_builds_the_quiz_and_card_links(self):
        self.assertIn('"../?questionIds=" + set.questions.map((item) => encodeURIComponent(item.questionId)).join(",") + "&from=practice#quiz"', self.script)
        self.assertIn('"&from=practice#study"', self.script)


class PickedQuestionsEntryTest(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_only_source_question_ids_pass(self):
        start = self.source.index("  function requestedPickedQuestionIds(")
        end = self.source.index("  function openRequestedPickedQuestions(", start)
        block = self.source[start:end]
        self.assertIn("/^goukakudojyo:\\d{1,8}$/.test(id)", block)
        self.assertIn("slice(0, 100)", block)

    def test_loaded_questions_only_and_order_kept(self):
        setup = self.source[self.source.index("  function setupQuiz("):self.source.index("  function refreshQuizTopicOptions(")]
        self.assertIn("state.pickedQuestionIds.filter((id) => loaded.has(id))", setup)
        pool = self.source[self.source.index("  function refreshQuizPool("):self.source.index("  function renderQuiz(")]
        self.assertIn('scope !== "review" || !isMastered(q.id)', pool)
        self.assertIn("if (!picked) state.quizPool.sort(", pool)

    def test_scope_option_and_startup_hook(self):
        self.assertIn('<option value="picked" hidden disabled>本試験形式の練習セット</option>', self.html)
        self.assertIn("|| openRequestedPickedQuestions(location.search)", self.source)

    def test_practice_label_for_picked_cards(self):
        self.assertIn('from === "practice" ? "本試験形式の練習のカード"', self.source)

    def test_page_is_linked_from_lab(self):
        architecture = (ROOT / "static" / "architecture.html").read_text(encoding="utf-8")
        self.assertIn('href="exam-practice/"', self.html)
        self.assertIn('href="exam-practice/"', architecture)


if __name__ == "__main__":
    unittest.main()
