import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "card_video" / "build_card_video.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_card_video", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass が自分のモジュールを引くため
    spec.loader.exec_module(module)
    return module


class CardVideoEpisodesTest(unittest.TestCase):
    """カード動画の回の定義が、実在するカードだけを指していること。"""

    def test_episode_card_ids_exist(self):
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        known = {card["id"] for card in cards}
        builder = load_builder()
        episodes = builder.load_episodes()
        self.assertTrue(episodes)
        seen = set()
        for episode in episodes:
            self.assertNotIn(episode["id"], seen)
            seen.add(episode["id"])
            self.assertRegex(episode["id"], r"^[a-z0-9-]+$")
            self.assertTrue(episode["cardIds"])
            self.assertEqual(len(episode["cardIds"]), len(set(episode["cardIds"])), episode["id"])
            missing = [cid for cid in episode["cardIds"] if cid not in known]
            self.assertEqual(missing, [], episode["id"])
            self.assertTrue(episode.get("group"), episode["id"])

    def test_series_covers_every_card_once(self):
        """「特集」以外の回で、全カードをちょうど1回ずつ流す（2026-10-27に全カードを動画にした）。
        カードを足したら、同じ作業でその分野の回の末尾へ足す。"""
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        series = [ep for ep in load_builder().load_episodes() if ep["group"] != "特集"]
        listed = [cid for ep in series for cid in ep["cardIds"]]
        self.assertEqual(sorted(listed), sorted(card["id"] for card in cards))
        for episode in series:
            # 1本はご飯1回ぶん（16枚で約20分）に収める
            self.assertLessEqual(len(episode["cardIds"]), 16, episode["id"])


class CardVideoNarrationTest(unittest.TestCase):
    def setUp(self):
        self.builder = load_builder()

    def test_spoken_strips_markup_and_symbols(self):
        text = self.builder.spoken("**即時強制**は==義務違反を待たない==。○と×、A→B")
        self.assertEqual(text, "即時強制は義務違反を待たない。まるとばつ、A、B")

    def test_spoken_reads_case_dates(self):
        self.assertEqual(self.builder.spoken("（最判昭30.4.19）"), "（最高裁、昭和30年4月19日判決）")
        self.assertEqual(self.builder.spoken("理由がない（最判昭30.4.19）", drop_parens=True), "理由がない")

    def test_reason_skips_bare_verdict(self):
        self.assertEqual(self.builder.reason_sentence("誤り。__点数__が残る間は消滅しません。次の文。"),
                         "点数が残る間は消滅しません。")
        self.assertEqual(self.builder.reason_sentence("そのとおりです。却下と棄却があります。"),
                         "却下と棄却があります。")

    def test_answer_is_shown_with_question(self):
        """動画は最初から答えを見せて読む（2026-10-26に利用者が指定）。Bは読み上げ、Cは画面だけ。"""
        card = {"id": "x", "subjectId": "administrative-law", "topic": "行政手続法", "subtopic": "標準処理期間",
                "variants": {"a": "標準処理期間を定めることは義務である。",
                             "b": "役所は、かかる日数の目安を必ず決めなければなりません。",
                             "c": "目安の日数 → 必ず決める"},
                "correct": False, "correction": "行政手続法6条は努力義務としています。", "memoryPoint": "決めるまでは努力。"}
        segs = self.builder.build_segments({"id": "t", "title": "テスト"}, [card], {})
        first = segs[1]
        self.assertEqual(first.chapter["cardId"], "x")
        self.assertIn('class="badge ng"', first.html)
        self.assertIn("答えは、ばつ。", first.narration)
        self.assertIn("やさしく言うと。役所は、かかる日数の目安を必ず決めなければなりません。", first.narration)
        self.assertIn("目安の日数", first.html)
        self.assertNotIn("目安の日数", first.narration)
        # B・CはAと同じ結論の文なので、Aが×ならB・Cも×だと添える
        self.assertIn("これも×", first.html)
        self.assertIn("理由。行政手続法6条は努力義務としています。", segs[2].narration)
        # VOICEVOXの声は、作者の決まりどおり画面にクレジットを出す
        if self.builder.VOICE["engine"] == "voicevox":
            for seg in segs:
                self.assertIn(self.builder.VOICE["credit"], seg.html)


class CardVideoPageTest(unittest.TestCase):
    def test_page_reads_media_path_and_records_nothing(self):
        script = (ROOT / "static" / "card-video" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"../media/card-video/"', script)
        # 再生ページは回答を記録しない（production.sqlite3 へ書かない）
        self.assertNotIn("method:", script)
        self.assertNotIn("/api/", script)

    def test_voice_page_reads_media_path_and_records_nothing(self):
        script = (ROOT / "static" / "card-video" / "voices.js").read_text(encoding="utf-8")
        self.assertIn('"../media/card-video/voice-samples/"', script)
        self.assertNotIn("method:", script)
        self.assertNotIn("/api/", script)


if __name__ == "__main__":
    unittest.main()
