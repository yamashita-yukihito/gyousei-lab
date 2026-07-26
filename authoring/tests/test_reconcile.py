from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.reconcile import (
    RESULT_FIELDS,
    ReconciliationError,
    main,
    normalize_written_answer,
    reconcile_records,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records_path = FIXTURES / "reconcile_extracted.json"
        self.official_path = FIXTURES / "reconcile_official_index.json"
        self.records = json.loads(self.records_path.read_text(encoding="utf-8"))
        self.official = json.loads(self.official_path.read_text(encoding="utf-8"))

    def build(self):
        return reconcile_records(
            self.records,
            self.official,
            generated_at="2026-07-18T12:00:00Z",
        )

    def test_reports_all_five_statuses_and_does_not_leak_question_material(self) -> None:
        report = self.build()
        by_id = {row["rawQuestionId"]: row for row in report["results"]}

        self.assertEqual("unavailable", by_id["provider:2016-8"]["status"])
        self.assertEqual("official_year_status_failed", by_id["provider:2016-8"]["reason"])
        self.assertEqual("exact", by_id["provider:2018-8"]["status"])
        self.assertEqual("match-after-normalization", by_id["provider:2018-9"]["status"])
        self.assertEqual("exact", by_id["provider:2018-42"]["status"])
        self.assertEqual("match-after-normalization", by_id["provider:2018-43"]["status"])
        self.assertEqual("match-after-normalization", by_id["provider:2018-44"]["status"])
        self.assertEqual("mismatch", by_id["provider:2019-8"]["status"])
        self.assertEqual("unsupported", by_id["provider:2019-42"]["status"])
        self.assertEqual("mismatch", by_id["provider:2019-44"]["status"])

        self.assertEqual([2016], report["summary"]["unavailableExamYears"])
        self.assertEqual(
            {
                "exact": 2,
                "match-after-normalization": 3,
                "mismatch": 2,
                "unavailable": 1,
                "unsupported": 1,
            },
            report["summary"]["statusCounts"],
        )
        for row in report["results"]:
            self.assertEqual(RESULT_FIELDS, tuple(row))
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("REPORT-MUST-NOT-COPY-QUESTION-TEXT", serialized)
        self.assertNotIn("REPORT-MUST-NOT-COPY-PROVIDER-EXPLANATION", serialized)

    def test_written_normalization_is_format_only_not_semantic(self) -> None:
        self.assertEqual(
            normalize_written_answer("行政庁は， 理由を示す。（10字）"),
            normalize_written_answer("行政庁は、理由を示す。"),
        )
        self.assertNotEqual(
            normalize_written_answer("処分の取消しを求める。"),
            normalize_written_answer("処分の取消を求める。"),
        )

    def test_ambiguous_official_answer_is_never_treated_as_match(self) -> None:
        official = json.loads(json.dumps(self.official))
        duplicate = dict(official["years"]["2018"]["answerDisplays"]["answers"][0])
        official["years"]["2018"]["answerDisplays"]["answers"].append(duplicate)
        report = reconcile_records([self.records[1]], official)
        self.assertEqual("unsupported", report["results"][0]["status"])
        self.assertEqual("official_answer_ambiguous", report["results"][0]["reason"])

    def test_rejects_unknown_official_index_schema(self) -> None:
        official = dict(self.official)
        official["schemaVersion"] = "unknown"
        with self.assertRaises(ReconciliationError):
            reconcile_records(self.records, official)

    def test_cli_atomically_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "reconciliation.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--records",
                        str(self.records_path),
                        "--official-index",
                        str(self.official_path),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(0, exit_code)
            self.assertTrue(output.is_file())
            printed = json.loads(stdout.getvalue())
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(printed, stored)


if __name__ == "__main__":
    unittest.main()
