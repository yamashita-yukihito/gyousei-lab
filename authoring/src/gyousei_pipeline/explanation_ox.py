"""Build private O/X propositions from explicit provider explanation verdicts.

This stage joins saved extracted questions to the separately normalized
provider explanation reference.  It promotes only one-to-one section headings
such as ``ア．正しい。`` or ``2．妥当でない。``.  Compound headings,
definition mappings, blanks, written answers, and any other ambiguous shape
remain in an editorial queue.

The generated sidecar contains provider text and must stay in the private
authoring tree.  It is never a production bundle and never auto-publishes a
card.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "explanation-derived-ox@1"
VALIDATION_SCHEMA_VERSION = "explanation-derived-ox-validation@1"
MANIFEST_SCHEMA_VERSION = "explanation-derived-ox-manifest@1"
PROVIDER_SCHEMA_VERSION = "provider-explanations@1"
TARGET_TASKS = {"combination", "count"}
CORROBORATION_TASKS = {"select_true", "select_false"}
SUPPORTED_TASKS = TARGET_TASKS | CORROBORATION_TASKS
TRUE_VERDICTS = {"正しい", "妥当である"}
FALSE_VERDICTS = {"誤り", "誤っている", "妥当でない"}
VERDICT_PATTERN = re.compile(
    r"^([1-5アイウエオカキクケコ])[.、:]?"
    r"(妥当でない|妥当である|正しい|誤っている|誤り)[。.]?$"
)
COMPOUND_VERDICT_PATTERN = re.compile(
    r"^([アイウエオ](?:[、・][アイウエオ])+)[.]?"
    r"(妥当でない|妥当である|正しい|誤っている|誤り)[。.]?$"
)
NEGATIVE_TARGET_PHRASES = (
    "妥当でない",
    "正しくない",
    "誤っている",
    "誤りである",
    "適切でない",
    "適当でない",
    "該当しない",
    "あてはまらない",
)
POSITIVE_TARGET_PHRASES = (
    "妥当な",
    "正しい",
    "適切な",
    "適当な",
    "該当する",
    "あてはまる",
)
COUNT_WORDS = {
    "一つ": 1,
    "二つ": 2,
    "三つ": 3,
    "四つ": 4,
    "五つ": 5,
    "1つ": 1,
    "2つ": 2,
    "3つ": 3,
    "4つ": 4,
    "5つ": 5,
}

DEFAULT_SCOPE = data_root() / "all_subjects" / "current_2016_2025"
DEFAULT_INPUT = DEFAULT_SCOPE / "extracted"
DEFAULT_EXPLANATIONS = DEFAULT_SCOPE / "curation" / "provider_explanations.json"
DEFAULT_OUTPUT = DEFAULT_SCOPE / "curation" / "explanation_ox_candidates.json"


class ExplanationOxError(ValueError):
    """Raised when source integrity or O/X inference is not trustworthy."""


def _normalized_heading(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(text.split())


def parse_explicit_verdict(value: Any) -> tuple[str, bool, str] | None:
    """Return ``(label, truth, verdict)`` only for a single explicit heading."""

    match = VERDICT_PATTERN.fullmatch(_normalized_heading(value))
    if match is None:
        return None
    label, verdict = match.groups()
    if verdict in TRUE_VERDICTS:
        truth = True
    elif verdict in FALSE_VERDICTS:
        truth = False
    else:  # pragma: no cover - guarded by the anchored pattern
        return None
    return label, truth, verdict


def _parse_compound_verdict_for_crosscheck(
    value: Any,
) -> tuple[tuple[str, ...], bool] | None:
    """Read a compound heading for validation only, never for card creation."""

    match = COMPOUND_VERDICT_PATTERN.fullmatch(_normalized_heading(value))
    if match is None:
        return None
    label_text, verdict = match.groups()
    labels = tuple(re.findall(r"[アイウエオ]", label_text))
    if len(labels) < 2 or len(labels) != len(set(labels)):
        return None
    return labels, verdict in TRUE_VERDICTS


def _normalized_for_match(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _source_text_match(statement: str, question_text: str) -> str:
    if statement in question_text:
        return "exact_substring"
    normalized_statement = _normalized_for_match(statement)
    normalized_question = _normalized_for_match(question_text)
    if normalized_statement and normalized_statement in normalized_question:
        return "normalized_substring"
    return "not_found_in_extracted_question_text"


def _record_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(record.get("examYear") or 0),
        int(record.get("questionNumber") or 0),
        str(record.get("rawQuestionId") or ""),
    )


def _documents(path: Path) -> list[dict[str, Any]]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    if not paths:
        raise ExplanationOxError(f"no extracted JSON found: {path}")
    records: list[dict[str, Any]] = []
    for document_path in paths:
        value = load_json(document_path)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict) and "rawQuestionId" in value:
            records.append(value)
        elif isinstance(value, dict):
            collection = next(
                (
                    value[key]
                    for key in ("records", "items")
                    if isinstance(value.get(key), list)
                ),
                None,
            )
            if collection is None:
                raise ExplanationOxError(
                    f"no extracted records in: {document_path}"
                )
            records.extend(item for item in collection if isinstance(item, dict))
        else:
            raise ExplanationOxError(f"unsupported JSON document: {document_path}")
    return records


def _unique_by_raw_id(
    values: Iterable[dict[str, Any]], *, source_name: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ExplanationOxError(f"{source_name}: item must be an object")
        raw_id = value.get("rawQuestionId")
        if not isinstance(raw_id, str) or not raw_id:
            raise ExplanationOxError(f"{source_name}: item missing rawQuestionId")
        if raw_id in result:
            raise ExplanationOxError(
                f"{source_name}: duplicate rawQuestionId: {raw_id}"
            )
        result[raw_id] = value
    return result


def _validate_reference(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ExplanationOxError("provider explanation reference must be an object")
    if value.get("schemaVersion") != PROVIDER_SCHEMA_VERSION:
        raise ExplanationOxError(
            "provider explanation schema mismatch: "
            f"{value.get('schemaVersion')!r}"
        )
    items = value.get("items")
    if not isinstance(items, list):
        raise ExplanationOxError("provider explanation reference has no items")
    return _unique_by_raw_id(items, source_name="provider explanations")


def _validate_join(
    record: dict[str, Any], explanation: dict[str, Any]
) -> None:
    raw_id = str(record["rawQuestionId"])
    comparisons = (
        ("examYear", "examYear"),
        ("questionNumber", "questionNumber"),
        ("listingKind", "format"),
        ("subjectId", "subjectId"),
        ("subjectLabel", "subjectLabel"),
        ("sourceBodySha256", "sourceBodySha256"),
    )
    for record_key, explanation_key in comparisons:
        if record.get(record_key) != explanation.get(explanation_key):
            raise ExplanationOxError(
                f"{raw_id}: source mismatch for {record_key}/"
                f"{explanation_key}"
            )
    digest = record.get("sourceBodySha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ExplanationOxError(f"{raw_id}: invalid sourceBodySha256")


def _task(record: dict[str, Any]) -> tuple[str, str]:
    task = record.get("task")
    if not isinstance(task, dict):
        return "unknown", "low"
    return str(task.get("kind") or "unknown"), str(
        task.get("confidence") or "low"
    )


def _source_citation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "sourceId": str(record.get("sourceId") or ""),
        "externalQuestionId": str(record.get("externalQuestionId") or ""),
        "sourceUrl": str(record.get("sourceUrl") or ""),
        "title": str(record.get("title") or ""),
        "eraYear": str(record.get("eraYear") or ""),
        "sourceBodySha256": str(record.get("sourceBodySha256") or ""),
    }


def _question_context(record: dict[str, Any]) -> dict[str, str]:
    return {
        "instructionText": str(record.get("instructionText") or ""),
        "questionText": str(record.get("questionText") or ""),
    }


def _extraction_is_clean(record: dict[str, Any]) -> bool:
    extraction = record.get("extraction")
    return bool(
        isinstance(extraction, dict)
        and extraction.get("status") == "parsed"
        and extraction.get("warnings") == []
    )


def _answer_derived_truth(record: dict[str, Any], label: str) -> bool:
    raw_id = str(record["rawQuestionId"])
    task_kind, task_confidence = _task(record)
    if task_kind not in CORROBORATION_TASKS or task_confidence != "high":
        raise ExplanationOxError(
            f"{raw_id}: cannot corroborate task {task_kind!r}"
        )
    if not label.isdigit():
        raise ExplanationOxError(
            f"{raw_id}: select task has non-numeric provider label {label!r}"
        )
    choices = record.get("choices")
    if not isinstance(choices, list) or len(choices) != 5:
        raise ExplanationOxError(f"{raw_id}: select task must have five choices")
    labels = [
        str(choice.get("label") or "")
        for choice in choices
        if isinstance(choice, dict)
    ]
    if len(labels) != 5 or len(set(labels)) != 5 or label not in labels:
        raise ExplanationOxError(f"{raw_id}: invalid or unmatched choice labels")
    answer = record.get("answer")
    if (
        not isinstance(answer, dict)
        or answer.get("kind") != "option"
        or isinstance(answer.get("value"), bool)
        or not isinstance(answer.get("value"), int)
    ):
        raise ExplanationOxError(f"{raw_id}: invalid select answer")
    answer_label = str(answer["value"])
    if answer_label not in labels:
        raise ExplanationOxError(f"{raw_id}: answer label is outside choices")
    is_answer = label == answer_label
    return is_answer if task_kind == "select_true" else not is_answer


def _answer_choice(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_id = str(record["rawQuestionId"])
    answer = record.get("answer")
    if (
        not isinstance(answer, dict)
        or answer.get("kind") != "option"
        or isinstance(answer.get("value"), bool)
        or not isinstance(answer.get("value"), int)
    ):
        raise ExplanationOxError(f"{raw_id}: invalid option answer")
    answer_label = str(answer["value"])
    choices = record.get("choices")
    if not isinstance(choices, list):
        raise ExplanationOxError(f"{raw_id}: choices must be a list")
    matching = [
        choice
        for choice in choices
        if isinstance(choice, dict)
        and str(choice.get("label") or "") == answer_label
    ]
    if len(matching) != 1:
        raise ExplanationOxError(
            f"{raw_id}: answer option does not identify one choice"
        )
    return answer_label, matching[0]


def _target_truth_from_prompt(record: dict[str, Any]) -> bool:
    raw_id = str(record["rawQuestionId"])
    task = record.get("task")
    prompt = _normalized_for_match(
        task.get("prompt") if isinstance(task, dict) else ""
    )
    if any(phrase in prompt for phrase in NEGATIVE_TARGET_PHRASES):
        return False
    if any(phrase in prompt for phrase in POSITIVE_TARGET_PHRASES):
        return True
    raise ExplanationOxError(
        f"{raw_id}: cannot determine requested truth polarity from prompt"
    )


def _crosscheck_target_answer(
    record: dict[str, Any], truth_by_label: dict[str, bool]
) -> dict[str, Any]:
    """Verify explanation verdicts can reconstruct the saved correct option."""

    raw_id = str(record["rawQuestionId"])
    task_kind, _ = _task(record)
    if not truth_by_label:
        raise ExplanationOxError(
            f"{raw_id}: no verdict vector available for answer cross-check"
        )
    answer_label, selected_choice = _answer_choice(record)

    if all(label.isdigit() for label in truth_by_label):
        choices = record.get("choices")
        choice_labels = {
            str(choice.get("label") or "")
            for choice in choices
            if isinstance(choices, list) and isinstance(choice, dict)
        }
        if set(truth_by_label) != choice_labels:
            raise ExplanationOxError(
                f"{raw_id}: direct option verdict vector is incomplete"
            )
        true_labels = {
            label for label, truth in truth_by_label.items() if truth
        }
        if true_labels != {answer_label}:
            raise ExplanationOxError(
                f"{raw_id}: direct provider option verdicts conflict with answer"
            )
        return {
            "result": "matched",
            "method": "direct_option_verdict",
            "answerOption": int(answer_label),
        }

    if any(label.isdigit() for label in truth_by_label):
        raise ExplanationOxError(
            f"{raw_id}: mixed numeric and kana verdict labels"
        )

    if task_kind == "count":
        target_truth = _target_truth_from_prompt(record)
        question_text = unicodedata.normalize(
            "NFKC", str(record.get("questionText") or "")
        )
        statement_labels = set(
            re.findall(r"(?:^|\s)([アイウエオ])[.]", question_text)
        )
        if not statement_labels or set(truth_by_label) != statement_labels:
            raise ExplanationOxError(
                f"{raw_id}: count verdict vector is incomplete"
            )
        selected_text = _normalized_for_match(selected_choice.get("text"))
        selected_count = COUNT_WORDS.get(selected_text)
        if selected_count is None:
            raise ExplanationOxError(
                f"{raw_id}: cannot parse selected count choice"
            )
        expected_count = sum(
            truth is target_truth for truth in truth_by_label.values()
        )
        if selected_count != expected_count:
            raise ExplanationOxError(
                f"{raw_id}: provider verdict count conflicts with answer"
            )
        return {
            "result": "matched",
            "method": "count_verdict_vector",
            "answerOption": int(answer_label),
            "requestedTruth": target_truth,
            "expectedCount": expected_count,
        }

    if task_kind != "combination":
        raise ExplanationOxError(
            f"{raw_id}: unsupported answer cross-check task {task_kind!r}"
        )

    cells = selected_choice.get("cells")
    if isinstance(cells, list) and cells:
        selected_vector: dict[str, bool] = {}
        for cell in cells:
            if not isinstance(cell, dict):
                raise ExplanationOxError(
                    f"{raw_id}: answer-vector cell must be an object"
                )
            label = str(cell.get("column") or "")
            value = _normalized_for_match(cell.get("text"))
            if value in {"正", "正しい", "○"}:
                truth = True
            elif value in {"誤", "誤り", "×"}:
                truth = False
            else:
                raise ExplanationOxError(
                    f"{raw_id}: unknown answer-vector value {value!r}"
                )
            if not label or label in selected_vector:
                raise ExplanationOxError(
                    f"{raw_id}: invalid answer-vector label {label!r}"
                )
            selected_vector[label] = truth
        if selected_vector != truth_by_label:
            raise ExplanationOxError(
                f"{raw_id}: provider verdict vector conflicts with answer"
            )
        return {
            "result": "matched",
            "method": "combination_truth_vector",
            "answerOption": int(answer_label),
        }

    target_truth = _target_truth_from_prompt(record)
    selected_text = _normalized_for_match(selected_choice.get("text"))
    if not re.fullmatch(
        r"[アイウエオ](?:[・、][アイウエオ])+", selected_text
    ):
        raise ExplanationOxError(
            f"{raw_id}: cannot parse selected combination choice"
        )
    all_choice_labels: set[str] = set()
    for choice in record.get("choices") or []:
        if not isinstance(choice, dict):
            raise ExplanationOxError(
                f"{raw_id}: combination choice must be an object"
            )
        choice_text = _normalized_for_match(choice.get("text"))
        if not re.fullmatch(
            r"[アイウエオ](?:[・、][アイウエオ])+", choice_text
        ):
            raise ExplanationOxError(
                f"{raw_id}: cannot parse all combination choices"
            )
        all_choice_labels.update(re.findall(r"[アイウエオ]", choice_text))
    if set(truth_by_label) != all_choice_labels:
        raise ExplanationOxError(
            f"{raw_id}: combination verdict vector is incomplete"
        )
    selected_labels = set(re.findall(r"[アイウエオ]", selected_text))
    expected_labels = {
        label
        for label, truth in truth_by_label.items()
        if truth is target_truth
    }
    if selected_labels != expected_labels:
        raise ExplanationOxError(
            f"{raw_id}: provider verdict labels conflict with answer"
        )
    return {
        "result": "matched",
        "method": "combination_selected_labels",
        "answerOption": int(answer_label),
        "requestedTruth": target_truth,
        "expectedLabels": sorted(expected_labels),
    }


def _base_private_item(
    record: dict[str, Any],
    *,
    section_index: int,
    statement_text: str,
    provider_verdict: str,
    explanation_paragraphs: list[str],
) -> dict[str, Any]:
    raw_id = str(record["rawQuestionId"])
    context = _question_context(record)
    return {
        "rawQuestionId": raw_id,
        "sourceSnapshotId": str(record.get("sourceSnapshotId") or ""),
        "sourceBodySha256": str(record.get("sourceBodySha256") or ""),
        "examYear": record.get("examYear"),
        "questionNumber": record.get("questionNumber"),
        "subjectId": str(record.get("subjectId") or ""),
        "subjectLabel": str(record.get("subjectLabel") or ""),
        "format": str(record.get("listingKind") or ""),
        "task": _task(record)[0],
        "sectionIndex": section_index,
        "statementText": statement_text,
        "providerVerdict": provider_verdict,
        "providerExplanationParagraphs": explanation_paragraphs,
        "questionContext": context,
        "sourceTextMatch": _source_text_match(
            statement_text, context["questionText"]
        ),
        "sourceCitation": _source_citation(record),
        "reviewed": False,
        "publishable": False,
    }


def _candidate(
    record: dict[str, Any],
    *,
    section_index: int,
    label: str,
    truth: bool,
    verdict: str,
    statement_text: str,
    explanation_paragraphs: list[str],
) -> dict[str, Any]:
    item = _base_private_item(
        record,
        section_index=section_index,
        statement_text=statement_text,
        provider_verdict=verdict,
        explanation_paragraphs=explanation_paragraphs,
    )
    item.update(
        {
            "candidateId": (
                f"{record['rawQuestionId']}:provider-explanation:{label}"
            ),
            "candidateKind": "provider_explanation_proposition",
            "statementLabel": label,
            "choiceLabel": label,
            "inferredTruth": truth,
            "truthBasis": "explicit_provider_choice_verdict",
            "oxEligible": True,
            "contextReviewRequired": True,
            "frequencyEligible": False,
            "decisionReason": "explicit_provider_choice_verdict",
        }
    )
    return item


def _corroboration(
    record: dict[str, Any],
    *,
    section_index: int,
    label: str,
    truth: bool,
    verdict: str,
    statement_text: str,
) -> dict[str, Any]:
    return {
        "candidateId": f"{record['rawQuestionId']}:choice:{label}",
        "rawQuestionId": str(record["rawQuestionId"]),
        "sourceBodySha256": str(record.get("sourceBodySha256") or ""),
        "examYear": record.get("examYear"),
        "questionNumber": record.get("questionNumber"),
        "subjectId": str(record.get("subjectId") or ""),
        "task": _task(record)[0],
        "sectionIndex": section_index,
        "choiceLabel": label,
        "statementText": statement_text,
        "inferredTruth": truth,
        "providerVerdict": verdict,
        "corroborationResult": "provider_verdict_matches_answer_inference",
    }


def _queue_item(
    record: dict[str, Any],
    *,
    reasons: Iterable[str],
    explicit_candidate_count: int = 0,
    editorial_mapping_count: int = 0,
) -> dict[str, Any]:
    return {
        "rawQuestionId": str(record["rawQuestionId"]),
        "examYear": record.get("examYear"),
        "questionNumber": record.get("questionNumber"),
        "subjectId": str(record.get("subjectId") or ""),
        "format": str(record.get("listingKind") or ""),
        "task": _task(record)[0],
        "reasons": sorted(set(reasons)),
        "explicitCandidateCount": explicit_candidate_count,
        "editorialMappingCount": editorial_mapping_count,
        "reviewed": False,
        "publishable": False,
    }


def build_inventory(
    records: Iterable[dict[str, Any]],
    provider_reference: dict[str, Any],
    *,
    source_input: str = "",
    source_explanations: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Join sources and return a deterministic, private explanation O/X sidecar."""

    record_map = _unique_by_raw_id(records, source_name="extracted questions")
    explanation_map = _validate_reference(provider_reference)
    if set(record_map) != set(explanation_map):
        missing_explanations = sorted(set(record_map) - set(explanation_map))
        missing_records = sorted(set(explanation_map) - set(record_map))
        raise ExplanationOxError(
            "rawQuestionId sets differ "
            f"(without explanation={missing_explanations[:3]}, "
            f"without record={missing_records[:3]})"
        )

    candidates: list[dict[str, Any]] = []
    corroborations: list[dict[str, Any]] = []
    editorial_mappings: list[dict[str, Any]] = []
    question_queue: list[dict[str, Any]] = []
    explicit_section_count = 0
    available_count = 0
    missing_count = 0
    excluded_withdrawn_count = 0
    target_answer_crosschecks: Counter[str] = Counter()
    ignored_statementless_section_count = 0

    for record in sorted(record_map.values(), key=_record_sort_key):
        raw_id = str(record["rawQuestionId"])
        explanation = explanation_map[raw_id]
        _validate_join(record, explanation)
        task_kind, task_confidence = _task(record)
        format_name = str(record.get("listingKind") or "")

        explanation_available = explanation.get("explanationAvailable")
        if type(explanation_available) is not bool:
            raise ExplanationOxError(
                f"{raw_id}: explanationAvailable must be boolean"
            )
        if explanation_available:
            available_count += 1
        else:
            missing_count += 1

        if record.get("isWithdrawn") is True:
            excluded_withdrawn_count += 1
            question_queue.append(
                _queue_item(record, reasons=["withdrawn_question_excluded"])
            )
            continue
        if not _extraction_is_clean(record):
            question_queue.append(
                _queue_item(
                    record,
                    reasons=["extraction_not_clean_for_ox_derivation"],
                )
            )
            continue

        if not explanation_available:
            question_queue.append(
                _queue_item(
                    record,
                    reasons=["provider_explanation_unavailable"],
                )
            )
            continue

        sections = explanation.get("sections")
        if not isinstance(sections, list):
            raise ExplanationOxError(f"{raw_id}: sections must be a list")
        if format_name != "regular" or task_kind not in SUPPORTED_TASKS:
            question_queue.append(
                _queue_item(
                    record,
                    reasons=["format_or_task_not_ox_section_eligible"],
                )
            )
            continue
        if task_confidence != "high":
            question_queue.append(
                _queue_item(
                    record,
                    reasons=["task_inference_not_high_confidence"],
                )
            )
            continue

        labels_seen: set[str] = set()
        truth_for_answer_crosscheck: dict[str, bool] = {}
        question_candidate_count = 0
        question_mapping_count = 0
        rejected_reasons: list[str] = []
        candidate_start = len(candidates)
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ExplanationOxError(
                    f"{raw_id}: explanation section must be an object"
                )
            statement_text = str(section.get("statementText") or "").strip()
            provider_verdict = str(section.get("providerVerdict") or "").strip()
            raw_paragraphs = section.get("explanationParagraphs")
            if not isinstance(raw_paragraphs, list):
                raise ExplanationOxError(
                    f"{raw_id}: explanationParagraphs must be a list"
                )
            explanation_paragraphs = [
                str(paragraph).strip()
                for paragraph in raw_paragraphs
                if str(paragraph).strip()
            ]
            parsed = parse_explicit_verdict(provider_verdict)
            compound_for_crosscheck = _parse_compound_verdict_for_crosscheck(
                provider_verdict
            )

            if task_kind in TARGET_TASKS and compound_for_crosscheck is not None:
                compound_labels, compound_truth = compound_for_crosscheck
                for compound_label in compound_labels:
                    previous = truth_for_answer_crosscheck.get(compound_label)
                    if previous is not None and previous is not compound_truth:
                        raise ExplanationOxError(
                            f"{raw_id}: conflicting compound verdict for "
                            f"{compound_label}"
                        )
                    truth_for_answer_crosscheck[compound_label] = compound_truth

            paragraph_required_but_missing = bool(
                task_kind in TARGET_TASKS and not explanation_paragraphs
            )
            if parsed is None or not statement_text or paragraph_required_but_missing:
                if task_kind in TARGET_TASKS and statement_text:
                    mapping = _base_private_item(
                        record,
                        section_index=section_index,
                        statement_text=statement_text,
                        provider_verdict=provider_verdict,
                        explanation_paragraphs=explanation_paragraphs,
                    )
                    mapping.update(
                        {
                            "mappingId": (
                                f"{raw_id}:provider-mapping:{section_index}"
                            ),
                            "candidateKind": "provider_editorial_mapping",
                            "oxEligible": False,
                            "decisionReason": (
                                "provider_heading_not_single_explicit_verdict"
                                if parsed is None
                                else "provider_explanation_paragraph_missing"
                            ),
                        }
                    )
                    editorial_mappings.append(mapping)
                    question_mapping_count += 1
                    rejected_reasons.append(
                        "section_not_single_explicit_verdict"
                        if parsed is None
                        else "section_statement_or_explanation_missing"
                    )
                elif task_kind in TARGET_TASKS and not statement_text:
                    ignored_statementless_section_count += 1
                continue

            label, truth, normalized_verdict = parsed
            if label in labels_seen:
                raise ExplanationOxError(
                    f"{raw_id}: duplicate explicit section label {label!r}"
                )
            labels_seen.add(label)
            explicit_section_count += 1
            if task_kind in TARGET_TASKS:
                previous = truth_for_answer_crosscheck.get(label)
                if previous is not None and previous is not truth:
                    raise ExplanationOxError(
                        f"{raw_id}: conflicting verdict for {label}"
                    )
                truth_for_answer_crosscheck[label] = truth

            if task_kind in CORROBORATION_TASKS:
                answer_truth = _answer_derived_truth(record, label)
                if answer_truth is not truth:
                    raise ExplanationOxError(
                        f"{raw_id}: provider verdict conflicts with "
                        f"answer-derived truth for choice {label}"
                    )
                corroborations.append(
                    _corroboration(
                        record,
                        section_index=section_index,
                        label=label,
                        truth=truth,
                        verdict=normalized_verdict,
                        statement_text=statement_text,
                    )
                )
                continue

            candidates.append(
                _candidate(
                    record,
                    section_index=section_index,
                    label=label,
                    truth=truth,
                    verdict=normalized_verdict,
                    statement_text=statement_text,
                    explanation_paragraphs=explanation_paragraphs,
                )
            )
            question_candidate_count += 1

        if task_kind in TARGET_TASKS and question_candidate_count:
            answer_crosscheck = _crosscheck_target_answer(
                record, truth_for_answer_crosscheck
            )
            target_answer_crosschecks[answer_crosscheck["method"]] += 1
            for candidate in candidates[candidate_start:]:
                candidate["sourceAnswerCrossCheck"] = answer_crosscheck

        if task_kind in TARGET_TASKS and (
            question_candidate_count == 0 or rejected_reasons
        ):
            reasons = rejected_reasons or ["no_explicit_ox_section_found"]
            question_queue.append(
                _queue_item(
                    record,
                    reasons=reasons,
                    explicit_candidate_count=question_candidate_count,
                    editorial_mapping_count=question_mapping_count,
                )
            )

    candidate_ids = [item["candidateId"] for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ExplanationOxError("generated duplicate candidateId")
    corroboration_ids = [item["candidateId"] for item in corroborations]
    if len(corroboration_ids) != len(set(corroboration_ids)):
        raise ExplanationOxError("generated duplicate corroboration candidateId")

    by_subject = Counter(item["subjectId"] for item in candidates)
    by_task = Counter(item["task"] for item in candidates)
    by_truth = Counter(
        "true" if item["inferredTruth"] else "false" for item in candidates
    )
    source_questions = {item["rawQuestionId"] for item in candidates}
    source_match = Counter(item["sourceTextMatch"] for item in candidates)
    queue_reasons = Counter(
        reason for item in question_queue for reason in item["reasons"]
    )
    generated = generated_at or utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated,
        "visibility": "private_editorial_candidate_not_for_web",
        "sourceInput": source_input,
        "sourceExplanations": source_explanations,
        "policy": {
            "autoPublish": False,
            "autoFrequency": False,
            "providerNarrativeIsPrivate": True,
            "acceptedTruthBasis": "explicit_provider_choice_verdict",
            "acceptedHeadingShape": (
                "one label plus one of 正しい・誤り・妥当である・妥当でない"
            ),
            "compoundHeadingsAutoSplit": False,
            "contextReviewRequiredBeforeCardPromotion": True,
            "frequencyUnitAfterIntegration": "one exam question per card",
        },
        "summary": {
            "rawQuestionCount": len(record_map),
            "providerExplanationAvailableCount": available_count,
            "providerExplanationUnavailableCount": missing_count,
            "withdrawnQuestionExcludedCount": excluded_withdrawn_count,
            "explicitVerdictSectionCount": explicit_section_count,
            "additionalCandidateCount": len(candidates),
            "additionalSourceQuestionCount": len(source_questions),
            "targetSourceAnswerCrossCheckCount": sum(
                target_answer_crosschecks.values()
            ),
            "targetSourceAnswerCrossChecksByMethod": dict(
                sorted(target_answer_crosschecks.items())
            ),
            "corroborationCount": len(corroborations),
            "editorialMappingCount": len(editorial_mappings),
            "ignoredStatementlessSectionCount": (
                ignored_statementless_section_count
            ),
            "questionQueueCount": len(question_queue),
            "additionalCandidatesBySubject": dict(sorted(by_subject.items())),
            "additionalCandidatesByTask": dict(sorted(by_task.items())),
            "additionalCandidatesByTruth": dict(sorted(by_truth.items())),
            "candidateSourceTextMatch": dict(sorted(source_match.items())),
            "questionQueueReasons": dict(sorted(queue_reasons.items())),
        },
        "candidates": candidates,
        "corroborations": corroborations,
        "editorialMappings": editorial_mappings,
        "questionQueue": question_queue,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validation_document(
    inventory: dict[str, Any], *, generated_at: str
) -> dict[str, Any]:
    summary = inventory["summary"]
    return {
        "schemaVersion": VALIDATION_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "passed": True,
        "checks": {
            "rawIdSetsExact": True,
            "sourceMetadataExact": True,
            "selectVerdictContradictionCount": 0,
            "targetAnswerContradictionCount": 0,
            "targetAnswerCrossCheckCount": summary[
                "targetSourceAnswerCrossCheckCount"
            ],
            "duplicateCandidateIdCount": 0,
            "autoPublish": False,
            "autoFrequency": False,
        },
        "summary": summary,
    }


def _write_private_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)
    os.chmod(path, 0o600)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="extracted current-question JSON file or directory",
    )
    parser.add_argument(
        "--explanations",
        type=Path,
        default=DEFAULT_EXPLANATIONS,
        help="private provider_explanations.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="private explanation O/X candidate sidecar",
    )
    parser.add_argument(
        "--validation-output",
        type=Path,
        help="validation JSON (default: next to --output)",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="manifest JSON (default: next to --output)",
    )
    parser.add_argument("--expected-count", type=int, default=569)
    parser.add_argument("--expected-candidate-count", type=int)
    parser.add_argument("--expected-corroboration-count", type=int)
    parser.add_argument("--expected-target-crosscheck-count", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.expected_count < 0:
        print("--expected-count must be non-negative", file=sys.stderr)
        return 2
    validation_output = args.validation_output or (
        args.output.parent / "explanation_ox_validation.json"
    )
    manifest_output = args.manifest_output or (
        args.output.parent / "explanation_ox_manifest.json"
    )
    try:
        records = _documents(args.input)
        if len(records) != args.expected_count:
            raise ExplanationOxError(
                f"expected {args.expected_count} records, got {len(records)}"
            )
        provider_reference = load_json(args.explanations)
        generated_at = utc_now()
        inventory = build_inventory(
            records,
            provider_reference,
            source_input=str(args.input),
            source_explanations=str(args.explanations),
            generated_at=generated_at,
        )
        expected_summary = (
            (
                "additionalCandidateCount",
                args.expected_candidate_count,
            ),
            (
                "corroborationCount",
                args.expected_corroboration_count,
            ),
            (
                "targetSourceAnswerCrossCheckCount",
                args.expected_target_crosscheck_count,
            ),
        )
        for summary_key, expected_value in expected_summary:
            if (
                expected_value is not None
                and inventory["summary"][summary_key] != expected_value
            ):
                raise ExplanationOxError(
                    f"expected {expected_value} for {summary_key}, got "
                    f"{inventory['summary'][summary_key]}"
                )
        _write_private_json(args.output, inventory)
        validation = _validation_document(
            inventory, generated_at=generated_at
        )
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
            "summary": inventory["summary"],
        }
        _write_private_json(manifest_output, manifest)
    except (ExplanationOxError, OSError, json.JSONDecodeError) as error:
        print(f"explanation O/X build failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "validation": str(validation_output),
                "manifest": str(manifest_output),
                **inventory["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
