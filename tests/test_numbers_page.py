"""数字の覚え方（static/numbers/）の形を見る。

図はページと同じフォルダのSVGだけを使い、年度・問番号は法律家の常識と同じ
exam-links.json で合格道場のリンクになる。リンクにならない書き方（存在しない問番号）を残さない。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "static" / "numbers" / "index.html"
LINKS = ROOT / "static" / "simple-rules" / "exam-links.json"
EXAM_REF = re.compile(r"(平成|令和)(元|\d+)年問(\d+)")


class NumbersPageTest(unittest.TestCase):
    def setUp(self):
        self.html = PAGE.read_text(encoding="utf-8")

    def test_figures_exist_next_to_page(self):
        sources = re.findall(r'<img src="([^"]+)"', self.html)
        self.assertGreaterEqual(len(sources), 5)
        for src in sources:
            self.assertNotIn("/", src, f"図はページと同じフォルダに置く: {src}")
            self.assertTrue((PAGE.parent / src).is_file(), src)

    def test_figures_have_alt(self):
        for tag in re.findall(r"<img [^>]+>", self.html):
            self.assertRegex(tag, r'alt="[^"]{10,}"', tag)

    def test_exam_refs_resolve_to_links(self):
        links = json.loads(LINKS.read_text(encoding="utf-8"))
        body = re.sub(r"<script.*?</script>", "", self.html, flags=re.S)
        refs = {f"{m[0]}{m[1]}年問{m[2]}" for m in EXAM_REF.findall(body)}
        self.assertGreater(len(refs), 20)
        missing = sorted(ref for ref in refs if ref not in links)
        self.assertEqual(missing, [], "exam-links.json に無い問番号")

    def test_page_is_linked_from_lab(self):
        index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        architecture = (ROOT / "static" / "architecture.html").read_text(encoding="utf-8")
        self.assertIn('href="numbers/"', index)
        self.assertIn('href="numbers/"', architecture)


if __name__ == "__main__":
    unittest.main()
