"""Build a content-free coverage and extraction-quality report.

Only aggregate metadata is emitted.  Question text, choices, answers, source
URLs, and provider explanations are deliberately excluded from the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .common import atomic_write_json, data_root, load_json, load_target, utc_now


REPORT_SCHEMA_VERSION = "corpus-report@1"
SOURCE_SCHEMA_VERSION = "raw-question@1"
LISTING_KINDS = ("regular", "multiple_blank", "written")
EXTRACTION_STATUSES = ("parsed", "needs_review", "parse_error")
MISSING_VALUE = "missing"


class ReportError(ValueError):
    """Raised when the report target configuration is unusable."""


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _expected_coverage(target: dict[str, Any]) -> tuple[list[int], dict[str, int], int]:
    try:
        settings = target["target"]
        years = [int(year) for year in settings["examYears"]]
        configured = settings["expectedPerYear"]
        expected_per_year = {
            "regular": int(configured["regular"]),
            "multiple_blank": int(configured["multipleChoice"]),
            "written": int(configured["written"]),
            "total": int(configured["total"]),
        }
        expected_total = int(settings["expectedTotal"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReportError("invalid target coverage configuration") from error
    if len(years) != len(set(years)):
        raise ReportError("target examYears contains duplicates")
    return years, expected_per_year, expected_total


def build_corpus_report(
    records: Iterable[dict[str, Any]],
    target: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate extracted records without copying any question material."""

    records = [record for record in records if isinstance(record, dict)]
    years, expected_per_year, expected_total = _expected_coverage(
        target or load_target()
    )
    target_years = set(years)

    format_counts: dict[int, Counter[str]] = defaultdict(Counter)
    unexpected_year_counts: Counter[str] = Counter()
    unassigned_year_count = 0
    unknown_listing_kind_count = 0
    task_kind_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    records_without_labels = 0
    amended_by_year: Counter[str] = Counter()
    amended_count = 0
    invalid_amended_flag_count = 0
    status_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    records_with_warnings = 0
    invalid_warnings_field_count = 0

    for record in records:
        year = record.get("examYear")
        listing_kind = record.get("listingKind")
        if isinstance(year, int):
            format_counts[year][
                listing_kind if listing_kind in LISTING_KINDS else "other"
            ] += 1
            if year not in target_years:
                unexpected_year_counts[str(year)] += 1
        else:
            unassigned_year_count += 1
        if listing_kind not in LISTING_KINDS:
            unknown_listing_kind_count += 1

        task = record.get("task")
        task_kind = task.get("kind") if isinstance(task, dict) else None
        task_kind_counts[
            task_kind if isinstance(task_kind, str) and task_kind else MISSING_VALUE
        ] += 1

        labels = record.get("labels")
        safe_labels = {
            label for label in labels or [] if isinstance(label, str) and label
        } if isinstance(labels, list) else set()
        if safe_labels:
            label_counts.update(safe_labels)
        else:
            records_without_labels += 1

        amended = record.get("isAmended")
        if amended is True:
            amended_count += 1
            amended_by_year[str(year) if isinstance(year, int) else MISSING_VALUE] += 1
        elif amended is not False:
            invalid_amended_flag_count += 1

        extraction = record.get("extraction")
        status = extraction.get("status") if isinstance(extraction, dict) else None
        status_counts[
            status if isinstance(status, str) and status else MISSING_VALUE
        ] += 1
        warnings = extraction.get("warnings") if isinstance(extraction, dict) else None
        if isinstance(warnings, list) and all(isinstance(item, str) for item in warnings):
            if warnings:
                records_with_warnings += 1
                warning_counts.update(warnings)
        else:
            invalid_warnings_field_count += 1

    by_year: dict[str, dict[str, Any]] = {}
    coverage_matches = True
    for year in years:
        counts = format_counts[year]
        actual = {
            "regular": counts["regular"],
            "multiple_blank": counts["multiple_blank"],
            "written": counts["written"],
            "total": sum(counts.values()),
        }
        matches = actual == expected_per_year and counts["other"] == 0
        coverage_matches = coverage_matches and matches
        by_year[str(year)] = {**actual, "matchesExpected": matches}

    total_matches = len(records) == expected_total
    coverage_ok = (
        total_matches
        and coverage_matches
        and not unexpected_year_counts
        and unassigned_year_count == 0
        and unknown_listing_kind_count == 0
    )
    recognized_status_count = sum(status_counts[status] for status in EXTRACTION_STATUSES)
    extraction_quality_ok = (
        recognized_status_count == len(records) and status_counts["parse_error"] == 0
    )

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAt": generated_at or utc_now(),
        "sourceSchemaVersion": SOURCE_SCHEMA_VERSION,
        "ok": coverage_ok and extraction_quality_ok,
        "coverage": {
            "ok": coverage_ok,
            "recordCount": len(records),
            "expectedTotal": expected_total,
            "totalMatches": total_matches,
            "expectedPerYear": expected_per_year,
            "byYear": by_year,
            "unexpectedYears": dict(sorted(unexpected_year_counts.items())),
            "unassignedYearCount": unassigned_year_count,
            "unknownListingKindCount": unknown_listing_kind_count,
        },
        "taskKinds": {
            "counts": _sorted_counts(task_kind_counts),
            "missingCount": task_kind_counts[MISSING_VALUE],
        },
        "labels": {
            "questionCounts": _sorted_counts(label_counts),
            "distinctCount": len(label_counts),
            "recordsWithoutLabels": records_without_labels,
        },
        "amendments": {
            "count": amended_count,
            "byYear": dict(sorted(amended_by_year.items())),
            "invalidFlagCount": invalid_amended_flag_count,
        },
        "extraction": {
            "qualityOk": extraction_quality_ok,
            "statusCounts": _sorted_counts(status_counts),
            "recordsWithWarnings": records_with_warnings,
            "warningOccurrenceCount": sum(warning_counts.values()),
            "warningCounts": _sorted_counts(warning_counts),
            "invalidWarningsFieldCount": invalid_warnings_field_count,
        },
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
    parser.add_argument("--target", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON destination; written atomically",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    target = load_json(args.target) if args.target else load_target()
    records = [
        document
        for document in _documents(args.records)
        if document.get("schemaVersion") == SOURCE_SCHEMA_VERSION
    ]
    report = build_corpus_report(records, target)
    if args.output:
        atomic_write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
