"""Build a private 2006-2015 corpus used only for frequency estimation."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "archive-frequency-corpus@1"
EXPECTED_YEARS = tuple(range(2006, 2016))
EXPECTED_NUMBERS = {
    "regular": frozenset(range(8, 27)),
    "multiple_blank": frozenset({42, 43}),
    "written": frozenset({44}),
}
DEFAULT_INPUT = data_root() / "archive_frequency" / "extracted"
DEFAULT_OUTPUT = data_root() / "archive_frequency" / "corpus.json"


class ArchiveFrequencyError(ValueError):
    """The internal frequency corpus is incomplete or unsafe."""


def _documents(path: Path) -> Iterable[dict[str, Any]]:
    for document_path in sorted(path.rglob("*.json")):
        value = load_json(document_path)
        if isinstance(value, dict):
            yield value


def _project(record: dict[str, Any]) -> dict[str, Any]:
    common = {
        "id": record["rawQuestionId"],
        "examYear": record["examYear"],
        "eraYear": record["eraYear"],
        "questionNumber": record["questionNumber"],
        "title": record["title"],
        "labels": record["labels"],
        "format": record["listingKind"],
        "amended": record["isAmended"],
        "sourceUrl": record["sourceUrl"],
        "sourceBodySha256": record["sourceBodySha256"],
        "task": record["task"],
        "answer": record["answer"],
    }
    if record["listingKind"] == "regular":
        common["content"] = {
            "instruction": record["instructionText"],
            "question": record["questionText"],
            "choices": record["choices"],
            "choiceFormat": record["choiceFormat"],
            "choiceColumns": record["choiceColumns"],
        }
    elif record["listingKind"] == "multiple_blank":
        common["content"] = {
            "instruction": record["instructionText"],
            "passage": record["passageText"],
            "sourceNote": record["sourceNote"],
            "blanks": record["blanks"],
            "wordBank": record["wordBank"],
        }
    else:
        common["content"] = {
            "question": record["questionText"],
            "referenceText": record["referenceText"],
            "characterLimit": record["characterLimit"],
            "characterLimitKind": record["characterLimitKind"],
            "modelAnswer": record["modelAnswer"],
        }
    return common


def build_corpus(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(records)
    if len(records) != 220:
        raise ArchiveFrequencyError(f"expected 220 archive questions, got {len(records)}")
    ids = [record.get("rawQuestionId") for record in records]
    if len(ids) != len(set(ids)):
        raise ArchiveFrequencyError("archive question ids are not unique")

    by_year: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    format_counts: Counter[str] = Counter()
    for record in records:
        if record.get("schemaVersion") != "raw-question@1":
            raise ArchiveFrequencyError("unsupported extracted archive schema")
        if record.get("endpointType") != "archive":
            raise ArchiveFrequencyError(f"not an archive endpoint: {record.get('rawQuestionId')}")
        extraction = record.get("extraction") or {}
        if extraction.get("status") != "parsed" or extraction.get("warnings"):
            raise ArchiveFrequencyError(
                f"archive extraction is not clean: {record.get('rawQuestionId')} {extraction}"
            )
        year = record.get("examYear")
        kind = record.get("listingKind")
        number = record.get("questionNumber")
        if year not in EXPECTED_YEARS or kind not in EXPECTED_NUMBERS:
            raise ArchiveFrequencyError(f"archive record is outside target: {record.get('rawQuestionId')}")
        by_year[year][kind].add(number)
        format_counts[kind] += 1
        answer = record.get("answer") or {}
        if kind == "regular" and not isinstance(answer.get("value"), int):
            note = str(answer.get("note") or "")
            if "没問" not in note and "正解肢なし" not in note:
                raise ArchiveFrequencyError(f"regular answer is missing: {record.get('rawQuestionId')}")
        if kind == "multiple_blank" and set(answer.get("values") or {}) != {"ア", "イ", "ウ", "エ"}:
            raise ArchiveFrequencyError(f"multiple-choice answer is missing: {record.get('rawQuestionId')}")
        if kind == "written" and not str(answer.get("value") or "").strip():
            raise ArchiveFrequencyError(f"written answer is missing: {record.get('rawQuestionId')}")

    for year in EXPECTED_YEARS:
        for kind, expected in EXPECTED_NUMBERS.items():
            if by_year[year].get(kind, set()) != expected:
                raise ArchiveFrequencyError(
                    f"{year} {kind} numbers are incomplete: {sorted(by_year[year].get(kind, set()))}"
                )

    projected = [_project(record) for record in records]
    projected.sort(key=lambda item: (item["examYear"], item["questionNumber"]))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_internal_frequency_only",
        "source": {
            "provider": "合格道場・過去問アーカイブ",
            "indexUrl": "https://www.pro.goukakudojyo.com/worksheet2/w_toparch.php",
            "examYears": list(EXPECTED_YEARS),
        },
        "policy": {
            "purpose": "frequency_estimation_only",
            "showInRelatedPastQuestions": False,
            "answerStored": True,
            "explanationStored": False,
            "note": "当時の答えは正誤方向の内部判定に使う。問題文・答えともWeb表示には使わない。",
        },
        "summary": {
            "questionCount": len(projected),
            "formatCounts": dict(sorted(format_counts.items())),
            "yearCounts": {str(year): sum(len(values) for values in by_year[year].values()) for year in EXPECTED_YEARS},
            "amendedCount": sum(item["amended"] for item in projected),
        },
        "questions": projected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    corpus = build_corpus(_documents(args.input))
    atomic_write_json(args.output, corpus)
    os.chmod(args.output, 0o600)
    print(json.dumps(corpus["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
