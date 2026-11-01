from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
NODE = shutil.which("node") or "/opt/homebrew/bin/node"
PAGES = [
    *(f"{name}-d" for name in ("minpo", "gyosei", "kenpo", "shoho", "kiso", "chishiki")),
    *(f"{name}-map" for name in ("minpo", "gyosei", "kenpo", "shoho", "kiso", "chishiki")),
    "memorize-map",
    "simple-rules",
    "technique",
]

CHECK = r"""
const T = require(process.argv[1]);
const samples = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const join = (parts) => parts.map((p) => p.text).join('');
const out = {changed: [], leftover: []};
for (const s of samples) {
  const stripped = s.replace(/(\*\*|__|==|!!|%%|@@)(.+?)\1/g, '$2');
  const parts = T.segments(s);
  if (join(parts) !== stripped) out.changed.push(s.slice(0, 40));
  if (parts.some((p) => !p.kind && /==|!!|%%|@@/.test(p.text))) out.leftover.push(s.slice(0, 40));
}
const kinds = (s, o) => T.segments(s, o).filter((p) => p.kind).map((p) => p.kind);
out.plain = kinds('行政庁は30日以内に取り消せません（5条）。', {plain: true});
out.noWarn = kinds('ひっかけに注意します。', {warn: false});
out.scenario = kinds('のび太は追認していません。', {mark: false});
out.quiet = T.segments('処分は無効です（行政事件訴訟法36条）。').map((p) => [p.kind || '', p.text]);
out.era = kinds('令和3年に改正しました。', {});
console.log(JSON.stringify(out));
"""


class TextMarksTest(unittest.TestCase):
    def test_every_page_loads_the_shared_script_and_style(self) -> None:
        for page in PAGES:
            html = (STATIC / page / "index.html").read_text(encoding="utf-8")
            self.assertIn('../assets/text-marks.js?v=', html, page)
            self.assertIn('../assets/text-marks.css?v=', html, page)
            # app.js より先に読まないと TextMarks が無い。
            self.assertLess(html.index("text-marks.js"), html.index('app.js?v='), page)

    def test_recall_mode_keeps_answers_hidden(self) -> None:
        css = (STATIC / "assets" / "text-marks.css").read_text(encoding="utf-8")
        self.assertIn("body.recall .ans:not(.shown) *", css)

    def test_rules_never_change_the_wording(self) -> None:
        samples: list[str] = []
        for line in (STATIC / "simple-rules" / "content.md").read_text(encoding="utf-8").splitlines():
            if " :: " in line:
                samples.append(line.split(" :: ", 1)[1])
        for page in ("gyosei-d", "minpo-d", "kenpo-d", "shoho-d", "kiso-d", "chishiki-d"):
            data = json.loads((STATIC / page / "chapters.json").read_text(encoding="utf-8"))
            for chapter in data["chapters"]:
                samples.append(chapter["goal"])
                for question in chapter["questions"]:
                    samples += [question["statement"], question["currentScenario"], question["explanation"]["summary"]]
        result = subprocess.run(
            [NODE, "-e", CHECK, str(STATIC / "assets" / "text-marks.js")],
            input=json.dumps(samples), capture_output=True, text=True, check=True,
        )
        out = json.loads(result.stdout)
        self.assertEqual([], out["changed"])
        self.assertEqual([], out["leftover"])
        # 本番変換は色なし。問題文に赤を使わない。事例文に黄を使わない。
        self.assertEqual([], out["plain"])
        self.assertEqual([], out["noWarn"])
        self.assertNotIn("mark", out["scenario"])
        # 文末の根拠を飛ばして結論を塗り、根拠は灰色にする。
        self.assertIn(["mark", "無効です"], out["quiet"])
        self.assertIn(["quiet", "（行政事件訴訟法36条）"], out["quiet"])
        # 元号の年を期間と取り違えない。
        self.assertNotIn("underline", out["era"])


if __name__ == "__main__":
    unittest.main()
