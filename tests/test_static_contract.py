from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticUiContractTest(unittest.TestCase):
    def test_initial_load_does_not_require_question_or_audit_sections(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = source.index("  async function init()")
        end = source.index("  function ingestInitialData", start)
        initial_loader = source[start:end]

        self.assertIn('fetchJson(API + "/overview")', initial_loader)
        self.assertIn("fetchAllCardPages()", initial_loader)
        self.assertNotIn('API + "/questions', initial_loader)
        self.assertNotIn('API + "/claude-reviews', initial_loader)
        self.assertNotIn('API + "/similarities', initial_loader)

    def test_asset_versions_and_dynamic_tab_counts_stay_in_sync(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'const APP_VERSION = "([^"]+)"', source)
        self.assertIsNotNone(match)
        version = match.group(1)

        self.assertIn(f"app.js?v={version}", html)
        self.assertIn(f"styles.css?v={version}", html)
        for element_id in (
            "quiz-tab-count",
            "written-tab-count",
            "claude-tab-count",
            "similarity-tab-count",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn("選択式210問", html)


if __name__ == "__main__":
    unittest.main()
