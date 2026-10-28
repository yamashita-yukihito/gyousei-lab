from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_DIR = ROOT / "static" / "gyosei-map"

# static/gyosei-map/app.js の parse() と同じ行の型。
LINE_PATTERNS = (
    re.compile(r"^#\s+.+$"),
    re.compile(r"^>\s?.*$"),
    re.compile(r"^( {2})*-\s+.+$"),
)


def content_lines() -> list[tuple[int, str]]:
    text = (MAP_DIR / "content.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--[\s\S]*?-->", lambda m: re.sub(r"[^\n]", "", m.group(0)), text)
    return [(no, line) for no, line in enumerate(text.splitlines(), 1) if line.strip()]


def body() -> str:
    return "\n".join(line for _, line in content_lines())


class GyoseiMapTest(unittest.TestCase):
    def test_files_exist(self) -> None:
        for name in ("index.html", "app.js", "content.md"):
            self.assertTrue((MAP_DIR / name).is_file(), name)

    def test_page_only_reads(self) -> None:
        # 頻出度と習得状況は読むだけ。回答・印を書き込むリクエストをこのページから出さない。
        script = (MAP_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("../api/card-progress", script)
        self.assertNotRegex(script, r"method\s*:")

    def test_every_line_is_readable(self) -> None:
        lines = content_lines()
        self.assertTrue(lines[0][1].startswith("# "), "最初の行は枝の見出し")
        bad = [no for no, line in lines if not any(p.match(line) for p in LINE_PATTERNS)]
        self.assertEqual([], bad, "content.md の書式として読めない行")
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
            self.assertLessEqual(current, depth + 1, f"{no}行目の字下げが深すぎる")
            depth = current

    def test_every_admin_card_is_on_the_map(self) -> None:
        cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        known = {card["id"] for card in cards}
        admin = {card["id"] for card in cards if card.get("subjectId") == "administrative-law"}
        refs = set(re.findall(r"\{([^{}]+)\}", body()))
        self.assertEqual([], sorted(refs - known), "content.md に実在しないカードID")
        self.assertEqual([], sorted(admin - refs), "行政法マップのどの枝にも結ばれていない行政法カード")

    def test_drill_chapters_exist(self) -> None:
        refs = re.findall(r"@([a-z0-9-]+)/([a-z0-9-]+)", body())
        self.assertTrue(refs)
        missing = []
        for drill, chapter in set(refs):
            path = ROOT / "static" / drill / "chapters.json"
            ids = {c["id"] for c in json.loads(path.read_text(encoding="utf-8"))["chapters"]} if path.is_file() else set()
            if chapter not in ids:
                missing.append(f"{drill}/{chapter}")
        self.assertEqual([], sorted(missing), "content.md に実在しない演習D案の章")

    def test_page_links_stay_inside_lab(self) -> None:
        for label, url in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", body()):
            self.assertNotRegex(url, r"^[a-z]+:", label)
            target = (MAP_DIR / url.split("#")[0]).resolve()
            self.assertTrue(target.exists(), f"{label}: {url}")

    def test_current_appeal_law(self) -> None:
        # 元の画像は2014年改正前の行政不服審査法だった。旧制度の言葉を戻さない。
        # 枝の説明（> の行）で「なくなった」と書くのはよいので、項目だけを見る。
        text = "\n".join(line for _, line in content_lines() if line.lstrip().startswith("-"))
        self.assertNotIn("異議申立て", text)
        self.assertIn("再調査の請求", text)
        self.assertIn("審理員", text)


if __name__ == "__main__":
    unittest.main()
