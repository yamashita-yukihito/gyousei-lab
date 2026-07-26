"""Build a private, fail-closed production bundle for the quiz API.

Only explicitly selected fields cross this boundary.  In particular, this
module never serializes a whole source record, provider explanation, Claude
prompt, Claude stdout, or an absolute review-file path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .common import data_root, load_json, utc_now
from .subjects import CANONICAL_SUBJECT_LABELS, canonical_subject_id


SCHEMA_VERSION = "gyousei-production-bundle@1"
RAW_QUESTION_SCHEMA_VERSION = "raw-question@1"
RECONCILIATION_SCHEMA_VERSION = "answer-reconciliation@1"
EXPLANATION_CARD_SCHEMA_VERSION = "0.4-prototype"
RELATED_QUESTION_SOURCE_SCHEMA_VERSION = "0.2-prototype"
SIMILARITY_SCHEMA_VERSION = "similarity-candidates@1"
LEGACY_FABLE_RESPONSE_SCHEMA_VERSION = "ai-legal-review-response@2"
LEGACY_FABLE_RUN_SCHEMA_VERSIONS = frozenset(
    {"claude-fable-review-run@1", "claude-fable-review-run@2"}
)
VISIBILITY = "private_not_for_web"
STUDY_DECK_VISIBILITY = "private"
EXPLANATION_VARIANT_FIELDS = frozenset(
    {"a", "b", "bCasual", "bCasualStyle", "c"}
)
DEFAULT_TARGET_YEARS = tuple(range(2016, 2026))
QUESTION_SUBJECT_LABELS_BY_ID = CANONICAL_SUBJECT_LABELS
TARGET_QUESTION_NUMBERS = {
    "regular": frozenset(range(8, 27)),
    "multiple_blank": frozenset({42, 43}),
    "written": frozenset({44}),
}
RECONCILIATION_STATUSES = frozenset(
    {"exact", "match-after-normalization", "mismatch", "unavailable", "unsupported"}
)


class ProductionBundleError(ValueError):
    """An input cannot safely cross the private production boundary."""


@dataclass(frozen=True)
class BundleExpectations:
    """Expected corpus sizes; production defaults deliberately fail closed."""

    # The administrative-law subset is checked by year/number below.  ``None``
    # lets future subjects be appended without changing code-level totals.
    question_count: int | None = None
    question_subject_counts: tuple[tuple[str, int], ...] | None = None
    question_format_counts: tuple[tuple[str, int], ...] | None = None
    study_deck_count: int = 1
    explanation_card_count: int = 55
    related_question_evidence_count: int = 211
    claude_review_count: int = 20
    claude_run_count: int = 6
    similarity_pair_count: int = 588
    target_years: tuple[int, ...] | None = DEFAULT_TARGET_YEARS


def _object(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionBundleError(f"{context} must be an object")
    return value


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProductionBundleError(f"{context} must be an array")
    return value


def _text(value: Any, context: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ProductionBundleError(f"{context} must be {qualifier}")
    return value


def _integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionBundleError(f"{context} must be an integer")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ProductionBundleError(f"{context} must be a boolean")
    return value


def _number(value: Any, context: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionBundleError(f"{context} must be a number")
    return value


def _string_array(value: Any, context: str) -> list[str]:
    result: list[str] = []
    for index, item in enumerate(_array(value, context)):
        result.append(_text(item, f"{context}[{index}]"))
    return result


def _choice_identifier(value: Any, context: str) -> str | int:
    if isinstance(value, bool):
        raise ProductionBundleError(f"{context} must be a string or integer")
    if isinstance(value, int):
        return value
    return _text(value, context)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_file_digest(paths: Sequence[Path], base: Path) -> str:
    entries = [
        {
            "path": path.relative_to(base).as_posix(),
            "sha256": _file_digest(path),
        }
        for path in sorted(paths)
    ]
    return _canonical_digest(entries)


def _state_fields(source: Mapping[str, Any], context: str) -> dict[str, bool]:
    """Copy review gates exactly when the source actually has them."""

    result: dict[str, bool] = {}
    for field in ("reviewed", "publishable"):
        if field in source:
            result[field] = _boolean(source[field], f"{context}.{field}")
    return result


def _project_task(value: Any, context: str) -> dict[str, str]:
    task = _object(value, context)
    return {
        "kind": _text(task.get("kind"), f"{context}.kind"),
        "prompt": _text(task.get("prompt"), f"{context}.prompt"),
        "confidence": _text(task.get("confidence"), f"{context}.confidence"),
    }


def _project_answer(value: Any, question_format: str, context: str) -> dict[str, Any]:
    answer = _object(value, context)
    kind = _text(answer.get("kind"), f"{context}.kind")
    if question_format == "regular" and kind == "option":
        return {"kind": kind, "value": _integer(answer.get("value"), f"{context}.value")}
    if question_format == "written" and kind == "model_answer":
        return {"kind": kind, "value": _text(answer.get("value"), f"{context}.value")}
    if question_format == "multiple_blank" and kind == "blank_numbers":
        values = _object(answer.get("values"), f"{context}.values")
        return {
            "kind": kind,
            "values": {
                _text(label, f"{context}.values key"): _integer(
                    option, f"{context}.values[{label}]"
                )
                for label, option in values.items()
            },
        }
    raise ProductionBundleError(
        f"{context}: answer kind {kind!r} is invalid for format {question_format!r}"
    )


def _project_choices(value: Any, context: str) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for index, item in enumerate(_array(value, context)):
        item_context = f"{context}[{index}]"
        choice = _object(item, item_context)
        projected: dict[str, Any] = {
            "label": _text(choice.get("label"), f"{item_context}.label"),
            "text": _text(choice.get("text"), f"{item_context}.text"),
        }
        if "cells" in choice:
            cells: list[dict[str, str]] = []
            for cell_index, item_cell in enumerate(
                _array(choice["cells"], f"{item_context}.cells")
            ):
                cell_context = f"{item_context}.cells[{cell_index}]"
                cell = _object(item_cell, cell_context)
                cells.append(
                    {
                        "column": _text(cell.get("column"), f"{cell_context}.column"),
                        "text": _text(cell.get("text"), f"{cell_context}.text"),
                    }
                )
            projected["cells"] = cells
        choices.append(projected)
    if not choices:
        raise ProductionBundleError(f"{context} must not be empty")
    return choices


def _project_question_content(
    record: Mapping[str, Any], question_format: str, context: str
) -> dict[str, Any]:
    if question_format == "regular":
        return {
            "instruction": _text(
                record.get("instructionText"), f"{context}.instructionText"
            ),
            "question": _text(record.get("questionText"), f"{context}.questionText"),
            "choices": _project_choices(record.get("choices"), f"{context}.choices"),
            "choiceFormat": _text(
                record.get("choiceFormat"), f"{context}.choiceFormat"
            ),
            "choiceColumns": _string_array(
                record.get("choiceColumns"), f"{context}.choiceColumns"
            ),
        }
    if question_format == "multiple_blank":
        word_bank: list[dict[str, Any]] = []
        for index, item in enumerate(
            _array(record.get("wordBank"), f"{context}.wordBank")
        ):
            item_context = f"{context}.wordBank[{index}]"
            entry = _object(item, item_context)
            word_bank.append(
                {
                    "number": _integer(entry.get("number"), f"{item_context}.number"),
                    "text": _text(entry.get("text"), f"{item_context}.text"),
                }
            )
        if not word_bank:
            raise ProductionBundleError(f"{context}.wordBank must not be empty")
        return {
            "instruction": _text(
                record.get("instructionText"), f"{context}.instructionText"
            ),
            "passage": _text(record.get("passageText"), f"{context}.passageText"),
            "sourceNote": _text(
                record.get("sourceNote"), f"{context}.sourceNote", allow_empty=True
            ),
            "blanks": _string_array(record.get("blanks"), f"{context}.blanks"),
            "wordBank": word_bank,
        }
    if question_format == "written":
        return {
            "question": _text(record.get("questionText"), f"{context}.questionText"),
            "referenceText": _text(
                record.get("referenceText"),
                f"{context}.referenceText",
                allow_empty=True,
            ),
            "characterLimit": _integer(
                record.get("characterLimit"), f"{context}.characterLimit"
            ),
            "characterLimitKind": _text(
                record.get("characterLimitKind"), f"{context}.characterLimitKind"
            ),
            "modelAnswer": _text(
                record.get("modelAnswer"), f"{context}.modelAnswer"
            ),
            "modelAnswerCharacterCount": _integer(
                record.get("modelAnswerCharacterCount"),
                f"{context}.modelAnswerCharacterCount",
            ),
        }
    raise ProductionBundleError(f"{context}.listingKind is unsupported: {question_format}")


def _project_question(value: Any, index: int) -> dict[str, Any]:
    context = f"questions[{index}]"
    record = _object(value, context)
    if record.get("schemaVersion") != RAW_QUESTION_SCHEMA_VERSION:
        raise ProductionBundleError(f"{context} uses an unsupported schema")
    extraction = _object(record.get("extraction"), f"{context}.extraction")
    if extraction.get("status") != "parsed":
        raise ProductionBundleError(f"{context} was not parsed successfully")
    if _array(extraction.get("warnings"), f"{context}.extraction.warnings"):
        raise ProductionBundleError(f"{context} has extraction warnings")
    if record.get("explanationCaptured") is not False:
        raise ProductionBundleError(f"{context} may contain a provider explanation")

    question_format = _text(record.get("listingKind"), f"{context}.listingKind")
    raw_id = _text(record.get("rawQuestionId"), f"{context}.rawQuestionId")
    labels = _string_array(record.get("labels"), f"{context}.labels")
    explicit_subject_id = record.get("subjectId")
    if explicit_subject_id is not None:
        raw_subject_id = _text(explicit_subject_id, f"{context}.subjectId")
        subject_id = canonical_subject_id(raw_subject_id)
        if subject_id not in QUESTION_SUBJECT_LABELS_BY_ID:
            raise ProductionBundleError(
                f"{context}.subjectId is not in the supported subject catalog"
            )
    else:
        subject_id = next(
            (
                canonical_subject_id(label)
                for label in labels
                if canonical_subject_id(label) in QUESTION_SUBJECT_LABELS_BY_ID
            ),
            "",
        )
        if not subject_id:
            raise ProductionBundleError(
                f"{context} has no recognizable administrative-scrivener subject label"
            )
    provider_updated_at = record.get("providerUpdatedAt")
    if provider_updated_at is not None:
        provider_updated_at = _text(
            provider_updated_at, f"{context}.providerUpdatedAt"
        )
    projected = {
        "id": raw_id,
        "source": {
            "provider": _text(record.get("sourceId"), f"{context}.sourceId"),
            "externalQuestionId": _text(
                record.get("externalQuestionId"), f"{context}.externalQuestionId"
            ),
            "url": _text(record.get("sourceUrl"), f"{context}.sourceUrl"),
            "snapshotId": _text(
                record.get("sourceSnapshotId"), f"{context}.sourceSnapshotId"
            ),
            "bodySha256": _text(
                record.get("sourceBodySha256"), f"{context}.sourceBodySha256"
            ),
            "providerUpdatedAt": provider_updated_at,
            "parserVersion": _text(
                record.get("parserVersion"), f"{context}.parserVersion"
            ),
            "providerExplanationCaptured": False,
        },
        "exam": {
            "year": _integer(record.get("examYear"), f"{context}.examYear"),
            "era": _text(record.get("eraYear"), f"{context}.eraYear"),
            "number": _integer(
                record.get("questionNumber"), f"{context}.questionNumber"
            ),
        },
        "title": _text(record.get("title"), f"{context}.title"),
        "subjectId": subject_id,
        "labels": labels,
        "format": question_format,
        "amended": _boolean(record.get("isAmended"), f"{context}.isAmended"),
        "content": _project_question_content(record, question_format, context),
        "task": _project_task(record.get("task"), f"{context}.task"),
        "answer": _project_answer(record.get("answer"), question_format, f"{context}.answer"),
    }
    projected.update(_state_fields(record, context))
    return projected


def _validate_question_corpus(
    questions: list[dict[str, Any]], expectations: BundleExpectations
) -> None:
    if (
        expectations.question_count is not None
        and len(questions) != expectations.question_count
    ):
        raise ProductionBundleError(
            f"expected {expectations.question_count} questions, got {len(questions)}"
        )
    ids = [question["id"] for question in questions]
    if len(ids) != len(set(ids)):
        raise ProductionBundleError("question ids are not unique")
    if expectations.question_subject_counts is not None:
        actual_subject_counts = Counter(
            question["subjectId"] for question in questions
        )
        expected_subject_counts = dict(expectations.question_subject_counts)
        if actual_subject_counts != expected_subject_counts:
            raise ProductionBundleError(
                "question subject counts differ from the release manifest: "
                f"expected {expected_subject_counts}, got {dict(actual_subject_counts)}"
            )
    if expectations.question_format_counts is not None:
        actual_format_counts = Counter(question["format"] for question in questions)
        expected_format_counts = dict(expectations.question_format_counts)
        if actual_format_counts != expected_format_counts:
            raise ProductionBundleError(
                "question format counts differ from the release manifest: "
                f"expected {expected_format_counts}, got {dict(actual_format_counts)}"
            )
    if expectations.target_years is None:
        return
    by_year_format: dict[int, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for question in questions:
        if question["subjectId"] != "administrative-law":
            continue
        year = question["exam"]["year"]
        question_format = question["format"]
        if question_format not in TARGET_QUESTION_NUMBERS:
            raise ProductionBundleError(f"unsupported question format: {question_format}")
        by_year_format[year][question_format].add(question["exam"]["number"])
    if set(by_year_format) != set(expectations.target_years):
        raise ProductionBundleError("question corpus does not contain exactly the target years")
    for year in expectations.target_years:
        for question_format, expected_numbers in TARGET_QUESTION_NUMBERS.items():
            if by_year_format[year].get(question_format, set()) != expected_numbers:
                raise ProductionBundleError(
                    f"{year} {question_format} question numbers are incomplete"
                )


def _project_reconciled_answer(
    value: Any,
    question: Mapping[str, Any],
    context: str,
) -> Any:
    if value is None:
        return None
    question_format = question["format"]
    if question_format == "written":
        return _text(value, context)
    if question_format == "regular":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ProductionBundleError(f"{context} must be an option string or integer")
        return _text(value, context) if isinstance(value, str) else value
    if question_format == "multiple_blank":
        answer = _object(value, context)
        expected_labels = set(question["content"]["blanks"])
        if set(answer) != expected_labels:
            raise ProductionBundleError(
                f"{context} keys do not match the question's blank labels"
            )
        projected: dict[str, int | str] = {}
        for label in question["content"]["blanks"]:
            option = answer[label]
            if isinstance(option, bool) or not isinstance(option, (int, str)):
                raise ProductionBundleError(
                    f"{context}[{label}] must be an option string or integer"
                )
            projected[label] = (
                _text(option, f"{context}[{label}]")
                if isinstance(option, str)
                else option
            )
        return projected
    raise ProductionBundleError(f"{context} has an unsupported question format")


def _project_reconciliation(
    document: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    if document.get("schemaVersion") != RECONCILIATION_SCHEMA_VERSION:
        raise ProductionBundleError("unsupported answer reconciliation schema")
    source_results = _array(document.get("results"), "reconciliation.results")
    question_by_id = {question["id"]: question for question in questions}
    result_by_id: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    for index, value in enumerate(source_results):
        context = f"reconciliation.results[{index}]"
        result = _object(value, context)
        question_id = _text(result.get("rawQuestionId"), f"{context}.rawQuestionId")
        if question_id in result_by_id:
            raise ProductionBundleError(f"duplicate reconciliation id: {question_id}")
        question = question_by_id.get(question_id)
        if question is None:
            raise ProductionBundleError(f"reconciliation has unknown question: {question_id}")
        status = _text(result.get("status"), f"{context}.status")
        if status not in RECONCILIATION_STATUSES:
            raise ProductionBundleError(f"{context}.status is unsupported: {status}")
        exam_year = _integer(result.get("examYear"), f"{context}.examYear")
        number = _integer(result.get("questionNumber"), f"{context}.questionNumber")
        question_format = _text(result.get("format"), f"{context}.format")
        if (
            exam_year != question["exam"]["year"]
            or number != question["exam"]["number"]
            or question_format != question["format"]
        ):
            raise ProductionBundleError(f"{context} does not match its question")
        projected = {
            "questionId": question_id,
            "examYear": exam_year,
            "questionNumber": number,
            "format": question_format,
            "providerAnswer": _project_reconciled_answer(
                result.get("providerAnswer"), question, f"{context}.providerAnswer"
            ),
            "officialAnswer": _project_reconciled_answer(
                result.get("officialAnswer"), question, f"{context}.officialAnswer"
            ),
            "status": status,
            "reason": _text(result.get("reason"), f"{context}.reason"),
        }
        result_by_id[question_id] = projected
        status_counts[status] += 1
    if set(result_by_id) != set(question_by_id):
        raise ProductionBundleError("reconciliation does not cover every question exactly once")

    summary = _object(document.get("summary"), "reconciliation.summary")
    if summary.get("total") != len(source_results):
        raise ProductionBundleError("reconciliation summary total is inconsistent")
    declared_counts = _object(
        summary.get("statusCounts"), "reconciliation.summary.statusCounts"
    )
    for status in RECONCILIATION_STATUSES:
        if declared_counts.get(status) != status_counts[status]:
            raise ProductionBundleError(
                f"reconciliation summary count for {status} is inconsistent"
            )
    ordered = [result_by_id[question["id"]] for question in questions]
    return ordered, status_counts


def _project_written_origin(value: Any, context: str) -> dict[str, str]:
    origin = _object(value, context)
    return {
        "questionId": _text(origin.get("questionId"), f"{context}.questionId"),
        "label": _text(origin.get("label"), f"{context}.label"),
        "promptSummary": _text(
            origin.get("promptSummary"), f"{context}.promptSummary"
        ),
        "officialQuestionUrl": _text(
            origin.get("officialQuestionUrl"), f"{context}.officialQuestionUrl"
        ),
    }


def _project_cross_field_comparison(value: Any, context: str) -> dict[str, str]:
    comparison = _object(value, context)
    projected = {
        "id": _text(comparison.get("id"), f"{context}.id"),
        "comparedCategory": _text(
            comparison.get("comparedCategory"), f"{context}.comparedCategory"
        ),
        "comparedTopic": _text(
            comparison.get("comparedTopic"), f"{context}.comparedTopic"
        ),
        "title": _text(comparison.get("title"), f"{context}.title"),
        "explanation": _text(
            comparison.get("explanation"), f"{context}.explanation"
        ),
        "memoryCue": _text(
            comparison.get("memoryCue"), f"{context}.memoryCue"
        ),
    }
    if "relatedCardId" in comparison:
        projected["relatedCardId"] = _text(
            comparison.get("relatedCardId"), f"{context}.relatedCardId"
        )
    return projected


def _project_explanation_card(value: Any, index: int) -> dict[str, Any]:
    context = f"explanationCards[{index}]"
    card = _object(value, context)
    variants = _object(card.get("variants"), f"{context}.variants")
    variant_fields = set(variants)
    if variant_fields != EXPLANATION_VARIANT_FIELDS:
        missing = sorted(EXPLANATION_VARIANT_FIELDS - variant_fields)
        unexpected = sorted(variant_fields - EXPLANATION_VARIANT_FIELDS)
        raise ProductionBundleError(
            f"{context}.variants must contain exactly the supported fields "
            f"(missing={missing}, unexpected={unexpected})"
        )
    explanations = _object(card.get("explanations"), f"{context}.explanations")
    deep_dive = _object(explanations.get("deepDive"), f"{context}.explanations.deepDive")
    projected: dict[str, Any] = {
        "id": _text(card.get("id"), f"{context}.id"),
        "subjectId": canonical_subject_id(
            _text(card.get("subjectId"), f"{context}.subjectId")
        ),
        "category": _text(card.get("category"), f"{context}.category"),
        "topic": _text(card.get("topic"), f"{context}.topic"),
        "subtopic": _text(card.get("subtopic"), f"{context}.subtopic"),
        "clusterId": _text(card.get("clusterId"), f"{context}.clusterId"),
        "variants": {
            "a": _text(variants.get("a"), f"{context}.variants.a"),
            "b": _text(variants.get("b"), f"{context}.variants.b"),
            "bCasual": _text(
                variants.get("bCasual"), f"{context}.variants.bCasual"
            ),
            "bCasualStyle": _text(
                variants.get("bCasualStyle"), f"{context}.variants.bCasualStyle"
            ),
            "c": _text(variants.get("c"), f"{context}.variants.c"),
        },
        "correct": _boolean(card.get("correct"), f"{context}.correct"),
        "correction": _text(card.get("correction"), f"{context}.correction"),
        "memoryPoint": _text(card.get("memoryPoint"), f"{context}.memoryPoint"),
        "explanations": {
            "normal": _text(
                explanations.get("normal"), f"{context}.explanations.normal"
            ),
            "deepDive": {
                "background": _text(
                    deep_dive.get("background"),
                    f"{context}.explanations.deepDive.background",
                ),
                "trap": _text(
                    deep_dive.get("trap"), f"{context}.explanations.deepDive.trap"
                ),
                "example": _text(
                    deep_dive.get("example"),
                    f"{context}.explanations.deepDive.example",
                ),
            },
            "commonSense": _text(
                explanations.get("commonSense"),
                f"{context}.explanations.commonSense",
            ),
        },
        "legalBasis": [],
        "sourceRefs": [],
        "relatedPastQuestions": [],
        "crossFieldComparisons": [],
        "review": {},
    }
    if "frequency" in card:
        frequency = _object(card.get("frequency"), f"{context}.frequency")
        projected["frequency"] = {
            "label": _text(frequency.get("label"), f"{context}.frequency.label"),
            "occurrences": _integer(
                frequency.get("occurrences"), f"{context}.frequency.occurrences"
            ),
            "yearCount": _integer(
                frequency.get("yearCount"), f"{context}.frequency.yearCount"
            ),
            "recentOccurrences": _integer(
                frequency.get("recentOccurrences"),
                f"{context}.frequency.recentOccurrences",
            ),
            "archiveOccurrences": _integer(
                frequency.get("archiveOccurrences"),
                f"{context}.frequency.archiveOccurrences",
            ),
            "scope": _text(frequency.get("scope"), f"{context}.frequency.scope"),
            "basis": _text(frequency.get("basis"), f"{context}.frequency.basis"),
        }
    for basis_index, basis_value in enumerate(
        _array(card.get("legalBasis"), f"{context}.legalBasis")
    ):
        basis_context = f"{context}.legalBasis[{basis_index}]"
        basis = _object(basis_value, basis_context)
        projected["legalBasis"].append(
            {
                "label": _text(basis.get("label"), f"{basis_context}.label"),
                "url": _text(basis.get("url"), f"{basis_context}.url"),
            }
        )
    for ref_index, ref_value in enumerate(
        _array(card.get("sourceRefs"), f"{context}.sourceRefs")
    ):
        ref_context = f"{context}.sourceRefs[{ref_index}]"
        ref = _object(ref_value, ref_context)
        projected_ref: dict[str, Any] = {
            "rawId": _text(ref.get("rawId"), f"{ref_context}.rawId"),
            "relationship": _text(
                ref.get("relationship"), f"{ref_context}.relationship"
            ),
        }
        if "choiceNumber" in ref:
            projected_ref["choiceNumber"] = _choice_identifier(
                ref.get("choiceNumber"), f"{ref_context}.choiceNumber"
            )
        projected["sourceRefs"].append(projected_ref)
    for related_index, related_value in enumerate(
        _array(card.get("relatedPastQuestions"), f"{context}.relatedPastQuestions")
    ):
        related_context = f"{context}.relatedPastQuestions[{related_index}]"
        related = _object(related_value, related_context)
        projected["relatedPastQuestions"].append(
            {
                "choiceId": _text(
                    related.get("choiceId"), f"{related_context}.choiceId"
                ),
                "relation": _text(
                    related.get("relation"), f"{related_context}.relation"
                ),
            }
        )
    for comparison_index, comparison_value in enumerate(
        _array(card.get("crossFieldComparisons", []), f"{context}.crossFieldComparisons")
    ):
        comparison_context = (
            f"{context}.crossFieldComparisons[{comparison_index}]"
        )
        projected["crossFieldComparisons"].append(
            _project_cross_field_comparison(comparison_value, comparison_context)
        )
    review = _object(card.get("review"), f"{context}.review")
    projected["review"] = {
        "currentLawStatus": _text(
            review.get("currentLawStatus"), f"{context}.review.currentLawStatus"
        ),
        "humanReview": _text(
            review.get("humanReview"), f"{context}.review.humanReview"
        ),
        **_state_fields(review, f"{context}.review"),
    }
    if "derivedFromWritten" in card:
        projected["derivedFromWritten"] = _project_written_origin(
            card["derivedFromWritten"], f"{context}.derivedFromWritten"
        )
    projected.update(_state_fields(card, context))
    return projected


def _project_explanation_cards(
    document: Mapping[str, Any], expectations: BundleExpectations
) -> tuple[list[dict[str, Any]], Mapping[str, Any]]:
    if document.get("schemaVersion") != EXPLANATION_CARD_SCHEMA_VERSION:
        raise ProductionBundleError("unsupported explanation-card schema")
    meta = _object(document.get("meta"), "explanationCards.meta")
    cards = [
        _project_explanation_card(value, index)
        for index, value in enumerate(
            _array(document.get("items"), "explanationCards.items")
        )
    ]
    if len(cards) != expectations.explanation_card_count:
        raise ProductionBundleError(
            f"expected {expectations.explanation_card_count} explanation cards, got {len(cards)}"
        )
    ids = [card["id"] for card in cards]
    if len(ids) != len(set(ids)):
        raise ProductionBundleError("explanation-card ids are not unique")
    card_ids = set(ids)
    for card in cards:
        comparison_ids = [
            comparison["id"] for comparison in card["crossFieldComparisons"]
        ]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ProductionBundleError(
                f"cross-field comparison ids are not unique on {card['id']}"
            )
        unknown_related_cards = {
            comparison["relatedCardId"]
            for comparison in card["crossFieldComparisons"]
            if "relatedCardId" in comparison
        } - card_ids
        if unknown_related_cards:
            raise ProductionBundleError(
                f"cross-field comparisons on {card['id']} reference unknown cards: "
                + ", ".join(sorted(unknown_related_cards))
            )

    subjects: list[dict[str, str]] = []
    subject_by_id: dict[str, str] = {}
    for subject_index, value in enumerate(
        _array(meta.get("subjects"), "explanationCards.meta.subjects")
    ):
        context = f"explanationCards.meta.subjects[{subject_index}]"
        subject = _object(value, context)
        subject_id = canonical_subject_id(_text(subject.get("id"), f"{context}.id"))
        if subject_id not in QUESTION_SUBJECT_LABELS_BY_ID:
            raise ProductionBundleError(f"{context}.id is not supported")
        if subject_id in subject_by_id:
            raise ProductionBundleError(f"duplicate subject id: {subject_id}")
        label = _text(subject.get("label"), f"{context}.label")
        subject_by_id[subject_id] = label
        subjects.append({"id": subject_id, "label": label})
    if not subjects:
        raise ProductionBundleError("explanationCards.meta.subjects must not be empty")
    for card in cards:
        subject_id = card["subjectId"]
        if subject_id not in subject_by_id:
            raise ProductionBundleError(
                f"card {card['id']} has unknown subjectId: {subject_id}"
            )
        if card["category"] != subject_by_id[subject_id]:
            raise ProductionBundleError(
                f"card {card['id']} category does not match its subject label"
            )
    projected_meta = dict(meta)
    projected_meta["subjects"] = subjects
    return cards, projected_meta


def _project_study_decks(
    document: Mapping[str, Any],
    cards: Sequence[Mapping[str, Any]],
    card_meta: Mapping[str, Any],
    expectations: BundleExpectations,
) -> list[dict[str, Any]]:
    source_decks = _array(document.get("studyDecks"), "explanationCards.studyDecks")
    if len(source_decks) != expectations.study_deck_count:
        raise ProductionBundleError(
            f"expected {expectations.study_deck_count} study decks, got {len(source_decks)}"
        )
    card_ids = {card["id"] for card in cards}
    card_by_id = {card["id"]: card for card in cards}
    projected: list[dict[str, Any]] = []
    seen_deck_ids: set[str] = set()
    covered_card_ids: set[str] = set()
    expected_law_as_of = _text(
        card_meta.get("examLawAsOf"), "explanationCards.meta.examLawAsOf"
    )
    for index, value in enumerate(source_decks):
        context = f"explanationCards.studyDecks[{index}]"
        deck = _object(value, context)
        deck_id = _text(deck.get("id"), f"{context}.id")
        if deck_id in seen_deck_ids:
            raise ProductionBundleError(f"duplicate study-deck id: {deck_id}")
        seen_deck_ids.add(deck_id)
        visibility = _text(deck.get("visibility"), f"{context}.visibility")
        if visibility != STUDY_DECK_VISIBILITY:
            raise ProductionBundleError(
                f"{context}.visibility must be {STUDY_DECK_VISIBILITY!r}"
            )
        law_as_of = _text(deck.get("lawAsOf"), f"{context}.lawAsOf")
        if law_as_of != expected_law_as_of:
            raise ProductionBundleError(
                f"{context}.lawAsOf must match explanationCards.meta.examLawAsOf"
            )
        ordered_card_ids = _string_array(deck.get("cardIds"), f"{context}.cardIds")
        if not ordered_card_ids:
            raise ProductionBundleError(f"{context}.cardIds must not be empty")
        if len(ordered_card_ids) != len(set(ordered_card_ids)):
            raise ProductionBundleError(f"{context}.cardIds are not unique")
        unknown = set(ordered_card_ids) - card_ids
        if unknown:
            raise ProductionBundleError(
                f"{context}.cardIds contain unknown cards: {', '.join(sorted(unknown))}"
            )
        covered_card_ids.update(ordered_card_ids)
        projected.append(
            {
                "id": deck_id,
                "title": _text(deck.get("title"), f"{context}.title"),
                "description": _text(
                    deck.get("description"), f"{context}.description"
                ),
                "visibility": visibility,
                "lawAsOf": law_as_of,
                "cardCount": len(ordered_card_ids),
                "cardIds": ordered_card_ids,
                "subjectIds": sorted(
                    {card_by_id[card_id]["subjectId"] for card_id in ordered_card_ids}
                ),
            }
        )
    if covered_card_ids != card_ids:
        missing = sorted(card_ids - covered_card_ids)
        raise ProductionBundleError(
            "explanation cards are missing from study decks: " + ", ".join(missing)
        )
    return projected


def _validate_written_origins(
    cards: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
) -> None:
    questions_by_id = {question["id"]: question for question in questions}
    for card in cards:
        origin = card.get("derivedFromWritten")
        if origin is None:
            continue
        question = questions_by_id.get(origin["questionId"])
        if question is None:
            raise ProductionBundleError(
                f"explanation card {card['id']} has an unknown written origin"
            )
        if question["format"] != "written":
            raise ProductionBundleError(
                f"explanation card {card['id']} origin is not a written question"
            )


def _project_related_question_evidence(
    document: Mapping[str, Any],
    cards: Sequence[Mapping[str, Any]],
    expectations: BundleExpectations,
) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != RELATED_QUESTION_SOURCE_SCHEMA_VERSION:
        raise ProductionBundleError("unsupported related-question source schema")

    referenced_ids = {
        related["choiceId"]
        for card in cards
        for related in card["relatedPastQuestions"]
    }
    referenced_question_ids = {
        ref["rawId"] for card in cards for ref in card["sourceRefs"]
    }
    referenced_source_choices = {
        (ref["rawId"], str(ref["choiceNumber"]))
        for card in cards
        for ref in card["sourceRefs"]
        if "choiceNumber" in ref
    }
    records_by_id: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(_array(document.get("records"), "relatedSource.records")):
        context = f"relatedSource.records[{index}]"
        record = _object(value, context)
        question_id = _text(record.get("rawId"), f"{context}.rawId")
        if question_id in records_by_id:
            raise ProductionBundleError(f"duplicate related-source question: {question_id}")
        records_by_id[question_id] = record
    missing_questions = referenced_question_ids - set(records_by_id)
    if missing_questions:
        raise ProductionBundleError(
            "card sourceRefs are missing related-source records for: "
            + ", ".join(sorted(missing_questions))
        )

    evidence_by_id: dict[str, dict[str, Any]] = {}
    source_choice_ids: set[str] = set()
    source_choice_keys: set[tuple[str, str]] = set()
    for index, value in enumerate(_array(document.get("choices"), "relatedSource.choices")):
        context = f"relatedSource.choices[{index}]"
        choice = _object(value, context)
        choice_id = _text(choice.get("choiceId"), f"{context}.choiceId")
        if choice_id in source_choice_ids:
            raise ProductionBundleError(f"duplicate related-source choice: {choice_id}")
        source_choice_ids.add(choice_id)
        question_id = _text(
            choice.get("rawQuestionId"), f"{context}.rawQuestionId"
        )
        choice_number = _choice_identifier(
            choice.get("choiceLabel"), f"{context}.choiceLabel"
        )
        source_choice_keys.add((question_id, str(choice_number)))
        if choice_id not in referenced_ids:
            continue
        record = records_by_id.get(question_id)
        if record is None:
            raise ProductionBundleError(
                f"{context} refers to missing related-source question {question_id}"
            )
        evidence: dict[str, Any] = {
            "choiceId": choice_id,
            "questionId": question_id,
            "statementText": _text(
                choice.get("officialOriginalText"),
                f"{context}.officialOriginalText",
            ),
            "examYear": _integer(record.get("examYear"), f"{context}.examYear"),
            "eraYear": _text(record.get("eraYear"), f"{context}.eraYear"),
            "questionNumber": _integer(
                record.get("questionNumber"), f"{context}.questionNumber"
            ),
            "choiceNumber": choice_number,
            "historicalTruth": _boolean(
                choice.get("examEvaluation"), f"{context}.examEvaluation"
            ),
            "currentTruth": _boolean(
                choice.get("currentEvaluation"), f"{context}.currentEvaluation"
            ),
            "currentLawAsOf": _text(
                choice.get("currentLawAsOf"), f"{context}.currentLawAsOf"
            ),
            "sourceUrl": _text(
                record.get("officialQuestionUrl"), f"{context}.officialQuestionUrl"
            ),
            "textVersion": _text(
                choice.get("textVersion"), f"{context}.textVersion"
            ),
            "modified": _boolean(
                choice.get("isModified"), f"{context}.isModified"
            ),
            "verification": _text(
                choice.get("verification"), f"{context}.verification"
            ),
        }
        for optional_field, output_field in (
            ("contextSummary", "contextSummary"),
            ("scopeLabel", "scopeLabel"),
        ):
            if optional_field in choice:
                evidence[output_field] = _text(
                    choice[optional_field], f"{context}.{optional_field}"
                )
        evidence.update(_state_fields(record, f"{context}.record"))
        evidence.update(_state_fields(choice, context))
        evidence_by_id[choice_id] = evidence

    missing_source_choices = referenced_source_choices - source_choice_keys
    if missing_source_choices:
        formatted = [f"{question_id}:{choice}" for question_id, choice in sorted(missing_source_choices)]
        raise ProductionBundleError(
            "card sourceRefs are missing related-source choices for: "
            + ", ".join(formatted)
        )

    if set(evidence_by_id) != referenced_ids:
        missing = sorted(referenced_ids - set(evidence_by_id))
        raise ProductionBundleError(
            "related-question evidence is missing for: " + ", ".join(missing)
        )
    evidence = [evidence_by_id[choice_id] for choice_id in sorted(referenced_ids)]
    if len(evidence) != expectations.related_question_evidence_count:
        raise ProductionBundleError(
            "expected "
            f"{expectations.related_question_evidence_count} related-question excerpts, "
            f"got {len(evidence)}"
        )
    return evidence


def _project_citation(value: Any, context: str) -> dict[str, str]:
    citation = _object(value, context)
    return {
        "citationType": _text(citation.get("citationType"), f"{context}.citationType"),
        "title": _text(citation.get("title"), f"{context}.title"),
        "url": _text(citation.get("url"), f"{context}.url"),
        "locator": _text(citation.get("locator"), f"{context}.locator"),
        "relevance": _text(citation.get("relevance"), f"{context}.relevance"),
    }


def _project_claude_reviews(
    responses: Sequence[Mapping[str, Any]], expectations: BundleExpectations
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, int]]]:
    projected: list[dict[str, Any]] = []
    response_info: dict[str, tuple[str, int]] = {}
    seen_candidates: set[str] = set()
    for response_index, value in enumerate(responses):
        context = f"claudeResponses[{response_index}]"
        response = _object(value, context)
        if response.get("schemaVersion") != LEGACY_FABLE_RESPONSE_SCHEMA_VERSION:
            raise ProductionBundleError(f"{context} is not a legacy v2 response")
        batch_id = _text(response.get("batchId"), f"{context}.batchId")
        legal_as_of = _text(response.get("legalAsOf"), f"{context}.legalAsOf")
        if batch_id in response_info:
            raise ProductionBundleError(f"duplicate Claude response batch: {batch_id}")
        items = _array(response.get("items"), f"{context}.items")
        response_info[batch_id] = (legal_as_of, len(items))
        for item_index, item_value in enumerate(items):
            item_context = f"{context}.items[{item_index}]"
            item = _object(item_value, item_context)
            candidate_id = _text(item.get("candidateId"), f"{item_context}.candidateId")
            if candidate_id in seen_candidates:
                raise ProductionBundleError(
                    f"duplicate Claude-reviewed candidate: {candidate_id}"
                )
            seen_candidates.add(candidate_id)
            citations = [
                _project_citation(citation, f"{item_context}.citationCandidates[{index}]")
                for index, citation in enumerate(
                    _array(
                        item.get("citationCandidates"),
                        f"{item_context}.citationCandidates",
                    )
                )
            ]
            projected.append(
                {
                    "batchId": batch_id,
                    "legalAsOf": legal_as_of,
                    "candidateId": candidate_id,
                    "currentLawStatus": _text(
                        item.get("currentLawStatus"),
                        f"{item_context}.currentLawStatus",
                    ),
                    "currentTruth": _boolean(
                        item.get("currentTruth"), f"{item_context}.currentTruth"
                    ),
                    "legalReviewStatus": _text(
                        item.get("legalReviewStatus"),
                        f"{item_context}.legalReviewStatus",
                    ),
                    "relationNotes": _string_array(
                        item.get("relationNotes"), f"{item_context}.relationNotes"
                    ),
                    "citationCandidates": citations,
                    "risks": _string_array(item.get("risks"), f"{item_context}.risks"),
                    "reviewed": _boolean(
                        item.get("reviewed"), f"{item_context}.reviewed"
                    ),
                    "publishable": _boolean(
                        item.get("publishable"), f"{item_context}.publishable"
                    ),
                }
            )
    if len(projected) != expectations.claude_review_count:
        raise ProductionBundleError(
            f"expected {expectations.claude_review_count} Claude reviews, got {len(projected)}"
        )
    return projected, response_info


def _project_claude_runs(
    runs: Sequence[Mapping[str, Any]],
    response_info: Mapping[str, tuple[str, int]],
    expectations: BundleExpectations,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    completed_batches: set[str] = set()
    for run_index, value in enumerate(runs):
        context = f"claudeRuns[{run_index}]"
        run = _object(value, context)
        if run.get("schemaVersion") not in LEGACY_FABLE_RUN_SCHEMA_VERSIONS:
            raise ProductionBundleError(f"{context} is not a supported legacy run")
        run_id = _text(run.get("runId"), f"{context}.runId")
        if run_id in seen_run_ids:
            raise ProductionBundleError(f"duplicate Claude run id: {run_id}")
        seen_run_ids.add(run_id)
        batch_id = _text(run.get("batchId"), f"{context}.batchId")
        item_count = _integer(run.get("itemCount"), f"{context}.itemCount")
        target_value = run.get("targetLegalAsOf")
        target_legal_as_of = (
            None
            if target_value is None
            else _text(target_value, f"{context}.targetLegalAsOf")
        )
        status = _text(run.get("status"), f"{context}.status")
        command = _object(run.get("command"), f"{context}.command")
        if command.get("safeMode") is not True or command.get("sessionPersistence") is not False:
            raise ProductionBundleError(f"{context} was not run with the required safety gates")
        if command.get("modelRequested") != "fable":
            raise ProductionBundleError(f"{context} did not request Claude Fable")
        if set(_string_array(command.get("tools"), f"{context}.command.tools")) != {
            "WebSearch",
            "WebFetch",
        }:
            raise ProductionBundleError(f"{context} used unexpected tools")

        claude_value = run.get("claude")
        models: list[str] = []
        if claude_value is not None:
            claude = _object(claude_value, f"{context}.claude")
            usage_value = claude.get("modelUsage")
            if usage_value is not None:
                model_usage = _object(usage_value, f"{context}.claude.modelUsage")
                models = sorted(
                    _text(model, f"{context}.claude.modelUsage key")
                    for model in model_usage
                )

        error_value = run.get("error")
        if status == "completed":
            if error_value is not None:
                raise ProductionBundleError(f"{context} completed with an error object")
            error_kind = None
            if batch_id in completed_batches:
                raise ProductionBundleError(
                    f"duplicate completed Claude run for batch: {batch_id}"
                )
            completed_batches.add(batch_id)
            response_details = response_info.get(batch_id)
            if response_details is None:
                raise ProductionBundleError(f"{context} has no bundled response")
            legal_as_of, response_item_count = response_details
            if item_count != response_item_count:
                raise ProductionBundleError(
                    f"{context} item count does not match its response"
                )
            if target_legal_as_of != legal_as_of:
                raise ProductionBundleError(
                    f"{context} legal date does not match its response"
                )
            process = _object(run.get("process"), f"{context}.process")
            if process.get("returnCode") != 0:
                raise ProductionBundleError(f"{context} process was not successful")
            claude = _object(claude_value, f"{context}.claude")
            if (
                claude.get("isError") is not False
                or claude.get("terminalReason") != "completed"
            ):
                raise ProductionBundleError(
                    f"{context} Claude result was not successful"
                )
            response = _object(run.get("response"), f"{context}.response")
            if response.get("itemCount") != item_count:
                raise ProductionBundleError(
                    f"{context} response count is inconsistent"
                )
        else:
            error = _object(error_value, f"{context}.error")
            error_kind = _text(error.get("kind"), f"{context}.error.kind")
            if run.get("response") is not None:
                raise ProductionBundleError(
                    f"{context} unsuccessful run unexpectedly has a response"
                )

        projected.append(
            {
                "runId": run_id,
                "batchId": batch_id,
                "itemCount": item_count,
                "targetLegalAsOf": target_legal_as_of,
                "status": status,
                "startedAt": _text(run.get("startedAt"), f"{context}.startedAt"),
                "finishedAt": _text(run.get("finishedAt"), f"{context}.finishedAt"),
                "elapsedSeconds": _number(
                    run.get("elapsedSeconds"), f"{context}.elapsedSeconds"
                ),
                "modelRequested": "fable",
                "models": models,
                "errorKind": error_kind,
            }
        )
    if len(projected) != expectations.claude_run_count:
        raise ProductionBundleError(
            f"expected {expectations.claude_run_count} Claude runs, got {len(projected)}"
        )
    if completed_batches != set(response_info):
        raise ProductionBundleError("not every Claude response has one completed run")
    return projected


def _project_similarity_member(value: Any, context: str) -> dict[str, Any]:
    member = _object(value, context)
    return {
        "candidateId": _text(member.get("candidateId"), f"{context}.candidateId"),
        "questionId": _text(member.get("rawQuestionId"), f"{context}.rawQuestionId"),
        "examYear": _integer(member.get("examYear"), f"{context}.examYear"),
        "questionNumber": _integer(
            member.get("questionNumber"), f"{context}.questionNumber"
        ),
        "choiceLabel": _text(member.get("choiceLabel"), f"{context}.choiceLabel"),
        "inferredTruth": _boolean(
            member.get("inferredTruth"), f"{context}.inferredTruth"
        ),
        "statementText": _text(
            member.get("statementText"), f"{context}.statementText"
        ),
    }


def _project_score_breakdown(value: Any, context: str) -> dict[str, int | float]:
    breakdown = _object(value, context)
    allowed = (
        "characterBigramIdfJaccard",
        "characterTrigramJaccard",
        "legalConceptIdfJaccard",
        "sharedLegalConceptRarity",
        "legalConceptRarityAdjustedJaccard",
    )
    return {
        field: _number(breakdown.get(field), f"{context}.{field}") for field in allowed
    }


def _project_neighbor_ranks(value: Any, context: str) -> dict[str, int]:
    ranks = _object(value, context)
    return {
        _text(candidate_id, f"{context} key"): _integer(rank, f"{context}[{candidate_id}]")
        for candidate_id, rank in ranks.items()
    }


def _project_similarity_pairs(
    document: Mapping[str, Any],
    question_ids: set[str],
    expectations: BundleExpectations,
) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != SIMILARITY_SCHEMA_VERSION:
        raise ProductionBundleError("unsupported similarity schema")
    source_pairs = _array(document.get("reviewPairs"), "similarity.reviewPairs")
    projected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for pair_index, value in enumerate(source_pairs):
        context = f"similarity.reviewPairs[{pair_index}]"
        pair = _object(value, context)
        pair_id = _text(pair.get("pairId"), f"{context}.pairId")
        if pair_id in seen_ids:
            raise ProductionBundleError(f"duplicate similarity pair: {pair_id}")
        seen_ids.add(pair_id)
        left = _project_similarity_member(pair.get("left"), f"{context}.left")
        right = _project_similarity_member(pair.get("right"), f"{context}.right")
        if left["questionId"] not in question_ids or right["questionId"] not in question_ids:
            raise ProductionBundleError(f"{context} refers to an unknown question")
        if left["questionId"] == right["questionId"]:
            raise ProductionBundleError(f"{context} compares choices in the same question")
        content_digest = _canonical_digest(
            {"pairId": pair_id, "left": left, "right": right}
        )
        projected.append(
            {
                "id": pair_id,
                "left": left,
                "right": right,
                "commonLabels": _string_array(
                    pair.get("commonLabels"), f"{context}.commonLabels"
                ),
                "sharedLegalConcepts": _string_array(
                    pair.get("sharedLegalConcepts"),
                    f"{context}.sharedLegalConcepts",
                ),
                "score": _number(pair.get("score"), f"{context}.score"),
                "reviewScore": _number(
                    pair.get("reviewScore"), f"{context}.reviewScore"
                ),
                "scoreBreakdown": _project_score_breakdown(
                    pair.get("scoreBreakdown"), f"{context}.scoreBreakdown"
                ),
                "tier": _text(pair.get("tier"), f"{context}.tier"),
                "method": _text(pair.get("method"), f"{context}.method"),
                "reasonCodes": _string_array(
                    pair.get("reasonCodes"), f"{context}.reasonCodes"
                ),
                "reasonSummary": _text(
                    pair.get("reasonSummary"), f"{context}.reasonSummary"
                ),
                "reviewed": _boolean(pair.get("reviewed"), f"{context}.reviewed"),
                "publishable": _boolean(
                    pair.get("publishable"), f"{context}.publishable"
                ),
                "selectedByCandidateIds": _string_array(
                    pair.get("selectedByCandidateIds"),
                    f"{context}.selectedByCandidateIds",
                ),
                "neighborRanks": _project_neighbor_ranks(
                    pair.get("neighborRanks"), f"{context}.neighborRanks"
                ),
                "pairContentDigest": content_digest,
            }
        )
    if len(projected) != expectations.similarity_pair_count:
        raise ProductionBundleError(
            f"expected {expectations.similarity_pair_count} similarity pairs, got {len(projected)}"
        )
    return projected


def build_production_bundle(
    questions: Sequence[Mapping[str, Any]],
    reconciliation: Mapping[str, Any],
    explanation_cards: Mapping[str, Any],
    related_question_source: Mapping[str, Any],
    claude_responses: Sequence[Mapping[str, Any]],
    claude_runs: Sequence[Mapping[str, Any]],
    similarity: Mapping[str, Any],
    *,
    generated_at: str | None = None,
    expectations: BundleExpectations | None = None,
    source_digests: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and project all private sources into the concise API schema."""

    expected = expectations or BundleExpectations()
    projected_questions = [
        _project_question(value, index) for index, value in enumerate(questions)
    ]
    projected_questions.sort(
        key=lambda question: (
            question["exam"]["year"],
            question["exam"]["number"],
            question["id"],
        )
    )
    _validate_question_corpus(projected_questions, expected)
    official_checks, official_counts = _project_reconciliation(
        reconciliation, projected_questions
    )
    cards, card_meta = _project_explanation_cards(explanation_cards, expected)
    decks = _project_study_decks(explanation_cards, cards, card_meta, expected)
    _validate_written_origins(cards, projected_questions)
    related_evidence = _project_related_question_evidence(
        related_question_source, cards, expected
    )
    reviews, response_info = _project_claude_reviews(claude_responses, expected)
    runs = _project_claude_runs(claude_runs, response_info, expected)
    similarity_pairs = _project_similarity_pairs(
        similarity, {question["id"] for question in projected_questions}, expected
    )

    question_format_counts = Counter(
        question["format"] for question in projected_questions
    )
    question_subject_counts = Counter(
        question["subjectId"] for question in projected_questions
    )
    subjects = [dict(subject) for subject in card_meta["subjects"]]
    known_subject_ids = {subject["id"] for subject in subjects}
    for subject_id in sorted(question_subject_counts):
        if subject_id in known_subject_ids:
            continue
        subjects.append(
            {"id": subject_id, "label": QUESTION_SUBJECT_LABELS_BY_ID[subject_id]}
        )
        known_subject_ids.add(subject_id)
    claude_run_status_counts = Counter(run["status"] for run in runs)
    digests = dict(source_digests or {})
    if not digests:
        digests = {
            "questions": _canonical_digest(list(questions)),
            "officialAnswerChecks": _canonical_digest(reconciliation),
            "explanationCards": _canonical_digest(explanation_cards),
            "relatedQuestionEvidence": _canonical_digest(related_question_source),
            "claudeResponses": _canonical_digest(list(claude_responses)),
            "claudeRuns": _canonical_digest(list(claude_runs)),
            "similarities": _canonical_digest(similarity),
        }
    required_digest_keys = {
        "questions",
        "officialAnswerChecks",
        "explanationCards",
        "relatedQuestionEvidence",
        "claudeResponses",
        "claudeRuns",
        "similarities",
    }
    if set(digests) != required_digest_keys or not all(
        isinstance(value, str) and len(value) == 64 for value in digests.values()
    ):
        raise ProductionBundleError("sourceDigests are incomplete or invalid")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at or utc_now(),
        "visibility": VISIBILITY,
        "legalAsOf": _text(
            card_meta.get("examLawAsOf"), "explanationCards.meta.examLawAsOf"
        ),
        "targetExam": _text(
            card_meta.get("targetExam"), "explanationCards.meta.targetExam"
        ),
        "subjects": subjects,
        "contentPolicy": {
            "providerExplanationsIncluded": False,
            "publicationApproved": False,
            "similarityDigestCovers": "pairId,left,right",
        },
        "summary": {
            "questionCount": len(projected_questions),
            "questionFormatCounts": dict(sorted(question_format_counts.items())),
            "questionSubjectCounts": dict(sorted(question_subject_counts.items())),
            "officialAnswerStatusCounts": {
                status: official_counts[status] for status in sorted(RECONCILIATION_STATUSES)
            },
            "studyDeckCount": len(decks),
            "explanationCardCount": len(cards),
            "relatedQuestionEvidenceCount": len(related_evidence),
            "claudeReviewCount": len(reviews),
            "claudeRunCount": len(runs),
            "claudeRunStatusCounts": dict(sorted(claude_run_status_counts.items())),
            "similarityPairCount": len(similarity_pairs),
        },
        "sourceDigests": dict(sorted(digests.items())),
        "questions": projected_questions,
        "officialAnswerChecks": official_checks,
        "studyDecks": decks,
        "explanationCards": cards,
        "relatedQuestionEvidence": related_evidence,
        "claudeReviews": reviews,
        "claudeRuns": runs,
        "similarityPairs": similarity_pairs,
    }


def _load_objects(paths: Iterable[Path]) -> list[tuple[Path, Mapping[str, Any]]]:
    loaded: list[tuple[Path, Mapping[str, Any]]] = []
    for path in sorted(paths):
        loaded.append((path, _object(load_json(path), str(path))))
    return loaded


def _select_legacy_responses(
    response_dir: Path,
) -> list[tuple[Path, Mapping[str, Any]]]:
    loaded = _load_objects(response_dir.glob("*.json"))
    return [
        (path, document)
        for path, document in loaded
        if document.get("schemaVersion") == LEGACY_FABLE_RESPONSE_SCHEMA_VERSION
    ]


def _select_legacy_runs(
    log_dir: Path,
) -> list[tuple[Path, Mapping[str, Any]]]:
    loaded = _load_objects(log_dir.glob("*.json"))
    return [
        (path, document)
        for path, document in loaded
        if document.get("schemaVersion") in LEGACY_FABLE_RUN_SCHEMA_VERSIONS
    ]


def _verify_run_response_files(
    responses: Sequence[tuple[Path, Mapping[str, Any]]],
    runs: Sequence[tuple[Path, Mapping[str, Any]]],
) -> None:
    response_by_batch = {
        _text(document.get("batchId"), f"{path}.batchId"): (path, document)
        for path, document in responses
    }
    if len(response_by_batch) != len(responses):
        raise ProductionBundleError("legacy response batch ids are not unique")
    for log_path, run in runs:
        if run.get("status") != "completed":
            continue
        batch_id = _text(run.get("batchId"), f"{log_path}.batchId")
        matched = response_by_batch.get(batch_id)
        if matched is None:
            raise ProductionBundleError(f"{log_path} has no matching response file")
        response_path, _ = matched
        response_meta = _object(run.get("response"), f"{log_path}.response")
        declared_path = _text(
            response_meta.get("path"), f"{log_path}.response.path"
        )
        if Path(declared_path).name != response_path.name:
            raise ProductionBundleError(f"{log_path} names a different response file")
        if response_meta.get("sha256") != _file_digest(response_path):
            raise ProductionBundleError(f"{log_path} response digest does not match")


def build_from_paths(
    *,
    questions_dir: Path,
    reconciliation_path: Path,
    explanation_cards_path: Path,
    related_question_source_path: Path,
    claude_responses_dir: Path,
    claude_logs_dir: Path,
    similarity_path: Path,
    generated_at: str | None = None,
    expectations: BundleExpectations | None = None,
) -> dict[str, Any]:
    """Load real source files, verify provenance, and build the private bundle."""

    question_paths = sorted(questions_dir.rglob("*.json"))
    question_documents = _load_objects(question_paths)
    responses = _select_legacy_responses(claude_responses_dir)
    runs = _select_legacy_runs(claude_logs_dir)
    _verify_run_response_files(responses, runs)

    reconciliation = _object(load_json(reconciliation_path), str(reconciliation_path))
    explanation_cards = _object(
        load_json(explanation_cards_path), str(explanation_cards_path)
    )
    related_question_source = _object(
        load_json(related_question_source_path), str(related_question_source_path)
    )
    similarity = _object(load_json(similarity_path), str(similarity_path))
    source_digests = {
        "questions": _aggregate_file_digest(question_paths, questions_dir),
        "officialAnswerChecks": _file_digest(reconciliation_path),
        "explanationCards": _file_digest(explanation_cards_path),
        "relatedQuestionEvidence": _file_digest(related_question_source_path),
        "claudeResponses": _aggregate_file_digest(
            [path for path, _ in responses], claude_responses_dir
        ),
        "claudeRuns": _aggregate_file_digest(
            [path for path, _ in runs], claude_logs_dir
        ),
        "similarities": _file_digest(similarity_path),
    }
    return build_production_bundle(
        [document for _, document in question_documents],
        reconciliation,
        explanation_cards,
        related_question_source,
        [document for _, document in responses],
        [document for _, document in runs],
        similarity,
        generated_at=generated_at,
        expectations=expectations,
        source_digests=source_digests,
    )


def expectations_from_question_manifest(path: Path) -> BundleExpectations:
    """Read exact question corpus expectations from an all-subject target."""

    document = _object(load_json(path), str(path))
    if document.get("schemaVersion") != "all-subjects-target@1":
        raise ProductionBundleError("unsupported question release manifest schema")
    target = _object(document.get("target"), f"{path}.target")
    question_count = _integer(
        target.get("expectedTotal"), f"{path}.target.expectedTotal"
    )
    years = tuple(
        _integer(value, f"{path}.target.examYears[{index}]")
        for index, value in enumerate(
            _array(target.get("examYears"), f"{path}.target.examYears")
        )
    )
    if not years or len(years) != len(set(years)):
        raise ProductionBundleError("question release manifest years are invalid")

    raw_subject_counts = _object(
        target.get("expectedBySubject"), f"{path}.target.expectedBySubject"
    )
    subject_counts: dict[str, int] = {}
    for raw_subject_id, raw_count in raw_subject_counts.items():
        subject_id = canonical_subject_id(
            _text(raw_subject_id, f"{path}.target.expectedBySubject key")
        )
        if subject_id not in QUESTION_SUBJECT_LABELS_BY_ID:
            raise ProductionBundleError(
                f"question release manifest has unsupported subject: {raw_subject_id}"
            )
        if subject_id in subject_counts:
            raise ProductionBundleError(
                f"question release manifest repeats subject: {subject_id}"
            )
        subject_counts[subject_id] = _integer(
            raw_count, f"{path}.target.expectedBySubject[{raw_subject_id}]"
        )

    raw_format_counts = _object(
        target.get("expectedByFormat"), f"{path}.target.expectedByFormat"
    )
    format_counts: dict[str, int] = {}
    for raw_format, raw_count in raw_format_counts.items():
        question_format = _text(
            raw_format, f"{path}.target.expectedByFormat key"
        )
        if question_format not in TARGET_QUESTION_NUMBERS:
            raise ProductionBundleError(
                f"question release manifest has unsupported format: {question_format}"
            )
        format_counts[question_format] = _integer(
            raw_count, f"{path}.target.expectedByFormat[{question_format}]"
        )

    if sum(subject_counts.values()) != question_count:
        raise ProductionBundleError(
            "question release manifest subject counts do not match expectedTotal"
        )
    if sum(format_counts.values()) != question_count:
        raise ProductionBundleError(
            "question release manifest format counts do not match expectedTotal"
        )
    return BundleExpectations(
        question_count=question_count,
        question_subject_counts=tuple(sorted(subject_counts.items())),
        question_format_counts=tuple(sorted(format_counts.items())),
        target_years=years,
    )


def write_private_bundle(path: Path, bundle: Mapping[str, Any]) -> None:
    """Atomically replace ``path`` with an fsynced mode-0600 JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(bundle, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _assert_private_output(path: Path) -> None:
    private_build_root = (data_root() / "builds").resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(private_build_root)
    except ValueError as error:
        raise ProductionBundleError(
            f"output must stay under the private build root: {private_build_root}"
        ) from error


def _parser() -> argparse.ArgumentParser:
    root = data_root()
    canonical_root = Path(
        os.environ.get("GYOUSEI_CANONICAL_ROOT", root / "canonical")
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions-dir", type=Path, default=root / "extracted")
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=root / "reports" / "answer-reconciliation.json",
    )
    parser.add_argument(
        "--explanation-cards",
        type=Path,
        default=canonical_root / "explanation_cards.json",
    )
    parser.add_argument(
        "--related-question-source",
        type=Path,
        default=canonical_root / "related_question_source.json",
    )
    parser.add_argument(
        "--claude-responses-dir",
        type=Path,
        default=root / "review" / "ai_responses",
    )
    parser.add_argument(
        "--claude-logs-dir", type=Path, default=root / "review" / "logs"
    )
    parser.add_argument(
        "--similarity",
        type=Path,
        default=root / "curation" / "similarity_candidates.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "builds" / "releases" / "gyousei-production.json",
    )
    parser.add_argument(
        "--question-manifest",
        type=Path,
        help=(
            "all-subject target JSON whose exact total, subject, format, and year "
            "counts must match"
        ),
    )
    parser.add_argument("--generated-at", help="fixed ISO timestamp for reproducible builds")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _assert_private_output(args.output)
        expectations = (
            expectations_from_question_manifest(args.question_manifest)
            if args.question_manifest
            else None
        )
        bundle = build_from_paths(
            questions_dir=args.questions_dir,
            reconciliation_path=args.reconciliation,
            explanation_cards_path=args.explanation_cards,
            related_question_source_path=args.related_question_source,
            claude_responses_dir=args.claude_responses_dir,
            claude_logs_dir=args.claude_logs_dir,
            similarity_path=args.similarity,
            generated_at=args.generated_at,
            expectations=expectations,
        )
        write_private_bundle(args.output, bundle)
    except (OSError, json.JSONDecodeError, ProductionBundleError) as error:
        print(f"production bundle failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"output": str(args.output), **bundle["summary"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
