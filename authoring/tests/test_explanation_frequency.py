from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.explanation_frequency import (
    ExplanationFrequencyError,
    build_crosswalk,
    main,
)


def candidate(
    candidate_id: str,
    raw_question_id: str,
    exam_year: int,
    question_number: int,
    subject_id: str,
) -> dict:
    return {
        "candidateId": candidate_id,
        "rawQuestionId": raw_question_id,
        "examYear": exam_year,
        "questionNumber": question_number,
        "subjectId": subject_id,
        "frequencyEligible": False,
    }


def candidate_document(schema: str, candidates: list[dict]) -> dict:
    return {"schemaVersion": schema, "candidates": candidates}


def cards() -> dict:
    return {
        "schemaVersion": "test",
        "items": [
            {"id": "card-a", "subjectId": "administrative-law"},
            {"id": "card-b", "subjectId": "administrative-law"},
        ],
    }


def audit() -> dict:
    return {
        "schemaVersion": "card-frequency-audit@3",
        "status": "independent_recheck_complete",
        "subjects": [
            {
                "subjectId": "administrative-law",
                "examYears": list(range(2006, 2026)),
                "questionCount": 440,
            }
        ],
        "cards": [
            {
                "cardId": "card-a",
                "subjectId": "administrative-law",
                "recent": {
                    "questionKeys": ["2025-q1"],
                    "count": 1,
                },
            },
            {
                "cardId": "card-b",
                "subjectId": "administrative-law",
                "recent": {
                    "questionKeys": [],
                    "count": 0,
                },
            },
        ],
    }


def documents() -> tuple[dict, dict]:
    base = candidate_document(
        "explanation-derived-ox@1",
        [
            candidate(
                "candidate-a",
                "question-a",
                2025,
                1,
                "administrative_law",
            ),
            candidate(
                "candidate-b",
                "question-b",
                2025,
                2,
                "administrative_law",
            ),
            candidate(
                "candidate-c",
                "question-c",
                2025,
                3,
                "civil_law",
            ),
        ],
    )
    mapping = candidate_document(
        "explanation-mapping-derived-ox@1",
        [
            candidate(
                "candidate-d",
                "question-a",
                2025,
                1,
                "administrative_law",
            )
        ],
    )
    return base, mapping


class ExplanationFrequencyTests(unittest.TestCase):
    def test_builds_question_level_card_relations_without_enabling_candidates(
        self,
    ) -> None:
        base, mapping = documents()

        crosswalk = build_crosswalk(
            base,
            mapping,
            audit(),
            cards(),
            generated_at="2026-07-26T00:00:00Z",
        )

        self.assertEqual(crosswalk["summary"]["candidateCount"], 4)
        self.assertEqual(crosswalk["summary"]["sourceQuestionCount"], 3)
        self.assertEqual(
            crosswalk["summary"][
                "administrativeSourceQuestionCountedForCurrentCards"
            ],
            1,
        )
        self.assertEqual(
            crosswalk["summary"][
                "administrativeSourceQuestionNotCountedForCurrentCards"
            ],
            1,
        )
        self.assertEqual(
            crosswalk["summary"]["otherSubjectSourceQuestionCount"],
            1,
        )
        self.assertEqual(
            crosswalk["summary"]["countedCardQuestionRelationCount"],
            1,
        )
        by_id = {
            item["rawQuestionId"]: item
            for item in crosswalk["sourceQuestions"]
        }
        self.assertEqual(
            by_id["question-a"]["status"], "counted_for_current_cards"
        )
        self.assertEqual(
            by_id["question-a"]["candidateIds"],
            ["candidate-a", "candidate-d"],
        )
        self.assertEqual(
            by_id["question-a"]["currentCardRelations"],
            [
                {
                    "cardId": "card-a",
                    "decision": "counted",
                    "basis": "completed_card_frequency_audit",
                }
            ],
        )
        self.assertEqual(
            by_id["question-b"]["status"],
            "not_counted_for_current_cards",
        )
        self.assertEqual(
            by_id["question-c"]["status"], "no_current_subject_cards"
        )
        self.assertFalse(
            crosswalk["policy"]["candidateGlobalFrequencyEligible"]
        )

    def test_rejects_candidate_global_frequency_enablement(self) -> None:
        base, mapping = documents()
        base["candidates"][0]["frequencyEligible"] = True

        with self.assertRaisesRegex(
            ExplanationFrequencyError,
            "frequencyEligible must remain false",
        ):
            build_crosswalk(base, mapping, audit(), cards())

    def test_rejects_incomplete_audit_and_card_drift(self) -> None:
        base, mapping = documents()
        incomplete = copy.deepcopy(audit())
        incomplete["status"] = "needs_review"
        with self.assertRaisesRegex(
            ExplanationFrequencyError, "not independently rechecked"
        ):
            build_crosswalk(base, mapping, incomplete, cards())

        changed_cards = copy.deepcopy(cards())
        changed_cards["items"].append({"id": "card-c"})
        with self.assertRaisesRegex(
            ExplanationFrequencyError, "card IDs differ"
        ):
            build_crosswalk(base, mapping, audit(), changed_cards)

    def test_rejects_legacy_schema_version(self) -> None:
        base, mapping = documents()
        legacy = copy.deepcopy(audit())
        legacy["schemaVersion"] = "card-frequency-audit@2"
        legacy["scope"] = {
            "examYears": legacy["subjects"][0]["examYears"],
            "questionCount": legacy["subjects"][0]["questionCount"],
        }
        del legacy["subjects"]
        with self.assertRaisesRegex(
            ExplanationFrequencyError, "card-frequency-audit@3"
        ):
            build_crosswalk(base, mapping, legacy, cards())

    def test_rejects_card_subject_id_outside_declared_scope(self) -> None:
        base, mapping = documents()
        drifted = copy.deepcopy(audit())
        drifted["cards"][0]["subjectId"] = "civil-law"
        with self.assertRaisesRegex(
            ExplanationFrequencyError,
            "is not declared in frequency audit subjects",
        ):
            build_crosswalk(base, mapping, drifted, cards())

    def test_rejects_audit_missing_administrative_law_subject_scope(self) -> None:
        base, mapping = documents()
        no_admin_scope = copy.deepcopy(audit())
        no_admin_scope["subjects"] = [
            {
                "subjectId": "civil-law",
                "examYears": [2025],
                "questionCount": 220,
            }
        ]
        with self.assertRaisesRegex(
            ExplanationFrequencyError,
            "subjects must include administrative-law",
        ):
            build_crosswalk(base, mapping, no_admin_scope, cards())

    def test_cli_writes_private_atomic_artifacts(self) -> None:
        base, mapping = documents()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "base.json"
            mapping_path = root / "mapping.json"
            audit_path = root / "audit.json"
            cards_path = root / "cards.json"
            output_path = root / "out" / "crosswalk.json"
            validation_path = root / "out" / "validation.json"
            manifest_path = root / "out" / "manifest.json"
            for path, value in (
                (base_path, base),
                (mapping_path, mapping),
                (audit_path, audit()),
                (cards_path, cards()),
            ):
                path.write_text(
                    json.dumps(value, ensure_ascii=False),
                    encoding="utf-8",
                )

            status_code = main(
                [
                    "--base-candidates",
                    str(base_path),
                    "--mapping-candidates",
                    str(mapping_path),
                    "--frequency-audit",
                    str(audit_path),
                    "--cards",
                    str(cards_path),
                    "--output",
                    str(output_path),
                    "--validation-output",
                    str(validation_path),
                    "--manifest-output",
                    str(manifest_path),
                    "--expected-candidate-count",
                    "4",
                    "--expected-source-question-count",
                    "3",
                    "--expected-current-card-count",
                    "2",
                    "--expected-administrative-candidate-count",
                    "3",
                    "--expected-administrative-source-question-count",
                    "2",
                ]
            )

            self.assertEqual(status_code, 0)
            for path in (output_path, validation_path, manifest_path):
                self.assertTrue(path.is_file())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(
                json.loads(validation_path.read_text(encoding="utf-8"))[
                    "passed"
                ]
            )
            self.assertEqual(list(output_path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
