"""Validate extracted administrative-law coverage and record semantics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .common import (
    atomic_write_json,
    data_root,
    load_json,
    load_target,
    read_gzip_blob,
    sha256_bytes,
)


SCHEMA_VERSION = "raw-question@1"
LISTING_KINDS = ("regular", "multiple_blank", "written")
EXPECTED_BLANKS = ("ア", "イ", "ウ", "エ")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DATE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}\+09:00)?$"
)
REGULAR_TASKS = {"select_true", "select_false", "combination", "count", "unknown"}


def _issue(
    issues: list[dict[str, Any]],
    level: str,
    code: str,
    message: str,
    record: dict[str, Any] | None = None,
) -> None:
    issue: dict[str, Any] = {"level": level, "code": code, "message": message}
    if record:
        issue["rawQuestionId"] = record.get("rawQuestionId")
    issues.append(issue)


def _required(
    record: dict[str, Any],
    keys: Iterable[str],
    issues: list[dict[str, Any]],
) -> None:
    for key in keys:
        if key not in record or record[key] is None or record[key] == "":
            _issue(issues, "error", "missing_required_field", f"missing {key}", record)


def _validate_common(
    record: dict[str, Any], issues: list[dict[str, Any]], target_years: set[int]
) -> None:
    _required(
        record,
        (
            "schemaVersion",
            "parserVersion",
            "rawQuestionId",
            "catalogId",
            "sourceSnapshotId",
            "sourceBodySha256",
            "sourceId",
            "externalQuestionId",
            "sourceUrl",
            "examYear",
            "eraYear",
            "questionNumber",
            "title",
            "labels",
            "catalogLabels",
            "listingKind",
            "endpointType",
            "isAmended",
            "providerUpdatedAt",
            "explanationCaptured",
            "extraction",
        ),
        issues,
    )
    if record.get("schemaVersion") != SCHEMA_VERSION:
        _issue(issues, "error", "schema_version", "unsupported schemaVersion", record)
    if record.get("rawQuestionId") != record.get("catalogId"):
        _issue(issues, "error", "raw_catalog_id_mismatch", "rawQuestionId != catalogId", record)
    if record.get("listingKind") not in LISTING_KINDS:
        _issue(issues, "error", "listing_kind", "invalid listingKind", record)
    if record.get("endpointType") not in {"regular", "archive"}:
        _issue(issues, "error", "endpoint_type", "invalid endpointType", record)
    if type(record.get("examYear")) is not int or record.get("examYear") not in target_years:
        _issue(issues, "error", "exam_year", "examYear is outside target", record)
    if type(record.get("questionNumber")) is not int or record.get("questionNumber", 0) <= 0:
        _issue(issues, "error", "question_number", "invalid questionNumber", record)
    if type(record.get("isAmended")) is not bool:
        _issue(issues, "error", "is_amended", "isAmended must be boolean", record)
    if record.get("explanationCaptured") is not False:
        _issue(
            issues,
            "error",
            "explanation_captured",
            "provider explanation must not be copied into extracted JSON",
            record,
        )
    if not HASH_PATTERN.fullmatch(str(record.get("sourceBodySha256", ""))):
        _issue(issues, "error", "snapshot_hash_format", "invalid sourceBodySha256", record)
    if not DATE_PATTERN.fullmatch(str(record.get("providerUpdatedAt", ""))):
        _issue(issues, "error", "provider_updated_at", "invalid providerUpdatedAt", record)
    labels = record.get("labels")
    if not isinstance(labels, list) or not labels or not all(isinstance(item, str) and item for item in labels):
        _issue(issues, "error", "labels", "labels must be a non-empty string list", record)
    elif "行政法" not in labels:
        _issue(issues, "error", "subject_label", "labels do not include 行政法", record)

    extraction = record.get("extraction")
    if not isinstance(extraction, dict):
        _issue(issues, "error", "extraction", "extraction must be an object", record)
    else:
        status = extraction.get("status")
        warnings = extraction.get("warnings")
        if status not in {"parsed", "needs_review", "parse_error"}:
            _issue(issues, "error", "parse_status", "invalid extraction status", record)
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            _issue(issues, "error", "parse_warnings", "warnings must be a string list", record)
        if status == "parse_error":
            _issue(issues, "error", "parse_error", "record has parse_error status", record)
        elif status == "needs_review":
            _issue(issues, "warning", "needs_review", "record needs human review", record)
        if status == "parsed" and warnings:
            _issue(issues, "error", "unreflected_warnings", "parsed record still has warnings", record)

    serialized = json.dumps(record, ensure_ascii=False)
    if "SlctChk" in serialized or "placeholderCheck" in serialized:
        _issue(issues, "error", "ui_control_leak", "provider UI controls leaked into JSON", record)
    if any(key in record for key in ("explanation", "explanations", "providerExplanation")):
        _issue(issues, "error", "explanation_field", "explanation field is forbidden", record)


def _validate_regular(record: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    _required(
        record,
        (
            "instructionText",
            "questionText",
            "choices",
            "choiceFormat",
            "choiceColumns",
            "task",
            "answer",
        ),
        issues,
    )
    if not isinstance(record.get("questionText"), str) or not record.get("questionText", "").strip():
        _issue(issues, "error", "regular_question_text", "empty regular questionText", record)
    choices = record.get("choices")
    if not isinstance(choices, list) or len(choices) != 5:
        _issue(issues, "error", "regular_choice_count", "regular question must have five choices", record)
    else:
        labels = []
        for choice in choices:
            if not isinstance(choice, dict):
                _issue(issues, "error", "regular_choice", "choice must be an object", record)
                continue
            labels.append(choice.get("label"))
            if not isinstance(choice.get("text"), str) or not choice["text"].strip():
                _issue(issues, "error", "regular_choice_text", "empty choice text", record)
        if labels != ["1", "2", "3", "4", "5"]:
            _issue(issues, "error", "regular_choice_labels", "choice labels must be 1..5", record)

    choice_format = record.get("choiceFormat")
    choice_columns = record.get("choiceColumns")
    if choice_format not in {"list", "table"}:
        _issue(issues, "error", "regular_choice_format", "invalid choiceFormat", record)
    elif choice_format == "list":
        if choice_columns != []:
            _issue(
                issues,
                "error",
                "regular_choice_columns",
                "list choices must have no columns",
                record,
            )
    else:
        columns_valid = (
            isinstance(choice_columns, list)
            and bool(choice_columns)
            and all(isinstance(column, str) and column for column in choice_columns)
            and len(set(choice_columns)) == len(choice_columns)
        )
        if not columns_valid:
            _issue(
                issues,
                "error",
                "regular_choice_columns",
                "table choice columns must be unique non-empty strings",
                record,
            )
        if isinstance(choices, list) and isinstance(choice_columns, list):
            for choice in choices:
                cells = choice.get("cells") if isinstance(choice, dict) else None
                cell_columns = (
                    [cell.get("column") for cell in cells if isinstance(cell, dict)]
                    if isinstance(cells, list)
                    else []
                )
                cell_texts_valid = isinstance(cells, list) and all(
                    isinstance(cell, dict)
                    and isinstance(cell.get("text"), str)
                    and bool(cell["text"].strip())
                    for cell in cells
                )
                if cell_columns != choice_columns or not cell_texts_valid:
                    _issue(
                        issues,
                        "error",
                        "regular_choice_cells",
                        "table choice cells must match columns and contain text",
                        record,
                    )

    task = record.get("task")
    if not isinstance(task, dict) or task.get("kind") not in REGULAR_TASKS:
        _issue(issues, "error", "regular_task", "invalid regular task", record)
    elif task.get("kind") == "unknown" and record.get("extraction", {}).get("status") != "needs_review":
        _issue(issues, "error", "unknown_task_not_queued", "unknown task must need review", record)

    answer = record.get("answer")
    if not isinstance(answer, dict) or answer.get("kind") != "option":
        _issue(issues, "error", "regular_answer", "regular answer must be option", record)
    elif type(answer.get("value")) is not int or not 1 <= answer["value"] <= 5:
        _issue(issues, "error", "regular_answer_value", "regular answer must be 1..5", record)


def _validate_multiple_blank(record: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    _required(
        record,
        ("instructionText", "passageText", "blanks", "wordBank", "task", "answer"),
        issues,
    )
    if not isinstance(record.get("instructionText"), str) or not record.get("instructionText", "").strip():
        _issue(issues, "error", "blank_instruction", "empty multiple-blank instruction", record)
    if not isinstance(record.get("passageText"), str) or not record.get("passageText", "").strip():
        _issue(issues, "error", "blank_passage", "empty multiple-blank passage", record)
    if tuple(record.get("blanks") or ()) != EXPECTED_BLANKS:
        _issue(issues, "error", "blank_labels", "blanks must be ア,イ,ウ,エ", record)

    word_bank = record.get("wordBank")
    if not isinstance(word_bank, list) or len(word_bank) != 20:
        _issue(issues, "error", "word_bank_count", "word bank must contain 20 terms", record)
    else:
        numbers = [item.get("number") for item in word_bank if isinstance(item, dict)]
        texts_ok = all(
            isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and bool(item["text"].strip())
            for item in word_bank
        )
        if numbers != list(range(1, 21)) or not texts_ok:
            _issue(issues, "error", "word_bank_values", "invalid word bank numbering/text", record)

    task = record.get("task")
    if not isinstance(task, dict) or task.get("kind") != "fill_four_blanks":
        _issue(issues, "error", "blank_task", "invalid multiple-blank task", record)
    answer = record.get("answer")
    values = answer.get("values") if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or answer.get("kind") != "blank_numbers":
        _issue(issues, "error", "blank_answer", "invalid multiple-blank answer kind", record)
    elif not isinstance(values, dict) or tuple(values) != EXPECTED_BLANKS:
        _issue(issues, "error", "blank_answer_labels", "answer labels must be ア,イ,ウ,エ", record)
    elif not all(type(value) is int and 1 <= value <= 20 for value in values.values()):
        _issue(issues, "error", "blank_answer_values", "blank answers must be 1..20", record)


def _validate_written(record: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    _required(
        record,
        (
            "questionText",
            "characterLimit",
            "characterLimitKind",
            "modelAnswer",
            "task",
            "answer",
        ),
        issues,
    )
    for key in ("questionText", "modelAnswer"):
        if not isinstance(record.get(key), str) or not record.get(key, "").strip():
            _issue(issues, "error", "written_text", f"empty {key}", record)
    if "referenceText" in record and not isinstance(record["referenceText"], str):
        _issue(issues, "error", "written_reference", "referenceText must be a string", record)
    if type(record.get("characterLimit")) is not int or not 1 <= record["characterLimit"] <= 200:
        _issue(issues, "error", "written_character_limit", "invalid characterLimit", record)
    if record.get("characterLimitKind") not in {"approximately", "maximum", "minimum", "exact"}:
        _issue(issues, "error", "written_limit_kind", "invalid characterLimitKind", record)
    task = record.get("task")
    if not isinstance(task, dict) or task.get("kind") != "written_response":
        _issue(issues, "error", "written_task", "invalid written task", record)
    answer = record.get("answer")
    if (
        not isinstance(answer, dict)
        or answer.get("kind") != "model_answer"
        or answer.get("value") != record.get("modelAnswer")
    ):
        _issue(issues, "error", "written_answer", "model answer fields disagree", record)


def _snapshot_checks(
    records: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    blob_loader: Callable[[str | Path], bytes] | None = None,
) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        snapshot_id = snapshot.get("snapshotId")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            _issue(issues, "error", "snapshot_id", "snapshot missing snapshotId")
            continue
        if snapshot_id in by_id:
            _issue(issues, "error", "duplicate_snapshot_id", f"duplicate {snapshot_id}")
        by_id[snapshot_id] = snapshot

    hashes: dict[str, str] = {}
    for record in records:
        snapshot = by_id.get(record.get("sourceSnapshotId"))
        if snapshot is None:
            _issue(issues, "error", "snapshot_reference", "source snapshot not found", record)
            continue
        if snapshot.get("fetchStatus") != "ok":
            _issue(issues, "error", "snapshot_status", "source snapshot is not successful", record)
        if snapshot.get("bodySha256") != record.get("sourceBodySha256"):
            _issue(issues, "error", "snapshot_hash_mismatch", "snapshot hash differs", record)
        for key in ("sourceId", "externalQuestionId"):
            if str(snapshot.get(key)) != str(record.get(key)):
                _issue(issues, "error", "snapshot_identity_mismatch", f"snapshot {key} differs", record)
        digest = str(record.get("sourceBodySha256", ""))
        previous = hashes.get(digest)
        if digest and previous and previous != record.get("rawQuestionId"):
            _issue(
                issues,
                "error",
                "duplicate_snapshot_hash",
                f"same HTML hash used by {previous}",
                record,
            )
        hashes[digest] = str(record.get("rawQuestionId"))
        if blob_loader is not None:
            try:
                body = blob_loader(snapshot["bodyPath"])
            except (KeyError, OSError, EOFError) as error:
                _issue(
                    issues,
                    "error",
                    "snapshot_blob_unreadable",
                    f"cannot read snapshot blob: {type(error).__name__}",
                    record,
                )
            else:
                if len(body) != snapshot.get("bodyBytes"):
                    _issue(issues, "error", "snapshot_blob_size", "snapshot blob size differs", record)
                if sha256_bytes(body) != snapshot.get("bodySha256"):
                    _issue(issues, "error", "snapshot_blob_hash", "snapshot blob hash differs", record)


def validate_dataset(
    records: Iterable[dict[str, Any]],
    snapshots: Iterable[dict[str, Any]] | None = None,
    target: dict[str, Any] | None = None,
    *,
    check_coverage: bool = True,
    blob_loader: Callable[[str | Path], bytes] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable validation report without mutating inputs."""

    records = list(records)
    target = target or load_target()
    target_settings = target["target"]
    years = [int(year) for year in target_settings["examYears"]]
    target_years = set(years)
    issues: list[dict[str, Any]] = []

    seen_ids: dict[str, str] = {}
    seen_external: dict[tuple[str, str], str] = {}
    seen_exam_questions: dict[tuple[int, int], str] = {}
    for record in records:
        _validate_common(record, issues, target_years)
        raw_id = str(record.get("rawQuestionId", ""))
        if raw_id in seen_ids:
            _issue(issues, "error", "duplicate_raw_id", f"duplicate {raw_id}", record)
        seen_ids[raw_id] = raw_id
        external_key = (str(record.get("sourceId", "")), str(record.get("externalQuestionId", "")))
        if external_key in seen_external:
            _issue(issues, "error", "duplicate_external_id", f"duplicate {external_key}", record)
        seen_external[external_key] = raw_id
        if type(record.get("examYear")) is int and type(record.get("questionNumber")) is int:
            exam_key = (record["examYear"], record["questionNumber"])
            if exam_key in seen_exam_questions:
                _issue(issues, "error", "duplicate_exam_question", f"duplicate {exam_key}", record)
            seen_exam_questions[exam_key] = raw_id

        kind = record.get("listingKind")
        if kind == "regular":
            _validate_regular(record, issues)
        elif kind == "multiple_blank":
            _validate_multiple_blank(record, issues)
        elif kind == "written":
            _validate_written(record, issues)

    if snapshots is not None:
        _snapshot_checks(records, list(snapshots), issues, blob_loader)

    per_year: dict[int, Counter[str]] = defaultdict(Counter)
    for record in records:
        if record.get("examYear") in target_years and record.get("listingKind") in LISTING_KINDS:
            per_year[record["examYear"]][record["listingKind"]] += 1

    expected_config = target_settings["expectedPerYear"]
    expected = {
        "regular": int(expected_config["regular"]),
        "multiple_blank": int(expected_config["multipleChoice"]),
        "written": int(expected_config["written"]),
    }
    if check_coverage:
        for year in years:
            actual_total = sum(per_year[year].values())
            if actual_total != int(expected_config["total"]):
                _issue(
                    issues,
                    "error",
                    "year_total",
                    f"{year}: expected {expected_config['total']}, got {actual_total}",
                )
            for kind, expected_count in expected.items():
                if per_year[year][kind] != expected_count:
                    _issue(
                        issues,
                        "error",
                        "year_format_count",
                        f"{year} {kind}: expected {expected_count}, got {per_year[year][kind]}",
                    )
        if len(records) != int(target_settings["expectedTotal"]):
            _issue(
                issues,
                "error",
                "corpus_total",
                f"expected {target_settings['expectedTotal']}, got {len(records)}",
            )

    errors = [issue for issue in issues if issue["level"] == "error"]
    warnings = [issue for issue in issues if issue["level"] == "warning"]
    counts = {
        str(year): {
            "regular": per_year[year]["regular"],
            "multiple_blank": per_year[year]["multiple_blank"],
            "written": per_year[year]["written"],
            "total": sum(per_year[year].values()),
        }
        for year in years
    }
    return {
        "ok": not errors,
        "recordCount": len(records),
        "expectedTotal": int(target_settings["expectedTotal"]),
        "countsByYear": counts,
        "errorCount": len(errors),
        "warningCount": len(warnings),
        "issues": issues,
    }


def _documents(path: Path, collection_key: str | None = None) -> list[dict[str, Any]]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    values: list[dict[str, Any]] = []
    for document_path in paths:
        document = load_json(document_path)
        if isinstance(document, list):
            values.extend(item for item in document if isinstance(item, dict))
        elif isinstance(document, dict):
            candidate_keys = tuple(
                dict.fromkeys(key for key in (collection_key, "items", "snapshots") if key)
            )
            collection = next(
                (
                    document[key]
                    for key in candidate_keys
                    if isinstance(document.get(key), list)
                ),
                None,
            )
            if collection is None:
                values.append(document)
            else:
                values.extend(item for item in collection if isinstance(item, dict))
    return values


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=data_root() / "extracted")
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=data_root() / "raw" / "snapshots" / "index.json",
    )
    parser.add_argument("--target", type=Path)
    parser.add_argument("--no-coverage", action="store_true")
    parser.add_argument("--output", type=Path, help="write the JSON report atomically")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    target = load_json(args.target) if args.target else load_target()
    records = [
        value
        for value in _documents(args.records)
        if value.get("schemaVersion") == SCHEMA_VERSION
    ]
    snapshots = _documents(args.snapshots, "snapshots")
    report = validate_dataset(
        records,
        snapshots,
        target,
        check_coverage=not args.no_coverage,
        blob_loader=read_gzip_blob,
    )
    if args.output:
        atomic_write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
