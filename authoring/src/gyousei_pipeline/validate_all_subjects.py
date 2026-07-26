"""Validate one private all-subject provider dataset end to end."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .common import atomic_write_json, load_json, utc_now
from .discover_all_subjects import _expected_numbers
from .fetch import _reusable


REPORT_SCHEMA = "all-subjects-validation@1"


def _json_documents(path: Path) -> list[dict[str, Any]]:
    return [
        value
        for document in sorted(path.rglob("*.json"))
        if isinstance((value := load_json(document)), dict)
    ]


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_dataset(
    *,
    root: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    catalog_path = root / "catalog" / "questions.json"
    snapshots_path = root / "raw" / "snapshots" / "index.json"
    extracted_path = root / "extracted"
    explanations_path = root / "curation" / "provider_explanations.json"
    errors: list[str] = []
    target = config["target"]
    expected_total = int(target["expectedTotal"])

    catalog = load_json(catalog_path)
    snapshots = load_json(snapshots_path)
    explanations = load_json(explanations_path)
    records = _json_documents(extracted_path)
    entries = catalog.get("entries", [])
    snapshot_items = snapshots.get("items", [])
    explanation_items = explanations.get("items", [])

    for label, value in (
        ("catalog entries", entries),
        ("snapshot items", snapshot_items),
        ("extracted records", records),
        ("explanation items", explanation_items),
    ):
        if not isinstance(value, list) or len(value) != expected_total:
            errors.append(
                f"{label}: expected {expected_total}, "
                f"got {len(value) if isinstance(value, list) else 'invalid'}"
            )

    entries_by_id = {
        str(item.get("catalogId")): item
        for item in entries
        if isinstance(item, dict)
    }
    records_by_id = {
        str(item.get("rawQuestionId")): item
        for item in records
        if isinstance(item, dict)
    }
    explanations_by_id = {
        str(item.get("rawQuestionId")): item
        for item in explanation_items
        if isinstance(item, dict)
    }
    snapshots_by_key = {
        (str(item.get("sourceId")), str(item.get("externalQuestionId"))): item
        for item in snapshot_items
        if isinstance(item, dict)
    }
    for label, mapping in (
        ("catalog IDs", entries_by_id),
        ("raw IDs", records_by_id),
        ("explanation IDs", explanations_by_id),
        ("snapshot keys", snapshots_by_key),
    ):
        if len(mapping) != expected_total:
            errors.append(f"{label}: duplicate or missing identifiers")

    years = [int(value) for value in target["examYears"]]
    numbers_by_year: dict[int, set[int]] = defaultdict(set)
    formats: Counter[str] = Counter()
    subjects: Counter[str] = Counter()
    explanation_expectations: Counter[str] = Counter()
    parse_statuses: Counter[str] = Counter()
    answer_kinds: Counter[str] = Counter()

    previous_data_root = os.environ.get("GYOUSEI_DATA_ROOT")
    os.environ["GYOUSEI_DATA_ROOT"] = str(root)
    try:
        for catalog_id, entry in entries_by_id.items():
            year = entry.get("examYear")
            number = entry.get("questionNumber")
            if type(year) is int and type(number) is int:
                numbers_by_year[year].add(number)
            formats[str(entry.get("listingKind"))] += 1
            subjects[str(entry.get("subjectId"))] += 1
            explanation_expectations[
                "available" if entry.get("explanationExpected") is True else "unavailable"
            ] += 1

            snapshot = snapshots_by_key.get(
                (
                    str(entry.get("sourceId")),
                    str(entry.get("externalQuestionId")),
                )
            )
            if snapshot is None:
                errors.append(f"{catalog_id}: snapshot missing")
            elif snapshot.get("url") != entry.get("url"):
                errors.append(f"{catalog_id}: snapshot URL differs")
            elif not _reusable(snapshot):
                errors.append(f"{catalog_id}: snapshot/event/blob integrity failed")

            record = records_by_id.get(catalog_id)
            if record is None:
                errors.append(f"{catalog_id}: extracted record missing")
                continue
            for key in (
                "catalogId",
                "sourceId",
                "externalQuestionId",
                "examYear",
                "questionNumber",
                "listingKind",
                "subjectId",
                "subjectLabel",
                "explanationExpected",
                "historicalUse",
            ):
                expected = catalog_id if key == "catalogId" else entry.get(key)
                if record.get(key) != expected:
                    errors.append(f"{catalog_id}: extracted {key} differs")
            if snapshot and record.get("sourceSnapshotId") != snapshot.get("snapshotId"):
                errors.append(f"{catalog_id}: extracted snapshot ID differs")
            if snapshot and record.get("sourceBodySha256") != snapshot.get("bodySha256"):
                errors.append(f"{catalog_id}: extracted body hash differs")

            extraction = record.get("extraction", {})
            status = str(extraction.get("status"))
            parse_statuses[status] += 1
            if status != "parsed" or extraction.get("warnings") != []:
                errors.append(
                    f"{catalog_id}: extraction is {status} "
                    f"{extraction.get('warnings')}"
                )

            kind = record.get("listingKind")
            answer = record.get("answer")
            answer_kinds[str(answer.get("kind") if isinstance(answer, dict) else None)] += 1
            if kind == "regular":
                choices = record.get("choices")
                if (
                    not isinstance(choices, list)
                    or len(choices) != 5
                    or any(
                        not isinstance(choice, dict)
                        or not str(choice.get("text", "")).strip()
                        for choice in choices
                    )
                ):
                    errors.append(f"{catalog_id}: invalid regular choices")
                value = answer.get("value") if isinstance(answer, dict) else None
                withdrawn = isinstance(answer, dict) and bool(answer.get("note"))
                if not (
                    type(value) is int
                    and 1 <= value <= 5
                    or value is None
                    and withdrawn
                ):
                    errors.append(f"{catalog_id}: invalid regular answer")
            elif kind == "multiple_blank":
                values = answer.get("values") if isinstance(answer, dict) else None
                if (
                    not isinstance(record.get("wordBank"), list)
                    or len(record["wordBank"]) != 20
                    or not isinstance(values, dict)
                    or set(values) != {"ア", "イ", "ウ", "エ"}
                ):
                    errors.append(f"{catalog_id}: invalid multiple-blank data")
            elif kind == "written":
                if (
                    not str(record.get("questionText", "")).strip()
                    or not str(record.get("modelAnswer", "")).strip()
                    or type(record.get("characterLimit")) is not int
                ):
                    errors.append(f"{catalog_id}: invalid written data")
            else:
                errors.append(f"{catalog_id}: invalid listing kind")

            explanation = explanations_by_id.get(catalog_id)
            if explanation is None:
                errors.append(f"{catalog_id}: explanation reference missing")
            else:
                available = explanation.get("explanationAvailable")
                expected_available = entry.get("explanationExpected")
                if available is not expected_available:
                    errors.append(
                        f"{catalog_id}: explanation availability differs"
                    )
                if (
                    explanation.get("sourceBodySha256")
                    != record.get("sourceBodySha256")
                ):
                    errors.append(f"{catalog_id}: explanation body hash differs")
                if expected_available and not str(
                    explanation.get("fullText", "")
                ).strip():
                    errors.append(f"{catalog_id}: expected explanation is empty")
                if not expected_available and (
                    explanation.get("fullText") != ""
                    or not explanation.get("missingReason")
                ):
                    errors.append(
                        f"{catalog_id}: unavailable explanation marker is invalid"
                    )
    finally:
        if previous_data_root is None:
            os.environ.pop("GYOUSEI_DATA_ROOT", None)
        else:
            os.environ["GYOUSEI_DATA_ROOT"] = previous_data_root

    for year in years:
        expected = _expected_numbers(target, year)
        if numbers_by_year[year] != expected:
            errors.append(
                f"{year}: question numbers differ "
                f"({sorted(numbers_by_year[year])})"
            )
    expected_formats = Counter(
        {str(key): int(value) for key, value in target["expectedByFormat"].items()}
    )
    expected_subjects = Counter(
        {str(key): int(value) for key, value in target["expectedBySubject"].items()}
    )
    expected_explanations = Counter(
        {
            str(key): int(value)
            for key, value in target["expectedExplanations"].items()
        }
    )
    if formats != expected_formats:
        errors.append(f"format counts differ: {dict(formats)}")
    if subjects != expected_subjects:
        errors.append(f"subject counts differ: {dict(subjects)}")
    if explanation_expectations != expected_explanations:
        errors.append(
            f"explanation counts differ: {dict(explanation_expectations)}"
        )
    explanation_summary = explanations.get("summary", {})
    if explanation_summary.get("availableCount") != expected_explanations["available"]:
        errors.append("explanation summary availableCount differs")
    if explanation_summary.get("missingCount") != expected_explanations["unavailable"]:
        errors.append("explanation summary missingCount differs")

    report = {
        "schemaVersion": REPORT_SCHEMA,
        "validatedAt": utc_now(),
        "ok": not errors,
        "datasetId": target["datasetId"],
        # Keep the copied report portable between the VPS and Mac.  The
        # validator receives the real root at runtime, but the report itself
        # must not retain a machine-specific absolute path.
        "root": ".",
        "summary": {
            "questionCount": len(entries),
            "snapshotCount": len(snapshot_items),
            "extractedCount": len(records),
            "providerReferenceCount": len(explanation_items),
            "formats": dict(sorted(formats.items())),
            "subjects": dict(sorted(subjects.items())),
            "explanations": dict(sorted(explanation_expectations.items())),
            "parseStatuses": dict(sorted(parse_statuses.items())),
            "answerKinds": dict(sorted(answer_kinds.items())),
        },
        "digests": {
            "configSha256": _digest_file(
                Path(config["_configPath"])
            )
            if "_configPath" in config
            else None,
            "catalogSha256": _digest_file(catalog_path),
            "snapshotIndexSha256": _digest_file(snapshots_path),
            "providerExplanationsSha256": _digest_file(explanations_path),
        },
        "errorCount": len(errors),
        "errors": errors,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config = load_json(args.config)
    config["_configPath"] = str(args.config)
    report = validate_dataset(root=args.root, config=config)
    output = args.output or args.root / "reports" / "validation.json"
    atomic_write_json(output, report)
    os.chmod(output, 0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
