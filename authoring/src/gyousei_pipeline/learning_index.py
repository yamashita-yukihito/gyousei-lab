"""Build a private, multi-subject learning-card candidate index.

The module accepts one or more explanation-card JSON documents and one or
more ``review_candidates`` documents.  It normalizes their subject hierarchy,
ranks same-field and cross-field past-question candidates separately, and
writes only a bounded number of matches per card.  Candidate pairs are scored
one card at a time through an inverted n-gram/legal-term index; a Cartesian
card-by-candidate list is never materialized.

The generated JSON contains past-question text and must remain private.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .common import data_root, load_json
from .subjects import CANONICAL_SUBJECT_LABELS, canonical_subject_id


SCHEMA_VERSION = "learning-index@1"
DEFAULT_NGRAM_SIZE = 2
DEFAULT_MIN_SCORE = 0.16
DEFAULT_SAME_FIELD_LIMIT = 12
DEFAULT_CROSS_FIELD_LIMIT = 8
MAX_QUERY_GRAMS = 256

# Deliberately finite and extensible from the CLI.  Character n-grams still
# provide recall for terms not present here; these entries add a stronger legal
# signal for common administrative-scrivener subjects.
IMPORTANT_LEGAL_TERMS = (
    "行政手続法",
    "行政不服審査法",
    "行政事件訴訟法",
    "行政代執行法",
    "国家賠償法",
    "地方自治法",
    "国家行政組織法",
    "行政機関情報公開法",
    "個人情報保護法",
    "日本国憲法",
    "憲法",
    "民法",
    "商法",
    "会社法",
    "取消訴訟",
    "無効等確認訴訟",
    "義務付け訴訟",
    "差止訴訟",
    "当事者訴訟",
    "審査請求",
    "再調査の請求",
    "執行停止",
    "原告適格",
    "訴えの利益",
    "処分性",
    "行政処分",
    "行政行為",
    "行政指導",
    "行政立法",
    "行政契約",
    "聴聞",
    "弁明の機会",
    "代理人",
    "審査基準",
    "処分基準",
    "理由提示",
    "公定力",
    "不可争力",
    "不可変更力",
    "自力執行力",
    "行政代執行",
    "直接強制",
    "即時強制",
    "住民監査請求",
    "住民訴訟",
    "条例",
    "法律の留保",
    "比例原則",
    "信頼保護原則",
    "表現の自由",
    "職業選択の自由",
    "財産権",
    "所有権",
    "契約自由の原則",
    "意思表示",
    "代理権",
    "時効",
    "債務不履行",
    "不法行為",
)

_SPACE_RE = re.compile(r"\s+")
_SEARCH_SEPARATOR_RE = re.compile(r"[\W_]+", re.UNICODE)
_RELATION_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


class LearningIndexError(ValueError):
    """Raised when an input cannot satisfy the learning-index contract."""


def normalize_dimension(value: Any) -> str:
    """Return a display-safe NFKC/whitespace-normalized hierarchy value."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).replace("\u3000", " ")
    return _SPACE_RE.sub(" ", normalized).strip()


def dimension_key(value: Any) -> str:
    """Return a punctuation/space-insensitive key for hierarchy comparison."""

    return _SEARCH_SEPARATOR_RE.sub("", normalize_dimension(value).casefold())


def normalize_subject_id(value: Any) -> str:
    """Normalize common Japanese subject names and arbitrary subject IDs."""

    text = normalize_dimension(value)
    if not text:
        return ""
    return canonical_subject_id(text)


def normalize_search_text(value: Any) -> str:
    """Compact text for Japanese/ASCII character n-gram matching."""

    return _SEARCH_SEPARATOR_RE.sub("", normalize_dimension(value).casefold())


def character_ngrams(value: Any, size: int = DEFAULT_NGRAM_SIZE) -> frozenset[str]:
    """Return unique compact character n-grams, including short whole strings."""

    if size < 1:
        raise LearningIndexError("ngram size must be positive")
    text = normalize_search_text(value)
    if not text:
        return frozenset()
    if len(text) <= size:
        return frozenset({text})
    return frozenset(
        text[index : index + size] for index in range(len(text) - size + 1)
    )


def normalize_legal_terms(extra_terms: Iterable[str] = ()) -> tuple[str, ...]:
    """Return deterministic, de-duplicated legal terms."""

    by_key: dict[str, str] = {}
    for value in (*IMPORTANT_LEGAL_TERMS, *tuple(extra_terms)):
        term = normalize_dimension(value)
        key = normalize_search_text(term)
        if key:
            by_key.setdefault(key, term)
    return tuple(
        by_key[key] for key in sorted(by_key, key=lambda item: (-len(item), item))
    )


def extract_legal_terms(
    value: Any, important_terms: Iterable[str] = IMPORTANT_LEGAL_TERMS
) -> frozenset[str]:
    """Extract configured legal terms contained in normalized text."""

    text = normalize_search_text(value)
    if not text:
        return frozenset()
    found: set[str] = set()
    for raw_term in important_terms:
        term = normalize_dimension(raw_term)
        key = normalize_search_text(term)
        if key and key in text:
            found.add(term)
    return frozenset(found)


def _nested_value(record: Mapping[str, Any], name: str) -> Any:
    if name in record:
        return record.get(name)
    for container_name in ("field", "classification", "taxonomy"):
        container = record.get(container_name)
        if isinstance(container, Mapping) and name in container:
            return container.get(name)
    return None


def _labels(record: Mapping[str, Any]) -> list[str]:
    values = record.get("labels")
    citation = record.get("sourceCitation")
    if not isinstance(values, list) and isinstance(citation, Mapping):
        values = citation.get("labels")
    if not isinstance(values, list):
        return []
    return [
        normalize_dimension(value) for value in values if normalize_dimension(value)
    ]


def _subject_from_values(*values: Any) -> str:
    for value in values:
        text = normalize_dimension(value)
        if not text:
            continue
        subject_id = canonical_subject_id(text)
        if subject_id in CANONICAL_SUBJECT_LABELS:
            return subject_id
    return ""


def normalize_field(
    record: Mapping[str, Any], *, require_subject: bool
) -> dict[str, str]:
    """Normalize ``subjectId/category/topic/subtopic`` from direct or label data."""

    labels = _labels(record)
    raw_subject = _nested_value(record, "subjectId") or _nested_value(record, "subject")
    category = normalize_dimension(_nested_value(record, "category"))
    topic = normalize_dimension(_nested_value(record, "topic"))
    subtopic = normalize_dimension(_nested_value(record, "subtopic"))

    subject_id = normalize_subject_id(raw_subject)
    if not subject_id:
        subject_id = _subject_from_values(category, topic, *labels)

    if not category and labels:
        category = labels[0]
    if not topic:
        if len(labels) >= 2:
            topic = labels[1]
        elif labels and not _subject_from_values(labels[0]):
            topic = labels[0]
    if not subtopic and len(labels) >= 3:
        subtopic = labels[2]

    if require_subject and not subject_id:
        identifier = record.get("id") or record.get("cardId") or "<unknown-card>"
        raise LearningIndexError(f"{identifier}: subjectId cannot be inferred")
    if not subject_id:
        subject_id = "unknown"

    category_key = dimension_key(category)
    topic_key = dimension_key(topic)
    subtopic_key = dimension_key(subtopic)
    path_parts = [subject_id]
    path_parts.extend(part for part in (category_key, topic_key, subtopic_key) if part)
    return {
        "subjectId": subject_id,
        "category": category,
        "categoryKey": category_key,
        "topic": topic,
        "topicKey": topic_key,
        "subtopic": subtopic,
        "subtopicKey": subtopic_key,
        "pathKey": "::".join(path_parts),
    }


def _mapping_texts(value: Any, approved_keys: Sequence[str]) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return [
        normalize_dimension(value.get(key)) for key in approved_keys if value.get(key)
    ]


def _card_text(record: Mapping[str, Any], field: Mapping[str, str]) -> str:
    parts = [field["category"], field["topic"], field["subtopic"]]
    parts.extend(
        _mapping_texts(
            record.get("variants"),
            ("a", "b", "bCasual", "c"),
        )
    )
    parts.extend(
        normalize_dimension(record.get(key))
        for key in ("correction", "memoryPoint", "questionText", "statementText")
        if record.get(key)
    )
    explanations = record.get("explanations")
    parts.extend(_mapping_texts(explanations, ("normal", "commonSense")))
    if isinstance(explanations, Mapping):
        parts.extend(
            _mapping_texts(
                explanations.get("deepDive"), ("background", "trap", "example")
            )
        )
    legal_basis = record.get("legalBasis")
    if isinstance(legal_basis, list):
        parts.extend(
            normalize_dimension(item.get("label"))
            for item in legal_basis
            if isinstance(item, Mapping) and item.get("label")
        )
    return " ".join(part for part in parts if part)


def _candidate_text(record: Mapping[str, Any], field: Mapping[str, str]) -> str:
    parts = [field["category"], field["topic"], field["subtopic"]]
    parts.extend(
        normalize_dimension(record.get(key))
        for key in ("statementText", "questionText", "instructionText", "passageText")
        if record.get(key)
    )
    task = record.get("taskMetadata")
    if isinstance(task, Mapping) and task.get("prompt"):
        parts.append(normalize_dimension(task.get("prompt")))
    original = record.get("originalQuestion")
    if isinstance(original, Mapping):
        parts.extend(
            _mapping_texts(
                original,
                ("questionText", "instructionText", "passageText", "referenceText"),
            )
        )
        choices = original.get("choices")
        if isinstance(choices, list):
            parts.extend(
                normalize_dimension(choice.get("text"))
                for choice in choices
                if isinstance(choice, Mapping) and choice.get("text")
            )
    return " ".join(part for part in parts if part)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _relation_code(value: Any) -> str:
    text = normalize_dimension(value).casefold()
    return _RELATION_SEPARATOR_RE.sub("_", text).strip("_")


def relation_codes(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect common relation fields without treating prose as a signal."""

    values: list[Any] = []
    for key in ("relation", "relationship", "relationType", "decision", "decisionType"):
        value = record.get(key)
        if isinstance(value, str):
            values.append(value)
    relations = record.get("relations")
    if isinstance(relations, list):
        values.extend(value for value in relations if isinstance(value, str))
    latest = record.get("latestDecision")
    if isinstance(latest, Mapping):
        for key in ("relation", "relationship", "decision"):
            if isinstance(latest.get(key), str):
                values.append(latest[key])
    return tuple(
        sorted({_relation_code(value) for value in values if _relation_code(value)})
    )


def normalize_card(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project and normalize one learning card."""

    identifier = normalize_dimension(record.get("id") or record.get("cardId"))
    if not identifier:
        raise LearningIndexError("card is missing id/cardId")
    field = normalize_field(record, require_subject=True)
    return {
        "cardId": identifier,
        "field": field,
        "searchText": _card_text(record, field),
    }


def _candidate_identifier(record: Mapping[str, Any]) -> str:
    identifier = normalize_dimension(record.get("candidateId") or record.get("id"))
    if identifier:
        return identifier
    raw_id = normalize_dimension(record.get("rawQuestionId"))
    choice = normalize_dimension(record.get("choiceLabel"))
    if raw_id:
        return f"{raw_id}:choice:{choice}" if choice else f"{raw_id}:original"
    raise LearningIndexError("review candidate is missing candidateId/rawQuestionId")


def normalize_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project one review candidate without copying provider explanations."""

    field = normalize_field(record, require_subject=False)
    citation = record.get("sourceCitation")
    if not isinstance(citation, Mapping):
        citation = {}
    codes = relation_codes(record)
    explicitly_disabled = (
        record.get("frequencyEligible") is False
        or record.get("autoAddToFrequency") is False
    )
    same_topic_only = set(codes) == {"same_topic"}
    statement = normalize_dimension(record.get("statementText"))
    if not statement:
        original = record.get("originalQuestion")
        if isinstance(original, Mapping):
            statement = normalize_dimension(
                original.get("questionText") or original.get("passageText")
            )
    if not statement:
        statement = normalize_dimension(record.get("questionText"))
    return {
        "candidateId": _candidate_identifier(record),
        "candidateKind": normalize_dimension(record.get("candidateKind")),
        "rawQuestionId": normalize_dimension(record.get("rawQuestionId")),
        "examYear": _positive_int(record.get("examYear")),
        "questionNumber": _positive_int(record.get("questionNumber")),
        "choiceLabel": normalize_dimension(record.get("choiceLabel")),
        "statementText": statement,
        "field": field,
        "relationCodes": list(codes),
        "frequencyEligible": not explicitly_disabled and not same_topic_only,
        "sameTopicOnly": same_topic_only,
        "sourceCitation": {
            "title": normalize_dimension(citation.get("title")),
            "sourceUrl": normalize_dimension(citation.get("sourceUrl")),
        },
        "searchText": _candidate_text(record, field),
    }


def same_field(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    """Return whether two normalized fields represent the same study field."""

    if left.get("subjectId") != right.get("subjectId"):
        return False
    left_topic = left.get("topicKey") or ""
    right_topic = right.get("topicKey") or ""
    if left_topic and right_topic:
        return left_topic == right_topic
    left_category = left.get("categoryKey") or ""
    right_category = right.get("categoryKey") or ""
    if left_category and right_category:
        return left_category == right_category
    return bool(left.get("subjectId") and left.get("subjectId") != "unknown")


def similarity_signals(
    card: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    ngram_size: int = DEFAULT_NGRAM_SIZE,
    important_terms: Iterable[str] = IMPORTANT_LEGAL_TERMS,
) -> dict[str, Any]:
    """Return pure n-gram/legal-term/hierarchy signals for a pair."""

    card_grams = character_ngrams(card.get("searchText"), ngram_size)
    candidate_grams = character_ngrams(candidate.get("searchText"), ngram_size)
    terms = normalize_legal_terms(important_terms)
    card_terms = extract_legal_terms(card.get("searchText"), terms)
    candidate_terms = extract_legal_terms(candidate.get("searchText"), terms)
    return _similarity_signals_from_features(
        card["field"],
        candidate["field"],
        card_grams,
        candidate_grams,
        card_terms,
        candidate_terms,
    )


def _similarity_signals_from_features(
    card_field: Mapping[str, str],
    candidate_field: Mapping[str, str],
    card_grams: frozenset[str],
    candidate_grams: frozenset[str],
    card_terms: frozenset[str],
    candidate_terms: frozenset[str],
) -> dict[str, Any]:
    """Score already prepared features without repeating pair tokenization."""

    shared_grams = card_grams & candidate_grams
    union_grams = card_grams | candidate_grams
    smaller = min(len(card_grams), len(candidate_grams))
    containment = len(shared_grams) / smaller if smaller else 0.0
    jaccard = len(shared_grams) / len(union_grams) if union_grams else 0.0
    ngram_score = 0.65 * containment + 0.35 * jaccard

    shared_terms = card_terms & candidate_terms
    smaller_terms = min(len(card_terms), len(candidate_terms))
    legal_term_score = len(shared_terms) / smaller_terms if smaller_terms else 0.0

    hierarchy_bonus = 0.0
    if card_field["subjectId"] == candidate_field["subjectId"]:
        hierarchy_bonus += 0.03
    if (
        card_field["categoryKey"]
        and card_field["categoryKey"] == candidate_field["categoryKey"]
    ):
        hierarchy_bonus += 0.05
    if card_field["topicKey"] and card_field["topicKey"] == candidate_field["topicKey"]:
        hierarchy_bonus += 0.08
    if (
        card_field["subtopicKey"]
        and card_field["subtopicKey"] == candidate_field["subtopicKey"]
    ):
        hierarchy_bonus += 0.04

    score = min(1.0, 0.65 * ngram_score + 0.25 * legal_term_score + hierarchy_bonus)
    return {
        "score": round(score, 6),
        "ngramScore": round(ngram_score, 6),
        "sharedNgramCount": len(shared_grams),
        "sharedLegalTerms": sorted(shared_terms),
        "hierarchyBonus": round(hierarchy_bonus, 6),
    }


@dataclass(frozen=True, slots=True)
class _PreparedCandidate:
    value: dict[str, Any]
    grams: frozenset[str]
    legal_terms: frozenset[str]


class _CandidateSearchIndex:
    """Inverted feature index used to avoid materializing all card pairs."""

    def __init__(
        self,
        candidates: Sequence[dict[str, Any]],
        *,
        ngram_size: int,
        important_terms: tuple[str, ...],
    ) -> None:
        self.ngram_size = ngram_size
        self.important_terms = important_terms
        self.candidates: list[_PreparedCandidate] = []
        self.gram_postings: dict[str, list[int]] = defaultdict(list)
        self.term_postings: dict[str, list[int]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            grams = character_ngrams(candidate["searchText"], ngram_size)
            terms = extract_legal_terms(candidate["searchText"], important_terms)
            self.candidates.append(_PreparedCandidate(candidate, grams, terms))
            for gram in grams:
                self.gram_postings[gram].append(index)
            for term in terms:
                self.term_postings[term].append(index)

    def matching_indexes(
        self, card_grams: frozenset[str], card_terms: frozenset[str]
    ) -> list[int]:
        matches: set[int] = set()
        # Rare postings are most useful.  The cap prevents a very long card from
        # unioning thousands of generic postings while preserving bounded memory
        # for one card at a time.
        postings = [
            self.gram_postings[gram]
            for gram in card_grams
            if gram in self.gram_postings
        ]
        postings.sort(key=len)
        for posting in postings[:MAX_QUERY_GRAMS]:
            matches.update(posting)
        for term in card_terms:
            matches.update(self.term_postings.get(term, ()))
        return sorted(
            matches, key=lambda index: self.candidates[index].value["candidateId"]
        )


@dataclass(slots=True)
class _HeapItem:
    score: float
    candidate_id: str
    value: dict[str, Any]

    def __lt__(self, other: "_HeapItem") -> bool:
        if self.score != other.score:
            return self.score < other.score
        # For equal scores the lexicographically larger ID is the worse item.
        return self.candidate_id > other.candidate_id


def _bounded_push(heap: list[_HeapItem], value: dict[str, Any], limit: int) -> None:
    if limit <= 0:
        return
    item = _HeapItem(value["score"], value["candidateId"], value)
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif heap[0] < item:
        heapq.heapreplace(heap, item)


def _ordered_heap(heap: list[_HeapItem]) -> list[dict[str, Any]]:
    return [
        item.value
        for item in sorted(heap, key=lambda item: (-item.score, item.candidate_id))
    ]


def _question_key(candidate: Mapping[str, Any]) -> tuple[int, int] | None:
    year = candidate.get("examYear")
    number = candidate.get("questionNumber")
    if isinstance(year, int) and isinstance(number, int):
        return (year, number)
    return None


def _rank_projection(
    candidate: Mapping[str, Any], signals: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "candidateId": candidate["candidateId"],
        "candidateKind": candidate["candidateKind"],
        "rawQuestionId": candidate["rawQuestionId"],
        "examYear": candidate["examYear"],
        "questionNumber": candidate["questionNumber"],
        "choiceLabel": candidate["choiceLabel"],
        "statementText": candidate["statementText"],
        "field": candidate["field"],
        "score": signals["score"],
        "signals": {
            "ngramScore": signals["ngramScore"],
            "sharedNgramCount": signals["sharedNgramCount"],
            "sharedLegalTerms": signals["sharedLegalTerms"],
            "hierarchyBonus": signals["hierarchyBonus"],
        },
        "relationCodes": candidate["relationCodes"],
        "frequencyEligible": candidate["frequencyEligible"],
        "sourceCitation": candidate["sourceCitation"],
    }


def _frequency_summary(
    included: set[tuple[int, int]],
    excluded: set[tuple[int, int]],
    matched_candidates: int,
) -> dict[str, Any]:
    ordered = sorted(included)
    years = sorted({year for year, _ in ordered})
    excluded_only = sorted(excluded - included)
    return {
        "questionCount": len(ordered),
        "yearCount": len(years),
        "examYears": years,
        "questions": [
            {"examYear": year, "questionNumber": number} for year, number in ordered
        ],
        "matchedCandidateCount": matched_candidates,
        "excludedSameTopicOnlyQuestionCount": len(excluded_only),
    }


def _rank_card(
    card: dict[str, Any],
    search_index: _CandidateSearchIndex,
    *,
    same_field_limit: int,
    cross_field_limit: int,
    min_score: float,
) -> dict[str, Any]:
    card_grams = character_ngrams(card["searchText"], search_index.ngram_size)
    card_terms = extract_legal_terms(card["searchText"], search_index.important_terms)
    same_heap: list[_HeapItem] = []
    cross_heap: list[_HeapItem] = []
    frequency_questions: set[tuple[int, int]] = set()
    same_topic_excluded: set[tuple[int, int]] = set()
    matched_candidates = 0

    for index in search_index.matching_indexes(card_grams, card_terms):
        prepared = search_index.candidates[index]
        candidate = prepared.value
        shared_grams = card_grams & prepared.grams
        shared_terms = card_terms & prepared.legal_terms
        if not shared_grams and not shared_terms:
            continue
        signals = _similarity_signals_from_features(
            card["field"],
            candidate["field"],
            card_grams,
            prepared.grams,
            card_terms,
            prepared.legal_terms,
        )
        if signals["score"] < min_score:
            continue
        projection = _rank_projection(candidate, signals)
        if same_field(card["field"], candidate["field"]):
            matched_candidates += 1
            _bounded_push(same_heap, projection, same_field_limit)
            question_key = _question_key(candidate)
            if question_key is not None:
                if candidate["frequencyEligible"]:
                    frequency_questions.add(question_key)
                elif candidate["sameTopicOnly"]:
                    same_topic_excluded.add(question_key)
        else:
            _bounded_push(cross_heap, projection, cross_field_limit)

    return {
        "cardId": card["cardId"],
        "field": card["field"],
        "frequency": _frequency_summary(
            frequency_questions, same_topic_excluded, matched_candidates
        ),
        "rankings": {
            "sameField": _ordered_heap(same_heap),
            "crossField": _ordered_heap(cross_heap),
        },
    }


def _deduplicate(
    records: Iterable[Mapping[str, Any]], normalizer: Any, identifier_key: str
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise LearningIndexError("input collection contains a non-object value")
        normalized = normalizer(raw_record)
        identifier = normalized[identifier_key]
        previous = by_id.get(identifier)
        if previous is None:
            by_id[identifier] = normalized
        elif previous != normalized:
            raise LearningIndexError(
                f"conflicting duplicate {identifier_key}: {identifier}"
            )
    return [by_id[identifier] for identifier in sorted(by_id)]


def _field_index(cards: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_subject: dict[str, list[str]] = defaultdict(list)
    by_category: dict[str, dict[str, Any]] = {}
    by_topic: dict[str, dict[str, Any]] = {}
    by_subtopic: dict[str, dict[str, Any]] = {}

    for card in cards:
        card_id = card["cardId"]
        field = card["field"]
        subject_id = field["subjectId"]
        by_subject[subject_id].append(card_id)
        levels = (
            ("category", "categoryKey", by_category),
            ("topic", "topicKey", by_topic),
            ("subtopic", "subtopicKey", by_subtopic),
        )
        for display_name, key_name, destination in levels:
            normalized_key = field[key_name]
            if not normalized_key:
                continue
            index_key = f"{subject_id}::{normalized_key}"
            row = destination.setdefault(
                index_key,
                {
                    "subjectId": subject_id,
                    display_name: field[display_name],
                    f"{display_name}Key": normalized_key,
                    "cardIds": [],
                },
            )
            row["cardIds"].append(card_id)

    for values in by_subject.values():
        values.sort()
    for destination in (by_category, by_topic, by_subtopic):
        for row in destination.values():
            row["cardIds"].sort()
    return {
        "bySubject": dict(sorted(by_subject.items())),
        "byCategory": dict(sorted(by_category.items())),
        "byTopic": dict(sorted(by_topic.items())),
        "bySubtopic": dict(sorted(by_subtopic.items())),
    }


def build_learning_index(
    card_records: Iterable[Mapping[str, Any]],
    candidate_records: Iterable[Mapping[str, Any]],
    *,
    same_field_limit: int = DEFAULT_SAME_FIELD_LIMIT,
    cross_field_limit: int = DEFAULT_CROSS_FIELD_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
    ngram_size: int = DEFAULT_NGRAM_SIZE,
    extra_legal_terms: Iterable[str] = (),
    generated_at: str | None = None,
    source_inputs: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic private index without a Cartesian pair list.

    This is the main pure function.  ``generated_at`` is caller-supplied so
    tests and reproducible builds do not depend on the clock.
    """

    if same_field_limit < 1 or cross_field_limit < 1:
        raise LearningIndexError("ranking limits must be positive")
    if not 0 <= min_score <= 1:
        raise LearningIndexError("min_score must be between 0 and 1")
    if not 1 <= ngram_size <= 8:
        raise LearningIndexError("ngram_size must be between 1 and 8")

    cards = _deduplicate(card_records, normalize_card, "cardId")
    candidates = _deduplicate(candidate_records, normalize_candidate, "candidateId")
    if not cards:
        raise LearningIndexError("at least one learning card is required")

    important_terms = normalize_legal_terms(extra_legal_terms)
    search_index = _CandidateSearchIndex(
        candidates, ngram_size=ngram_size, important_terms=important_terms
    )
    ranked_cards = [
        _rank_card(
            card,
            search_index,
            same_field_limit=same_field_limit,
            cross_field_limit=cross_field_limit,
            min_score=min_score,
        )
        for card in cards
    ]
    fields = _field_index(cards)
    subjects = sorted(fields["bySubject"])
    candidate_subjects = sorted(
        {candidate["field"]["subjectId"] for candidate in candidates}
    )
    unique_questions = {
        key for candidate in candidates if (key := _question_key(candidate)) is not None
    }
    summary = {
        "cardCount": len(cards),
        "subjectCount": len(subjects),
        "candidateCount": len(candidates),
        "candidateSubjectCount": len(candidate_subjects),
        "uniqueQuestionCount": len(unique_questions),
        "sameFieldRankedCount": sum(
            len(card["rankings"]["sameField"]) for card in ranked_cards
        ),
        "crossFieldRankedCount": sum(
            len(card["rankings"]["crossField"]) for card in ranked_cards
        ),
    }
    document: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "visibility": "private_not_for_web",
        "summary": summary,
        "rankingPolicy": {
            "ngramSize": ngram_size,
            "minScore": min_score,
            "sameFieldLimit": same_field_limit,
            "crossFieldLimit": cross_field_limit,
            "sameTopicOnlyAutoFrequency": False,
            "questionIdentity": ["examYear", "questionNumber"],
            "pairMaterialization": "bounded_per_card_via_inverted_index",
        },
        "subjects": [
            {"subjectId": subject_id, "cardCount": len(fields["bySubject"][subject_id])}
            for subject_id in subjects
        ],
        "fieldIndex": fields,
        "cards": ranked_cards,
    }
    if generated_at is not None:
        document["generatedAt"] = generated_at
    if source_inputs is not None:
        document["sourceInputs"] = {
            key: list(values) for key, values in sorted(source_inputs.items())
        }
    return document


def atomic_write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace ``path`` with an fsynced mode-0600 JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _json_paths(paths: Sequence[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            matches = sorted(path.rglob("*.json"))
            if not matches:
                raise LearningIndexError(f"no JSON input found: {path}")
            expanded.extend(matches)
        elif path.is_file():
            expanded.append(path)
        else:
            raise LearningIndexError(f"JSON input does not exist: {path}")
    return expanded


def load_json_records(
    paths: Sequence[Path],
    *,
    collection_keys: Sequence[str],
    singular_keys: Sequence[str],
) -> list[dict[str, Any]]:
    """Load records from repeated files/directories and common wrapper shapes."""

    records: list[dict[str, Any]] = []
    for path in _json_paths(paths):
        value = load_json(path)
        collection: Any = None
        if isinstance(value, list):
            collection = value
        elif isinstance(value, Mapping):
            for key in collection_keys:
                if isinstance(value.get(key), list):
                    collection = value[key]
                    break
            if collection is None and any(key in value for key in singular_keys):
                collection = [value]
        if not isinstance(collection, list):
            raise LearningIndexError(f"no supported records in: {path}")
        for item in collection:
            if not isinstance(item, Mapping):
                raise LearningIndexError(f"non-object record in: {path}")
            records.append(dict(item))
    return records


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cards",
        type=Path,
        action="extend",
        nargs="+",
        required=True,
        help="one or more card JSON files/directories; the option may be repeated",
    )
    parser.add_argument(
        "--review-candidates",
        type=Path,
        action="extend",
        nargs="+",
        required=True,
        help="one or more review_candidates JSON files/directories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "curation" / "learning_index.json",
        help="private atomic JSON destination (written with mode 0600)",
    )
    parser.add_argument(
        "--same-field-limit", type=int, default=DEFAULT_SAME_FIELD_LIMIT
    )
    parser.add_argument(
        "--cross-field-limit", type=int, default=DEFAULT_CROSS_FIELD_LIMIT
    )
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--ngram-size", type=int, default=DEFAULT_NGRAM_SIZE)
    parser.add_argument(
        "--legal-term",
        action="append",
        default=[],
        help="additional important legal term; may be repeated",
    )
    parser.add_argument(
        "--generated-at",
        help="fixed ISO timestamp for reproducible builds (default: current UTC)",
    )
    return parser


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        cards = load_json_records(
            args.cards,
            collection_keys=("explanationCards", "cards", "items"),
            singular_keys=("id", "cardId"),
        )
        candidates = load_json_records(
            args.review_candidates,
            collection_keys=("candidates", "reviewCandidates", "items"),
            singular_keys=("candidateId", "rawQuestionId"),
        )
        document = build_learning_index(
            cards,
            candidates,
            same_field_limit=args.same_field_limit,
            cross_field_limit=args.cross_field_limit,
            min_score=args.min_score,
            ngram_size=args.ngram_size,
            extra_legal_terms=args.legal_term,
            generated_at=args.generated_at or _utc_now(),
            source_inputs={
                "cards": [str(path) for path in args.cards],
                "reviewCandidates": [str(path) for path in args.review_candidates],
            },
        )
        atomic_write_private_json(args.output, document)
    except (LearningIndexError, OSError, json.JSONDecodeError) as error:
        print(f"learning index failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"output": str(args.output), **document["summary"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
