"""Build a private, fail-closed inventory for human question review.

Only ordinary five-choice questions whose task is an unambiguous
``select_true`` or ``select_false`` are split into statement candidates.  Every
other format stays as one original-question review item so that combination,
count, multiple-blank, and written questions are never flattened by accident.

The output contains past-question text and is intended for the private
``gyousei_data/curation`` directory, not for a web root.  Provider explanations
are deliberately excluded through field-by-field projection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "review-candidate-inventory@1"
SAFE_REGULAR_TASKS = {"select_true", "select_false"}


class CandidateBuildError(ValueError):
    """Raised when extracted input cannot satisfy the review contract."""


def _require(record: dict[str, Any], keys: Iterable[str]) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        identifier = record.get("rawQuestionId", "<unknown>")
        raise CandidateBuildError(
            f"{identifier}: missing required fields: {', '.join(missing)}"
        )


def _task_metadata(record: dict[str, Any]) -> dict[str, Any]:
    task = record.get("task")
    if not isinstance(task, dict):
        return {"kind": "unknown", "prompt": "", "confidence": "low"}
    return {
        "kind": str(task.get("kind") or "unknown"),
        "prompt": str(task.get("prompt") or ""),
        "confidence": str(task.get("confidence") or "low"),
    }


def _source_citation(record: dict[str, Any]) -> dict[str, Any]:
    """Project only provenance fields needed to trace the original question."""

    labels = record.get("labels")
    if not isinstance(labels, list):
        labels = []
    return {
        "sourceId": str(record.get("sourceId") or ""),
        "externalQuestionId": str(record.get("externalQuestionId") or ""),
        "sourceUrl": str(record.get("sourceUrl") or ""),
        "title": str(record.get("title") or ""),
        "eraYear": str(record.get("eraYear") or ""),
        "labels": [str(label) for label in labels],
        "isAmended": bool(record.get("isAmended", False)),
        "sourceBodySha256": str(record.get("sourceBodySha256") or ""),
    }


def _choice_projection(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    projected: list[dict[str, str]] = []
    for choice in value:
        if not isinstance(choice, dict):
            projected.append({"label": "", "text": ""})
            continue
        projected.append(
            {
                "label": str(choice.get("label") or ""),
                "text": str(choice.get("text") or ""),
            }
        )
    return projected


def _answer_projection(record: dict[str, Any], format_name: str) -> dict[str, Any]:
    """Copy only known answer fields, never arbitrary provider-owned fields."""

    answer = record.get("answer")
    if not isinstance(answer, dict):
        return {}
    if format_name == "regular":
        return {"kind": str(answer.get("kind") or ""), "value": answer.get("value")}
    if format_name == "multiple_blank":
        values = answer.get("values")
        if not isinstance(values, dict):
            values = {}
        return {
            "kind": str(answer.get("kind") or ""),
            "values": {str(key): value for key, value in values.items()},
        }
    if format_name == "written":
        return {"kind": str(answer.get("kind") or ""), "value": answer.get("value")}
    return {}


def _original_content(record: dict[str, Any], format_name: str) -> dict[str, Any]:
    """Preserve each question format without copying unapproved extra fields."""

    if format_name == "regular":
        return {
            "instructionText": str(record.get("instructionText") or ""),
            "questionText": str(record.get("questionText") or ""),
            "choices": _choice_projection(record.get("choices")),
            "answer": _answer_projection(record, format_name),
        }
    if format_name == "multiple_blank":
        blanks = record.get("blanks")
        if not isinstance(blanks, list):
            blanks = []
        word_bank: list[dict[str, Any]] = []
        source_word_bank = record.get("wordBank")
        if isinstance(source_word_bank, list):
            for option in source_word_bank:
                if not isinstance(option, dict):
                    continue
                word_bank.append(
                    {"number": option.get("number"), "text": str(option.get("text") or "")}
                )
        return {
            "instructionText": str(record.get("instructionText") or ""),
            "passageText": str(record.get("passageText") or ""),
            "sourceNote": str(record.get("sourceNote") or ""),
            "blanks": [str(label) for label in blanks],
            "wordBank": word_bank,
            "answer": _answer_projection(record, format_name),
        }
    if format_name == "written":
        return {
            "questionText": str(record.get("questionText") or ""),
            "referenceText": str(record.get("referenceText") or ""),
            "characterLimit": record.get("characterLimit"),
            "characterLimitKind": record.get("characterLimitKind"),
            "modelAnswer": str(record.get("modelAnswer") or ""),
            "modelAnswerRaw": str(record.get("modelAnswerRaw") or ""),
            "modelAnswerCharacterCount": record.get("modelAnswerCharacterCount"),
            "answer": _answer_projection(record, format_name),
        }
    return {
        "questionText": str(record.get("questionText") or ""),
        "instructionText": str(record.get("instructionText") or ""),
    }


def _base_candidate(
    record: dict[str, Any],
    *,
    candidate_id: str,
    candidate_kind: str,
    decision_reason: str,
) -> dict[str, Any]:
    task = _task_metadata(record)
    return {
        "candidateId": candidate_id,
        "candidateKind": candidate_kind,
        "rawQuestionId": str(record["rawQuestionId"]),
        "sourceSnapshotId": str(record["sourceSnapshotId"]),
        "examYear": record["examYear"],
        "questionNumber": record["questionNumber"],
        "format": str(record.get("listingKind") or "unknown"),
        "task": task["kind"],
        "taskMetadata": task,
        "decisionReason": decision_reason,
        "reviewed": False,
        "publishable": False,
        "sourceCitation": _source_citation(record),
    }


def _unsafe_reason(record: dict[str, Any]) -> str | None:
    """Return why a record must remain whole, or ``None`` when splitting is safe."""

    extraction = record.get("extraction")
    if isinstance(extraction, dict) and extraction.get("status") == "parse_error":
        return "extraction_parse_error_requires_question_level_review"
    if record.get("isWithdrawn") is True:
        return "withdrawn_question_requires_question_level_review"

    format_name = str(record.get("listingKind") or "unknown")
    task = _task_metadata(record)
    task_kind = task["kind"]
    if format_name != "regular":
        return f"format_{format_name}_requires_question_level_review"
    if task_kind not in SAFE_REGULAR_TASKS:
        if task_kind in {"combination", "count", "unknown"}:
            return f"task_{task_kind}_requires_question_level_review"
        return "unsupported_regular_task_requires_question_level_review"
    if task["confidence"] != "high":
        return "regular_task_inference_not_high_confidence"

    choices = _choice_projection(record.get("choices"))
    if len(choices) != 5:
        return "regular_choice_count_not_five"
    labels = [choice["label"] for choice in choices]
    if any(not choice["text"].strip() for choice in choices):
        return "regular_choice_text_missing"
    if any(not label for label in labels) or len(set(labels)) != 5:
        return "regular_choice_labels_invalid"

    answer = record.get("answer")
    if not isinstance(answer, dict) or answer.get("kind") != "option":
        return "regular_answer_kind_not_option"
    answer_value = answer.get("value")
    if isinstance(answer_value, bool) or not isinstance(answer_value, int):
        return "regular_answer_option_invalid"
    if str(answer_value) not in labels:
        return "regular_answer_option_out_of_range"
    return None


def candidates_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Create review items for one extracted question using fail-closed rules."""

    _require(
        record,
        (
            "rawQuestionId",
            "sourceSnapshotId",
            "examYear",
            "questionNumber",
            "listingKind",
            "task",
        ),
    )
    raw_id = str(record["rawQuestionId"])
    format_name = str(record["listingKind"])
    reason = _unsafe_reason(record)
    if reason is not None:
        candidate = _base_candidate(
            record,
            candidate_id=f"{raw_id}:original",
            candidate_kind="original_question",
            decision_reason=reason,
        )
        candidate["originalQuestion"] = _original_content(record, format_name)
        return [candidate]

    task_kind = _task_metadata(record)["kind"]
    answer_value = record["answer"]["value"]
    decision_reason = (
        "single_select_true_answer_allows_choice_truth_inference"
        if task_kind == "select_true"
        else "single_select_false_answer_allows_choice_truth_inference"
    )
    candidates: list[dict[str, Any]] = []
    for choice in _choice_projection(record["choices"]):
        label = choice["label"]
        is_answer = label == str(answer_value)
        inferred_truth = is_answer if task_kind == "select_true" else not is_answer
        candidate = _base_candidate(
            record,
            candidate_id=f"{raw_id}:choice:{label}",
            candidate_kind="choice_proposition",
            decision_reason=decision_reason,
        )
        candidate.update(
            {
                "instructionText": str(record.get("instructionText") or ""),
                "questionText": str(record.get("questionText") or ""),
                "choiceLabel": label,
                "statementText": choice["text"],
                "inferredTruth": inferred_truth,
                "answerOption": answer_value,
            }
        )
        candidates.append(candidate)
    return candidates


def _record_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    def numeric(value: Any) -> tuple[int, Any]:
        try:
            return (0, int(value))
        except (TypeError, ValueError):
            return (1, str(value))

    return (
        numeric(record.get("examYear")),
        numeric(record.get("questionNumber")),
        str(record.get("rawQuestionId") or ""),
    )


def build_inventory(
    records: Iterable[dict[str, Any]], *, source_input: str = ""
) -> dict[str, Any]:
    """Build a deterministic-order private inventory from extracted records."""

    ordered = sorted(records, key=_record_sort_key)
    seen_raw_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []
    question_formats: Counter[str] = Counter()
    for record in ordered:
        if not isinstance(record, dict):
            raise CandidateBuildError("extracted record must be an object")
        raw_id = str(record.get("rawQuestionId") or "")
        if not raw_id:
            raise CandidateBuildError("record missing rawQuestionId")
        if raw_id in seen_raw_ids:
            raise CandidateBuildError(f"duplicate rawQuestionId: {raw_id}")
        seen_raw_ids.add(raw_id)
        question_formats[str(record.get("listingKind") or "unknown")] += 1
        candidates.extend(candidates_for_record(record))

    candidate_kinds = Counter(item["candidateKind"] for item in candidates)
    reasons = Counter(item["decisionReason"] for item in candidates)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_not_for_web",
        "sourceInput": source_input,
        "reviewPolicy": {
            "autoPublish": False,
            "narrativeExplanationIncluded": False,
            "safeSplitTasks": sorted(SAFE_REGULAR_TASKS),
            "unsafeFormatsStayWhole": [
                "regular_combination",
                "regular_count",
                "regular_unknown",
                "multiple_blank",
                "written",
            ],
        },
        "summary": {
            "rawQuestionCount": len(ordered),
            "candidateCount": len(candidates),
            "choicePropositionCount": candidate_kinds["choice_proposition"],
            "originalQuestionQueueCount": candidate_kinds["original_question"],
            "rawQuestionsByFormat": dict(sorted(question_formats.items())),
            "candidatesByDecisionReason": dict(sorted(reasons.items())),
        },
        "candidates": candidates,
    }


def _documents(path: Path) -> list[dict[str, Any]]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    if not paths:
        raise CandidateBuildError(f"no JSON input found: {path}")
    records: list[dict[str, Any]] = []
    for document_path in paths:
        value = load_json(document_path)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
            continue
        if not isinstance(value, dict):
            raise CandidateBuildError(f"unsupported JSON document: {document_path}")
        if "rawQuestionId" in value:
            records.append(value)
            continue
        collection = next(
            (
                value[key]
                for key in ("records", "items")
                if isinstance(value.get(key), list)
            ),
            None,
        )
        if collection is None:
            raise CandidateBuildError(f"no extracted records in: {document_path}")
        records.extend(item for item in collection if isinstance(item, dict))
    return records


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root() / "extracted",
        help="extracted question JSON file or directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "curation" / "review_candidates.json",
        help="private atomic JSON destination (must not be a web root)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        records = _documents(args.input)
        inventory = build_inventory(records, source_input=str(args.input))
        atomic_write_json(args.output, inventory)
    except (CandidateBuildError, OSError, json.JSONDecodeError) as error:
        print(f"candidate inventory failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                **inventory["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
