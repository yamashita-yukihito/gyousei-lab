from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_DIR = ROOT / "static" / "memorize-map"
FREQUENT = {"最頻出", "頻出"}

# static/memorize-map/app.js の parse() と同じ行の型。
ITEM = re.compile(r"^-\s+\[(暗|考)(★?)\]\s*(.+)$")
LINE_PATTERNS = (
    re.compile(r"^##\s+.+$"),
    re.compile(r"^#\s+.+$"),
    re.compile(r"^>\s?.*$"),
    ITEM,
    re.compile(r"^\s{2,}-\s+.+$"),
)


def content_lines() -> list[tuple[int, str]]:
    text = (MAP_DIR / "content.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--[\s\S]*?-->", lambda m: re.sub(r"[^\n]", "", m.group(0)), text)
    return [(no, line) for no, line in enumerate(text.splitlines(), 1) if line.strip()]


def card_labels() -> dict[str, str]:
    cards = json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))
    return {item["id"]: item["frequency"]["label"] for item in cards["items"]}


class MemorizeMapTest(unittest.TestCase):
    def test_files_exist(self) -> None:
        for name in ("index.html", "app.js", "content.md"):
            self.assertTrue((MAP_DIR / name).is_file(), name)

    def test_page_only_reads(self) -> None:
        # 習得状況は読むだけ。回答・印を書き込むリクエストをこのページから出さない。
        script = (MAP_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("../api/card-progress", script)
        self.assertNotRegex(script, r"method\s*:")

    def test_every_line_is_readable(self) -> None:
        lines = content_lines()
        self.assertTrue(lines[0][1].startswith("# "), "最初の行は科目見出し")
        bad = [no for no, line in lines if not any(p.match(line) for p in LINE_PATTERNS)]
        self.assertEqual([], bad, "content.md の書式として読めない行")
        kinds = {ITEM.match(line).group(1) for _, line in lines if ITEM.match(line)}
        self.assertEqual({"暗", "考"}, kinds)

    def test_card_ids_exist(self) -> None:
        known = card_labels()
        refs = re.findall(r"\{([^{}]+)\}", "\n".join(line for _, line in content_lines()))
        self.assertTrue(refs)
        self.assertEqual([], sorted(set(refs) - known.keys()), "content.md に実在しないカードID")

    def test_star_matches_card_frequency(self) -> None:
        # ★は「頻出」「最頻出」のカードに結んだ項目に必ず付け、それ以外のカードだけに結んだ項目には付けない。
        # カードに結んでいない項目の★は、過去問の出題数を確かめたうえで手で付けてよい。
        labels = card_labels()
        missing, extra = [], []
        for no, line in content_lines():
            m = ITEM.match(line)
            if not m:
                continue
            ids = re.findall(r"\{([a-z0-9-]+)\}", line)
            frequent = any(labels.get(i) in FREQUENT for i in ids)
            if frequent and not m.group(2):
                missing.append(no)
            if m.group(2) and ids and not frequent:
                extra.append(no)
        self.assertEqual([], missing, "頻出カードに結んだのに★が無い行")
        self.assertEqual([], extra, "頻出でないカードだけに結んだのに★がある行")

    def test_every_frequent_card_is_listed(self) -> None:
        labels = card_labels()
        refs = set(re.findall(r"\{([a-z0-9-]+)\}", "\n".join(line for _, line in content_lines())))
        frequent = {cid for cid, label in labels.items() if label in FREQUENT}
        self.assertEqual([], sorted(frequent - refs), "このページに載っていない頻出カード")


if __name__ == "__main__":
    unittest.main()
