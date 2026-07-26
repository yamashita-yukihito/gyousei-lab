"""Build a sanitized inventory of the private all-subject source datasets.

The output contains counts and definitions only.  It deliberately excludes
question text, provider explanations, source URLs, identifiers, and local
paths, so a copy can be served by the private study application's API.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .candidates import candidates_for_record
from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "gyousei-data-inventory@1"
ARCHITECTURE_VERSION = "2026-07-26"
SUBJECT_ORDER = (
    "legal_foundations",
    "constitutional_law",
    "administrative_law",
    "civil_law",
    "commercial_law",
    "basic_knowledge",
)
SUBJECT_LABELS = {
    "legal_foundations": "基礎法学",
    "constitutional_law": "憲法",
    "administrative_law": "行政法",
    "civil_law": "民法",
    "commercial_law": "商法・会社法",
    "basic_knowledge": "基礎知識",
}
METRIC_KEYS = (
    "questionUnits",
    "regularQuestions",
    "regularChoiceCount",
    "safeOxQuestionCount",
    "safeOxChoiceCount",
    "wholeQuestionQueueCount",
    "multipleBlankQuestions",
    "wordBankEntryCount",
    "blankSlotCount",
    "writtenQuestions",
    "withdrawnQuestionCount",
    "amendedQuestionCount",
    "explanationAvailableCount",
    "explanationUnavailableCount",
)


class DatasetInventoryError(ValueError):
    """Raised when source records cannot produce a reliable inventory."""


def _zero_metrics() -> dict[str, int]:
    return {key: 0 for key in METRIC_KEYS}


def _record_metrics(record: dict[str, Any]) -> tuple[dict[str, int], str]:
    extraction = record.get("extraction")
    if (
        not isinstance(extraction, dict)
        or extraction.get("status") != "parsed"
        or extraction.get("warnings") != []
    ):
        raise DatasetInventoryError("inventory source contains extraction errors or warnings")

    listing_kind = record.get("listingKind")
    metrics = _zero_metrics()
    metrics["questionUnits"] = 1
    metrics["withdrawnQuestionCount"] = int(record.get("isWithdrawn") is True)
    metrics["amendedQuestionCount"] = int(record.get("isAmended") is True)
    explanation_available = record.get("explanationExpected") is True
    metrics[
        "explanationAvailableCount"
        if explanation_available
        else "explanationUnavailableCount"
    ] = 1

    if listing_kind == "regular":
        choices = record.get("choices")
        metrics["regularQuestions"] = 1
        metrics["regularChoiceCount"] = len(choices) if isinstance(choices, list) else 0
    elif listing_kind == "multiple_blank":
        word_bank = record.get("wordBank")
        blanks = record.get("blanks")
        metrics["multipleBlankQuestions"] = 1
        metrics["wordBankEntryCount"] = (
            len(word_bank) if isinstance(word_bank, list) else 0
        )
        metrics["blankSlotCount"] = len(blanks) if isinstance(blanks, list) else 0
    elif listing_kind == "written":
        metrics["writtenQuestions"] = 1
    else:
        raise DatasetInventoryError(f"unsupported listingKind: {listing_kind!r}")

    candidates = candidates_for_record(record)
    if candidates and all(
        item.get("candidateKind") == "choice_proposition" for item in candidates
    ):
        metrics["safeOxQuestionCount"] = 1
        metrics["safeOxChoiceCount"] = len(candidates)
        reason = "split_into_choice_propositions"
    else:
        metrics["wholeQuestionQueueCount"] = 1
        reason = str(
            candidates[0].get("decisionReason")
            if candidates
            else "candidate_generation_returned_no_items"
        )
    return metrics, reason


def _add_metrics(destination: dict[str, int], source: dict[str, int]) -> None:
    for key in METRIC_KEYS:
        destination[key] += int(source.get(key, 0))


def _scope_inventory(
    *,
    scope_id: str,
    label: str,
    records: Iterable[dict[str, Any]],
    historical_use: str,
) -> dict[str, Any]:
    records = list(records)
    if not records:
        raise DatasetInventoryError(f"{scope_id}: no extracted records")

    years: set[int] = set()
    raw_ids: set[str] = set()
    by_subject = {subject_id: _zero_metrics() for subject_id in SUBJECT_ORDER}
    reasons: Counter[str] = Counter()

    for record in records:
        raw_id = record.get("rawQuestionId")
        year = record.get("examYear")
        subject_id = record.get("subjectId")
        if not isinstance(raw_id, str) or not raw_id or raw_id in raw_ids:
            raise DatasetInventoryError(f"{scope_id}: invalid or duplicate rawQuestionId")
        if type(year) is not int:
            raise DatasetInventoryError(f"{scope_id}: invalid examYear")
        if subject_id not in by_subject:
            raise DatasetInventoryError(f"{scope_id}: unknown subjectId {subject_id!r}")
        raw_ids.add(raw_id)
        years.add(year)
        metrics, reason = _record_metrics(record)
        _add_metrics(by_subject[subject_id], metrics)
        if metrics["wholeQuestionQueueCount"]:
            reasons[reason] += 1

    subject_rows = []
    totals = _zero_metrics()
    for subject_id in SUBJECT_ORDER:
        metrics = by_subject[subject_id]
        _add_metrics(totals, metrics)
        subject_rows.append(
            {
                "subjectId": subject_id,
                "subjectLabel": SUBJECT_LABELS[subject_id],
                **metrics,
            }
        )

    return {
        "id": scope_id,
        "label": label,
        "examYears": sorted(years),
        "historicalUse": historical_use,
        "subjects": subject_rows,
        "totals": totals,
        "safeOxExclusionReasons": dict(sorted(reasons.items())),
    }


def _omissions(stored_years: list[int], stored_count: int) -> list[dict[str, Any]]:
    if not stored_years:
        return []
    first, last = min(stored_years), max(stored_years)
    omissions: list[dict[str, Any]] = []
    if stored_years == list(range(first, last + 1)) and first == 2006 and last == 2025:
        omissions.extend(
            (
                {
                    "kind": "publicTextUnavailable",
                    "questionNumbers": [58, 59, 60],
                    "years": 20,
                    "questionUnits": 60,
                    "reason": "著作権上の理由で公開過去問本文に含まれない文章理解",
                },
                {
                    "kind": "providerIndexAbsent",
                    "examYear": 2017,
                    "questionNumber": 39,
                    "questionUnits": 1,
                    "reason": "取得元の年度一覧にリンクがないため未収録",
                },
            )
        )
    expected = len(stored_years) * 60
    explained = sum(int(item["questionUnits"]) for item in omissions)
    unexplained = expected - stored_count - explained
    if unexplained > 0:
        omissions.append(
            {
                "kind": "unexplained",
                "questionUnits": unexplained,
                "reason": "保存件数との差分。データ監査が必要",
            }
        )
    return omissions


def build_data_inventory(
    current_records: Iterable[dict[str, Any]],
    archive_records: Iterable[dict[str, Any]],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a sanitized inventory for current, archive, and combined scopes."""

    current_records = list(current_records)
    archive_records = list(archive_records)
    current = _scope_inventory(
        scope_id="current",
        label="直近10年",
        records=current_records,
        historical_use="current_editorial_reference",
    )
    archive = _scope_inventory(
        scope_id="archive",
        label="旧10年",
        records=archive_records,
        historical_use="frequency_only",
    )
    combined = _scope_inventory(
        scope_id="all",
        label="20年合計",
        records=current_records + archive_records,
        historical_use="current_reference_plus_frequency_history",
    )
    years = combined["examYears"]
    stored = combined["totals"]["questionUnits"]
    expected = len(years) * 60
    return {
        "schemaVersion": SCHEMA_VERSION,
        "architectureVersion": ARCHITECTURE_VERSION,
        "generatedAt": generated_at or utc_now(),
        "examPlan": {
            "examYear": 2026,
            "examDate": "2026-11-08",
            "lawAsOf": "2026-04-01",
            "officialQuestionPlan": {
                "totalQuestions": 60,
                "legalQuestions": 46,
                "basicKnowledgeQuestions": 14,
            },
            "latestConfirmedDetailedFormat": {
                "examYear": 2025,
                "legalRegularQuestions": 40,
                "legalMultipleBlankQuestions": 3,
                "legalWrittenQuestions": 3,
                "basicKnowledgeRegularQuestions": 14,
                "totalPoints": 300,
            },
            "note": "2026年度の細かな形式別配点は事前確定値として扱わない",
        },
        "coverage": {
            "firstExamYear": min(years),
            "lastExamYear": max(years),
            "yearCount": len(years),
            "expectedQuestionUnits": expected,
            "storedQuestionUnits": stored,
            "notStoredQuestionUnits": expected - stored,
            "omissions": _omissions(years, stored),
        },
        "scopes": [current, archive, combined],
        "definitions": {
            "questionUnits": "本試験の問番号単位。5肢択一・多肢選択・記述を各1問と数える",
            "regularChoiceCount": "通常5肢択一の元の選択肢数。1問につき原則5肢",
            "safeOxChoiceCount": "正解番号だけから各肢の真偽を安全に決められる厳格候補数。現行法での有効性確認済みという意味ではない",
            "wholeQuestionQueueCount": "組合せ・個数・多肢選択・記述・没問など、自動で肢別○×へ分解しない問題数",
            "wordBankEntryCount": "多肢選択の画面に並ぶ語群項目数。○×の肢数ではない",
            "blankSlotCount": "多肢選択で埋める空欄数",
            "publishedCards": "本番画面へ公開済みの○×学習カード。取得保存数とは別に数える",
        },
        "privacy": {
            "containsQuestionText": False,
            "containsProviderExplanations": False,
            "containsSourceIdentifiers": False,
            "containsLocalPaths": False,
        },
    }


def _json_documents(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in sorted(path.rglob("*.json")):
        value = load_json(document)
        if not isinstance(value, dict):
            raise DatasetInventoryError(f"non-object JSON in {path}")
        records.append(value)
    return records


def build_argument_parser() -> argparse.ArgumentParser:
    root = data_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current-root",
        type=Path,
        default=root / "all_subjects" / "current_2016_2025",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=root / "all_subjects" / "archive_2006_2015",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "all_subjects" / "data_inventory.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    inventory = build_data_inventory(
        _json_documents(args.current_root / "extracted"),
        _json_documents(args.archive_root / "extracted"),
    )
    atomic_write_json(args.output, inventory)
    os.chmod(args.output, 0o600)
    print(
        f"{args.output}: {inventory['coverage']['storedQuestionUnits']} question units, "
        f"{next(scope for scope in inventory['scopes'] if scope['id'] == 'all')['totals']['safeOxChoiceCount']} safe OX choices"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
