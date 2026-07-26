from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gyousei_pipeline.candidates import (
    CandidateBuildError,
    build_argument_parser,
    build_inventory,
    candidates_for_record,
    main,
)


FIXTURE = Path(__file__).parent / "fixtures" / "candidate_records.json"
REQUIRED_FIELDS = {
    "rawQuestionId",
    "sourceSnapshotId",
    "examYear",
    "questionNumber",
    "format",
    "task",
    "decisionReason",
}


class CandidateInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def by_raw_id(self, inventory: dict, raw_id: str) -> list[dict]:
        return [
            candidate
            for candidate in inventory["candidates"]
            if candidate["rawQuestionId"] == raw_id
        ]

    def test_only_unambiguous_single_select_questions_are_split(self) -> None:
        inventory = build_inventory(self.records, source_input=str(FIXTURE))

        true_candidates = self.by_raw_id(inventory, "gkd-2025-q8")
        self.assertEqual(len(true_candidates), 5)
        self.assertTrue(all(item["candidateKind"] == "choice_proposition" for item in true_candidates))
        self.assertEqual(
            {item["choiceLabel"]: item["inferredTruth"] for item in true_candidates},
            {"1": False, "2": True, "3": False, "4": False, "5": False},
        )

        false_candidates = self.by_raw_id(inventory, "gkd-2025-q9")
        self.assertEqual(len(false_candidates), 5)
        self.assertEqual(
            {item["choiceLabel"]: item["inferredTruth"] for item in false_candidates},
            {"1": True, "2": True, "3": True, "4": False, "5": True},
        )
        self.assertEqual(
            false_candidates[0]["decisionReason"],
            "single_select_false_answer_allows_choice_truth_inference",
        )

    def test_complex_formats_stay_as_one_original_question(self) -> None:
        inventory = build_inventory(self.records)
        expected_reasons = {
            "gkd-2025-q10": "task_combination_requires_question_level_review",
            "gkd-2025-q11": "task_count_requires_question_level_review",
            "gkd-2025-q12": "task_unknown_requires_question_level_review",
            "gkd-2025-q42": "format_multiple_blank_requires_question_level_review",
            "gkd-2025-q44": "format_written_requires_question_level_review",
        }
        for raw_id, reason in expected_reasons.items():
            with self.subTest(raw_id=raw_id):
                candidates = self.by_raw_id(inventory, raw_id)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["candidateKind"], "original_question")
                self.assertEqual(candidates[0]["decisionReason"], reason)
                self.assertNotIn("inferredTruth", candidates[0])

        combination = self.by_raw_id(inventory, "gkd-2025-q10")[0]
        self.assertEqual(len(combination["originalQuestion"]["choices"]), 5)
        self.assertEqual(
            combination["originalQuestion"]["questionText"],
            "アからオまでを検討し、正しい組合せを選べ。",
        )
        multiple = self.by_raw_id(inventory, "gkd-2025-q42")[0]
        self.assertEqual(multiple["originalQuestion"]["blanks"], ["ア", "イ", "ウ", "エ"])
        written = self.by_raw_id(inventory, "gkd-2025-q44")[0]
        self.assertEqual(written["originalQuestion"]["characterLimit"], 40)

    def test_every_candidate_is_traceable_and_never_prepublished(self) -> None:
        inventory = build_inventory(self.records)
        for candidate in inventory["candidates"]:
            with self.subTest(candidate=candidate["candidateId"]):
                self.assertTrue(REQUIRED_FIELDS <= set(candidate))
                self.assertFalse(candidate["reviewed"])
                self.assertFalse(candidate["publishable"])
                self.assertTrue(candidate["sourceCitation"]["sourceUrl"])
                self.assertTrue(candidate["sourceCitation"]["externalQuestionId"])
        self.assertEqual(inventory["visibility"], "private_not_for_web")
        self.assertFalse(inventory["reviewPolicy"]["autoPublish"])

    def test_provider_explanation_fields_are_not_copied(self) -> None:
        serialized = json.dumps(build_inventory(self.records), ensure_ascii=False)
        self.assertNotIn("PROVIDER-EXPLANATION-MUST-NOT-LEAK", serialized)
        self.assertNotIn("providerExplanation", serialized)
        self.assertFalse(
            build_inventory(self.records)["reviewPolicy"]["narrativeExplanationIncluded"]
        )

    def test_invalid_answer_choice_count_or_low_confidence_fails_closed(self) -> None:
        base = self.records[0]
        mutations = []

        invalid_answer = copy.deepcopy(base)
        invalid_answer["answer"]["value"] = 9
        mutations.append((invalid_answer, "regular_answer_option_out_of_range"))

        four_choices = copy.deepcopy(base)
        four_choices["choices"] = four_choices["choices"][:4]
        mutations.append((four_choices, "regular_choice_count_not_five"))

        low_confidence = copy.deepcopy(base)
        low_confidence["task"]["confidence"] = "low"
        mutations.append((low_confidence, "regular_task_inference_not_high_confidence"))

        parse_error = copy.deepcopy(base)
        parse_error["extraction"] = {"status": "parse_error", "warnings": ["broken"]}
        mutations.append(
            (parse_error, "extraction_parse_error_requires_question_level_review")
        )

        for record, expected_reason in mutations:
            with self.subTest(reason=expected_reason):
                candidates = candidates_for_record(record)
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["candidateKind"], "original_question")
                self.assertEqual(candidates[0]["decisionReason"], expected_reason)

    def test_withdrawn_question_is_never_split_into_ox_candidates(self) -> None:
        withdrawn = copy.deepcopy(self.records[0])
        withdrawn["isWithdrawn"] = True

        candidates = candidates_for_record(withdrawn)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidateKind"], "original_question")
        self.assertEqual(
            candidates[0]["decisionReason"],
            "withdrawn_question_requires_question_level_review",
        )
        self.assertNotIn("inferredTruth", candidates[0])

    def test_duplicate_raw_question_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(CandidateBuildError, "duplicate rawQuestionId"):
            build_inventory([self.records[0], copy.deepcopy(self.records[0])])

    def test_cli_writes_atomic_json_to_explicit_private_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "curation" / "review_candidates.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--input", str(FIXTURE), "--output", str(output)])
            self.assertEqual(status, 0, stderr.getvalue())
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["schemaVersion"], "review-candidate-inventory@1")
            self.assertEqual(value["summary"]["rawQuestionCount"], 7)
            self.assertEqual(value["summary"]["candidateCount"], 15)
            self.assertEqual(value["summary"]["choicePropositionCount"], 10)
            self.assertEqual(value["summary"]["originalQuestionQueueCount"], 5)
            self.assertEqual(list(output.parent.glob(output.name + ".*")), [])
            self.assertIn(str(output), stdout.getvalue())

    def test_default_output_is_private_curation_directory(self) -> None:
        args = build_argument_parser().parse_args([])
        self.assertEqual(args.output.parent.name, "curation")
        self.assertEqual(args.output.name, "review_candidates.json")


if __name__ == "__main__":
    unittest.main()
