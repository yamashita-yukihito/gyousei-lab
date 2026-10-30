from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
MAP_DIR = STATIC / "gyosei-map"
# 科目ごとのマップ。画面（app.js・map.css）は gyosei-map のものを共用し、各科目は index.html と content.md だけを持つ。
MAPS = {
    "gyosei-map": "administrative-law",
    "minpo-map": "civil-law",
    "kenpo-map": "constitutional-law",
    "shoho-map": "commercial-law",
    "kiso-map": "legal-foundations",
    "chishiki-map": "general-knowledge",
}

PRIORITY = re.compile(r"\[(重点|抑えめ|捨て候補)\]")

# static/gyosei-map/app.js の parse() と同じ行の型。
LINE_PATTERNS = (
    re.compile(r"^#\s+.+$"),
    re.compile(r"^>\s?.*$"),
    re.compile(r"^( {2})*-\s+.+$"),
)


def content_lines(directory: str) -> list[tuple[int, str]]:
    text = (STATIC / directory / "content.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--[\s\S]*?-->", lambda m: re.sub(r"[^\n]", "", m.group(0)), text)
    return [(no, line) for no, line in enumerate(text.splitlines(), 1) if line.strip()]


def body(directory: str) -> str:
    return "\n".join(line for _, line in content_lines(directory))


class LawMapTest(unittest.TestCase):
    def test_files_exist(self) -> None:
        for name in ("index.html", "app.js", "map.css", "content.md"):
            self.assertTrue((MAP_DIR / name).is_file(), name)
        for directory in MAPS:
            for name in ("index.html", "content.md"):
                self.assertTrue((STATIC / directory / name).is_file(), f"{directory}/{name}")

    def test_pages_share_one_script(self) -> None:
        # 画面のコードを科目ごとに複製しない。版番号もそろえる。
        script = (MAP_DIR / "app.js").read_text(encoding="utf-8")
        versions = set()
        keys = set()
        for directory in MAPS:
            html = (STATIC / directory / "index.html").read_text(encoding="utf-8")
            prefix = "" if directory == "gyosei-map" else "../gyosei-map/"
            found = re.search(r'<script src="' + re.escape(prefix) + r'app\.js\?v=([\w-]+)"', html)
            self.assertIsNotNone(found, directory)
            versions.add(found.group(1))
            self.assertIn(f'href="{prefix}map.css?v=', html, directory)
            key = re.search(r'data-store-key="([^"]+)"', html)
            self.assertIsNotNone(key, directory)
            keys.add(key.group(1))
            self.assertIn(f'dir: "{directory}"', script, f"app.js の MAPS に {directory} が無い")
        self.assertEqual(1, len(versions), "app.js の版番号が科目ごとにずれている")
        self.assertEqual(len(MAPS), len(keys), "localStorage の保存先が科目どうしで重なっている")

    def test_page_only_reads(self) -> None:
        # 頻出度と習得状況は読むだけ。回答・印を書き込むリクエストをこのページから出さない。
        script = (MAP_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("../api/card-progress", script)
        self.assertNotRegex(script, r"method\s*:")

    def test_every_line_is_readable(self) -> None:
        for directory in MAPS:
            lines = content_lines(directory)
            self.assertTrue(lines[0][1].startswith("# "), f"{directory}: 最初の行は枝の見出し")
            bad = [no for no, line in lines if not any(p.match(line) for p in LINE_PATTERNS)]
            self.assertEqual([], bad, f"{directory}/content.md の書式として読めない行")
            # 字下げは1段ずつしか深くならない（飛ばすと親が決まらない）
            depth = -1
            for no, line in lines:
                if line.startswith("#"):
                    depth = -1
                    continue
                m = re.match(r"^( *)-", line)
                if not m:
                    continue
                current = len(m.group(1)) // 2
                self.assertLessEqual(current, depth + 1, f"{directory}: {no}行目の字下げが深すぎる")
                depth = current

    def test_every_card_is_on_its_subject_map(self) -> None:
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual(set(MAPS.values()), {card["subjectId"] for card in cards}, "マップの無い科目がある")
        for directory, subject in MAPS.items():
            want = {card["id"] for card in cards if card["subjectId"] == subject}
            refs = set(re.findall(r"\{([^{}]+)\}", body(directory)))
            self.assertEqual([], sorted(refs - want), f"{directory}: 実在しないか、別の科目のカードID")
            self.assertEqual([], sorted(want - refs), f"{directory}: どの枝にも結ばれていないカード")

    def test_drill_chapters_exist(self) -> None:
        for directory in MAPS:
            refs = re.findall(r"@([a-z0-9-]+)/([a-z0-9-]+)", body(directory))
            self.assertTrue(refs, directory)
            missing = []
            for drill, chapter in set(refs):
                path = STATIC / drill / "chapters.json"
                ids = {c["id"] for c in json.loads(path.read_text(encoding="utf-8"))["chapters"]} if path.is_file() else set()
                if chapter not in ids:
                    missing.append(f"{drill}/{chapter}")
            self.assertEqual([], sorted(missing), f"{directory}: 実在しない演習D案の章")

    def test_page_links_stay_inside_lab(self) -> None:
        for directory in MAPS:
            for label, url in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", body(directory)):
                self.assertNotRegex(url, r"^[a-z]+:", label)
                target = (STATIC / directory / url.split("#")[0]).resolve()
                self.assertTrue(target.exists(), f"{directory}: {label}: {url}")

    def test_every_branch_has_priority(self) -> None:
        # 優先度（重点・抑えめ・捨て候補）は枝に必ず書き、項目は書かなければ親から受け継ぐ。
        for directory in MAPS:
            heads = [line for _, line in content_lines(directory) if line.startswith("# ")]
            missing = [line for line in heads if not PRIORITY.search(line)]
            self.assertEqual([], missing, f"{directory}: 優先度の無い枝")

    def test_frequent_cards_are_focus(self) -> None:
        # 過去問で6問以上出た（最頻出・頻出）カードを、抑えめや捨て候補の項目にだけ置かない。
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        frequent = {card["id"] for card in cards if card["frequency"]["label"] in ("最頻出", "頻出")}
        rank = {"重点": 3, "抑えめ": 2, "捨て候補": 1}
        for directory in MAPS:
            best: dict[str, str] = {}
            branch = ""
            stack: list[str] = []
            for _, line in content_lines(directory):
                if line.startswith("# "):
                    branch = PRIORITY.search(line).group(1)
                    stack = []
                    continue
                m = re.match(r"^( *)-\s+(.+)$", line)
                if not m:
                    continue
                depth = len(m.group(1)) // 2
                stack = stack[:depth]
                own = PRIORITY.search(m.group(2))
                prio = own.group(1) if own else (stack[-1] if stack else branch)
                stack.append(prio)
                for card in re.findall(r"\{([^{}]+)\}", m.group(2)):
                    if card not in best or rank[prio] > rank[best[card]]:
                        best[card] = prio
            demoted = sorted(card for card, prio in best.items() if card in frequent and prio != "重点")
            self.assertEqual([], demoted, f"{directory}: 頻出なのに重点でないカード")

    def test_drill_skip_marks_match_the_map(self) -> None:
        # 演習D案の「今年は飛ばしてよい」（skipNote）は、マップの優先度とずれないようにする。
        # 重点のカードの問を飛ばさせない。捨て候補のカードの問は必ず飛ばせる。
        drills = {
            "minpo-d": "minpo-map", "gyosei-d": "gyosei-map", "kenpo-d": "kenpo-map",
            "shoho-d": "shoho-map", "kiso-d": "kiso-map", "chishiki-d": "chishiki-map",
        }
        rank = {"重点": 3, "抑えめ": 2, "捨て候補": 1}
        for drill, directory in drills.items():
            best: dict[str, str] = {}
            stack: list[str] = []
            branch = ""
            for _, line in content_lines(directory):
                if line.startswith("# "):
                    branch = PRIORITY.search(line).group(1)
                    stack = []
                    continue
                m = re.match(r"^( *)-\s+(.+)$", line)
                if not m:
                    continue
                depth = len(m.group(1)) // 2
                stack = stack[:depth]
                own = PRIORITY.search(m.group(2))
                prio = own.group(1) if own else (stack[-1] if stack else branch)
                stack.append(prio)
                for card in re.findall(r"\{([^{}]+)\}", m.group(2)):
                    if card not in best or rank[prio] > rank[best[card]]:
                        best[card] = prio
            chapters = json.loads((STATIC / drill / "chapters.json").read_text(encoding="utf-8"))["chapters"]
            for chapter in chapters:
                # 民法の第1〜4章は勉強済みなので、印を付けず全部解く。
                settled = drill == "minpo-d" and float(chapter["number"]) < 5
                for q in chapter["questions"]:
                    prio = best.get(q.get("cardId"))
                    if q.get("skipNote"):
                        self.assertFalse(settled, q["id"])
                        self.assertNotEqual("重点", prio, q["id"])
                        self.assertNotEqual("chishiki-d", drill, "基礎知識は足切りがあるので飛ばす問を作らない")
                    elif prio == "捨て候補" and not settled:
                        self.fail(f"{q['id']}: カードは捨て候補なのに飛ばせない")

    def test_every_page_has_favicon(self) -> None:
        # 科目のページは科目の字（民・行・憲・商・基・知）、ほかはラボの字のアイコンを出す。
        want = {"minpo": "min", "gyosei": "gyo", "kenpo": "ken", "shoho": "sho", "kiso": "kiso", "chishiki": "chi"}
        for subject, icon in want.items():
            for kind in ("d", "map"):
                html = (STATIC / f"{subject}-{kind}" / "index.html").read_text(encoding="utf-8")
                self.assertIn(f'href="../assets/favicons/{icon}.svg"', html, subject)
        for page in STATIC.rglob("*.html"):
            if "docs" in page.parts:
                continue
            found = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)"', page.read_text(encoding="utf-8"))
            self.assertIsNotNone(found, str(page))
            self.assertTrue((page.parent / found.group(1)).is_file(), str(page))

    def test_study_screen_reads_every_map(self) -> None:
        # 学習画面（static/app.js）も同じ content.md から優先度を読み、解いているカードに印を出す。
        # マップを足したのに学習画面の一覧へ足し忘れると、その科目のカードだけ印が出なくなる。
        main = (STATIC / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const PRIORITY_MAPS = \[([\s\S]*?)\];", main)
        self.assertIsNotNone(block)
        self.assertEqual(set(MAPS), set(re.findall(r'dir: "([a-z-]+)"', block.group(1))))

    def test_current_appeal_law(self) -> None:
        # 行政法マップの元の画像は2014年改正前の行政不服審査法だった。旧制度の言葉を戻さない。
        # 枝の説明（> の行）で「なくなった」と書くのはよいので、項目だけを見る。
        text = "\n".join(line for _, line in content_lines("gyosei-map") if line.lstrip().startswith("-"))
        self.assertNotIn("異議申立て", text)
        self.assertIn("再調査の請求", text)
        self.assertIn("審理員", text)

    def test_current_civil_law(self) -> None:
        # 2020年改正後の連帯債務（請求・免除・時効は相対効）と、2025年からの拘禁刑で書く。
        civil = body("minpo-map")
        self.assertRegex(civil, r"請求・免除・時効の完成は効かない")
        for directory in MAPS:
            self.assertNotRegex(body(directory), r"懲役|禁錮", directory)


if __name__ == "__main__":
    unittest.main()
