"""カードの出題面に出す「この問題で使える常識・テクニック」の正本を確かめる（AGENTS.md §5.9）。"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
SPEC = importlib.util.spec_from_file_location("simple_rules_measure", ROOT / "scripts" / "simple_rules" / "measure.py")
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


class CardHintsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        items = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        cls.cards = {card["id"]: card for card in items}
        cls.breaks = json.loads((STATIC / "technique" / "card-breaks.json").read_text(encoding="utf-8"))
        cls.signals = json.loads((STATIC / "technique" / "word-signals.json").read_text(encoding="utf-8"))
        cls.app = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_every_false_card_has_one_break_kind(self):
        listed = [card_id for ids in self.breaks["cards"].values() for card_id in ids]
        self.assertEqual(len(listed), len(set(listed)), "同じカードを2つの壊し方に入れない")
        expected = {card_id for card_id, card in self.cards.items() if not card["correct"]}
        self.assertEqual(set(listed), expected, "×のカードを足したら card-breaks.json にも入れる")

    def test_break_kinds_match_the_technique_page(self):
        page = (STATIC / "technique" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(set(self.breaks["kinds"]), set(self.breaks["cards"]))
        for kind in self.breaks["kinds"].values():
            self.assertIn(kind["label"], page)
            self.assertIn(kind["look"], page)

    def test_word_patterns_are_the_measured_ones(self):
        rules = {rule[0]: rule for rule in measure.RULES}
        for rule in self.signals["rules"]:
            self.assertEqual(rule["pattern"], rules[rule["id"]][2])
            self.assertEqual(rule["exclude"], rules[rule["id"]][4])

    def test_word_patterns_compile_in_javascript(self):
        patterns = [value for rule in self.signals["rules"] for value in (rule["pattern"], rule["exclude"]) if value]
        script = "for (const p of JSON.parse(process.argv[1])) new RegExp(p);"
        subprocess.run(["/opt/homebrew/bin/node", "-e", script, json.dumps(patterns)], check=True)

    def test_panel_is_above_the_answer_buttons_and_hidden_in_rapid(self):
        self.assertLess(self.html.index('id="study-hints"'), self.html.index('class="study-answer-buttons"'))
        body = self.app[self.app.index("function renderStudyHints"):]
        self.assertIn("isRapidStudyView()", body[:400])
        self.assertIn("renderStudyHints(item);", self.app)

    def test_hints_do_not_write_anything(self):
        start = self.app.index("function loadCardHintIndex")
        end = self.app.index("function renderStudyPriority")
        self.assertNotIn("POST", self.app[start:end])
        self.assertNotIn("localStorage", self.app[start:end])

    def test_rule_text_is_not_cut_to_the_first_sentence(self):
        # 1文目だけを出すと「目的によって逆になります」で切れ、カードの答えを逆に読ませた。
        self.assertNotIn("firstSentence", self.app)
        self.assertIn('id="study-hints-ng-note"', self.html)

    def test_only_stable_words_are_shown(self):
        self.assertTrue(re.search(r"rule\.stable && rule\.pattern", self.app))


if __name__ == "__main__":
    unittest.main()
