"""Reconcile explanation-derived O/X sources with the audited card frequency.

``frequencyEligible`` is intentionally a fail-closed, candidate-global flag.
Actual frequency is a relation between one learning card and one source exam
question, so this module joins the generated candidates to the completed
``card-frequency-audit@2`` without enabling automatic candidate counting.

The output contains private editorial IDs and must never be served to the web.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "explanation-ox-frequency-crosswalk@1"
VALIDATION_SCHEMA_VERSION = "explanation-ox-frequency-crosswalk-validation@1"
MANIFEST_SCHEMA_VERSION = "explanation-ox-frequency-crosswalk-manifest@1"

DEFAULT_BASE_CANDIDATES = (
    data_root()
    / "all_subjects"
    / "current_2016_2025"
    / "curation"
    / "explanation_ox_candidates.json"
)
DEFAULT_MAPPING_CANDIDATES = (
    data_root()
    / "all_subjects"
    / "current_2016_2025"
    / "curation"
    / "explanation_mapping_ox_candidates.json"
)
DEFAULT_FREQUENCY_AUDIT = (
    data_root() / "curation" / "card_frequency_2006_2025.json"
)
DEFAULT_CARDS = data_root() / "canonical" / "explanation_cards.json"
DEFAULT_OUTPUT = (
    data_root()
    / "all_subjects"
    / "current_2016_2025"
    / "curation"
    / "explanation_ox_frequency_crosswalk.json"
)

ADMINISTRATIVE_LAW = "administrative_law"
COMPLETE_AUDIT_STATUS = "independent_recheck_complete"


class ExplanationFrequencyError(ValueError):
    """Raised when frequency reconciliation cannot fail closed."""


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplanationFrequencyError(f"{name} must be a non-empty string")
    return value.strip()


def _required_integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExplanationFrequencyError(f"{name} must be an integer")
    return value


def _candidate_list(
    document: dict[str, Any], *, expected_schema: str, source_name: str
) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ExplanationFrequencyError(f"{source_name} must be an object")
    if document.get("schemaVersion") != expected_schema:
        raise ExplanationFrequencyError(
            f"{source_name} must use {expected_schema}"
        )
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise ExplanationFrequencyError(
            f"{source_name}.candidates must be a list"
        )
    if any(not isinstance(candidate, dict) for candidate in candidates):
        raise ExplanationFrequencyError(
            f"{source_name}.candidates must contain only objects"
        )
    return candidates


def _current_card_ids(cards_document: dict[str, Any]) -> set[str]:
    if not isinstance(cards_document, dict):
        raise ExplanationFrequencyError("cards document must be an object")
    items = cards_document.get("items")
    if not isinstance(items, list) or not items:
        raise ExplanationFrequencyError("cards.items must be a non-empty list")
    card_ids: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExplanationFrequencyError(
                f"cards.items[{index}] must be an object"
            )
        card_ids.append(
            _required_text(item.get("id"), name=f"cards.items[{index}].id")
        )
    if len(card_ids) != len(set(card_ids)):
        raise ExplanationFrequencyError("cards document has duplicate card IDs")
    return set(card_ids)


def _audited_relations(
    audit: dict[str, Any], *, current_card_ids: set[str]
) -> tuple[dict[str, list[str]], set[int], int]:
    if not isinstance(audit, dict):
        raise ExplanationFrequencyError("frequency audit must be an object")
    if audit.get("schemaVersion") != "card-frequency-audit@2":
        raise ExplanationFrequencyError(
            "frequency audit must use card-frequency-audit@2"
        )
    if audit.get("status") != COMPLETE_AUDIT_STATUS:
        raise ExplanationFrequencyError(
            "frequency audit is not independently rechecked and complete"
        )
    scope = audit.get("scope")
    if not isinstance(scope, dict):
        raise ExplanationFrequencyError("frequency audit scope must be an object")
    raw_years = scope.get("examYears")
    if not isinstance(raw_years, list) or not raw_years:
        raise ExplanationFrequencyError(
            "frequency audit scope.examYears must be a non-empty list"
        )
    years = {
        _required_integer(year, name="frequency audit scope exam year")
        for year in raw_years
    }
    question_count = _required_integer(
        scope.get("questionCount"), name="frequency audit scope.questionCount"
    )
    if question_count <= 0:
        raise ExplanationFrequencyError(
            "frequency audit scope.questionCount must be positive"
        )

    cards = audit.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ExplanationFrequencyError(
            "frequency audit cards must be a non-empty list"
        )
    audit_card_ids: list[str] = []
    relations: dict[str, list[str]] = defaultdict(list)
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ExplanationFrequencyError(
                f"frequency audit cards[{index}] must be an object"
            )
        card_id = _required_text(
            card.get("cardId"), name=f"frequency audit cards[{index}].cardId"
        )
        audit_card_ids.append(card_id)
        recent = card.get("recent")
        if not isinstance(recent, dict):
            raise ExplanationFrequencyError(
                f"{card_id}: recent audit must be an object"
            )
        raw_keys = recent.get("questionKeys")
        if not isinstance(raw_keys, list):
            raise ExplanationFrequencyError(
                f"{card_id}: recent.questionKeys must be a list"
            )
        question_keys = [
            _required_text(value, name=f"{card_id}: recent question key")
            for value in raw_keys
        ]
        if len(question_keys) != len(set(question_keys)):
            raise ExplanationFrequencyError(
                f"{card_id}: duplicate recent question key"
            )
        recent_count = _required_integer(
            recent.get("count"), name=f"{card_id}: recent.count"
        )
        if recent_count != len(question_keys):
            raise ExplanationFrequencyError(
                f"{card_id}: recent count does not match unique question keys"
            )
        for question_key in question_keys:
            relations[question_key].append(card_id)

    if len(audit_card_ids) != len(set(audit_card_ids)):
        raise ExplanationFrequencyError("frequency audit has duplicate card IDs")
    if set(audit_card_ids) != current_card_ids:
        missing = sorted(current_card_ids - set(audit_card_ids))
        extra = sorted(set(audit_card_ids) - current_card_ids)
        raise ExplanationFrequencyError(
            "frequency audit card IDs differ from current cards "
            f"(missing={missing[:3]}, extra={extra[:3]})"
        )
    return (
        {
            question_key: sorted(card_ids)
            for question_key, card_ids in relations.items()
        },
        years,
        question_count,
    )


def _question_key(exam_year: int, question_number: int) -> str:
    return f"{exam_year}-q{question_number}"


def build_crosswalk(
    base_candidates_document: dict[str, Any],
    mapping_candidates_document: dict[str, Any],
    frequency_audit: dict[str, Any],
    cards_document: dict[str, Any],
    *,
    source_base_candidates: str = "",
    source_mapping_candidates: str = "",
    source_frequency_audit: str = "",
    source_cards: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a source-question-by-card frequency crosswalk."""

    base_candidates = _candidate_list(
        base_candidates_document,
        expected_schema="explanation-derived-ox@1",
        source_name="base candidates",
    )
    mapping_candidates = _candidate_list(
        mapping_candidates_document,
        expected_schema="explanation-mapping-derived-ox@1",
        source_name="mapping candidates",
    )
    candidates = [*base_candidates, *mapping_candidates]
    current_card_ids = _current_card_ids(cards_document)
    relations_by_question, audited_years, audited_question_count = (
        _audited_relations(
            frequency_audit,
            current_card_ids=current_card_ids,
        )
    )

    candidate_ids: set[str] = set()
    questions: dict[str, dict[str, Any]] = {}
    candidate_subjects: Counter[str] = Counter()
    for index, candidate in enumerate(candidates):
        candidate_id = _required_text(
            candidate.get("candidateId"), name=f"candidate[{index}].candidateId"
        )
        if candidate_id in candidate_ids:
            raise ExplanationFrequencyError(
                f"duplicate candidateId: {candidate_id}"
            )
        candidate_ids.add(candidate_id)
        if candidate.get("frequencyEligible") is not False:
            raise ExplanationFrequencyError(
                f"{candidate_id}: frequencyEligible must remain false"
            )
        raw_question_id = _required_text(
            candidate.get("rawQuestionId"),
            name=f"{candidate_id}.rawQuestionId",
        )
        exam_year = _required_integer(
            candidate.get("examYear"), name=f"{candidate_id}.examYear"
        )
        question_number = _required_integer(
            candidate.get("questionNumber"),
            name=f"{candidate_id}.questionNumber",
        )
        subject_id = _required_text(
            candidate.get("subjectId"), name=f"{candidate_id}.subjectId"
        )
        if subject_id == ADMINISTRATIVE_LAW and exam_year not in audited_years:
            raise ExplanationFrequencyError(
                f"{candidate_id}: administrative-law year is outside audit scope"
            )
        candidate_subjects[subject_id] += 1
        metadata = {
            "rawQuestionId": raw_question_id,
            "examYear": exam_year,
            "questionNumber": question_number,
            "questionKey": _question_key(exam_year, question_number),
            "subjectId": subject_id,
        }
        existing = questions.get(raw_question_id)
        if existing is None:
            questions[raw_question_id] = {
                **metadata,
                "candidateIds": [candidate_id],
            }
            continue
        if any(existing[key] != metadata[key] for key in metadata):
            raise ExplanationFrequencyError(
                f"{raw_question_id}: candidate source metadata conflicts"
            )
        existing["candidateIds"].append(candidate_id)

    source_questions: list[dict[str, Any]] = []
    source_statuses: Counter[str] = Counter()
    relation_count = 0
    for question in sorted(
        questions.values(),
        key=lambda item: (
            item["examYear"],
            item["questionNumber"],
            item["rawQuestionId"],
        ),
    ):
        card_ids = (
            relations_by_question.get(question["questionKey"], [])
            if question["subjectId"] == ADMINISTRATIVE_LAW
            else []
        )
        if card_ids:
            status = "counted_for_current_cards"
        elif question["subjectId"] == ADMINISTRATIVE_LAW:
            status = "not_counted_for_current_cards"
        else:
            status = "no_current_subject_cards"
        relations = [
            {
                "cardId": card_id,
                "decision": "counted",
                "basis": "completed_card_frequency_audit",
            }
            for card_id in card_ids
        ]
        relation_count += len(relations)
        source_statuses[status] += 1
        source_questions.append(
            {
                **question,
                "candidateIds": sorted(question["candidateIds"]),
                "status": status,
                "currentCardRelations": relations,
            }
        )

    administrative_candidate_count = candidate_subjects[ADMINISTRATIVE_LAW]
    other_subject_candidate_count = len(candidates) - administrative_candidate_count
    summary = {
        "candidateCount": len(candidates),
        "sourceQuestionCount": len(source_questions),
        "currentCardCount": len(current_card_ids),
        "auditedQuestionCount": audited_question_count,
        "administrativeCandidateCount": administrative_candidate_count,
        "administrativeSourceQuestionCount": (
            source_statuses["counted_for_current_cards"]
            + source_statuses["not_counted_for_current_cards"]
        ),
        "administrativeSourceQuestionCountedForCurrentCards": (
            source_statuses["counted_for_current_cards"]
        ),
        "administrativeSourceQuestionNotCountedForCurrentCards": (
            source_statuses["not_counted_for_current_cards"]
        ),
        "otherSubjectCandidateCount": other_subject_candidate_count,
        "otherSubjectSourceQuestionCount": source_statuses[
            "no_current_subject_cards"
        ],
        "countedCardQuestionRelationCount": relation_count,
        "candidateFrequencyEligibleTrueCount": 0,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at or utc_now(),
        "visibility": "private_authoring_artifact_not_for_web",
        "sourceBaseCandidates": source_base_candidates,
        "sourceMappingCandidates": source_mapping_candidates,
        "sourceFrequencyAudit": source_frequency_audit,
        "sourceCards": source_cards,
        "policy": {
            "frequencyAuthority": "card-frequency-audit@2",
            "frequencyUnit": "one source exam question per learning card",
            "candidateGlobalFrequencyEligible": False,
            "candidateGlobalFlagReason": (
                "a candidate-global flag cannot represent card-question relations"
            ),
            "absenceFromCompletedAdministrativeAuditMeans": (
                "not counted for any current administrative-law card"
            ),
            "otherSubjects": "no current subject cards to relate",
        },
        "summary": summary,
        "sourceQuestions": source_questions,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)
    os.chmod(path, 0o600)


def _positive_expected(value: int, *, option: str) -> int:
    if value < 0:
        raise ExplanationFrequencyError(f"{option} must be non-negative")
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-candidates", type=Path, default=DEFAULT_BASE_CANDIDATES
    )
    parser.add_argument(
        "--mapping-candidates", type=Path, default=DEFAULT_MAPPING_CANDIDATES
    )
    parser.add_argument(
        "--frequency-audit", type=Path, default=DEFAULT_FREQUENCY_AUDIT
    )
    parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation-output", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--expected-candidate-count", type=int, default=673)
    parser.add_argument("--expected-source-question-count", type=int, default=140)
    parser.add_argument("--expected-current-card-count", type=int, default=55)
    parser.add_argument(
        "--expected-administrative-candidate-count", type=int, default=207
    )
    parser.add_argument(
        "--expected-administrative-source-question-count",
        type=int,
        default=46,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    validation_output = args.validation_output or (
        args.output.parent / "explanation_ox_frequency_crosswalk_validation.json"
    )
    manifest_output = args.manifest_output or (
        args.output.parent / "explanation_ox_frequency_crosswalk_manifest.json"
    )
    try:
        expected = {
            "candidateCount": _positive_expected(
                args.expected_candidate_count,
                option="--expected-candidate-count",
            ),
            "sourceQuestionCount": _positive_expected(
                args.expected_source_question_count,
                option="--expected-source-question-count",
            ),
            "currentCardCount": _positive_expected(
                args.expected_current_card_count,
                option="--expected-current-card-count",
            ),
            "administrativeCandidateCount": _positive_expected(
                args.expected_administrative_candidate_count,
                option="--expected-administrative-candidate-count",
            ),
            "administrativeSourceQuestionCount": _positive_expected(
                args.expected_administrative_source_question_count,
                option="--expected-administrative-source-question-count",
            ),
        }
        generated_at = utc_now()
        crosswalk = build_crosswalk(
            load_json(args.base_candidates),
            load_json(args.mapping_candidates),
            load_json(args.frequency_audit),
            load_json(args.cards),
            source_base_candidates=str(args.base_candidates),
            source_mapping_candidates=str(args.mapping_candidates),
            source_frequency_audit=str(args.frequency_audit),
            source_cards=str(args.cards),
            generated_at=generated_at,
        )
        for key, expected_value in expected.items():
            actual = crosswalk["summary"][key]
            if actual != expected_value:
                raise ExplanationFrequencyError(
                    f"expected {expected_value} for {key}, got {actual}"
                )
        _write_private_json(args.output, crosswalk)
        validation = {
            "schemaVersion": VALIDATION_SCHEMA_VERSION,
            "generatedAt": generated_at,
            "passed": True,
            "checks": {
                "frequencyAuditComplete": True,
                "currentCardIdsExact": True,
                "allCandidatesAccountedFor": True,
                "allCandidateGlobalFrequencyFlagsFalse": True,
                "administrativeQuestionYearsWithinAuditScope": True,
                "duplicateCandidateIdCount": 0,
                "conflictingSourceMetadataCount": 0,
            },
            "summary": crosswalk["summary"],
        }
        _write_private_json(validation_output, validation)
        manifest = {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "generatedAt": generated_at,
            "visibility": "private_authoring_artifact",
            "algorithmSchemaVersion": SCHEMA_VERSION,
            "artifacts": [
                {
                    "name": args.output.name,
                    "sha256": _sha256_file(args.output),
                    "mode": "0600",
                },
                {
                    "name": validation_output.name,
                    "sha256": _sha256_file(validation_output),
                    "mode": "0600",
                },
            ],
            "summary": crosswalk["summary"],
        }
        _write_private_json(manifest_output, manifest)
    except (ExplanationFrequencyError, OSError, json.JSONDecodeError) as error:
        print(f"explanation frequency build failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "validation": str(validation_output),
                "manifest": str(manifest_output),
                **crosswalk["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
