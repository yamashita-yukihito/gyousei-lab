"""Reconcile extracted provider answers with preserved official answer displays.

The generated report intentionally contains answers and identifiers only.  It
does not copy question text, choices, source URLs, or provider explanations.
Written answers are compared conservatively: only Unicode, whitespace,
punctuation, and a trailing character-count annotation are normalized.
Semantically similar paraphrases are therefore reported as mismatches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .common import atomic_write_json, data_root, load_json, utc_now
from .subjects import canonical_subject_id


REPORT_SCHEMA_VERSION = "answer-reconciliation@1"
SOURCE_SCHEMA_VERSION = "raw-question@1"
OFFICIAL_SCHEMA_VERSION = "official-snapshots@1"
STATUS_ORDER = (
    "exact",
    "match-after-normalization",
    "mismatch",
    "unavailable",
    "unsupported",
)
RESULT_FIELDS = (
    "rawQuestionId",
    "examYear",
    "questionNumber",
    "format",
    "providerAnswer",
    "officialAnswer",
    "status",
    "reason",
)
TARGET_REGULAR = frozenset(range(8, 27))
TARGET_MULTIPLE_BLANK = frozenset({42, 43})
TARGET_WRITTEN = frozenset({44})
BLANK_LABELS = ("ア", "イ", "ウ", "エ")
CHARACTER_COUNT_SUFFIX = re.compile(r"\(\s*[0-9]{1,3}\s*字\s*\)\s*$")
SINGLE_OPTION = re.compile(r"[1-5]")
BLANK_OPTION = re.compile(r"(?:[1-9]|1[0-9]|20)")
PUNCTUATION_TRANSLATION = str.maketrans(
    {
        ",": "、",
        "，": "、",
        "､": "、",
        ".": "。",
        "．": "。",
        "｡": "。",
    }
)


class ReconciliationError(ValueError):
    """Raised when a report-level input contract is unusable."""


def normalize_written_answer(value: str) -> str:
    """Return the deliberately narrow comparison form for a written answer."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = CHARACTER_COUNT_SUFFIX.sub("", normalized)
    normalized = normalized.translate(PUNCTUATION_TRANSLATION)
    return re.sub(r"\s+", "", normalized)


def _normalize_integer(value: Any, pattern: re.Pattern[str]) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
    else:
        return None
    if not pattern.fullmatch(text):
        return None
    return int(text)


def _result(
    record: Mapping[str, Any],
    *,
    provider_answer: Any,
    official_answer: Any,
    status: str,
    reason: str,
) -> dict[str, Any]:
    result = {
        "rawQuestionId": record.get("rawQuestionId"),
        "examYear": record.get("examYear"),
        "questionNumber": record.get("questionNumber"),
        "format": record.get("listingKind") or "unknown",
        "providerAnswer": provider_answer,
        "officialAnswer": official_answer,
        "status": status,
        "reason": reason,
    }
    # Keep this assertion close to construction so later additions cannot leak
    # question text or provider narrative into the report unnoticed.
    if tuple(result) != RESULT_FIELDS:
        raise AssertionError("reconciliation result fields changed")
    return result


def _provider_answer(record: Mapping[str, Any]) -> Any:
    answer = record.get("answer")
    if not isinstance(answer, Mapping):
        return None
    kind = answer.get("kind")
    if kind in {"option", "model_answer"}:
        return answer.get("value")
    if kind == "blank_numbers":
        return answer.get("values")
    return None


def _provider_answer_kind(record: Mapping[str, Any]) -> Any:
    answer = record.get("answer")
    return answer.get("kind") if isinstance(answer, Mapping) else None


def _year_state(
    official_index: Mapping[str, Any], exam_year: int
) -> tuple[Mapping[str, Any] | None, str | None]:
    years = official_index.get("years")
    if not isinstance(years, Mapping):
        return None, "official_year_index_unavailable"
    state = years.get(str(exam_year))
    if not isinstance(state, Mapping):
        return None, "official_year_missing"
    fetch_status = state.get("fetchStatus")
    if fetch_status != "complete":
        suffix = fetch_status if isinstance(fetch_status, str) and fetch_status else "unknown"
        return None, f"official_year_status_{suffix}"
    return state, None


def _official_candidates(
    state: Mapping[str, Any], question_number: int
) -> tuple[list[Mapping[str, Any]] | None, str | None]:
    displays = state.get("answerDisplays")
    if not isinstance(displays, Mapping):
        return None, "official_answer_displays_unsupported"
    answers = displays.get("answers")
    if not isinstance(answers, list):
        return None, "official_answer_list_unsupported"
    candidates = [
        answer
        for answer in answers
        if isinstance(answer, Mapping) and answer.get("questionNumber") == question_number
    ]
    if not candidates:
        return [], "official_answer_missing"
    if len(candidates) != 1:
        return candidates, "official_answer_ambiguous"
    return candidates, None


def _expected_format(question_number: int) -> str | None:
    if question_number in TARGET_REGULAR:
        return "regular"
    if question_number in TARGET_MULTIPLE_BLANK:
        return "multiple_blank"
    if question_number in TARGET_WRITTEN:
        return "written"
    return None


def _compare_regular(
    record: Mapping[str, Any], official: Mapping[str, Any]
) -> dict[str, Any]:
    provider = _provider_answer(record)
    official_value = official.get("answer")
    if _provider_answer_kind(record) != "option":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_provider_answer_kind",
        )
    if official.get("kind") != "single":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_official_answer_kind",
        )
    provider_normalized = _normalize_integer(provider, SINGLE_OPTION)
    official_normalized = _normalize_integer(official_value, SINGLE_OPTION)
    if provider_normalized is None:
        status, reason = "unsupported", "invalid_provider_option"
    elif official_normalized is None:
        status, reason = "unsupported", "invalid_official_option"
    elif provider_normalized != official_normalized:
        status, reason = "mismatch", "option_values_differ"
    elif type(provider) is int and type(official_value) is int and provider == official_value:
        status, reason = "exact", "option_values_identical"
    else:
        status, reason = "match-after-normalization", "option_values_equal_after_normalization"
    return _result(
        record,
        provider_answer=provider,
        official_answer=official_value,
        status=status,
        reason=reason,
    )


def _normalize_blanks(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping) or set(value) != set(BLANK_LABELS):
        return None
    normalized: dict[str, int] = {}
    for label in BLANK_LABELS:
        option = _normalize_integer(value.get(label), BLANK_OPTION)
        if option is None:
            return None
        normalized[label] = option
    return normalized


def _is_exact_blank_map(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(BLANK_LABELS)
        and all(type(value[label]) is int for label in BLANK_LABELS)
    )


def _compare_multiple_blank(
    record: Mapping[str, Any], official: Mapping[str, Any]
) -> dict[str, Any]:
    provider = _provider_answer(record)
    official_value = official.get("blanks")
    if _provider_answer_kind(record) != "blank_numbers":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_provider_answer_kind",
        )
    if official.get("kind") != "multiple_blank":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_official_answer_kind",
        )
    provider_normalized = _normalize_blanks(provider)
    official_normalized = _normalize_blanks(official_value)
    if provider_normalized is None:
        status, reason = "unsupported", "invalid_provider_blank_answers"
    elif official_normalized is None:
        status, reason = "unsupported", "invalid_official_blank_answers"
    elif provider_normalized != official_normalized:
        status, reason = "mismatch", "blank_values_differ"
    elif (
        _is_exact_blank_map(provider)
        and _is_exact_blank_map(official_value)
        and provider == official_value
    ):
        status, reason = "exact", "blank_values_identical"
    else:
        status, reason = "match-after-normalization", "blank_values_equal_after_normalization"
    return _result(
        record,
        provider_answer=provider,
        official_answer=official_value,
        status=status,
        reason=reason,
    )


def _compare_written(
    record: Mapping[str, Any], official: Mapping[str, Any]
) -> dict[str, Any]:
    provider = _provider_answer(record)
    official_value = official.get("answerText")
    if not isinstance(official_value, str):
        official_value = official.get("display")
    if _provider_answer_kind(record) != "model_answer":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_provider_answer_kind",
        )
    if official.get("kind") != "written":
        return _result(
            record,
            provider_answer=provider,
            official_answer=official_value,
            status="unsupported",
            reason="unsupported_official_answer_kind",
        )
    if not isinstance(provider, str) or not provider.strip():
        status, reason = "unsupported", "invalid_provider_written_answer"
    elif not isinstance(official_value, str) or not official_value.strip():
        status, reason = "unsupported", "invalid_official_written_answer"
    elif provider == official_value:
        status, reason = "exact", "written_text_identical"
    elif normalize_written_answer(provider) == normalize_written_answer(official_value):
        status, reason = (
            "match-after-normalization",
            "written_text_equal_after_format_normalization",
        )
    else:
        status, reason = "mismatch", "written_text_differs"
    return _result(
        record,
        provider_answer=provider,
        official_answer=official_value,
        status=status,
        reason=reason,
    )


def reconcile_record(
    record: Mapping[str, Any], official_index: Mapping[str, Any]
) -> dict[str, Any]:
    """Reconcile one extracted record without guessing through ambiguity."""

    provider = _provider_answer(record)
    exam_year = record.get("examYear")
    question_number = record.get("questionNumber")
    if (
        not isinstance(record.get("rawQuestionId"), str)
        or type(exam_year) is not int
        or type(question_number) is not int
    ):
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status="unsupported",
            reason="invalid_record_identity",
        )

    explicit_subject_id = canonical_subject_id(record.get("subjectId"))
    if explicit_subject_id and explicit_subject_id != "administrative-law":
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status="unavailable",
            reason="official_reconciliation_not_run_for_subject",
        )

    state, unavailable_reason = _year_state(official_index, exam_year)
    if state is None:
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status="unavailable",
            reason=unavailable_reason or "official_year_unavailable",
        )

    expected_format = _expected_format(question_number)
    if expected_format is None or record.get("listingKind") != expected_format:
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status="unsupported",
            reason="unsupported_question_format",
        )
    extraction = record.get("extraction")
    if isinstance(extraction, Mapping) and extraction.get("status") == "parse_error":
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status="unsupported",
            reason="provider_extraction_parse_error",
        )

    candidates, candidate_reason = _official_candidates(state, question_number)
    if candidate_reason:
        status = "unavailable" if candidate_reason == "official_answer_missing" else "unsupported"
        return _result(
            record,
            provider_answer=provider,
            official_answer=None,
            status=status,
            reason=candidate_reason,
        )
    assert candidates is not None and len(candidates) == 1
    official = candidates[0]
    if expected_format == "regular":
        return _compare_regular(record, official)
    if expected_format == "multiple_blank":
        return _compare_multiple_blank(record, official)
    return _compare_written(record, official)


def reconcile_records(
    records: Iterable[Mapping[str, Any]],
    official_index: Mapping[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, question-content-free reconciliation report."""

    if official_index.get("schemaVersion") != OFFICIAL_SCHEMA_VERSION:
        raise ReconciliationError("unsupported official snapshot index schema")
    source_records = [
        record
        for record in records
        if isinstance(record, Mapping)
        and record.get("schemaVersion") == SOURCE_SCHEMA_VERSION
    ]
    results = [reconcile_record(record, official_index) for record in source_records]
    results.sort(
        key=lambda row: (
            row["examYear"] if type(row["examYear"]) is int else 10**9,
            row["questionNumber"] if type(row["questionNumber"]) is int else 10**9,
            str(row["rawQuestionId"]),
        )
    )

    status_counts = Counter(result["status"] for result in results)
    by_year: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        year_key = str(result["examYear"])
        by_year[year_key][result["status"]] += 1
    unavailable_years = sorted(
        {
            result["examYear"]
            for result in results
            if result["status"] == "unavailable"
            and result["reason"].startswith("official_year_")
            and type(result["examYear"]) is int
        }
    )
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": generated_at or utc_now(),
        "summary": {
            "total": len(results),
            "statusCounts": {status: status_counts[status] for status in STATUS_ORDER},
            "byYear": {
                year: {status: counts[status] for status in STATUS_ORDER}
                for year, counts in sorted(by_year.items(), key=lambda item: item[0])
            },
            "unavailableExamYears": unavailable_years,
        },
        "results": results,
    }


def _documents(path: Path) -> list[dict[str, Any]]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    documents: list[dict[str, Any]] = []
    for document_path in paths:
        value = load_json(document_path)
        if isinstance(value, list):
            documents.extend(item for item in value if isinstance(item, dict))
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
                documents.append(value)
            else:
                documents.extend(item for item in collection if isinstance(item, dict))
    return documents


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=data_root() / "extracted")
    parser.add_argument(
        "--official-index",
        type=Path,
        default=data_root() / "raw" / "snapshots" / "official" / "index.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "reports" / "answer-reconciliation.json",
        help="JSON destination; always written atomically",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    records = _documents(args.records)
    official_index = load_json(args.official_index)
    report = reconcile_records(records, official_index)
    atomic_write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
