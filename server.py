#!/usr/bin/env python3
"""Private production API for the administrative scrivener study app."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import unicodedata
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DATA_DIR = Path.home() / ".local" / "share" / "yuki-services" / "gyousei-lab"
HOST = os.environ.get("GYOUSEI_LAB_HOST", "127.0.0.1")
PORT = int(
    os.environ.get(
        "GYOUSEI_LAB_PORT",
        os.environ.get("PORT", os.environ.get("GYOUSEI_PRODUCTION_PORT", "8817")),
    )
)
DB_PATH = Path(
    os.environ.get(
        "GYOUSEI_LAB_DB",
        os.environ.get(
            "GYOUSEI_PRODUCTION_DB",
            str(DATA_DIR / "production.sqlite3"),
        ),
    )
)
BUNDLE_PATH = Path(
    os.environ.get(
        "GYOUSEI_LAB_BUNDLE",
        os.environ.get(
            "GYOUSEI_PRODUCTION_BUNDLE",
            str(DATA_DIR / "gyousei-production.json"),
        ),
    )
)
INVENTORY_PATH = Path(
    os.environ.get(
        "GYOUSEI_LAB_DATA_INVENTORY",
        str(DATA_DIR / "all-subject-inventory.json"),
    )
)
WEAKNESS_PATH = Path(
    os.environ.get(
        "GYOUSEI_LAB_WEAKNESS_SNAPSHOT",
        str(DATA_DIR / "weakness-latest.json"),
    )
)

MAX_BODY_BYTES = 128 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_INVENTORY_BYTES = 2 * 1024 * 1024
MAX_WEAKNESS_BYTES = 8 * 1024 * 1024
MAX_SELECTED_ANSWER_BYTES = 32 * 1024
MAX_ANSWER_TEXT_CHARS = 20_000
MAX_NOTE_CHARS = 4_000
CLIENT_HEADER = "web-v1"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DIGEST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
ANSWER_REVISION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
QUESTION_FORMATS = {"regular", "multiple_blank", "written"}
DATABASE_SCHEMA_VERSION = 4
# 卒業判定のしきい値。static/app.js の MASTERY_SCORE と同じ値でなければならない。
MASTERY_SCORE = 3
CARD_MARK_ACTIONS = ("certain", "uncertain", "reset", "confidence")
# 回答ごとの自己申告。2026-08-05にFSRSの4段階へ作り替えた。
# **again / hard / good / easy が現在の値**で、意味は「どれだけ苦労して思い出したか」。
# sure / likely / guess は2026-08-05より前の記録で、読むためだけに残している。
# 新しく書けるのは CARD_MARK_RATINGS だけ。
CARD_MARK_RATINGS = ("again", "hard", "good", "easy")
CARD_MARK_LEGACY_CONFIDENCE = ("sure", "likely", "guess")
CARD_MARK_CONFIDENCE = CARD_MARK_RATINGS + CARD_MARK_LEGACY_CONFIDENCE
SIMILARITY_DECISIONS = {"merge", "related", "reject", "defer"}
RELATION_TYPES = {"opposite_claim", "exception", "contrast", "same_topic"}
MERGE_RELATION_TYPE = "same_proposition"
WRITE_LOCK = threading.Lock()
DROP = object()
INVENTORY_METRIC_KEYS = (
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
INVENTORY_DEFINITION_KEYS = {
    "questionUnits",
    "regularChoiceCount",
    "safeOxChoiceCount",
    "wholeQuestionQueueCount",
    "wordBankEntryCount",
    "blankSlotCount",
    "publishedCards",
}
INVENTORY_EXCLUSION_REASONS = {
    "format_multiple_blank_requires_question_level_review",
    "format_written_requires_question_level_review",
    "regular_answer_option_invalid",
    "task_combination_requires_question_level_review",
    "task_count_requires_question_level_review",
    "task_unknown_requires_question_level_review",
    "withdrawn_question_requires_question_level_review",
}
WEAKNESS_SCHEMA_VERSION = "gyousei-weakness-snapshot@1"
WEAKNESS_ANALYZER_VERSION = "card-attempts-v2"
WEAKNESS_STATUSES = (
    "unlearned",
    "learning",
    "watch",
    "weak",
    "recovering",
    "mastered",
)
WEAKNESS_TARGET_STATUSES = {"watch", "weak"}
WEAKNESS_REASON_CODES = {
    "consecutive_incorrect_2",
    "recent_accuracy_lte_50",
    "single_error_watch",
    "latest_incorrect_watch",
    "recovering_2_correct",
    "mastered_3_correct",
    "stale_revision_ignored",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def next_card_event_time(connection: sqlite3.Connection) -> str:
    """回答と印の追記順が、同一ミリ秒でも逆転しない時刻を返す。"""
    latest = connection.execute(
        """
        SELECT MAX(created_at_server)
        FROM (
            SELECT created_at_server FROM card_attempts
            UNION ALL
            SELECT created_at_server FROM card_marks
        )
        """
    ).fetchone()[0]
    candidate = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
    if latest is not None:
        latest_time = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        if candidate <= latest_time:
            candidate = latest_time + timedelta(milliseconds=1)
    return candidate.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(ID_PATTERN.fullmatch(value))


def parse_timestamp(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 40:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}") from error
    return value


def short_text(
    value: object,
    field: str,
    maximum: int,
    *,
    required: bool = False,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
    if required and not value.strip():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
    return value


def canonical_json(value: object, field: str, maximum_bytes: int) -> str:
    def validate(current: object, depth: int = 0) -> None:
        if depth > 12:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is too deeply nested")
        if current is None or type(current) in {bool, int, str}:
            if isinstance(current, str) and len(current) > 20_000:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
            return
        if type(current) is float:
            if not math.isfinite(current):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
            return
        if isinstance(current, list):
            if len(current) > 500:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
            for item in current:
                validate(item, depth + 1)
            return
        if isinstance(current, dict):
            if len(current) > 500:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
            for key, item in current.items():
                if not isinstance(key, str) or not key or len(key) > 200:
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")
                validate(item, depth + 1)
            return
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {field}")

    validate(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"{field} is too large")
    return encoded


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _looks_like_internal_path(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(
        (
            "/home/",
            "/users/",
            "/root/",
            "/private/",
            "/var/",
            "/tmp/",
            "/etc/",
            "file://",
        )
    )


def _unsafe_public_key(key: str, ancestors: tuple[str, ...]) -> bool:
    normalized = _normalized_key(key)
    normalized_ancestors = {_normalized_key(value) for value in ancestors}
    if normalized == "paircontentdigest":
        return False
    if normalized == "providerexplanationcaptured":
        return False
    if key.startswith("_") or normalized.startswith("internal"):
        return True
    # 取得元の内部ID。bundle生成でも落としているが、作り直す前の古いbundleが
    # 載っていると projection を素通りしてしまう（2026-08-05の再レビュー指摘）。
    if normalized == "rawid" and "sourcerefs" in normalized_ancestors:
        return True
    if normalized in {
        "snapshotid",
        "bodysha256",
        "parserversion",
        "sourcedigests",
        "process",
        "command",
        "streamevidence",
        "response",
        "sha256",
        "inventorydigest",
    }:
        return True
    if normalized.endswith("sha256") or normalized.endswith("digest"):
        return True
    if normalized.endswith("path") or normalized.endswith("paths"):
        return True
    if "provider" in normalized and any(
        token in normalized for token in ("explanation", "prompt", "response", "output")
    ):
        return True
    if normalized in {
        "rawprompt",
        "rawresponse",
        "providerprompt",
        "providerresponse",
        "stdout",
        "stderr",
    }:
        return True
    provider_context = any("provider" in value for value in normalized_ancestors)
    source_context = "source" in normalized_ancestors
    if normalized in {"explanation", "explanationhtml", "explanationtext"} and (
        provider_context or source_context
    ):
        return True
    return False


def public_projection(
    value: object,
    *,
    ancestors: tuple[str, ...] = (),
    depth: int = 0,
) -> object:
    """Return JSON-safe bundle data without private/provider-only material."""
    if depth > 50:
        raise ValueError("bundle nesting is too deep")
    if isinstance(value, dict):
        projected: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str) or _unsafe_public_key(key, ancestors):
                continue
            safe_child = public_projection(
                child,
                ancestors=ancestors + (key,),
                depth=depth + 1,
            )
            if safe_child is not DROP:
                projected[key] = safe_child
        return projected
    if isinstance(value, list):
        projected_list = []
        for child in value:
            safe_child = public_projection(child, ancestors=ancestors, depth=depth + 1)
            if safe_child is not DROP:
                projected_list.append(safe_child)
        return projected_list
    if isinstance(value, str) and _looks_like_internal_path(value):
        return DROP
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("bundle contains a non-JSON value")


def _inventory_text(value: object, maximum: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid inventory text")
    if _looks_like_internal_path(value):
        raise ValueError("inventory contains an internal path")
    return value


def _inventory_count(value: object) -> int:
    if type(value) is not int or value < 0 or value > 10_000_000:
        raise ValueError("invalid inventory count")
    return value


def _inventory_metrics(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("invalid inventory metrics")
    return {key: _inventory_count(value.get(key)) for key in INVENTORY_METRIC_KEYS}


def _project_inventory(decoded: object) -> dict:
    """Rebuild the public inventory from a fixed schema and known fields only."""

    if not isinstance(decoded, dict):
        raise ValueError("inventory root must be an object")
    if decoded.get("schemaVersion") != "gyousei-data-inventory@1":
        raise ValueError("unsupported inventory schema")

    exam_plan = decoded.get("examPlan")
    official_plan = exam_plan.get("officialQuestionPlan") if isinstance(exam_plan, dict) else None
    detailed = (
        exam_plan.get("latestConfirmedDetailedFormat")
        if isinstance(exam_plan, dict)
        else None
    )
    if not all(isinstance(item, dict) for item in (exam_plan, official_plan, detailed)):
        raise ValueError("invalid exam plan")
    projected_exam_plan = {
        "examYear": _inventory_count(exam_plan.get("examYear")),
        "examDate": _inventory_text(exam_plan.get("examDate"), 20),
        "lawAsOf": _inventory_text(exam_plan.get("lawAsOf"), 20),
        "officialQuestionPlan": {
            "totalQuestions": _inventory_count(official_plan.get("totalQuestions")),
            "legalQuestions": _inventory_count(official_plan.get("legalQuestions")),
            "basicKnowledgeQuestions": _inventory_count(
                official_plan.get("basicKnowledgeQuestions")
            ),
        },
        "latestConfirmedDetailedFormat": {
            key: _inventory_count(detailed.get(key))
            for key in (
                "examYear",
                "legalRegularQuestions",
                "legalMultipleBlankQuestions",
                "legalWrittenQuestions",
                "basicKnowledgeRegularQuestions",
                "totalPoints",
            )
        },
        "note": _inventory_text(exam_plan.get("note")),
    }

    coverage = decoded.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("invalid inventory coverage")
    omission_items = coverage.get("omissions")
    if not isinstance(omission_items, list) or len(omission_items) > 20:
        raise ValueError("invalid inventory omissions")
    projected_omissions = []
    for item in omission_items:
        if not isinstance(item, dict):
            raise ValueError("invalid inventory omission")
        projected = {
            "kind": _inventory_text(item.get("kind"), 50),
            "questionUnits": _inventory_count(item.get("questionUnits")),
            "reason": _inventory_text(item.get("reason")),
        }
        for key in ("years", "examYear", "questionNumber"):
            if item.get(key) is not None:
                projected[key] = _inventory_count(item.get(key))
        if item.get("questionNumbers") is not None:
            numbers = item.get("questionNumbers")
            if (
                not isinstance(numbers, list)
                or len(numbers) > 20
                or any(type(number) is not int for number in numbers)
            ):
                raise ValueError("invalid inventory omission question numbers")
            projected["questionNumbers"] = [_inventory_count(number) for number in numbers]
        projected_omissions.append(projected)
    projected_coverage = {
        key: _inventory_count(coverage.get(key))
        for key in (
            "firstExamYear",
            "lastExamYear",
            "yearCount",
            "expectedQuestionUnits",
            "storedQuestionUnits",
            "notStoredQuestionUnits",
        )
    }
    projected_coverage["omissions"] = projected_omissions

    scopes = decoded.get("scopes")
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 10:
        raise ValueError("invalid inventory scopes")
    projected_scopes = []
    seen_scope_ids: set[str] = set()
    for scope in scopes:
        if not isinstance(scope, dict):
            raise ValueError("invalid inventory scope")
        scope_id = _inventory_text(scope.get("id"), 40)
        if scope_id in seen_scope_ids:
            raise ValueError("duplicate inventory scope")
        seen_scope_ids.add(scope_id)
        years = scope.get("examYears")
        subjects = scope.get("subjects")
        reasons = scope.get("safeOxExclusionReasons")
        if (
            not isinstance(years, list)
            or not isinstance(subjects, list)
            or not isinstance(reasons, dict)
            or len(subjects) > 20
        ):
            raise ValueError("invalid inventory scope contents")
        projected_subjects = []
        for subject in subjects:
            if not isinstance(subject, dict):
                raise ValueError("invalid inventory subject")
            projected_subjects.append(
                {
                    "subjectId": _inventory_text(subject.get("subjectId"), 80),
                    "subjectLabel": _inventory_text(subject.get("subjectLabel"), 80),
                    **_inventory_metrics(subject),
                }
            )
        projected_reasons = {
            reason: _inventory_count(count)
            for reason, count in reasons.items()
            if reason in INVENTORY_EXCLUSION_REASONS
        }
        projected_scopes.append(
            {
                "id": scope_id,
                "label": _inventory_text(scope.get("label"), 80),
                "examYears": [_inventory_count(year) for year in years],
                "historicalUse": _inventory_text(scope.get("historicalUse"), 100),
                "subjects": projected_subjects,
                "totals": _inventory_metrics(scope.get("totals")),
                "safeOxExclusionReasons": projected_reasons,
            }
        )

    definitions = decoded.get("definitions")
    privacy = decoded.get("privacy")
    if not isinstance(definitions, dict) or not isinstance(privacy, dict):
        raise ValueError("invalid inventory metadata")
    projected_definitions = {
        key: _inventory_text(definitions.get(key), 500)
        for key in INVENTORY_DEFINITION_KEYS
    }
    projected_privacy = {}
    for key in (
        "containsQuestionText",
        "containsProviderExplanations",
        "containsSourceIdentifiers",
        "containsLocalPaths",
    ):
        if type(privacy.get(key)) is not bool:
            raise ValueError("invalid inventory privacy marker")
        projected_privacy[key] = privacy[key]

    return {
        "available": True,
        "schemaVersion": "gyousei-data-inventory@1",
        "architectureVersion": _inventory_text(
            decoded.get("architectureVersion"), 40
        ),
        "generatedAt": _inventory_text(decoded.get("generatedAt"), 40),
        "examPlan": projected_exam_plan,
        "coverage": projected_coverage,
        "scopes": projected_scopes,
        "definitions": projected_definitions,
        "privacy": projected_privacy,
    }


def data_inventory() -> dict:
    """Load the sanitized count inventory without exposing filesystem details."""

    try:
        if INVENTORY_PATH.stat().st_size > MAX_INVENTORY_BYTES:
            raise ValueError("inventory is too large")
        decoded = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        return _project_inventory(decoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {
            "available": False,
            "schemaVersion": "gyousei-data-inventory@1",
            "message": "過去問データの集計ファイルを確認できません",
        }


def _list_section(bundle: dict[str, object], primary: str, *aliases: str) -> list[dict]:
    value: object | None = bundle.get(primary)
    if value is None:
        for alias in aliases:
            if bundle.get(alias) is not None:
                value = bundle[alias]
                break
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"bundle section {primary} must be a list of objects")
    return value


def _study_deck_section(bundle: dict[str, object]) -> list[dict]:
    value = bundle.get("studyDecks")
    if value is None:
        value = bundle.get("studyDeck")
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("bundle section studyDecks must be an object or list of objects")
    return value


@dataclass(frozen=True)
class BundleSnapshot:
    bundle: dict[str, object]
    questions: dict[str, dict]
    cards: dict[str, dict]
    study_decks: dict[str, dict]
    similarity_pairs: dict[str, dict]
    revision: str
    loaded_at: str
    fingerprint: tuple[int, int]

    def metadata(self) -> dict:
        return {
            "schemaVersion": self.bundle.get("schemaVersion"),
            "generatedAt": self.bundle.get("generatedAt"),
            "visibility": self.bundle.get("visibility"),
            "legalAsOf": self.bundle.get("legalAsOf"),
            "revision": self.revision,
            "loadedAt": self.loaded_at,
        }


class BundleCatalog:
    """Load an atomically replaced production bundle, cached by mtime and size."""

    def __init__(self, path: Path):
        self.path = path
        self._snapshot: BundleSnapshot | None = None
        self._failed_fingerprint: tuple[int, int] | None = None
        self._last_reload_failed = False
        self._lock = threading.Lock()

    def _read_stable(self) -> tuple[bytes, tuple[int, int]]:
        for _ in range(2):
            before = self.path.stat()
            if before.st_size > MAX_BUNDLE_BYTES:
                raise ValueError("bundle is too large")
            raw = self.path.read_bytes()
            after = self.path.stat()
            before_fingerprint = (before.st_mtime_ns, before.st_size)
            after_fingerprint = (after.st_mtime_ns, after.st_size)
            if before_fingerprint == after_fingerprint and len(raw) == after.st_size:
                return raw, after_fingerprint
        raise ValueError("bundle changed while being read")

    def _build_snapshot(
        self,
        raw: bytes,
        fingerprint: tuple[int, int],
    ) -> BundleSnapshot:
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("bundle root must be an object")
        projected = public_projection(decoded)
        if not isinstance(projected, dict):
            raise ValueError("bundle projection failed")

        questions_list = _list_section(projected, "questions", "items")
        questions: dict[str, dict] = {}
        for question in questions_list:
            question_id = question.get("id")
            question_format = question.get("format")
            if not valid_id(question_id):
                raise ValueError("bundle contains an invalid question id")
            if question_format not in QUESTION_FORMATS:
                raise ValueError("bundle contains an invalid question format")
            if question_id in questions:
                raise ValueError("bundle contains a duplicate question id")
            questions[question_id] = question

        card_list = _list_section(projected, "explanationCards", "cards")
        cards: dict[str, dict] = {}
        for card in card_list:
            card_id = card.get("id")
            variants = card.get("variants")
            if not valid_id(card_id):
                raise ValueError("bundle contains an invalid card id")
            if card_id in cards:
                raise ValueError("bundle contains a duplicate card id")
            if card_id in questions:
                raise ValueError("bundle card and question ids must not overlap")
            if type(card.get("correct")) is not bool:
                raise ValueError("bundle contains a card without a boolean correct answer")
            if (
                not isinstance(variants, dict)
                or not isinstance(variants.get("a"), str)
                or not variants["a"].strip()
                or not isinstance(variants.get("c"), str)
                or not variants["c"].strip()
            ):
                raise ValueError("bundle contains a card without variants a and c")
            law_as_of = card.get("lawAsOf")
            if law_as_of is not None and (
                not isinstance(law_as_of, str) or not law_as_of.strip()
            ):
                raise ValueError("bundle contains an invalid card lawAsOf")
            cards[card_id] = card

        deck_list = _study_deck_section(projected)
        study_decks: dict[str, dict] = {}
        for deck in deck_list:
            deck_id = deck.get("id")
            card_ids = deck.get("cardIds")
            if not valid_id(deck_id):
                raise ValueError("bundle contains an invalid study deck id")
            if deck_id in study_decks:
                raise ValueError("bundle contains a duplicate study deck id")
            if (
                not isinstance(card_ids, list)
                or not card_ids
                or not all(valid_id(card_id) for card_id in card_ids)
                or len(card_ids) != len(set(card_ids))
            ):
                raise ValueError("bundle contains invalid study deck cardIds")
            if any(card_id not in cards for card_id in card_ids):
                raise ValueError("study deck refers to an unknown card")
            law_as_of = deck.get("lawAsOf")
            if law_as_of is not None and (
                not isinstance(law_as_of, str) or not law_as_of.strip()
            ):
                raise ValueError("bundle contains an invalid study deck lawAsOf")
            study_decks[deck_id] = deck

        pair_list = _list_section(projected, "similarityPairs", "similarities")
        similarity_pairs: dict[str, dict] = {}
        for pair in pair_list:
            pair_id = pair.get("id")
            digest = pair.get("pairContentDigest")
            if not valid_id(pair_id):
                raise ValueError("bundle contains an invalid similarity pair id")
            if (
                not isinstance(digest, str)
                or not DIGEST_PATTERN.fullmatch(digest)
            ):
                raise ValueError("bundle contains an invalid pairContentDigest")
            if pair_id in similarity_pairs:
                raise ValueError("bundle contains a duplicate similarity pair id")
            similarity_pairs[pair_id] = pair

        revision = hashlib.sha256(raw).hexdigest()
        return BundleSnapshot(
            bundle=projected,
            questions=questions,
            cards=cards,
            study_decks=study_decks,
            similarity_pairs=similarity_pairs,
            revision=revision,
            loaded_at=utc_now(),
            fingerprint=fingerprint,
        )

    def load(self) -> BundleSnapshot:
        with self._lock:
            try:
                stat = self.path.stat()
                fingerprint = (stat.st_mtime_ns, stat.st_size)
                if self._snapshot and fingerprint == self._snapshot.fingerprint:
                    self._last_reload_failed = False
                    return self._snapshot
                if self._snapshot and fingerprint == self._failed_fingerprint:
                    self._last_reload_failed = True
                    return self._snapshot
                raw, stable_fingerprint = self._read_stable()
                snapshot = self._build_snapshot(raw, stable_fingerprint)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._last_reload_failed = True
                try:
                    stat = self.path.stat()
                    self._failed_fingerprint = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    self._failed_fingerprint = None
                if self._snapshot is not None:
                    return self._snapshot
                raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "bundle unavailable")

            self._snapshot = snapshot
            self._failed_fingerprint = None
            self._last_reload_failed = False
            return snapshot

    def status(self) -> dict:
        try:
            snapshot = self.load()
        except ApiError:
            return {
                "available": False,
                "stale": False,
            }
        return {
            "available": True,
            "stale": self._last_reload_failed,
            **snapshot.metadata(),
        }

    def question(self, question_id: str) -> tuple[dict, BundleSnapshot]:
        snapshot = self.load()
        question = snapshot.questions.get(question_id)
        if question is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown questionId")
        return question, snapshot

    def similarity_pair(self, pair_id: str) -> tuple[dict, BundleSnapshot]:
        snapshot = self.load()
        pair = snapshot.similarity_pairs.get(pair_id)
        if pair is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown pairId")
        return pair, snapshot

    def card(self, card_id: str) -> tuple[dict, BundleSnapshot]:
        snapshot = self.load()
        card = snapshot.cards.get(card_id)
        if card is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown cardId")
        return card, snapshot


CATALOG = BundleCatalog(BUNDLE_PATH)

# ---- 画面からのカード編集 -------------------------------------------------
# 正本を書き換えてから bundle を作り直し、その場で差し替える。検証ルールは
# card_edit.py が正本で、authoring/tools/card_exchange.py と同じものを通す。
# bundle生成側の収録減チェック（--compare-to）もそのまま働く。
CARD_EDIT_LOCK = threading.Lock()
AUTHORING_SRC = Path(__file__).resolve().parent / "authoring" / "src"


def _card_edit_modules():
    """編集を実際に使うときだけ読み込む。起動時に authoring 側の有無へ依存させない。"""
    import sys

    if str(AUTHORING_SRC) not in sys.path:
        sys.path.insert(0, str(AUTHORING_SRC))
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import card_edit
    from gyousei_pipeline import production_bundle

    return card_edit, production_bundle


def _rebuild_bundle_in_place() -> str:
    """正本から bundle を作り直し、稼働中のものと差し替えて revision を返す。"""
    import io
    import contextlib

    _, production_bundle = _card_edit_modules()
    # 出力先は生成側が非公開のbuild rootへ限っている。既定のreleasesへ書かせ、
    # 手作業のときと同じように、そこから稼働中のbundleへ写す。
    # 件数のfail closed検証は、稼働中のbundleと同じ数を要求する。この口はカードの
    # 中身を直すだけで、増減はしないため。既定値（55など）のままでは必ず落ちる。
    summary = CATALOG.load().bundle.get("summary") or {}
    argv = ["--compare-to", str(BUNDLE_PATH)]
    for option, key in (
        ("--expected-card-count", "explanationCardCount"),
        ("--expected-evidence-count", "relatedQuestionEvidenceCount"),
        ("--expected-similarity-count", "similarityPairCount"),
    ):
        value = summary.get(key)
        if not isinstance(value, int):
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"published bundle has no {key}; rebuild by hand first",
            )
        argv += [option, str(value)]
    stderr = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
        code = production_bundle.main(argv)
    if code != 0:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "bundle rebuild failed: " + (stderr.getvalue().strip() or "unknown"),
        )
    released = production_bundle.data_root() / "builds" / "releases" / "gyousei-production.json"
    staging = BUNDLE_PATH.with_name(".gyousei-production.install.json")
    try:
        staging.write_bytes(released.read_bytes())
        os.chmod(staging, 0o600)
        os.replace(staging, BUNDLE_PATH)
    finally:
        staging.unlink(missing_ok=True)
    return CATALOG.load().revision


def save_card_edit(payload: dict) -> dict:
    card_id = payload.get("cardId")
    if not valid_id(card_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid cardId")
    editable = payload.get("editable")
    if not isinstance(editable, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "editable must be an object")
    card_edit, _ = _card_edit_modules()
    # 編集してよい項目の一覧も card_edit.py が正本。ここで持ち直さない。
    unknown = sorted(set(editable) - set(card_edit.EDITABLE_FIELDS))
    if unknown:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, "editable has unknown fields: " + ", ".join(unknown)
        )

    with CARD_EDIT_LOCK:
        document = card_edit.load_canonical()
        index = next(
            (i for i, item in enumerate(document["items"]) if item["id"] == card_id),
            None,
        )
        if index is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown cardId")

        merged = card_edit.merge_editable(document["items"][index], editable)
        problems: list[str] = []
        card_edit.validate_card(
            merged,
            known_ids={item["id"] for item in document["items"]},
            known_choice_ids=set(),
            is_new=False,
            problems=problems,
        )
        if problems:
            raise ApiError(HTTPStatus.BAD_REQUEST, "; ".join(problems))

        before = document["items"][index]
        if json.dumps(before, ensure_ascii=False, sort_keys=True) == json.dumps(
            merged, ensure_ascii=False, sort_keys=True
        ):
            snapshot = CATALOG.load()
            return {
                "saved": False,
                "unchanged": True,
                "cardId": card_id,
                "bundle": snapshot.metadata(),
            }

        # 作り直しに失敗したら正本ごと元へ戻す。画面に出ない内容が正本に残らないようにする。
        rollback = json.loads(json.dumps(document, ensure_ascii=False))
        document["items"][index] = merged
        card_edit.write_canonical(document)
        try:
            revision = _rebuild_bundle_in_place()
        except BaseException:
            card_edit.write_canonical(rollback)
            CATALOG.load()
            raise

    snapshot = CATALOG.load()
    return {
        "saved": True,
        "unchanged": False,
        "cardId": card_id,
        "revision": revision,
        "card": card_for_response(
            snapshot.cards[card_id], snapshot, default_study_deck(snapshot)
        ),
        "bundle": snapshot.metadata(),
    }


def default_study_deck(snapshot: BundleSnapshot) -> dict | None:
    if not snapshot.study_decks:
        return None
    explicit = [
        deck
        for deck in snapshot.study_decks.values()
        if deck.get("default") is True or deck.get("active") is True
    ]
    if len(explicit) == 1:
        return explicit[0]
    if len(snapshot.study_decks) == 1:
        return next(iter(snapshot.study_decks.values()))
    return None


def resolve_study_deck(
    snapshot: BundleSnapshot,
    requested_id: object,
    *,
    require_if_ambiguous: bool,
) -> dict | None:
    if requested_id is not None:
        if not valid_id(requested_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid studyDeckId")
        deck = snapshot.study_decks.get(requested_id)
        if deck is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown studyDeckId")
        return deck
    deck = default_study_deck(snapshot)
    if deck is not None:
        return deck
    if snapshot.study_decks and require_if_ambiguous:
        raise ApiError(HTTPStatus.BAD_REQUEST, "studyDeckId is required")
    return None


def study_deck_id_from_payload(payload: dict) -> object:
    study_deck_id = payload.get("studyDeckId")
    deck_id = payload.get("deckId")
    if study_deck_id is not None and deck_id is not None and study_deck_id != deck_id:
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting study deck identifiers")
    return study_deck_id if study_deck_id is not None else deck_id


def card_law_as_of(
    card: dict,
    snapshot: BundleSnapshot,
    deck: dict | None = None,
) -> str:
    values = (
        card.get("lawAsOf"),
        deck.get("lawAsOf") if deck else None,
        snapshot.bundle.get("legalAsOf"),
    )
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


DISPLAY_MARKUP_PATTERN = re.compile(r"(\*\*|__|==|!!|%%|@@)(.+?)\1", re.DOTALL)


def strip_display_markup(value: object) -> object:
    """本文の装飾記法を取り除く。

    装飾は表示だけの変更で、命題そのものは変わらない。回答revisionの算出から
    外さないと、色を付けただけで過去の回答が習得判定から落ちてしまう。
    """
    if not isinstance(value, str):
        return value
    previous = None
    current = value
    while previous != current:
        previous = current
        current = DISPLAY_MARKUP_PATTERN.sub(r"\2", current)
    return current


def card_answer_revision(
    card: dict,
    snapshot: BundleSnapshot,
    deck: dict | None = None,
) -> str:
    variants = card.get("variants") or {}
    revision_source = {
        "a": strip_display_markup(variants.get("a")),
        "c": strip_display_markup(variants.get("c")),
        "correct": card.get("correct"),
        "lawAsOf": card_law_as_of(card, snapshot, deck),
    }
    encoded = json.dumps(
        revision_source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def card_for_response(
    card: dict,
    snapshot: BundleSnapshot,
    deck: dict | None = None,
) -> dict:
    result = dict(card)
    result["lawAsOf"] = card_law_as_of(card, snapshot, deck)
    result["answerRevision"] = card_answer_revision(card, snapshot, deck)
    return result


def cards_for_study_deck(
    snapshot: BundleSnapshot,
    deck: dict | None,
) -> list[dict]:
    if deck is None:
        return list(snapshot.cards.values())
    return [snapshot.cards[card_id] for card_id in deck["cardIds"]]


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


# `card_marks` の定義。init_database と、CHECK制約を広げる移行の両方で使う。
# 二重に書くと、片方だけ直したときに作り直しで制約が戻ってしまう。
CARD_MARKS_DDL = """
CREATE TABLE IF NOT EXISTS card_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    study_deck_id TEXT,
    card_id TEXT,
    answer_revision TEXT,
    attempt_event_id TEXT,
    action TEXT NOT NULL
        CHECK (action IN ('certain', 'uncertain', 'reset', 'confidence')),
    scope TEXT NOT NULL CHECK (scope IN ('card', 'deck')),
    -- again/hard/good/easy が現在の値。sure/likely/guess は
    -- 2026-08-05より前の記録で、読むためだけに残している。
    confidence TEXT
        CHECK (
            confidence IS NULL
            OR confidence IN (
                'again', 'hard', 'good', 'easy',
                'sure', 'likely', 'guess'
            )
        ),
    marked_at_client TEXT NOT NULL,
    app_version TEXT,
    payload_digest TEXT NOT NULL,
    created_at_server TEXT NOT NULL,
    CHECK (
        (scope = 'deck' AND action = 'reset' AND card_id IS NULL)
        OR (scope = 'card' AND card_id IS NOT NULL)
    ),
    CHECK (
        (
            action = 'confidence'
            AND confidence IS NOT NULL
            AND attempt_event_id IS NOT NULL
        )
        OR (
            action <> 'confidence'
            AND confidence IS NULL
            AND attempt_event_id IS NULL
        )
    )
);
"""


def _migrate_card_marks_confidence(connection: sqlite3.Connection) -> bool:
    """`card_marks.confidence` のCHECK制約へFSRSの4段階を足す。

    SQLiteはCHECK制約をALTERで変えられないので、テーブルを作り直して移し替える。
    **既存の行はそのまま運ぶ**（sure/likely/guess も引き続き読める値として残す）。

    作り直しは1回だけで、2回目以降は制約を見て何もしない。失敗したときは
    トランザクションごと巻き戻るので、元のテーブルが残る。
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='card_marks'"
    ).fetchone()
    if row is None or "'again'" in (row[0] or ""):
        return False

    before = connection.execute("SELECT COUNT(*) FROM card_marks").fetchone()[0]
    columns = [
        info[1] for info in connection.execute("PRAGMA table_info(card_marks)")
    ]
    column_list = ", ".join(columns)
    connection.execute("ALTER TABLE card_marks RENAME TO card_marks_old")
    connection.executescript(CARD_MARKS_DDL)
    connection.execute(
        f"INSERT INTO card_marks ({column_list}) SELECT {column_list} FROM card_marks_old"
    )
    after = connection.execute("SELECT COUNT(*) FROM card_marks").fetchone()[0]
    if after != before:
        # 件数が合わなければ移し替えを完了させない。呼び出し側で巻き戻す。
        raise RuntimeError(
            f"card_marks の移行で件数が変わった: {before} -> {after}"
        )
    connection.execute("DROP TABLE card_marks_old")
    return True


def init_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with connect() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        # 3 は card_marks を入れる前の版。表を足すだけなので既存の行には触れない。
        if schema_version not in {0, 3, DATABASE_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported database schema version: {schema_version}"
            )
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS answer_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                question_format TEXT NOT NULL
                    CHECK (question_format IN ('regular', 'multiple_blank', 'written')),
                selected_answer_json TEXT,
                answer_text TEXT,
                is_correct INTEGER CHECK (is_correct IS NULL OR is_correct IN (0, 1)),
                mode TEXT NOT NULL,
                answered_at_client TEXT NOT NULL,
                shown_at_client TEXT,
                response_ms INTEGER
                    CHECK (response_ms IS NULL OR response_ms BETWEEN 0 AND 86400000),
                question_position INTEGER
                    CHECK (question_position IS NULL OR question_position BETWEEN 1 AND 100000),
                app_version TEXT,
                payload_digest TEXT NOT NULL,
                created_at_server TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attempts_question_id
                ON answer_attempts(question_id, id);
            CREATE INDEX IF NOT EXISTS idx_attempts_session_id
                ON answer_attempts(session_id, id);

            CREATE TABLE IF NOT EXISTS card_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                study_deck_id TEXT,
                card_id TEXT NOT NULL,
                answer_revision TEXT NOT NULL,
                selected_answer INTEGER NOT NULL CHECK (selected_answer IN (0, 1)),
                correct_answer INTEGER NOT NULL CHECK (correct_answer IN (0, 1)),
                is_correct INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
                mode TEXT NOT NULL,
                order_mode TEXT,
                topic_filter TEXT,
                answered_at_client TEXT NOT NULL,
                shown_at_client TEXT,
                response_ms INTEGER
                    CHECK (response_ms IS NULL OR response_ms BETWEEN 0 AND 86400000),
                question_position INTEGER
                    CHECK (question_position IS NULL OR question_position BETWEEN 1 AND 100000),
                app_version TEXT,
                payload_digest TEXT NOT NULL,
                created_at_server TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_card_attempts_card_id
                ON card_attempts(card_id, id);
            CREATE INDEX IF NOT EXISTS idx_card_attempts_session_id
                ON card_attempts(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_card_attempts_revision
                ON card_attempts(card_id, answer_revision, id);

            -- 卒業・絶対覚えた・自信度。回答と同じく追記のみで、過去の事実を消さない。
            -- reset は「ここより前の回答を習得判定に使わない」という区切りを置くだけで、
            -- card_attempts の行はそのまま残る。
            """ + CARD_MARKS_DDL + """

            CREATE INDEX IF NOT EXISTS idx_card_marks_card_id
                ON card_marks(card_id, id);
            CREATE INDEX IF NOT EXISTS idx_card_marks_action
                ON card_marks(action, id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_card_marks_attempt
                ON card_marks(attempt_event_id)
                WHERE attempt_event_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS similarity_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                pair_id TEXT NOT NULL,
                decision TEXT NOT NULL
                    CHECK (decision IN ('merge', 'related', 'reject', 'defer')),
                relation_type TEXT
                    CHECK (
                        relation_type IS NULL OR relation_type IN (
                            'opposite_claim', 'exception', 'contrast', 'same_topic',
                            'same_proposition'
                        )
                    ),
                pair_content_digest TEXT NOT NULL,
                supersedes_decision_id TEXT,
                decided_at_client TEXT NOT NULL,
                note TEXT,
                payload_digest TEXT NOT NULL,
                created_at_server TEXT NOT NULL,
                CHECK (
                    (decision = 'related' AND relation_type IS NOT NULL)
                    OR (
                        decision = 'merge'
                        AND (
                            relation_type IS NULL
                            OR relation_type = 'same_proposition'
                        )
                    )
                    OR (
                        decision IN ('reject', 'defer')
                        AND relation_type IS NULL
                    )
                ),
                FOREIGN KEY (supersedes_decision_id)
                    REFERENCES similarity_decisions(decision_id)
            );

            CREATE INDEX IF NOT EXISTS idx_similarity_pair_id
                ON similarity_decisions(pair_id, id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_similarity_one_successor
                ON similarity_decisions(supersedes_decision_id)
                WHERE supersedes_decision_id IS NOT NULL;

            CREATE TRIGGER IF NOT EXISTS answer_attempts_no_update
            BEFORE UPDATE ON answer_attempts
            BEGIN
                SELECT RAISE(ABORT, 'answer_attempts is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS answer_attempts_no_delete
            BEFORE DELETE ON answer_attempts
            BEGIN
                SELECT RAISE(ABORT, 'answer_attempts is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS card_attempts_no_update
            BEFORE UPDATE ON card_attempts
            BEGIN
                SELECT RAISE(ABORT, 'card_attempts is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS card_attempts_no_delete
            BEFORE DELETE ON card_attempts
            BEGIN
                SELECT RAISE(ABORT, 'card_attempts is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS card_marks_no_update
            BEFORE UPDATE ON card_marks
            BEGIN
                SELECT RAISE(ABORT, 'card_marks is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS card_marks_no_delete
            BEFORE DELETE ON card_marks
            BEGIN
                SELECT RAISE(ABORT, 'card_marks is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS similarity_decisions_no_update
            BEFORE UPDATE ON similarity_decisions
            BEGIN
                SELECT RAISE(ABORT, 'similarity_decisions is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS similarity_decisions_no_delete
            BEFORE DELETE ON similarity_decisions
            BEGIN
                SELECT RAISE(ABORT, 'similarity_decisions is append-only');
            END;

            """
        )
        # CHECK制約はALTERで変えられないので、必要なときだけ作り直して移し替える。
        # 上の CREATE TABLE IF NOT EXISTS は既存テーブルには効かないため、ここで行う。
        if _migrate_card_marks_confidence(connection):
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"card_marks の移行後に quick_check が失敗: {check}")
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        expected_card_columns = {
            "id",
            "event_id",
            "session_id",
            "study_deck_id",
            "card_id",
            "answer_revision",
            "selected_answer",
            "correct_answer",
            "is_correct",
            "mode",
            "order_mode",
            "topic_filter",
            "answered_at_client",
            "shown_at_client",
            "response_ms",
            "question_position",
            "app_version",
            "payload_digest",
            "created_at_server",
        }
        actual_card_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(card_attempts)")
        }
        if actual_card_columns != expected_card_columns:
            raise RuntimeError("card_attempts schema does not match this server")
        expected_mark_columns = {
            "id",
            "event_id",
            "session_id",
            "study_deck_id",
            "card_id",
            "answer_revision",
            "attempt_event_id",
            "action",
            "scope",
            "confidence",
            "marked_at_client",
            "app_version",
            "payload_digest",
            "created_at_server",
        }
        actual_mark_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(card_marks)")
        }
        if actual_mark_columns != expected_mark_columns:
            raise RuntimeError("card_marks schema does not match this server")
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def _accuracy(correct: int, incorrect: int) -> float | None:
    graded = correct + incorrect
    return round(correct / graded, 6) if graded else None


# 席を外したままの回答で解答時間が壊れないよう、5分を超える計測は捨てる
RESPONSE_SAMPLE_LIMIT_MS = 300_000


def _median_ms(samples: list[int]) -> int | None:
    """解答時間は平均より中央値を使う。1回の中断で数値が跳ねないようにする。"""
    if not samples:
        return None
    ordered = sorted(samples)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def progress_statistics(
    connection: sqlite3.Connection,
    snapshot: BundleSnapshot | None = None,
) -> dict:
    if snapshot is None:
        snapshot = CATALOG.load()
    rows = connection.execute(
        """
        SELECT
            question_id,
            COUNT(*) AS attempts,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct,
            SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) AS incorrect,
            (
                SELECT recent.answered_at_client
                FROM answer_attempts AS recent
                WHERE recent.question_id = answer_attempts.question_id
                ORDER BY recent.id DESC
                LIMIT 1
            ) AS last_answered_at
        FROM answer_attempts
        GROUP BY question_id
        ORDER BY question_id
        """
    ).fetchall()

    by_question: dict[str, dict] = {
        question_id: {
            "attempts": 0,
            "correct": 0,
            "incorrect": 0,
            "ungraded": 0,
            "accuracy": None,
            "lastAnsweredAt": None,
        }
        for question_id in snapshot.questions
    }
    total_attempts = 0
    total_correct = 0
    total_incorrect = 0
    for row in rows:
        attempts = int(row["attempts"])
        correct = int(row["correct"])
        incorrect = int(row["incorrect"])
        total_attempts += attempts
        total_correct += correct
        total_incorrect += incorrect
        by_question[row["question_id"]] = {
            "attempts": attempts,
            "correct": correct,
            "incorrect": incorrect,
            "ungraded": attempts - correct - incorrect,
            "accuracy": _accuracy(correct, incorrect),
            "lastAnsweredAt": row["last_answered_at"],
        }

    return {
        "overall": {
            "attempts": total_attempts,
            "correct": total_correct,
            "incorrect": total_incorrect,
            "ungraded": total_attempts - total_correct - total_incorrect,
            "accuracy": _accuracy(total_correct, total_incorrect),
        },
        "byQuestion": by_question,
    }


def _attempt_from_row(row: sqlite3.Row) -> dict:
    return {
        "eventId": row["event_id"],
        "sessionId": row["session_id"],
        "questionId": row["question_id"],
        "format": row["question_format"],
        "selectedAnswer": (
            json.loads(row["selected_answer_json"])
            if row["selected_answer_json"] is not None
            else None
        ),
        "answerText": row["answer_text"],
        "isCorrect": None if row["is_correct"] is None else bool(row["is_correct"]),
        "mode": row["mode"],
        "answeredAt": row["answered_at_client"],
        "shownAt": row["shown_at_client"],
        "responseMs": row["response_ms"],
        "questionPosition": row["question_position"],
        "appVersion": row["app_version"],
        "savedAt": row["created_at_server"],
    }


def add_attempt(payload: dict) -> tuple[dict, bool]:
    event_id = payload.get("eventId")
    session_id = payload.get("sessionId")
    question_id = payload.get("questionId")
    question_format = payload.get("format")
    if not valid_id(event_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid eventId")
    if not valid_id(session_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid sessionId")
    if not valid_id(question_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid questionId")

    question, snapshot = CATALOG.question(question_id)
    catalog_format = question.get("format")
    if question_format not in QUESTION_FORMATS or question_format != catalog_format:
        raise ApiError(HTTPStatus.BAD_REQUEST, "format does not match question")

    selected_present = "selectedAnswer" in payload and payload.get("selectedAnswer") is not None
    answer_text = short_text(payload.get("answerText"), "answerText", MAX_ANSWER_TEXT_CHARS)
    if question_format == "written":
        if not answer_text or not answer_text.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "answerText is required for written questions")
    elif not selected_present:
        raise ApiError(HTTPStatus.BAD_REQUEST, "selectedAnswer is required")

    selected_json = (
        canonical_json(
            payload.get("selectedAnswer"),
            "selectedAnswer",
            MAX_SELECTED_ANSWER_BYTES,
        )
        if selected_present
        else None
    )
    is_correct = payload.get("isCorrect")
    if is_correct is not None and type(is_correct) is not bool:
        raise ApiError(HTTPStatus.BAD_REQUEST, "isCorrect must be boolean or null")

    mode = short_text(payload.get("mode"), "mode", 64, required=True)
    answered_at = parse_timestamp(payload.get("answeredAt"), "answeredAt", required=True)
    shown_at = parse_timestamp(payload.get("shownAt"), "shownAt")
    response_ms = payload.get("responseMs")
    if response_ms is not None and (
        type(response_ms) is not int or not 0 <= response_ms <= 86_400_000
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid responseMs")
    question_position = payload.get("questionPosition")
    if question_position is not None and (
        type(question_position) is not int or not 1 <= question_position <= 100_000
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid questionPosition")
    app_version = short_text(payload.get("appVersion"), "appVersion", 64)

    normalized = {
        "eventId": event_id,
        "sessionId": session_id,
        "questionId": question_id,
        "format": question_format,
        "selectedAnswer": json.loads(selected_json) if selected_json is not None else None,
        "answerText": answer_text,
        "isCorrect": is_correct,
        "mode": mode,
        "answeredAt": answered_at,
        "shownAt": shown_at,
        "responseMs": response_ms,
        "questionPosition": question_position,
        "appVersion": app_version,
    }
    payload_digest = hashlib.sha256(
        canonical_json(normalized, "attempt", MAX_BODY_BYTES).encode("utf-8")
    ).hexdigest()
    now = utc_now()

    with WRITE_LOCK, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM answer_attempts WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(existing["payload_digest"], payload_digest):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "eventId already exists with different content",
                )
            connection.commit()
            return _attempt_from_row(existing), False

        connection.execute(
            """
            INSERT INTO answer_attempts (
                event_id, session_id, question_id, question_format,
                selected_answer_json, answer_text, is_correct, mode,
                answered_at_client, shown_at_client, response_ms,
                question_position, app_version, payload_digest, created_at_server
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                question_id,
                question_format,
                selected_json,
                answer_text,
                None if is_correct is None else int(is_correct),
                mode,
                answered_at,
                shown_at,
                response_ms,
                question_position,
                app_version,
                payload_digest,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM answer_attempts WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        connection.commit()
    return _attempt_from_row(row), True


def _card_attempt_from_row(row: sqlite3.Row) -> dict:
    return {
        "eventId": row["event_id"],
        "sessionId": row["session_id"],
        "studyDeckId": row["study_deck_id"],
        "cardId": row["card_id"],
        "answerRevision": row["answer_revision"],
        "selectedAnswer": bool(row["selected_answer"]),
        "correctAnswer": bool(row["correct_answer"]),
        "isCorrect": bool(row["is_correct"]),
        "scopeMode": row["mode"],
        "mode": row["mode"],
        "orderMode": row["order_mode"],
        "topicFilter": row["topic_filter"],
        "answeredAt": row["answered_at_client"],
        "shownAt": row["shown_at_client"],
        "responseMs": row["response_ms"],
        "questionPosition": row["question_position"],
        "appVersion": row["app_version"],
        "savedAt": row["created_at_server"],
    }


def card_progress_statistics(
    connection: sqlite3.Connection,
    snapshot: BundleSnapshot | None = None,
    deck: dict | None = None,
) -> dict:
    if snapshot is None:
        snapshot = CATALOG.load()
    eligible_cards = cards_for_study_deck(snapshot, deck)
    current_revisions = {
        card["id"]: card_answer_revision(card, snapshot, deck)
        for card in eligible_cards
    }
    by_card: dict[str, dict] = {
        card["id"]: {
            "attempts": 0,
            "correct": 0,
            "incorrect": 0,
            "accuracy": None,
            "streak": 0,
            "maxStreak": 0,
            "lastAnsweredAt": None,
            "answerRevision": current_revisions[card["id"]],
            "responseSamples": 0,
            "medianResponseMs": None,
            "lastResponseMs": None,
            # ここから下は卒業・絶対覚えた・自信度。過去の事実は消さずに持ち続ける。
            "certain": False,
            "certainAt": None,
            "resetAt": None,
            "resetCount": 0,
            "sinceResetAttempts": 0,
            "sinceResetCorrect": 0,
            "sinceResetIncorrect": 0,
            "masteryScore": 0,
            "mastered": False,
            "graduatedTimes": 0,
            "lastGraduatedAt": None,
            "graduated": False,
            "confidenceCounts": {name: 0 for name in CARD_MARK_CONFIDENCE},
            "lastConfidence": None,
        }
        for card in eligible_cards
    }
    response_samples: dict[str, list[int]] = {card["id"]: [] for card in eligible_cards}

    marks = connection.execute("SELECT * FROM card_marks ORDER BY id").fetchall()
    deck_reset_at: str | None = None
    for mark in marks:
        if mark["action"] != "reset" or mark["scope"] != "deck":
            continue
        deck_reset_at = mark["created_at_server"]
    card_reset_at: dict[str, str] = {}
    for mark in marks:
        if mark["action"] != "reset" or mark["scope"] != "card":
            continue
        item = by_card.get(mark["card_id"])
        if item is None:
            continue
        card_reset_at[mark["card_id"]] = mark["created_at_server"]
        item["resetCount"] += 1
    deck_reset_points = sorted(
        mark["created_at_server"]
        for mark in marks
        if mark["action"] == "reset" and mark["scope"] == "deck"
    )
    card_reset_points: dict[str, list[str]] = {}
    for mark in marks:
        if mark["action"] != "reset" or mark["scope"] != "card":
            continue
        card_reset_points.setdefault(mark["card_id"], []).append(
            mark["created_at_server"]
        )
    # 全リセットと個別リセットは同じ区切りの列に並べる。新しい区切りを越えるたびに
    # 習得判定の数え直しが始まるが、card_attempts の行そのものは残したままにする。
    reset_points: dict[str, list[str]] = {
        card_id: sorted(deck_reset_points + card_reset_points.get(card_id, []))
        for card_id in by_card
    }
    for card_id, item in by_card.items():
        points = reset_points[card_id]
        item["resetAt"] = points[-1] if points else None
        item["resetCount"] = len(points)
    for mark in marks:
        item = by_card.get(mark["card_id"]) if mark["card_id"] else None
        if item is None:
            continue
        # 2026-07-30に方針を変えた。印はカードIDだけで引き継ぐ。
        # 押した時点の版は answer_revision として残すが、集計では見ない。
        # リセットは版に依存しない区切りで、answer_revision を持たない。
        if mark["action"] in {"certain", "uncertain"}:
            item["certain"] = mark["action"] == "certain"
            item["certainAt"] = mark["marked_at_client"] if item["certain"] else None
        elif mark["action"] == "confidence":
            item["confidenceCounts"][mark["confidence"]] += 1
            item["lastConfidence"] = mark["confidence"]

    reset_cursor: dict[str, int] = {card_id: 0 for card_id in by_card}
    graduated_in_cycle: dict[str, bool] = {card_id: False for card_id in by_card}

    rows = connection.execute(
        "SELECT * FROM card_attempts ORDER BY id"
    ).fetchall()
    relevant_all_time = 0
    current_attempts = 0
    current_correct = 0
    current_incorrect = 0
    for row in rows:
        # 2026-07-30に方針を変えた。回答はカードIDだけで数える。A・B・C・正解・
        # 法令基準日を直しても、同じカードなら過去の回答と卒業回数を引き継ぐ。
        # answer_revision は「どの版に対する回答か」の記録として残すが、集計では見ない。
        if row["card_id"] not in by_card:
            continue
        relevant_all_time += 1
        item = by_card[row["card_id"]]
        current_attempts += 1
        item["attempts"] += 1
        is_correct = bool(row["is_correct"])
        if is_correct:
            current_correct += 1
            item["correct"] += 1
            item["streak"] += 1
            item["maxStreak"] = max(item["maxStreak"], item["streak"])
        else:
            current_incorrect += 1
            item["incorrect"] += 1
            item["streak"] = 0
        item["lastAnsweredAt"] = row["answered_at_client"]
        elapsed = row["response_ms"]
        if elapsed is not None and 0 <= elapsed <= RESPONSE_SAMPLE_LIMIT_MS:
            response_samples[row["card_id"]].append(int(elapsed))
            item["lastResponseMs"] = int(elapsed)

        # リセットの区切りを越えたら習得の数え直しを始める。卒業した事実は回数として残す。
        cursor = reset_cursor[row["card_id"]]
        points = reset_points[row["card_id"]]
        created = row["created_at_server"]
        while cursor < len(points) and points[cursor] <= created:
            cursor += 1
            item["sinceResetAttempts"] = 0
            item["sinceResetCorrect"] = 0
            item["sinceResetIncorrect"] = 0
            item["masteryScore"] = 0
            graduated_in_cycle[row["card_id"]] = False
        reset_cursor[row["card_id"]] = cursor
        item["sinceResetAttempts"] += 1
        if is_correct:
            item["sinceResetCorrect"] += 1
        else:
            item["sinceResetIncorrect"] += 1
        item["masteryScore"] = item["sinceResetCorrect"] - item["sinceResetIncorrect"]
        if item["masteryScore"] >= MASTERY_SCORE and not graduated_in_cycle[row["card_id"]]:
            graduated_in_cycle[row["card_id"]] = True
            item["graduatedTimes"] += 1
            item["lastGraduatedAt"] = row["answered_at_client"]

    for card_id, item in by_card.items():
        item["accuracy"] = _accuracy(item["correct"], item["incorrect"])
        samples = response_samples[card_id]
        item["responseSamples"] = len(samples)
        item["medianResponseMs"] = _median_ms(samples)
        # 最後のリセットが最後の回答より後なら、そこで数え直しになっている
        points = reset_points[card_id]
        if points and (
            item["lastAnsweredAt"] is None
            or reset_cursor[card_id] < len(points)
        ):
            item["sinceResetAttempts"] = 0
            item["sinceResetCorrect"] = 0
            item["sinceResetIncorrect"] = 0
            item["masteryScore"] = 0
        item["mastered"] = item["masteryScore"] >= MASTERY_SCORE
        # 「絶対覚えた」は全リセットの対象外なので、卒業状態はリセット後も残る
        item["graduated"] = item["mastered"] or item["certain"]

    return {
        "schemaVersion": 1,
        "serverTime": utc_now(),
        "studyDeck": dict(deck) if deck is not None else None,
        "overall": {
            "attempts": current_attempts,
            "correct": current_correct,
            "incorrect": current_incorrect,
            "accuracy": _accuracy(current_correct, current_incorrect),
            "medianResponseMs": _median_ms(
                [value for samples in response_samples.values() for value in samples]
            ),
        },
        "byCard": by_card,
        "stats": {
            "allTimeAttempts": len(rows),
            "deckAllTimeAttempts": relevant_all_time,
            "masteredCards": sum(1 for item in by_card.values() if item["mastered"]),
            "certainCards": sum(1 for item in by_card.values() if item["certain"]),
            "graduatedCards": sum(1 for item in by_card.values() if item["graduated"]),
            "everGraduatedCards": sum(
                1 for item in by_card.values() if item["graduatedTimes"]
            ),
            "confidenceMarks": sum(
                sum(item["confidenceCounts"].values()) for item in by_card.values()
            ),
            "masteryScore": MASTERY_SCORE,
        },
    }


def _build_current_weakness_snapshot(
    snapshot: BundleSnapshot,
    deck: dict | None,
) -> dict:
    from weakness_analysis import build_weakness_snapshot

    eligible_cards = cards_for_study_deck(snapshot, deck)
    current_revisions = {
        card["id"]: card_answer_revision(card, snapshot, deck)
        for card in eligible_cards
    }
    with connect() as connection:
        return build_weakness_snapshot(
            connection,
            eligible_cards,
            current_revisions,
            bundle_revision=snapshot.revision,
            study_deck=deck,
            known_card_ids=snapshot.cards,
            generated_at=utc_now(),
        )


def refresh_weakness_latest(
    snapshot: BundleSnapshot,
    deck: dict | None,
) -> None:
    from weakness_analysis import atomic_write_private_json

    analysis = _build_current_weakness_snapshot(snapshot, deck)
    atomic_write_private_json(WEAKNESS_PATH, analysis)


def _weakness_int(
    value: object,
    field: str,
    *,
    maximum: int = 100_000_000,
) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"invalid {field}")
    return value


def _weakness_accuracy(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {field}")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"invalid {field}")
    return number


def _weakness_public_projection(
    analysis: object,
    snapshot: BundleSnapshot,
    deck: dict | None,
    *,
    source: str,
    stored_available: bool,
    stored_fresh: bool,
    stale_reasons: list[str],
) -> dict:
    if not isinstance(analysis, dict):
        raise ValueError("weakness snapshot root must be an object")
    if analysis.get("schemaVersion") != WEAKNESS_SCHEMA_VERSION:
        raise ValueError("unsupported weakness snapshot schema")
    if analysis.get("analyzerVersion") != WEAKNESS_ANALYZER_VERSION:
        raise ValueError("unsupported weakness analyzer")
    generated_at = analysis.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at or len(generated_at) > 40:
        raise ValueError("invalid weakness generatedAt")
    datetime.fromisoformat(generated_at.replace("Z", "+00:00"))

    expected_cards = {
        card["id"] for card in cards_for_study_deck(snapshot, deck)
    }
    raw_summary = analysis.get("summary")
    if not isinstance(raw_summary, dict):
        raise ValueError("invalid weakness summary")
    raw_status_counts = raw_summary.get("statusCounts")
    if not isinstance(raw_status_counts, dict):
        raise ValueError("invalid weakness status counts")
    status_counts = {
        status: _weakness_int(
            raw_status_counts.get(status),
            f"statusCounts.{status}",
        )
        for status in WEAKNESS_STATUSES
    }

    raw_targets = analysis.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) > len(expected_cards):
        raise ValueError("invalid weakness targets")
    targets = []
    seen_ids: set[str] = set()
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict):
            raise ValueError("invalid weakness target")
        card_id = raw_target.get("cardId")
        status = raw_target.get("status")
        if (
            not valid_id(card_id)
            or card_id not in expected_cards
            or card_id in seen_ids
            or status not in WEAKNESS_TARGET_STATUSES
        ):
            raise ValueError("invalid weakness target identity")
        seen_ids.add(card_id)
        reason_codes = raw_target.get("reasonCodes")
        if (
            not isinstance(reason_codes, list)
            or not all(
                isinstance(code, str) and code in WEAKNESS_REASON_CODES
                for code in reason_codes
            )
        ):
            raise ValueError("invalid weakness target reasons")
        raw_evidence = raw_target.get("evidence")
        if not isinstance(raw_evidence, dict):
            raise ValueError("invalid weakness target evidence")
        priority_band = raw_target.get("priorityBand")
        if priority_band not in {"high", "medium"}:
            raise ValueError("invalid weakness priority band")
        targets.append(
            {
                "cardId": card_id,
                "status": status,
                "priority": _weakness_int(
                    raw_target.get("priority"),
                    "target.priority",
                    maximum=10_000,
                ),
                "priorityBand": priority_band,
                "reasonCodes": list(reason_codes),
                "evidence": {
                    "attempts": _weakness_int(
                        raw_evidence.get("attempts"),
                        "target.evidence.attempts",
                    ),
                    "correct": _weakness_int(
                        raw_evidence.get("correct"),
                        "target.evidence.correct",
                    ),
                    "incorrect": _weakness_int(
                        raw_evidence.get("incorrect"),
                        "target.evidence.incorrect",
                    ),
                    "recentWindowSize": _weakness_int(
                        raw_evidence.get("recentWindowSize"),
                        "target.evidence.recentWindowSize",
                        maximum=5,
                    ),
                    "recentIncorrect": _weakness_int(
                        raw_evidence.get("recentIncorrect"),
                        "target.evidence.recentIncorrect",
                        maximum=5,
                    ),
                    "recentAccuracy": _weakness_accuracy(
                        raw_evidence.get("recentAccuracy"),
                        "target.evidence.recentAccuracy",
                    ),
                    "correctStreak": _weakness_int(
                        raw_evidence.get("correctStreak"),
                        "target.evidence.correctStreak",
                    ),
                    "incorrectStreak": _weakness_int(
                        raw_evidence.get("incorrectStreak"),
                        "target.evidence.incorrectStreak",
                    ),
                },
            }
        )
    target_count = _weakness_int(
        raw_summary.get("targetCount"),
        "summary.targetCount",
    )
    if target_count != len(targets):
        raise ValueError("weakness target count mismatch")
    card_count = _weakness_int(
        raw_summary.get("cardCount"),
        "summary.cardCount",
    )
    counted_attempts = _weakness_int(
        raw_summary.get("countedAttempts"),
        "summary.countedAttempts",
    )
    correct = _weakness_int(
        raw_summary.get("correct"),
        "summary.correct",
    )
    incorrect = _weakness_int(
        raw_summary.get("incorrect"),
        "summary.incorrect",
    )
    if (
        card_count != len(expected_cards)
        or sum(status_counts.values()) != card_count
        or target_count
        != sum(status_counts[status] for status in WEAKNESS_TARGET_STATUSES)
        or correct + incorrect != counted_attempts
    ):
        raise ValueError("inconsistent weakness summary")

    return {
        "schemaVersion": 1,
        "available": True,
        "serverTime": utc_now(),
        "studyView": {
            "id": "weakness",
            "label": "苦手・要観察",
            "targetStatuses": ["weak", "watch"],
        },
        "analysis": {
            "generatedAt": generated_at,
            "analyzerVersion": WEAKNESS_ANALYZER_VERSION,
            "source": source,
        },
        "freshness": {
            "storedSnapshotAvailable": stored_available,
            "storedSnapshotFresh": stored_fresh,
            "fallbackToLiveAnalysis": source == "live",
            "reasonCodes": stale_reasons,
        },
        "bundle": snapshot.metadata(),
        "studyDeck": (
            {
                "id": deck["id"],
                "cardCount": len(expected_cards),
            }
            if deck is not None
            else None
        ),
        "summary": {
            "cardCount": card_count,
            "targetCount": target_count,
            "countedAttempts": counted_attempts,
            "correct": correct,
            "incorrect": incorrect,
            "accuracy": _weakness_accuracy(
                raw_summary.get("accuracy"),
                "summary.accuracy",
            ),
            "statusCounts": status_counts,
        },
        "targets": targets,
    }


def _read_weakness_snapshot() -> tuple[dict | None, str | None]:
    try:
        if WEAKNESS_PATH.stat().st_size > MAX_WEAKNESS_BYTES:
            return None, "invalid"
        decoded = json.loads(WEAKNESS_PATH.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            return None, "invalid"
        return decoded, None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid"


def _normalize_search(value: object) -> str:
    """検索語の正規化。**画面の `normalizeText()` と同じにする。**

    画面は NFKC 正規化 → 小文字化 → 空白除去 で照合している。サーバーが素の
    部分一致で絞ると、小文字の `fp` や全角の `ＦＰ` が画面では当たるのに
    キューでは0枚になる（2026-08-05の再レビュー指摘）。
    `tests/test_server.py` の SharedRuleDriftTest が、画面側とずれていないか見ている。
    """
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text)


def _search_haystack(card: dict, evidence: dict[str, dict]) -> str:
    """検索の対象にする文字列。**画面の `studySearchHaystack()` と同じ範囲にする。**

    カードID・分類・A/B/C・解説・⑥・⑦に加えて、⑤の肢の本文と年度まで含める。
    ここを狭めると、⑤の言い回しで探したときに画面とキューの結果が食い違う。
    """
    card_edit, _ = _card_edit_modules()
    variants = card.get("variants") or {}
    explanations = card.get("explanations") or {}
    deep = explanations.get("deepDive") or {}
    parts: list[object] = [
        card.get("id"), card.get("category"), card.get("topic"), card.get("subtopic"),
        variants.get("a"), variants.get("b"), variants.get("bCasual"), variants.get("c"),
        card.get("correction"), card.get("memoryPoint"),
        explanations.get("normal"), deep.get("background"), deep.get("trap"),
        deep.get("example"), explanations.get("commonSense"),
        (card.get("frequency") or {}).get("label"),
    ]
    for comparison in card.get("crossFieldComparisons") or []:
        parts += [comparison.get("title"), comparison.get("explanation"),
                  comparison.get("memoryCue")]
    table = card.get("comparisonTable")
    if isinstance(table, dict):
        parts += [table.get("title"), table.get("memoryCue")]
        for row in table.get("rows") or []:
            parts += [row.get("label"), row.get("article"), row.get("rule"),
                      row.get("conclusion")]
    for ref in card.get("relatedPastQuestions") or []:
        item = evidence.get(ref.get("choiceId", ""))
        if item:
            parts += [item.get("statementText"), item.get("eraYear")]
    joined = " ".join(str(p) for p in parts if p)
    return _normalize_search(card_edit.strip_markup(joined))


def card_edit_module():
    module, _ = _card_edit_modules()
    return module


def study_queue_response(query: dict[str, list[str]]) -> dict:
    """「今日の学習」キュー。py-fsrs が決めた期日で、今日出すカードを並べる。

    状態は `card_attempts` と `card_marks` から毎回導出する。専用テーブルを作らず、
    `production.sqlite3` のschemaも変えない。理由は `study_queue.py` の冒頭に書いた。
    """
    import study_queue

    snapshot = CATALOG.load()
    study_deck_id = _single_query(query, "studyDeckId")
    deck_id = _single_query(query, "deckId")
    if study_deck_id is not None and deck_id is not None and study_deck_id != deck_id:
        # /api/card-progress と同じく、食い違う指定は黙って片方を採らない。
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting study deck identifiers")
    if study_deck_id is None:
        study_deck_id = deck_id
    deck = resolve_study_deck(snapshot, study_deck_id, require_if_ambiguous=True)
    cards = cards_for_study_deck(snapshot, deck)

    # 暗記もの／理解もので絞る。8月は暗記ものだけを回す使い方をするので、
    # キューの側でも同じ範囲に揃えないと期日ぎれの枚数が合わなくなる。
    # 分類の無いカードは古いbundleの可能性があるので、黙って「暗記もの」へ混ぜず止める。
    # **絞り込みの指定と無関係に確かめる。** 「すべて」を選んだときや learningType を
    # 省いたときにも未分類が混ざるため（2026-08-05の再レビュー指摘）。
    known_types = card_edit_module().LEARNING_TYPES
    unclassified = [
        card["id"] for card in cards if card.get("learningType") not in known_types
    ]
    if unclassified:
        raise ApiError(
            HTTPStatus.CONFLICT,
            "learningType のないカードがあります（bundleを作り直してください）: "
            + ", ".join(sorted(unclassified)[:5]),
        )
    learning_type = _single_query(query, "learningType")
    if learning_type is not None:
        if learning_type not in known_types:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "learningType must be memorize or understand",
            )
        cards = [card for card in cards if card["learningType"] == learning_type]

    def _count(name: str, fallback: int, *, low: int) -> int:
        text = _single_query(query, name)
        if text is None:
            return fallback
        # str.isdigit() は "²" のようなUnicode数字でもTrueになるが int() は落ちる。
        # 判定を挟まず、変換そのものを例外で受ける（2026-08-05のレビュー指摘）。
        try:
            value = int(text)
        except (TypeError, ValueError) as error:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"{name} must be an integer"
            ) from error
        if not low <= value <= study_queue.MAX_QUEUE_LIMIT:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                f"{name} must be between {low} and {study_queue.MAX_QUEUE_LIMIT}",
            )
        return value

    # 画面が科目・分野で絞っているなら、キューも同じ範囲で組む。全カードから
    # 組むと、その日の枠を他の科目に使ってしまい、選んだ科目が空に見える。
    subject_id = _single_query(query, "subjectId")
    if subject_id is not None:
        cards = [card for card in cards if card.get("subjectId") == subject_id]
    topic = _single_query(query, "topic")
    if topic is not None:
        cards = [card for card in cards if card.get("topic") == topic]
    # 検索語で絞っているときも同じ母集団から組む。渡さないと、検索に当たらない
    # カードでその日の枠を使ってしまう（2026-08-05の再レビュー指摘）。
    search = _single_query(query, "search")
    if search:
        words = [_normalize_search(word) for word in search.split()]
        words = [word for word in words if word]
        if words:
            evidence = {
                item["choiceId"]: item
                for item in _list_section(snapshot.bundle, "relatedQuestionEvidence")
                if isinstance(item, dict) and isinstance(item.get("choiceId"), str)
            }
            cards = [
                card
                for card in cards
                if all(word in _search_haystack(card, evidence) for word in words)
            ]

    limit = _count("limit", study_queue.DEFAULT_QUEUE_LIMIT, low=1)
    # はじめてのカードだけ別枠で絞れるようにする。0にすれば復習だけになる。
    new_limit = _count("newLimit", study_queue.DEFAULT_NEW_LIMIT, low=0)

    retention = 0.9
    retention_text = _single_query(query, "desiredRetention")
    if retention_text is not None:
        try:
            retention = float(retention_text)
        except ValueError as error:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "desiredRetention must be a number"
            ) from error
        if not 0.7 <= retention <= 0.97:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "desiredRetention must be between 0.7 and 0.97",
            )

    with connect() as connection:
        result = study_queue.build_queue(
            connection,
            cards,
            limit=limit,
            new_limit=new_limit,
            desired_retention=retention,
            study_deck_id=(deck or {}).get("id"),
        )
    # 画面がそのまま出題できるよう、公開projectionを通したカード本体も返す。
    by_id = {card["id"]: card for card in cards}
    result["cards"] = [
        card_for_response(by_id[card_id], snapshot, deck)
        for card_id in result["cardIds"]
        if card_id in by_id
    ]
    result["studyDeck"] = dict(deck) if deck is not None else None
    result["bundle"] = snapshot.metadata()
    return result


def rating_preview_response(query: dict[str, list[str]]) -> dict:
    """1枚ぶんの「この評価を選ぶと次はいつか」を返す。評価ボタンへ出すためのもの。"""
    import study_queue

    card_id = _single_query(query, "cardId")
    if not card_id or not valid_id(card_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "cardId is required")
    snapshot = CATALOG.load()
    study_deck_id = _single_query(query, "studyDeckId")
    deck = resolve_study_deck(snapshot, study_deck_id, require_if_ambiguous=True)
    known = {card["id"] for card in cards_for_study_deck(snapshot, deck)}
    if card_id not in known:
        raise ApiError(HTTPStatus.NOT_FOUND, "unknown card")
    with connect() as connection:
        previews = study_queue.rating_previews(
            connection, card_id, study_deck_id=(deck or {}).get("id")
        )
    return {"cardId": card_id, "intervals": previews, "bundle": snapshot.metadata()}


def learning_analysis() -> dict:
    snapshot = CATALOG.load()
    deck = default_study_deck(snapshot)
    with connect() as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM card_attempts"
        ).fetchone()
        current_max_attempt_id = int(row["max_id"])

    stored, read_issue = _read_weakness_snapshot()
    stale_reasons = [read_issue] if read_issue else []
    stored_available = stored is not None
    if stored is not None:
        if stored.get("schemaVersion") != WEAKNESS_SCHEMA_VERSION:
            stale_reasons.append("schema")
        if stored.get("analyzerVersion") != WEAKNESS_ANALYZER_VERSION:
            stale_reasons.append("analyzer")
        if stored.get("bundleRevision") != snapshot.revision:
            stale_reasons.append("bundle")
        if stored.get("maxAttemptId") != current_max_attempt_id:
            stale_reasons.append("attempts")
        stored_deck = stored.get("studyDeck")
        expected_deck_id = deck["id"] if deck is not None else None
        stored_deck_id = (
            stored_deck.get("id") if isinstance(stored_deck, dict) else None
        )
        if stored_deck_id != expected_deck_id:
            stale_reasons.append("study_deck")
        if not stale_reasons:
            try:
                return _weakness_public_projection(
                    stored,
                    snapshot,
                    deck,
                    source="stored",
                    stored_available=True,
                    stored_fresh=True,
                    stale_reasons=[],
                )
            except (TypeError, ValueError):
                stale_reasons.append("invalid")

    current = _build_current_weakness_snapshot(snapshot, deck)
    return _weakness_public_projection(
        current,
        snapshot,
        deck,
        source="live",
        stored_available=stored_available,
        stored_fresh=False,
        stale_reasons=list(dict.fromkeys(stale_reasons)),
    )


def add_card_attempt(payload: dict) -> tuple[dict, bool, BundleSnapshot, dict | None]:
    event_id = payload.get("eventId")
    session_id = payload.get("sessionId")
    card_id = payload.get("cardId")
    if not valid_id(event_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid eventId")
    if not valid_id(session_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid sessionId")
    if not valid_id(card_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid cardId")

    card, snapshot = CATALOG.card(card_id)
    deck = resolve_study_deck(
        snapshot,
        study_deck_id_from_payload(payload),
        require_if_ambiguous=True,
    )
    if deck is not None and card_id not in deck["cardIds"]:
        raise ApiError(HTTPStatus.BAD_REQUEST, "cardId is not in the study deck")

    # 2026-07-30まではここで、送られてきた版が現在の版と違えば409を返し、
    # カードを直した後に届いた回答を捨てていた。履歴はカードIDだけで引き継ぐ方針に
    # したので、受け取って記録する。記録するのは画面が実際に出していた版であり、
    # 「どの版に対する回答か」を後から追うための情報として持つ。集計では見ない。
    supplied_revision = payload.get("answerRevision")
    if not isinstance(supplied_revision, str) or not ANSWER_REVISION_PATTERN.fullmatch(
        supplied_revision
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid answerRevision")

    selected_answer = payload.get("selectedAnswer")
    if type(selected_answer) is not bool:
        raise ApiError(HTTPStatus.BAD_REQUEST, "selectedAnswer must be boolean")
    correct_answer = card["correct"]
    is_correct = selected_answer == correct_answer
    supplied_result = payload.get("isCorrect")
    if supplied_result is not None:
        if type(supplied_result) is not bool:
            raise ApiError(HTTPStatus.BAD_REQUEST, "isCorrect must be boolean")
        if supplied_result != is_correct:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "isCorrect does not match the server result",
            )

    has_scope_mode = "scopeMode" in payload
    has_legacy_mode = "mode" in payload
    if has_scope_mode and has_legacy_mode:
        scope_mode = short_text(
            payload.get("scopeMode"),
            "scopeMode",
            64,
            required=True,
        )
        legacy_mode = short_text(
            payload.get("mode"),
            "mode",
            64,
            required=True,
        )
        if scope_mode != legacy_mode:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "scopeMode and mode must match",
            )
    elif has_scope_mode:
        scope_mode = short_text(
            payload.get("scopeMode"),
            "scopeMode",
            64,
            required=True,
        )
    else:
        scope_mode = short_text(
            payload.get("mode"),
            "scopeMode",
            64,
            required=True,
        )
    order_mode = short_text(payload.get("orderMode"), "orderMode", 64)
    topic_filter = short_text(payload.get("topicFilter"), "topicFilter", 100)
    answered_at = parse_timestamp(payload.get("answeredAt"), "answeredAt", required=True)
    shown_at = parse_timestamp(payload.get("shownAt"), "shownAt")
    response_ms = payload.get("responseMs")
    if response_ms is not None and (
        type(response_ms) is not int or not 0 <= response_ms <= 86_400_000
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid responseMs")
    question_position = payload.get("questionPosition")
    if question_position is not None and (
        type(question_position) is not int or not 1 <= question_position <= 100_000
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid questionPosition")
    app_version = short_text(payload.get("appVersion"), "appVersion", 64)
    study_deck_id = deck["id"] if deck is not None else None

    normalized = {
        "eventId": event_id,
        "sessionId": session_id,
        "studyDeckId": study_deck_id,
        "cardId": card_id,
        "answerRevision": supplied_revision,
        "selectedAnswer": selected_answer,
        "correctAnswer": correct_answer,
        "isCorrect": is_correct,
        "scopeMode": scope_mode,
        "orderMode": order_mode,
        "topicFilter": topic_filter,
        "answeredAt": answered_at,
        "shownAt": shown_at,
        "responseMs": response_ms,
        "questionPosition": question_position,
        "appVersion": app_version,
    }
    payload_digest = hashlib.sha256(
        canonical_json(normalized, "card attempt", MAX_BODY_BYTES).encode("utf-8")
    ).hexdigest()

    with WRITE_LOCK, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM card_attempts WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(existing["payload_digest"], payload_digest):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "eventId already exists with different content",
                )
            connection.commit()
            return _card_attempt_from_row(existing), False, snapshot, deck

        now = next_card_event_time(connection)

        connection.execute(
            """
            INSERT INTO card_attempts (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                selected_answer, correct_answer, is_correct, mode, order_mode,
                topic_filter, answered_at_client,
                shown_at_client, response_ms, question_position, app_version,
                payload_digest, created_at_server
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                study_deck_id,
                card_id,
                supplied_revision,
                int(selected_answer),
                int(correct_answer),
                int(is_correct),
                scope_mode,
                order_mode,
                topic_filter,
                answered_at,
                shown_at,
                response_ms,
                question_position,
                app_version,
                payload_digest,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM card_attempts WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        connection.commit()
    return _card_attempt_from_row(row), True, snapshot, deck


def _card_mark_from_row(row: sqlite3.Row) -> dict:
    return {
        "eventId": row["event_id"],
        "sessionId": row["session_id"],
        "studyDeckId": row["study_deck_id"],
        "cardId": row["card_id"],
        "answerRevision": row["answer_revision"],
        "attemptEventId": row["attempt_event_id"],
        "action": row["action"],
        "scope": row["scope"],
        "confidence": row["confidence"],
        "markedAt": row["marked_at_client"],
        "appVersion": row["app_version"],
        "createdAt": row["created_at_server"],
    }


def add_card_mark(payload: dict) -> tuple[dict, bool, BundleSnapshot, dict | None]:
    """卒業・絶対覚えた・自信度を追記する。回答も過去の卒業事実も書き換えない。"""
    event_id = payload.get("eventId")
    session_id = payload.get("sessionId")
    if not valid_id(event_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid eventId")
    if not valid_id(session_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid sessionId")

    action = payload.get("action")
    if action not in CARD_MARK_ACTIONS:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid action")
    scope = payload.get("scope", "card")
    if scope not in {"card", "deck"}:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid scope")
    if scope == "deck" and action != "reset":
        raise ApiError(HTTPStatus.BAD_REQUEST, "deck scope is only valid for reset")

    card_id = payload.get("cardId")
    snapshot = CATALOG.load()
    card = None
    if scope == "card":
        if not valid_id(card_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid cardId")
        card, snapshot = CATALOG.card(card_id)
    elif card_id is not None:
        raise ApiError(HTTPStatus.BAD_REQUEST, "cardId is not allowed for deck scope")

    deck = resolve_study_deck(
        snapshot,
        study_deck_id_from_payload(payload),
        require_if_ambiguous=True,
    )
    if card is not None and deck is not None and card_id not in deck["cardIds"]:
        raise ApiError(HTTPStatus.BAD_REQUEST, "cardId is not in the study deck")

    # 「絶対覚えた」はどのカードの、どの版に対する判断かを残す。リセットは版に依存しない。
    answer_revision = None
    if card is not None and action in {"certain", "uncertain", "confidence"}:
        answer_revision = card_answer_revision(card, snapshot, deck)

    attempt_event_id = payload.get("attemptEventId")
    confidence = payload.get("confidence")
    if action == "confidence":
        if confidence not in CARD_MARK_RATINGS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid confidence")
        if not valid_id(attempt_event_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid attemptEventId")
    else:
        if confidence is not None:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "confidence is only valid for confidence marks"
            )
        if attempt_event_id is not None:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "attemptEventId is only valid for confidence marks",
            )

    marked_at = parse_timestamp(payload.get("markedAt"), "markedAt", required=True)
    app_version = short_text(payload.get("appVersion"), "appVersion", 64)
    study_deck_id = deck["id"] if deck is not None else None

    normalized = {
        "eventId": event_id,
        "sessionId": session_id,
        "studyDeckId": study_deck_id,
        "cardId": card_id if scope == "card" else None,
        "answerRevision": answer_revision,
        "attemptEventId": attempt_event_id,
        "action": action,
        "scope": scope,
        "confidence": confidence,
        "markedAt": marked_at,
        "appVersion": app_version,
    }
    payload_digest = hashlib.sha256(
        canonical_json(normalized, "card mark", MAX_BODY_BYTES).encode("utf-8")
    ).hexdigest()
    with WRITE_LOCK, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM card_marks WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(existing["payload_digest"], payload_digest):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "eventId already exists with different content",
                )
            connection.commit()
            return _card_mark_from_row(existing), False, snapshot, deck
        if action == "confidence":
            attempt = connection.execute(
                "SELECT card_id FROM card_attempts WHERE event_id = ?",
                (attempt_event_id,),
            ).fetchone()
            if attempt is None:
                raise ApiError(HTTPStatus.BAD_REQUEST, "unknown attemptEventId")
            if attempt["card_id"] != card_id:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "attemptEventId belongs to another card"
                )
            duplicate = connection.execute(
                "SELECT 1 FROM card_marks WHERE attempt_event_id = ?",
                (attempt_event_id,),
            ).fetchone()
            if duplicate is not None:
                raise ApiError(
                    HTTPStatus.CONFLICT, "this answer already has a confidence mark"
                )
        now = next_card_event_time(connection)
        connection.execute(
            """
            INSERT INTO card_marks (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                attempt_event_id, action, scope, confidence, marked_at_client,
                app_version, payload_digest, created_at_server
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                study_deck_id,
                card_id if scope == "card" else None,
                answer_revision,
                attempt_event_id,
                action,
                scope,
                confidence,
                marked_at,
                app_version,
                payload_digest,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM card_marks WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        connection.commit()
    return _card_mark_from_row(row), True, snapshot, deck


def _decision_from_row(row: sqlite3.Row, current_digest: str | None = None) -> dict:
    digest_matches = (
        current_digest is not None
        and hmac.compare_digest(row["pair_content_digest"], current_digest)
    )
    result = {
        "eventId": row["decision_id"],
        "decisionId": row["decision_id"],
        "pairId": row["pair_id"],
        "decision": row["decision"],
        "relationType": row["relation_type"],
        "pairContentDigest": row["pair_content_digest"],
        "supersedesEventId": row["supersedes_decision_id"],
        "supersedes": row["supersedes_decision_id"],
        "reviewedAt": row["decided_at_client"],
        "decidedAt": row["decided_at_client"],
        "note": row["note"],
        "savedAt": row["created_at_server"],
    }
    if current_digest is not None:
        result["matchesCurrentContent"] = digest_matches
    return result


def latest_similarity_decisions(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT current.*
        FROM similarity_decisions AS current
        LEFT JOIN similarity_decisions AS newer
          ON newer.pair_id = current.pair_id
         AND newer.id > current.id
        WHERE newer.id IS NULL
        ORDER BY current.pair_id
        """
    ).fetchall()
    return {row["pair_id"]: row for row in rows}


def similarities_with_latest(
    connection: sqlite3.Connection,
    snapshot: BundleSnapshot | None = None,
) -> list[dict]:
    if snapshot is None:
        snapshot = CATALOG.load()
    latest = latest_similarity_decisions(connection)
    result: list[dict] = []
    for pair in snapshot.similarity_pairs.values():
        item = dict(pair)
        row = latest.get(pair["id"])
        if row is None:
            item["latestDecision"] = None
            item["decisionState"] = "unreviewed"
        else:
            digest = pair["pairContentDigest"]
            decision = _decision_from_row(row, digest)
            item["latestDecision"] = decision
            item["decisionState"] = (
                "current" if decision["matchesCurrentContent"] else "stale"
            )
        result.append(item)
    return result


def add_similarity_decision(payload: dict) -> tuple[dict, bool]:
    decision_id = payload.get("decisionId")
    if decision_id is None:
        decision_id = payload.get("eventId")
    if (
        payload.get("decisionId") is not None
        and payload.get("eventId") is not None
        and payload["decisionId"] != payload["eventId"]
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting decision identifiers")
    pair_id = payload.get("pairId")
    if not valid_id(decision_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid decisionId")
    if not valid_id(pair_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid pairId")

    pair, _ = CATALOG.similarity_pair(pair_id)
    supplied_digest = payload.get("pairContentDigest")
    current_digest = pair["pairContentDigest"]
    if not isinstance(supplied_digest, str) or not hmac.compare_digest(
        supplied_digest,
        current_digest,
    ):
        raise ApiError(HTTPStatus.CONFLICT, "pair content has changed; reload before deciding")

    decision = payload.get("decision")
    relation_type = payload.get("relationType")
    if decision not in SIMILARITY_DECISIONS:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid decision")
    if decision == "related":
        if relation_type not in RELATION_TYPES:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "related requires a valid relationType",
            )
    elif decision == "merge":
        if relation_type not in {None, MERGE_RELATION_TYPE}:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "merge relationType must be same_proposition or null",
            )
    elif relation_type is not None:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "relationType is only valid for related",
        )

    supersedes = payload.get("supersedes")
    supersedes_event_id = payload.get("supersedesEventId")
    if (
        supersedes is not None
        and supersedes_event_id is not None
        and supersedes != supersedes_event_id
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting supersedes identifiers")
    if supersedes is None:
        supersedes = supersedes_event_id
    if supersedes is not None and not valid_id(supersedes):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid supersedes")
    decided_at_value = payload.get("decidedAt")
    reviewed_at_value = payload.get("reviewedAt")
    if (
        decided_at_value is not None
        and reviewed_at_value is not None
        and decided_at_value != reviewed_at_value
    ):
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting review timestamps")
    if decided_at_value is None:
        decided_at_value = reviewed_at_value
    decided_at = parse_timestamp(decided_at_value, "reviewedAt", required=True)
    note = short_text(payload.get("note"), "note", MAX_NOTE_CHARS)

    normalized = {
        "decisionId": decision_id,
        "pairId": pair_id,
        "decision": decision,
        "relationType": relation_type,
        "pairContentDigest": supplied_digest,
        "supersedes": supersedes,
        "decidedAt": decided_at,
        "note": note,
    }
    payload_digest = hashlib.sha256(
        canonical_json(normalized, "similarity decision", MAX_BODY_BYTES).encode("utf-8")
    ).hexdigest()
    now = utc_now()

    with WRITE_LOCK, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM similarity_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(existing["payload_digest"], payload_digest):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "decisionId already exists with different content",
                )
            connection.commit()
            return _decision_from_row(existing, current_digest), False

        latest = connection.execute(
            """
            SELECT *
            FROM similarity_decisions
            WHERE pair_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
        if latest is None and supersedes is not None:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "first decision must not supersede another decision",
            )
        if latest is not None and supersedes != latest["decision_id"]:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "supersedes must identify the latest decision",
            )

        connection.execute(
            """
            INSERT INTO similarity_decisions (
                decision_id, pair_id, decision, relation_type,
                pair_content_digest, supersedes_decision_id,
                decided_at_client, note, payload_digest, created_at_server
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                pair_id,
                decision,
                relation_type,
                supplied_digest,
                supersedes,
                decided_at,
                note,
                payload_digest,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM similarity_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        connection.commit()
    return _decision_from_row(row, current_digest), True


def similarity_review_summary(
    connection: sqlite3.Connection,
    snapshot: BundleSnapshot,
) -> dict:
    pairs = similarities_with_latest(connection, snapshot)
    result = {
        "total": len(pairs),
        "unreviewed": 0,
        "current": 0,
        "stale": 0,
        "byDecision": {decision: 0 for decision in sorted(SIMILARITY_DECISIONS)},
    }
    for pair in pairs:
        state = pair["decisionState"]
        result[state] += 1
        latest = pair["latestDecision"]
        if latest is not None and state == "current":
            result["byDecision"][latest["decision"]] += 1
    return result


def _question_id_in(value: object, question_id: str) -> bool:
    if value == question_id:
        return True
    if isinstance(value, dict):
        return any(_question_id_in(child, question_id) for child in value.values())
    if isinstance(value, list):
        return any(_question_id_in(child, question_id) for child in value)
    return False


def _referenced_question_ids(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        question_id = value.get("questionId")
        if isinstance(question_id, str):
            result.add(question_id)
        for child in value.values():
            result.update(_referenced_question_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_referenced_question_ids(child))
    return result


def _single_query(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid {name}")
    return values[0]


def _pagination(query: dict[str, list[str]]) -> tuple[int, int]:
    limit_text = _single_query(query, "limit")
    offset_text = _single_query(query, "offset")
    try:
        limit = 1000 if limit_text is None else int(limit_text)
        offset = 0 if offset_text is None else int(offset_text)
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid pagination") from error
    if not 1 <= limit <= 2000 or not 0 <= offset <= 1_000_000:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid pagination")
    return limit, offset


def _question_filters(query: dict[str, list[str]]) -> dict[str, object | None]:
    subject_id = _single_query(query, "subjectId")
    topic = _single_query(query, "topic")
    question_format = _single_query(query, "format")
    year_text = _single_query(query, "year")
    if subject_id is not None and not valid_id(subject_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid subjectId")
    if topic is not None and (not topic.strip() or len(topic) > 200):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid topic")
    if question_format is not None and question_format not in QUESTION_FORMATS:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid format")
    try:
        year = None if year_text is None else int(year_text)
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid year") from error
    if year is not None and not 1900 <= year <= 2100:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid year")
    return {
        "subjectId": subject_id,
        "topic": topic,
        "format": question_format,
        "year": year,
    }


def _matches_question_filters(
    question: dict,
    filters: dict[str, object | None],
) -> bool:
    subject_id = filters["subjectId"]
    if subject_id is not None and question.get("subjectId") != subject_id:
        return False
    question_format = filters["format"]
    if question_format is not None and question.get("format") != question_format:
        return False
    year = filters["year"]
    exam = question.get("exam")
    if year is not None and (
        not isinstance(exam, dict) or exam.get("year") != year
    ):
        return False
    topic = filters["topic"]
    if topic is not None:
        topic_values = {
            value
            for value in (question.get("topic"), question.get("subtopic"))
            if isinstance(value, str)
        }
        labels = question.get("labels")
        if isinstance(labels, list):
            topic_values.update(value for value in labels if isinstance(value, str))
        if topic not in topic_values:
            return False
    return True


def _has_question_filters(query: dict[str, list[str]]) -> bool:
    return any(name in query for name in ("subjectId", "year", "topic", "format"))


def paginated(
    items: list[dict],
    query: dict[str, list[str]],
    snapshot: BundleSnapshot,
) -> dict:
    limit, offset = _pagination(query)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(page),
            "total": len(items),
            "hasMore": offset + len(page) < len(items),
        },
        "bundle": snapshot.metadata(),
    }


def question_items(snapshot: BundleSnapshot, query: dict[str, list[str]]) -> list[dict]:
    question_id = _single_query(query, "questionId") or _single_query(query, "id")
    if question_id is not None and not valid_id(question_id):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid questionId")
    filters = _question_filters(query)

    checks = _list_section(snapshot.bundle, "officialAnswerChecks")
    checks_by_question: dict[str, list[dict]] = {}
    for check in checks:
        check_question_id = check.get("questionId")
        if isinstance(check_question_id, str):
            checks_by_question.setdefault(check_question_id, []).append(check)

    result: list[dict] = []
    for question in snapshot.questions.values():
        if question_id is not None and question.get("id") != question_id:
            continue
        if not _matches_question_filters(question, filters):
            continue
        item = dict(question)
        item["officialAnswerChecks"] = checks_by_question.get(question["id"], [])
        result.append(item)
    return result


def section_items(
    snapshot: BundleSnapshot,
    section: str,
    query: dict[str, list[str]],
) -> list[dict]:
    aliases = {
        "claudeReviews": ("claude_reviews",),
        "explanationCards": ("cards",),
    }
    items = _list_section(snapshot.bundle, section, *aliases.get(section, ()))
    question_id = _single_query(query, "questionId")
    if question_id is None:
        return list(items)
    return [item for item in items if _question_id_in(item, question_id)]


def cards_payload(
    snapshot: BundleSnapshot,
    query: dict[str, list[str]],
) -> dict:
    study_deck_id = _single_query(query, "studyDeckId")
    deck_id = _single_query(query, "deckId")
    if study_deck_id is not None and deck_id is not None and study_deck_id != deck_id:
        raise ApiError(HTTPStatus.BAD_REQUEST, "conflicting study deck identifiers")
    requested_deck_id = study_deck_id if study_deck_id is not None else deck_id
    deck = resolve_study_deck(
        snapshot,
        requested_deck_id,
        require_if_ambiguous=True,
    )
    cards = cards_for_study_deck(snapshot, deck)
    question_id = _single_query(query, "questionId")
    if question_id is not None:
        cards = [card for card in cards if _question_id_in(card, question_id)]
    filters = _question_filters(query)
    subject_id = filters["subjectId"]
    topic = filters["topic"]
    if subject_id is not None:
        cards = [card for card in cards if card.get("subjectId") == subject_id]
    if topic is not None:
        cards = [
            card
            for card in cards
            if topic in {card.get("topic"), card.get("subtopic")}
        ]
    if filters["year"] is not None or filters["format"] is not None:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "year and format filters are not supported for cards",
        )
    limit, offset = _pagination(query)
    page_cards = [
        card_for_response(card, snapshot, deck)
        for card in cards[offset : offset + limit]
    ]

    referenced_choice_ids: set[str] = set()
    for card in page_cards:
        related = card.get("relatedPastQuestions")
        if not isinstance(related, list):
            continue
        for reference in related:
            if isinstance(reference, dict) and isinstance(reference.get("choiceId"), str):
                referenced_choice_ids.add(reference["choiceId"])

    all_evidence = _list_section(snapshot.bundle, "relatedQuestionEvidence")
    evidence = [
        item
        for item in all_evidence
        if isinstance(item.get("choiceId"), str)
        and item["choiceId"] in referenced_choice_ids
    ]
    return {
        "studyDeck": dict(deck) if deck is not None else None,
        "studyDecks": [dict(item) for item in snapshot.study_decks.values()],
        "subjects": [dict(item) for item in snapshot.bundle.get("subjects", [])]
        if isinstance(snapshot.bundle.get("subjects"), list)
        else [],
        "explanationCards": page_cards,
        "relatedQuestionEvidence": evidence,
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(page_cards),
            "total": len(cards),
            "hasMore": offset + len(page_cards) < len(cards),
        },
        "bundle": snapshot.metadata(),
    }


def overview() -> dict:
    snapshot = CATALOG.load()
    with connect() as connection:
        progress = progress_statistics(connection, snapshot)
        similarity = similarity_review_summary(connection, snapshot)
    reviews = _list_section(snapshot.bundle, "claudeReviews", "claude_reviews")
    runs = _list_section(snapshot.bundle, "claudeRuns")
    cards = _list_section(snapshot.bundle, "explanationCards", "cards")
    evidence = _list_section(snapshot.bundle, "relatedQuestionEvidence")
    return {
        "schemaVersion": 1,
        "serverTime": utc_now(),
        "bundle": snapshot.metadata(),
        "summary": snapshot.bundle.get("summary") or {},
        "catalog": {
            "questions": len(snapshot.questions),
            "officialAnswerChecks": len(
                _list_section(snapshot.bundle, "officialAnswerChecks")
            ),
            "explanationCards": len(cards),
            "studyDecks": len(snapshot.study_decks),
            "relatedQuestionEvidence": len(evidence),
            "claudeReviews": len(reviews),
            "claudeRuns": len(runs),
            "similarityPairs": len(snapshot.similarity_pairs),
        },
        "dataInventory": data_inventory(),
        **progress,
        "similarityReview": similarity,
    }


def export_data() -> dict:
    snapshot = CATALOG.load()
    with connect() as connection:
        attempts = [
            _attempt_from_row(row)
            for row in connection.execute(
                "SELECT * FROM answer_attempts ORDER BY id"
            )
        ]
        decision_rows = connection.execute(
            "SELECT * FROM similarity_decisions ORDER BY id"
        ).fetchall()
        decisions = []
        for row in decision_rows:
            pair = snapshot.similarity_pairs.get(row["pair_id"])
            current_digest = pair.get("pairContentDigest") if pair else None
            decisions.append(_decision_from_row(row, current_digest))
        progress = progress_statistics(connection, snapshot)
        card_attempts = [
            _card_attempt_from_row(row)
            for row in connection.execute(
                "SELECT * FROM card_attempts ORDER BY id"
            )
        ]
        card_marks = [
            _card_mark_from_row(row)
            for row in connection.execute("SELECT * FROM card_marks ORDER BY id")
        ]
        card_progress = card_progress_statistics(
            connection,
            snapshot,
            default_study_deck(snapshot),
        )
    return {
        "schemaVersion": 1,
        "exportedAt": utc_now(),
        "bundle": snapshot.metadata(),
        **progress,
        "attempts": attempts,
        "cardProgress": card_progress,
        "cardAttempts": card_attempts,
        "cardMarks": card_marks,
        "similarityDecisions": decisions,
    }


class ProductionHandler(BaseHTTPRequestHandler):
    server_version = "GyouseiProduction/1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path == "/health":
                status = CATALOG.status()
                self.send_json(
                    {"ok": status["available"], "time": utc_now(), "bundle": status},
                    HTTPStatus.OK if status["available"] else HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            if parsed.path == "/api/overview":
                self.send_json(overview())
                return
            if parsed.path == "/api/data-inventory":
                self.send_json(data_inventory())
                return
            if parsed.path == "/api/progress":
                snapshot = CATALOG.load()
                with connect() as connection:
                    result = progress_statistics(connection, snapshot)
                result["bundle"] = snapshot.metadata()
                result["serverTime"] = utc_now()
                self.send_json(result)
                return
            if parsed.path == "/api/card-source":
                # 編集フォームは、bundleの投影ではなく正本そのものを読む。
                card_id = _single_query(query, "cardId")
                if not valid_id(card_id):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "invalid cardId")
                card_edit, _ = _card_edit_modules()
                document = card_edit.load_canonical()
                card = next(
                    (i for i in document["items"] if i["id"] == card_id), None
                )
                if card is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown cardId")
                self.send_json(
                    {
                        "cardId": card_id,
                        "editable": card_edit.editable_of(card),
                        "figureChoices": sorted(
                            "assets/card-figures/" + p.name
                            for p in card_edit.FIGURE_DIR.glob("*")
                            if p.is_file() and not p.name.startswith(".")
                        ),
                        "bundle": CATALOG.load().metadata(),
                    }
                )
                return
            if parsed.path == "/api/card-progress":
                snapshot = CATALOG.load()
                study_deck_id = _single_query(query, "studyDeckId")
                deck_id = _single_query(query, "deckId")
                if (
                    study_deck_id is not None
                    and deck_id is not None
                    and study_deck_id != deck_id
                ):
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "conflicting study deck identifiers",
                    )
                requested_deck_id = (
                    study_deck_id if study_deck_id is not None else deck_id
                )
                deck = resolve_study_deck(
                    snapshot,
                    requested_deck_id,
                    require_if_ambiguous=True,
                )
                with connect() as connection:
                    result = card_progress_statistics(connection, snapshot, deck)
                result["bundle"] = snapshot.metadata()
                self.send_json(result)
                return
            if parsed.path == "/api/study-queue":
                self.send_json(study_queue_response(query))
                return
            if parsed.path == "/api/rating-preview":
                self.send_json(rating_preview_response(query))
                return
            if parsed.path == "/api/learning-analysis":
                self.send_json(learning_analysis())
                return
            if parsed.path == "/api/questions":
                snapshot = CATALOG.load()
                self.send_json(paginated(question_items(snapshot, query), query, snapshot))
                return
            if parsed.path == "/api/claude-reviews":
                snapshot = CATALOG.load()
                items = section_items(snapshot, "claudeReviews", query)
                response = paginated(items, query, snapshot)
                response["claudeRuns"] = _list_section(snapshot.bundle, "claudeRuns")
                self.send_json(response)
                return
            if parsed.path == "/api/cards":
                snapshot = CATALOG.load()
                self.send_json(cards_payload(snapshot, query))
                return
            if parsed.path == "/api/similarities":
                snapshot = CATALOG.load()
                with connect() as connection:
                    items = similarities_with_latest(connection, snapshot)
                pair_id = _single_query(query, "pairId")
                question_id = _single_query(query, "questionId")
                decision_state = _single_query(query, "decisionState")
                if pair_id is not None:
                    items = [item for item in items if item.get("id") == pair_id]
                if question_id is not None:
                    items = [
                        item for item in items if _question_id_in(item, question_id)
                    ]
                if _has_question_filters(query):
                    filters = _question_filters(query)
                    matching_question_ids = {
                        question["id"]
                        for question in snapshot.questions.values()
                        if _matches_question_filters(question, filters)
                    }
                    items = [
                        item
                        for item in items
                        if _referenced_question_ids(item) & matching_question_ids
                    ]
                if decision_state is not None:
                    if decision_state not in {"unreviewed", "current", "stale"}:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid decisionState")
                    items = [
                        item
                        for item in items
                        if item["decisionState"] == decision_state
                    ]
                self.send_json(paginated(items, query, snapshot))
                return
            if parsed.path == "/api/export":
                self.send_json(export_data())
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self.send_json({"error": error.message}, error.status)
        except Exception:
            self.send_json(
                {"error": "internal server error"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        try:
            self.require_write_request()
            payload = self.read_json()
            path = urlsplit(self.path).path
            if path == "/api/card-attempts":
                attempt, inserted, snapshot, deck = add_card_attempt(payload)
                with connect() as connection:
                    progress = card_progress_statistics(connection, snapshot, deck)
                progress["bundle"] = snapshot.metadata()
                analysis_updated = False
                if inserted:
                    try:
                        refresh_weakness_latest(snapshot, deck)
                        analysis_updated = True
                    except Exception:
                        analysis_updated = False
                self.send_json(
                    {
                        "saved": inserted,
                        "duplicate": not inserted,
                        "attempt": attempt,
                        "learningAnalysisUpdated": analysis_updated,
                        **progress,
                    },
                    HTTPStatus.CREATED if inserted else HTTPStatus.OK,
                )
                return
            if path == "/api/card-source":
                self.send_json(save_card_edit(payload))
                return
            if path == "/api/card-marks":
                mark, inserted, snapshot, deck = add_card_mark(payload)
                with connect() as connection:
                    progress = card_progress_statistics(connection, snapshot, deck)
                progress["bundle"] = snapshot.metadata()
                self.send_json(
                    {
                        "saved": inserted,
                        "duplicate": not inserted,
                        "mark": mark,
                        **progress,
                    },
                    HTTPStatus.CREATED if inserted else HTTPStatus.OK,
                )
                return
            if path == "/api/attempts":
                attempt, inserted = add_attempt(payload)
                with connect() as connection:
                    progress = progress_statistics(connection)
                self.send_json(
                    {
                        "saved": inserted,
                        "duplicate": not inserted,
                        "attempt": attempt,
                        **progress,
                    },
                    HTTPStatus.CREATED if inserted else HTTPStatus.OK,
                )
                return
            if path == "/api/similarity-decisions":
                decision, inserted = add_similarity_decision(payload)
                self.send_json(
                    {
                        "saved": inserted,
                        "duplicate": not inserted,
                        "decision": decision,
                    },
                    HTTPStatus.CREATED if inserted else HTTPStatus.OK,
                )
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self.close_connection = True
            self.send_json({"error": error.message}, error.status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.close_connection = True
            self.send_json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self.close_connection = True
            self.send_json(
                {"error": "internal server error"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_OPTIONS(self) -> None:
        self.send_json(
            {"error": "cross-origin requests are not allowed"},
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    def require_write_request(self) -> None:
        if self.headers.get("X-Gyousei-Client") != CLIENT_HEADER:
            raise ApiError(HTTPStatus.FORBIDDEN, "missing client header")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "application/json required",
            )

        origin = self.headers.get("Origin")
        if not origin:
            raise ApiError(HTTPStatus.FORBIDDEN, "Origin required")
        forwarded_host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        forwarded_proto = self.headers.get("X-Forwarded-Proto") or "http"
        if not forwarded_host or forwarded_proto not in {"http", "https"}:
            raise ApiError(HTTPStatus.FORBIDDEN, "origin not allowed")
        if origin != f"{forwarded_proto}://{forwarded_host}":
            raise ApiError(HTTPStatus.FORBIDDEN, "origin not allowed")

    def read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from error
        if not 0 <= length <= MAX_BODY_BYTES:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request body too large",
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ApiError(HTTPStatus.BAD_REQUEST, "incomplete request body")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON object required")
        return payload

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        if len(args) > 1 and str(args[1]).startswith("5"):
            super().log_message(format_string, *args)


def main() -> None:
    init_database()
    server = ThreadingHTTPServer((HOST, PORT), ProductionHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
