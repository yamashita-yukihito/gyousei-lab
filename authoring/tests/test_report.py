from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.report import (
    REPORT_SCHEMA_VERSION,
    _documents,
    build_corpus_report,
    main,
)


FIXTURES = Path(__file__).parent / "fixtures"


class CorpusReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records_path = FIXTURES / "report_records.json"
        self.target_path = FIXTURES / "report_target.json"
        self.records = _documents(self.records_path)
        self.target = json.loads(self.target_path.read_text(encoding="utf-8"))

    def test_aggregates_coverage_tasks_labels_amendments_and_extraction(self) -> None:
        report = build_corpus_report(
            self.records,
            self.target,
            generated_at="2026-07-18T12:00:00Z",
        )

        self.assertEqual(report["schemaVersion"], REPORT_SCHEMA_VERSION)
        self.assertTrue(report["ok"])
        self.assertEqual(report["coverage"]["recordCount"], 4)
        self.assertEqual(
            report["coverage"]["byYear"]["2016"],
            {
                "regular": 2,
                "multiple_blank": 1,
                "written": 1,
                "total": 4,
                "matchesExpected": True,
            },
        )
        self.assertEqual(
            report["taskKinds"]["counts"],
            {
                "fill_four_blanks": 1,
                "select_false": 1,
                "select_true": 1,
                "written_response": 1,
            },
        )
        self.assertEqual(report["labels"]["questionCounts"]["行政法"], 4)
        self.assertEqual(report["labels"]["questionCounts"]["行政手続法"], 2)
        self.assertEqual(report["amendments"]["count"], 1)
        self.assertEqual(report["amendments"]["byYear"], {"2016": 1})
        self.assertEqual(
            report["extraction"]["statusCounts"],
            {"needs_review": 1, "parsed": 3},
        )
        self.assertEqual(report["extraction"]["recordsWithWarnings"], 1)
        self.assertEqual(
            report["extraction"]["warningCounts"],
            {"catalog_page_labels_mismatch": 1},
        )

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("REPORT-MUST-NOT-COPY-QUESTION-TEXT", serialized)
        self.assertNotIn("REPORT-MUST-NOT-COPY-PROVIDER-EXPLANATION", serialized)

    def test_parse_error_marks_quality_and_whole_report_not_ok(self) -> None:
        records = json.loads(json.dumps(self.records))
        records[0]["extraction"] = {
            "status": "parse_error",
            "warnings": ["missing_question_container"],
        }
        report = build_corpus_report(records, self.target)
        self.assertTrue(report["coverage"]["ok"])
        self.assertFalse(report["extraction"]["qualityOk"])
        self.assertFalse(report["ok"])

    def test_cli_atomically_writes_same_json_that_it_prints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "corpus-report.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--records",
                        str(self.records_path),
                        "--target",
                        str(self.target_path),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            printed = json.loads(stdout.getvalue())
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(printed, stored)
            self.assertTrue(stored["coverage"]["byYear"]["2016"]["matchesExpected"])


if __name__ == "__main__":
    unittest.main()
