#!/usr/bin/env python3
"""Build a deterministic private weakness snapshot from card attempts only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import server as service


DATA_DIR = Path.home() / ".local" / "share" / "yuki-services" / "gyousei-lab"
DEFAULT_DB_PATH = DATA_DIR / "production.sqlite3"
DEFAULT_BUNDLE_PATH = DATA_DIR / "gyousei-production.json"
DEFAULT_SNAPSHOT_DIR = DATA_DIR / "analytics" / "snapshots"
DEFAULT_LATEST_PATH = DATA_DIR / "weakness-latest.json"

SCHEMA_VERSION = "gyousei-weakness-snapshot@1"
ANALYZER_VERSION = "card-attempts-v2"
# 3はcard_marksを入れる前の版。どちらでも card_attempts の構造は同じなので読める。
SUPPORTED_DATABASE_SCHEMA_VERSIONS = (3, 4)
RECENT_WINDOW_SIZE = 5
MASTERY_SCORE = 3
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
STATUSES = (
    "unlearned",
    "learning",
    "watch",
    "weak",
    "recovering",
    "mastered",
)
TARGET_BASE_PRIORITY = {
    "weak": 300,
    "watch": 200,
}
TARGET_PRIORITY_BANDS = {
    "weak": "high",
    "watch": "medium",
}


class WeaknessAnalysisError(ValueError):
    """Raised when a safe weakness snapshot cannot be built."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise WeaknessAnalysisError("generatedAt must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WeaknessAnalysisError(
            "generatedAt must be an ISO timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise WeaknessAnalysisError("generatedAt must include a timezone")
    return value


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeaknessAnalysisError(f"{name} must be a non-empty string")
    return value.strip()


def _accuracy(correct: int, incorrect: int) -> float | None:
    total = correct + incorrect
    return round(correct / total, 6) if total else None


def _weak_reasons(results: Sequence[bool]) -> list[str]:
    incorrect = sum(not result for result in results)
    if incorrect < 2:
        return []
    reasons: list[str] = []
    if len(results) >= 2 and not results[-1] and not results[-2]:
        reasons.append("consecutive_incorrect_2")
    recent = results[-RECENT_WINDOW_SIZE:]
    recent_incorrect = sum(not result for result in recent)
    recent_accuracy = sum(recent) / len(recent)
    if recent_incorrect >= 2 and recent_accuracy <= 0.5:
        reasons.append("recent_accuracy_lte_50")
    return reasons


def _was_weak_before_trailing_recovery(results: Sequence[bool]) -> bool:
    if len(results) < 4 or not all(results[-2:]):
        return False
    return any(
        _weak_reasons(results[:end])
        for end in range(2, len(results) - 1)
    )


def _streaks(results: Sequence[bool]) -> tuple[int, int]:
    correct_streak = 0
    incorrect_streak = 0
    for result in reversed(results):
        if result:
            if incorrect_streak:
                break
            correct_streak += 1
        else:
            if correct_streak:
                break
            incorrect_streak += 1
    return correct_streak, incorrect_streak


def classify_results(results: Sequence[bool]) -> tuple[str, list[str]]:
    """Classify current-revision results ordered by database ID."""

    if not results:
        return "unlearned", []
    correct = sum(results)
    incorrect = len(results) - correct
    if (
        len(results) >= 3
        and all(results[-3:])
        and correct - incorrect >= MASTERY_SCORE
    ):
        return "mastered", ["mastered_3_correct"]
    if _was_weak_before_trailing_recovery(results):
        return "recovering", ["recovering_2_correct"]
    weak_reasons = _weak_reasons(results)
    if weak_reasons:
        return "weak", weak_reasons
    if incorrect == 1:
        return "watch", ["single_error_watch"]
    if not results[-1]:
        return "watch", ["latest_incorrect_watch"]
    return "learning", []


def _card_metadata(card: Mapping[str, Any]) -> dict[str, str]:
    card_id = _required_text(card.get("id"), name="card.id")
    return {
        "cardId": card_id,
        "subjectId": _required_text(
            card.get("subjectId"), name=f"{card_id}.subjectId"
        ),
        "category": _required_text(
            card.get("category"), name=f"{card_id}.category"
        ),
        "topic": _required_text(card.get("topic"), name=f"{card_id}.topic"),
        "subtopic": _required_text(
            card.get("subtopic"), name=f"{card_id}.subtopic"
        ),
    }


def _empty_status_counts() -> dict[str, int]:
    return {status: 0 for status in STATUSES}


def _group_rows(
    cards: Sequence[dict[str, Any]],
    dimensions: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for card in cards:
        key = tuple(str(card[dimension]) for dimension in dimensions)
        group = groups.setdefault(
            key,
            {
                **{
                    dimension: card[dimension]
                    for dimension in dimensions
                },
                "cardCount": 0,
                "attempts": 0,
                "correct": 0,
                "incorrect": 0,
                "accuracy": None,
                "statusCounts": _empty_status_counts(),
            },
        )
        group["cardCount"] += 1
        group["attempts"] += card["attempts"]
        group["correct"] += card["correct"]
        group["incorrect"] += card["incorrect"]
        group["statusCounts"][card["status"]] += 1
    for group in groups.values():
        group["accuracy"] = _accuracy(group["correct"], group["incorrect"])
    return [groups[key] for key in sorted(groups)]


def _target(card: Mapping[str, Any]) -> dict[str, Any] | None:
    status = str(card["status"])
    base = TARGET_BASE_PRIORITY.get(status)
    if base is None:
        return None
    evidence_bonus = min(
        99,
        int(card["recentIncorrect"]) * 20
        + int(card["incorrectStreak"]) * 10,
    )
    return {
        "cardId": card["cardId"],
        "priority": base + evidence_bonus,
        "priorityBand": TARGET_PRIORITY_BANDS[status],
        "status": status,
        "reasonCodes": list(card["reasonCodes"]),
        "evidence": {
            "attempts": card["attempts"],
            "correct": card["correct"],
            "incorrect": card["incorrect"],
            "recentWindowSize": card["recentWindowSize"],
            "recentIncorrect": card["recentIncorrect"],
            "recentAccuracy": card["recentAccuracy"],
            "correctStreak": card["correctStreak"],
            "incorrectStreak": card["incorrectStreak"],
            "lastAttemptId": card["lastAttemptId"],
        },
    }


def build_weakness_snapshot(
    connection: sqlite3.Connection,
    cards: Iterable[Mapping[str, Any]],
    current_revisions: Mapping[str, str],
    *,
    bundle_revision: str,
    study_deck: Mapping[str, Any] | None,
    known_card_ids: Iterable[str] | None = None,
    generated_at: str,
) -> dict[str, Any]:
    """Build a deterministic snapshot without modifying SQLite."""

    generated = _timestamp(generated_at)
    if not DIGEST_PATTERN.fullmatch(bundle_revision):
        raise WeaknessAnalysisError("bundleRevision must be a SHA-256 digest")
    normalized_cards = [_card_metadata(card) for card in cards]
    card_ids = [card["cardId"] for card in normalized_cards]
    if len(card_ids) != len(set(card_ids)):
        raise WeaknessAnalysisError("cards contain duplicate IDs")
    if set(current_revisions) != set(card_ids):
        raise WeaknessAnalysisError(
            "current revision IDs must exactly match eligible card IDs"
        )
    for card_id, revision in current_revisions.items():
        if not isinstance(revision, str) or not DIGEST_PATTERN.fullmatch(revision):
            raise WeaknessAnalysisError(
                f"{card_id}: current revision must be a SHA-256 digest"
            )

    eligible = set(card_ids)
    known = set(known_card_ids or eligible)
    if not eligible <= known:
        raise WeaknessAnalysisError("eligible cards must be known bundle cards")
    state = {
        card_id: {"current": []}
        for card_id in card_ids
    }
    rows = connection.execute(
        """
        SELECT
            id, card_id, answer_revision, is_correct,
            answered_at_client, response_ms
        FROM card_attempts
        ORDER BY id
        """
    ).fetchall()
    max_attempt_id = max((int(row["id"]) for row in rows), default=0)
    outside_deck = 0
    unknown_card = 0
    for row in rows:
        card_id = str(row["card_id"])
        if card_id not in eligible:
            if card_id in known:
                outside_deck += 1
            else:
                unknown_card += 1
            continue
        # 2026-07-30に方針を変えた。回答はカードIDだけで数える。
        # answer_revision は記録として残すが、弱点分析でも見ない。
        if row["is_correct"] not in (0, 1):
            raise WeaknessAnalysisError(
                f"card attempt {row['id']} has invalid is_correct"
            )
        state[card_id]["current"].append(
            {
                "id": int(row["id"]),
                "isCorrect": bool(row["is_correct"]),
                "answeredAt": row["answered_at_client"],
                "responseMs": row["response_ms"],
            }
        )

    analyzed_cards: list[dict[str, Any]] = []
    for metadata in sorted(normalized_cards, key=lambda item: item["cardId"]):
        card_id = metadata["cardId"]
        attempts = state[card_id]["current"]
        results = [attempt["isCorrect"] for attempt in attempts]
        correct = sum(results)
        incorrect = len(results) - correct
        recent = results[-RECENT_WINDOW_SIZE:]
        recent_correct = sum(recent)
        recent_incorrect = len(recent) - recent_correct
        correct_streak, incorrect_streak = _streaks(results)
        status, reason_codes = classify_results(results)
        response_values = [
            int(attempt["responseMs"])
            for attempt in attempts
            if isinstance(attempt["responseMs"], int)
            and not isinstance(attempt["responseMs"], bool)
        ]
        last_attempt = attempts[-1] if attempts else None
        analyzed_cards.append(
            {
                **metadata,
                "answerRevision": current_revisions[card_id],
                "status": status,
                "reasonCodes": reason_codes,
                "attempts": len(results),
                "correct": correct,
                "incorrect": incorrect,
                "accuracy": _accuracy(correct, incorrect),
                "score": correct - incorrect,
                "recentWindowSize": len(recent),
                "recentResults": [
                    "correct" if result else "incorrect"
                    for result in recent
                ],
                "recentCorrect": recent_correct,
                "recentIncorrect": recent_incorrect,
                "recentAccuracy": _accuracy(
                    recent_correct, recent_incorrect
                ),
                "correctStreak": correct_streak,
                "incorrectStreak": incorrect_streak,
                "lastAttemptId": (
                    int(last_attempt["id"]) if last_attempt else None
                ),
                "lastAnsweredAt": (
                    last_attempt["answeredAt"] if last_attempt else None
                ),
                "responseTime": {
                    "count": len(response_values),
                    "averageMs": (
                        round(sum(response_values) / len(response_values))
                        if response_values
                        else None
                    ),
                    "lastMs": (
                        last_attempt["responseMs"]
                        if last_attempt
                        and isinstance(last_attempt["responseMs"], int)
                        else None
                    ),
                },
            }
        )

    status_counts = Counter(card["status"] for card in analyzed_cards)
    total_correct = sum(card["correct"] for card in analyzed_cards)
    total_incorrect = sum(card["incorrect"] for card in analyzed_cards)
    targets = [
        target
        for card in analyzed_cards
        if (target := _target(card)) is not None
    ]
    targets.sort(key=lambda item: (-item["priority"], item["cardId"]))
    deck_projection = None
    if study_deck is not None:
        deck_projection = {
            "id": _required_text(
                study_deck.get("id"), name="studyDeck.id"
            ),
            "cardCount": len(analyzed_cards),
        }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated,
        "analyzerVersion": ANALYZER_VERSION,
        "bundleRevision": bundle_revision,
        "maxAttemptId": max_attempt_id,
        "visibility": "private_analysis_not_for_web",
        "studyDeck": deck_projection,
        "policy": {
            "attemptSource": "card_attempts",
            "answerAttemptsIncluded": False,
            "revisionScope": "all_attempts_of_the_card_id",
            "attemptOrder": "database_id_ascending",
            "recentWindowSize": RECENT_WINDOW_SIZE,
            "wallClockDecay": False,
            "weakMinimumIncorrect": 2,
            "weakConditions": [
                "latest_two_attempts_incorrect",
                "recent_five_has_two_incorrect_and_accuracy_lte_50",
            ],
            "recoveringCondition": (
                "previously_weak_and_latest_two_attempts_correct"
            ),
            "masteredCondition": (
                "latest_three_attempts_correct_and_correct_minus_incorrect_gte_3"
            ),
        },
        "summary": {
            "cardCount": len(analyzed_cards),
            "statusCounts": {
                status: status_counts.get(status, 0)
                for status in STATUSES
            },
            "targetCount": len(targets),
            "countedAttempts": total_correct + total_incorrect,
            "correct": total_correct,
            "incorrect": total_incorrect,
            "accuracy": _accuracy(total_correct, total_incorrect),
            "allCardAttempts": len(rows),
            "outsideDeckAttemptsIgnored": outside_deck,
            "unknownCardAttemptsIgnored": unknown_card,
        },
        "bySubject": _group_rows(analyzed_cards, ("subjectId",)),
        "byTopic": _group_rows(
            analyzed_cards, ("subjectId", "category", "topic")
        ),
        "bySubtopic": _group_rows(
            analyzed_cards,
            ("subjectId", "category", "topic", "subtopic"),
        ),
        "cards": analyzed_cards,
        "targets": targets,
    }


def atomic_write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace a private JSON document with mode 0600."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                value,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise WeaknessAnalysisError("SQLite database does not exist")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in SUPPORTED_DATABASE_SCHEMA_VERSIONS:
        connection.close()
        raise WeaknessAnalysisError(
            f"unsupported database schema version: {version}"
        )
    check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if check != "ok":
        connection.close()
        raise WeaknessAnalysisError(f"SQLite quick_check failed: {check}")
    return connection


def _snapshot_filename(generated_at: str) -> str:
    safe_timestamp = re.sub(r"[^0-9A-Za-z.-]", "-", generated_at)
    return f"weakness-{safe_timestamp}.json"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument("--study-deck-id")
    parser.add_argument("--snapshot-output", type=Path)
    parser.add_argument(
        "--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR
    )
    parser.add_argument(
        "--latest-output", type=Path, default=DEFAULT_LATEST_PATH
    )
    parser.add_argument("--generated-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        generated_at = _timestamp(args.generated_at or utc_now())
        catalog = service.BundleCatalog(args.bundle)
        bundle_snapshot = catalog.load()
        deck = service.resolve_study_deck(
            bundle_snapshot,
            args.study_deck_id,
            require_if_ambiguous=True,
        )
        eligible_cards = service.cards_for_study_deck(
            bundle_snapshot, deck
        )
        current_revisions = {
            card["id"]: service.card_answer_revision(
                card, bundle_snapshot, deck
            )
            for card in eligible_cards
        }
        connection = _readonly_connection(args.db)
        try:
            snapshot = build_weakness_snapshot(
                connection,
                eligible_cards,
                current_revisions,
                bundle_revision=bundle_snapshot.revision,
                study_deck=deck,
                known_card_ids=bundle_snapshot.cards,
                generated_at=generated_at,
            )
        finally:
            connection.close()
        snapshot_output = args.snapshot_output or (
            args.snapshot_dir / _snapshot_filename(generated_at)
        )
        if snapshot_output.resolve() == args.latest_output.resolve():
            raise WeaknessAnalysisError(
                "snapshot output and latest output must differ"
            )
        atomic_write_private_json(snapshot_output, snapshot)
        atomic_write_private_json(args.latest_output, snapshot)
    except (
        WeaknessAnalysisError,
        service.ApiError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as error:
        print(f"weakness analysis failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "snapshot": str(snapshot_output),
                "latest": str(args.latest_output),
                "schemaVersion": snapshot["schemaVersion"],
                "bundleRevision": snapshot["bundleRevision"],
                "maxAttemptId": snapshot["maxAttemptId"],
                **snapshot["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
