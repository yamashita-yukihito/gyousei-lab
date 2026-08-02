from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINPO_B = ROOT / "static" / "minpo-b"


def load_data() -> dict:
    return json.loads((MINPO_B / "problems.json").read_text(encoding="utf-8"))


class MinpoBTest(unittest.TestCase):
    def test_files_and_problem_ids(self) -> None:
        for name in ("index.html", "styles.css", "app.js", "problems.json"):
            self.assertTrue((MINPO_B / name).is_file(), name)

        data = load_data()
        self.assertEqual("minpo-drill-b@1", data["schemaVersion"])
        self.assertEqual(5, len(data["scenes"]))
        statements = [item for scene in data["scenes"] for item in scene["statements"]]
        ids = [item["id"] for item in statements]
        self.assertEqual(31, len(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(item["answer"], bool) for item in statements))

    def test_reviewed_legal_corrections_do_not_regress(self) -> None:
        source = (MINPO_B / "problems.json").read_text(encoding="utf-8")
        self.assertIn("民法1049条1項・2項", source)
        self.assertIn("民事執行法180条2号", source)
        self.assertNotIn("民法1043条", source)
        self.assertNotIn("民事執行法93条", source)

        statements = {
            item["id"]: item
            for scene in load_data()["scenes"]
            for item in scene["statements"]
        }
        self.assertIn("抵当権が設定された時点", statements["scene-003-s4"]["text"])
        self.assertIn("しずかは復帰登記がなくても", statements["scene-001-s3"]["text"])
        self.assertNotIn("民法395条", statements["scene-003-s5"]["explanation"]["keyArticle"])

    def test_assets_are_cache_busted(self) -> None:
        html = (MINPO_B / "index.html").read_text(encoding="utf-8")
        js = (MINPO_B / "app.js").read_text(encoding="utf-8")
        self.assertIn("styles.css?v=20260802-b1", html)
        self.assertIn("app.js?v=20260802-b1", html)
        self.assertIn("problems.json?v=${DATA_VERSION}", js)
        self.assertIn("cache: 'no-store'", js)


if __name__ == "__main__":
    unittest.main()
