from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.dataset_inventory import (
    DatasetInventoryError,
    build_data_inventory,
    main,
)


FIXTURE = Path(__file__).parent / "fixtures" / "candidate_records.json"


def records_for(year: int, subject_id: str, subject_label: str) -> list[dict]:
    records = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))
    for index, record in enumerate(records):
        record["rawQuestionId"] = f"{year}-{index}"
        record["sourceSnapshotId"] = f"snapshot-{year}-{index}"
        record["examYear"] = year
        record["subjectId"] = subject_id
        record["subjectLabel"] = subject_label
        record["explanationExpected"] = year >= 2016
        record["extraction"] = {"status": "parsed", "warnings": []}
    return records


class DatasetInventoryTests(unittest.TestCase):
    def test_metrics_keep_questions_choices_and_ox_candidates_distinct(self) -> None:
        current = records_for(2025, "administrative_law", "行政法")
        archive = records_for(2015, "civil_law", "民法")
        current[0]["isWithdrawn"] = True

        inventory = build_data_inventory(
            current,
            archive,
            generated_at="2026-07-26T00:00:00Z",
        )

        current_scope = inventory["scopes"][0]
        current_admin = next(
            row
            for row in current_scope["subjects"]
            if row["subjectId"] == "administrative_law"
        )
        self.assertEqual(current_admin["questionUnits"], 7)
        self.assertEqual(current_admin["regularQuestions"], 5)
        self.assertEqual(current_admin["regularChoiceCount"], 25)
        self.assertEqual(current_admin["safeOxQuestionCount"], 1)
        self.assertEqual(current_admin["safeOxChoiceCount"], 5)
        self.assertEqual(current_admin["multipleBlankQuestions"], 1)
        self.assertEqual(current_admin["wordBankEntryCount"], 3)
        self.assertEqual(current_admin["blankSlotCount"], 4)
        self.assertEqual(current_admin["writtenQuestions"], 1)
        self.assertEqual(current_admin["withdrawnQuestionCount"], 1)
        self.assertEqual(current_admin["wholeQuestionQueueCount"], 6)

        all_scope = inventory["scopes"][2]
        self.assertEqual(all_scope["totals"]["questionUnits"], 14)
        self.assertEqual(all_scope["totals"]["regularChoiceCount"], 50)
        self.assertEqual(all_scope["totals"]["safeOxChoiceCount"], 15)

    def test_serialized_inventory_contains_no_private_source_content(self) -> None:
        current = records_for(2025, "administrative_law", "行政法")
        archive = records_for(2015, "civil_law", "民法")
        current[0]["questionText"] = "PRIVATE QUESTION TEXT"
        current[0]["providerExplanation"] = "PRIVATE PROVIDER EXPLANATION"
        current[0]["sourceUrl"] = "https://example.invalid/private"

        serialized = json.dumps(
            build_data_inventory(current, archive),
            ensure_ascii=False,
        )

        self.assertNotIn("PRIVATE QUESTION TEXT", serialized)
        self.assertNotIn("PRIVATE PROVIDER EXPLANATION", serialized)
        self.assertNotIn("example.invalid", serialized)
        self.assertTrue(
            build_data_inventory(current, archive)["privacy"]["containsQuestionText"]
            is False
        )

    def test_extraction_warning_fails_closed(self) -> None:
        current = records_for(2025, "administrative_law", "行政法")
        archive = records_for(2015, "civil_law", "民法")
        current[0]["extraction"]["warnings"] = ["unexpected"]
        with self.assertRaises(DatasetInventoryError):
            build_data_inventory(current, archive)

    def test_cli_writes_private_atomic_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_root = root / "current"
            archive_root = root / "archive"
            for target, records in (
                (current_root, records_for(2025, "administrative_law", "行政法")),
                (archive_root, records_for(2015, "civil_law", "民法")),
            ):
                extracted = target / "extracted" / str(records[0]["examYear"])
                extracted.mkdir(parents=True)
                for index, record in enumerate(records):
                    (extracted / f"{index}.json").write_text(
                        json.dumps(record, ensure_ascii=False),
                        encoding="utf-8",
                    )
            output = root / "out" / "inventory.json"

            status_code = main(
                [
                    "--current-root",
                    str(current_root),
                    "--archive-root",
                    str(archive_root),
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(status_code, 0)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["schemaVersion"],
                "gyousei-data-inventory@1",
            )
            self.assertEqual(list(output.parent.glob(output.name + ".*")), [])


if __name__ == "__main__":
    unittest.main()
