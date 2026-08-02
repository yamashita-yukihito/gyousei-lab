from __future__ import annotations

import json
import re
import subprocess
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
        self.assertIn('fetchJson(API + "/learning-analysis")', initial_loader)
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

    def test_weakness_view_uses_the_shared_study_engine(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="study-view"', html)
        self.assertIn('id="study-card-correct-count"', html)
        self.assertIn('id="study-card-incorrect-count"', html)
        self.assertIn('<option value="weakness">苦手・要観察</option>', html)
        self.assertIn('fetchJson(API + "/learning-analysis")', source)
        self.assertIn("state.weaknessTargets.has(studyCardId(item))", source)
        self.assertEqual(source.count('postJson(API + "/card-attempts"'), 1)

    def test_all_scope_intentionally_includes_certain_cards(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = source.index("  function studyScopePool(cards)")
        end = source.index("  function queueCardAttempt", start)
        scope_pool = source[start:end]

        self.assertIn(
            'if (scope === "graduated") return cards.filter(isStudyGraduated);',
            scope_pool,
        )
        self.assertIn(
            'if (scope === "all") return cards;',
            scope_pool,
        )
        self.assertIn(
            "return cards.filter((item) => !isStudyGraduated(item));",
            scope_pool,
        )
        self.assertNotIn('scope === "all") return cards.filter', scope_pool)

    def test_card_id_deep_link_accepts_only_an_existing_card(self) -> None:
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cards = json.loads(
            (ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8")
        )["items"]
        existing_id = cards[0]["id"]
        missing_id = "card-that-does-not-exist"
        self.assertNotIn(missing_id, {card["id"] for card in cards})

        start = source.index("  function requestedStudyCardId(search)")
        end = source.index("\n  function openRequestedStudyCard", start)
        helper = source[start:end]
        script = f"""
const state = {{ studyById: new Map([[{json.dumps(existing_id)}, {{}}]]) }};
{helper}
const checks = [
  requestedStudyCardId("?cardId=" + encodeURIComponent({json.dumps(existing_id)})) === {json.dumps(existing_id)},
  requestedStudyCardId("?cardId=" + encodeURIComponent({json.dumps(missing_id)})) === null,
  requestedStudyCardId("?cardId=" + "a".repeat(129)) === null,
  requestedStudyCardId("") === null,
];
if (!checks.every(Boolean)) process.exit(1);
"""
        completed = subprocess.run(
            ["/opt/homebrew/bin/node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "studyReady && openRequestedStudyCard(location.search)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
