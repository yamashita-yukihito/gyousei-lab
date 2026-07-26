"""Build strict and high-recall private similarity candidates for human review."""

from __future__ import annotations

import argparse
import math
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .common import atomic_write_json, data_root, load_json, utc_now


SCHEMA_VERSION = "similarity-candidates@1"
INPUT_SCHEMA_VERSION = "review-candidate-inventory@1"
DEFAULT_THRESHOLD = 0.55
DEFAULT_REVIEW_THRESHOLD = 0.18
DEFAULT_MAX_REVIEW_NEIGHBORS = 4
MIN_NEAR_LENGTH = 30
REVIEW_BIGRAM_WEIGHT = 0.55
REVIEW_TRIGRAM_WEIGHT = 0.25
REVIEW_CONCEPT_WEIGHT = 0.20

# These are concepts rather than stop-word-like legal prose.  They supplement
# character n-grams when the same issue is phrased differently in two exams.
# Matching is literal and deterministic; no unpublished answer is inferred.
LEGAL_CONCEPTS = frozenset(
    {
        "法律による行政",
        "法律の留保",
        "信義則",
        "比例原則",
        "平等原則",
        "行政行為",
        "公定力",
        "不可争力",
        "自力執行力",
        "行政裁量",
        "裁量権",
        "重大かつ明白",
        "無効確認",
        "行政上の強制執行",
        "行政代執行",
        "代執行",
        "執行罰",
        "直接強制",
        "即時強制",
        "行政刑罰",
        "秩序罰",
        "過料",
        "行政調査",
        "申請に対する処分",
        "審査基準",
        "標準処理期間",
        "理由提示",
        "理由の提示",
        "不利益処分",
        "処分基準",
        "聴聞",
        "弁明の機会",
        "文書閲覧",
        "代理人",
        "参加人",
        "行政指導",
        "意見公募手続",
        "意見公募",
        "命令等制定機関",
        "審査請求",
        "再調査の請求",
        "再審査請求",
        "審理員意見書",
        "審理員",
        "行政不服審査会",
        "執行停止",
        "認容裁決",
        "事情裁決",
        "却下裁決",
        "棄却裁決",
        "口頭意見陳述",
        "反論書",
        "弁明書",
        "不作為についての審査請求",
        "取消訴訟",
        "処分取消訴訟",
        "裁決取消訴訟",
        "無効等確認の訴え",
        "無効等確認訴訟",
        "不作為の違法確認",
        "義務付けの訴え",
        "義務付け訴訟",
        "差止めの訴え",
        "当事者訴訟",
        "民衆訴訟",
        "機関訴訟",
        "客観訴訟",
        "原告適格",
        "被告適格",
        "訴えの利益",
        "出訴期間",
        "専属管轄",
        "仮の義務付け",
        "仮の差止め",
        "事情判決",
        "釈明処分",
        "関連請求",
        "第三者効",
        "拘束力",
        "処分性",
        "公権力の行使",
        "国家賠償",
        "求償権",
        "求償",
        "営造物責任",
        "公の営造物",
        "設置又は管理の瑕疵",
        "設置または管理の瑕疵",
        "相互保証",
        "費用負担者",
        "自治事務",
        "法定受託事務",
        "条例制定権",
        "直接請求",
        "事務監査請求",
        "議会の解散請求",
        "解職請求",
        "住民監査請求",
        "住民訴訟",
        "財務会計行為",
        "公の施設",
        "指定管理者",
        "国地方係争処理委員会",
        "自治紛争処理委員",
        "普通地方公共団体",
        "特別地方公共団体",
        "法定外公共物",
        "開示請求",
        "不開示情報",
        "情報公開",
        "行政文書",
        "公文書管理",
        "個人情報保護",
    }
)
LEGAL_CONCEPT_ALIASES = {
    "行政代執行": "代執行",
    "理由の提示": "理由提示",
    "意見公募": "意見公募手続",
    "処分取消訴訟": "取消訴訟",
    "裁決取消訴訟": "取消訴訟",
    "無効等確認の訴え": "無効確認",
    "無効等確認訴訟": "無効確認",
    "義務付け訴訟": "義務付けの訴え",
    "設置または管理の瑕疵": "設置又は管理の瑕疵",
}


class SimilarityError(ValueError):
    """The private candidate inventory does not satisfy the expected contract."""


def normalize_statement(value: str) -> str:
    """Normalize only presentation differences; retain letters, numbers and words."""

    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if character.isalnum())


def character_ngrams(value: str, size: int = 3) -> frozenset[str]:
    if not value:
        return frozenset()
    if len(value) <= size:
        return frozenset({value})
    return frozenset(value[index : index + size] for index in range(len(value) - size + 1))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _extract_legal_concepts(value: str) -> frozenset[str]:
    """Return longest matching legal concepts, avoiding nested double counting."""

    matched = {concept for concept in LEGAL_CONCEPTS if concept in value}
    longest = {
        concept
        for concept in matched
        if not any(concept != other and concept in other for other in matched)
    }
    return frozenset(LEGAL_CONCEPT_ALIASES.get(concept, concept) for concept in longest)


def _inverse_document_frequencies(
    feature_sets: Iterable[frozenset[str]],
) -> dict[str, float]:
    feature_sets = list(feature_sets)
    document_count = len(feature_sets)
    frequencies = Counter(feature for features in feature_sets for feature in features)
    return {
        feature: math.log((document_count + 1) / (frequency + 1)) + 1.0
        for feature, frequency in sorted(frequencies.items())
    }


def _weighted_jaccard(
    left: frozenset[str], right: frozenset[str], weights: Mapping[str, float]
) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    denominator = sum(weights.get(feature, 1.0) for feature in sorted(union))
    if denominator == 0:
        return 0.0
    return (
        sum(weights.get(feature, 1.0) for feature in sorted(left & right)) / denominator
    )


def _choice_candidates(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != INPUT_SCHEMA_VERSION:
        raise SimilarityError("unsupported review candidate inventory schema")
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise SimilarityError("candidate inventory has no candidates list")

    choices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("candidateKind") != "choice_proposition":
            continue
        required = (
            "candidateId",
            "rawQuestionId",
            "examYear",
            "questionNumber",
            "choiceLabel",
            "statementText",
            "inferredTruth",
            "sourceCitation",
        )
        if any(key not in candidate for key in required):
            raise SimilarityError("choice proposition is missing a required field")
        candidate_id = candidate["candidateId"]
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen:
            raise SimilarityError("candidateId is empty or duplicated")
        if not isinstance(candidate["statementText"], str) or not candidate["statementText"].strip():
            raise SimilarityError(f"empty statementText: {candidate_id}")
        citation = candidate["sourceCitation"]
        labels = citation.get("labels") if isinstance(citation, dict) else None
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise SimilarityError(f"invalid source labels: {candidate_id}")
        normalized = normalize_statement(candidate["statementText"])
        if not normalized:
            raise SimilarityError(f"statement normalizes to empty: {candidate_id}")
        copied = dict(candidate)
        copied["_normalized"] = normalized
        copied["_bigrams"] = character_ngrams(normalized, size=2)
        copied["_grams"] = character_ngrams(normalized)
        copied["_concepts"] = _extract_legal_concepts(
            unicodedata.normalize("NFKC", candidate["statementText"])
        )
        copied["_labels"] = frozenset(label for label in labels if label != "行政法")
        choices.append(copied)
        seen.add(candidate_id)
    return sorted(choices, key=lambda item: item["candidateId"])


def _member(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidateId": candidate["candidateId"],
        "rawQuestionId": candidate["rawQuestionId"],
        "examYear": candidate["examYear"],
        "questionNumber": candidate["questionNumber"],
        "choiceLabel": candidate["choiceLabel"],
        "inferredTruth": candidate["inferredTruth"],
        "statementText": candidate["statementText"],
    }


def _pairs(choices: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(choices):
        for right in choices[left_index + 1 :]:
            if left["rawQuestionId"] == right["rawQuestionId"]:
                continue
            common_labels = sorted(left["_labels"] & right["_labels"])
            if not common_labels:
                continue
            if left["_normalized"] == right["_normalized"]:
                score = 1.0
                method = "normalized_exact"
            else:
                if min(len(left["_normalized"]), len(right["_normalized"])) < MIN_NEAR_LENGTH:
                    continue
                score = jaccard(left["_grams"], right["_grams"])
                if score < threshold:
                    continue
                method = "character_trigram_jaccard"
            left_id = left["candidateId"]
            right_id = right["candidateId"]
            pairs.append(
                {
                    "pairId": f"{left_id}--{right_id}",
                    "left": _member(left),
                    "right": _member(right),
                    "commonLabels": common_labels,
                    "score": round(score, 6),
                    "method": method,
                    "reviewed": False,
                    "publishable": False,
                }
            )
    return sorted(
        pairs,
        key=lambda pair: (-pair["score"], pair["left"]["candidateId"], pair["right"]["candidateId"]),
    )


def _review_reason_summary(
    common_labels: list[str],
    common_concepts: list[str],
    bigram_score: float,
    trigram_score: float,
) -> tuple[list[str], str]:
    reason_codes = ["shared_administrative_sub_label", "character_ngram_overlap"]
    details = [f"同じ分野（{'、'.join(common_labels)}）"]
    if common_concepts:
        reason_codes.append("shared_legal_concepts")
        details.append(f"共通する法令概念（{'、'.join(common_concepts)}）")
    details.append(f"文字の重なり（2-gram {bigram_score:.3f} / 3-gram {trigram_score:.3f}）")
    return reason_codes, "、".join(details)


def _review_pairs(
    choices: list[dict[str, Any]],
    strict_pairs: list[dict[str, Any]],
    *,
    review_threshold: float,
    max_neighbors: int,
) -> list[dict[str, Any]]:
    """Build a bounded, recall-oriented queue without changing strict results.

    Every non-strict pair must be in the top-k results of at least one endpoint.
    The union rule may give a popular candidate more than k incoming pairs, but
    bounds the exploratory queue to at most ``len(choices) * k`` pairs.
    """

    bigram_idf = _inverse_document_frequencies(choice["_bigrams"] for choice in choices)
    concept_idf = _inverse_document_frequencies(choice["_concepts"] for choice in choices)
    strict_by_id = {pair["pairId"]: pair for pair in strict_pairs}
    eligible: dict[str, dict[str, Any]] = {}

    for left_index, left in enumerate(choices):
        for right in choices[left_index + 1 :]:
            if left["rawQuestionId"] == right["rawQuestionId"]:
                continue
            common_labels = sorted(left["_labels"] & right["_labels"])
            if not common_labels:
                continue

            left_id = left["candidateId"]
            right_id = right["candidateId"]
            pair_id = f"{left_id}--{right_id}"
            strict_pair = strict_by_id.get(pair_id)
            bigram_score = _weighted_jaccard(left["_bigrams"], right["_bigrams"], bigram_idf)
            trigram_score = jaccard(left["_grams"], right["_grams"])
            concept_score = _weighted_jaccard(
                left["_concepts"], right["_concepts"], concept_idf
            )
            common_concept_set = left["_concepts"] & right["_concepts"]
            maximum_concept_weight = max(concept_idf.values(), default=1.0)
            concept_rarity = (
                max(concept_idf[concept] for concept in common_concept_set)
                / maximum_concept_weight
                if common_concept_set
                else 0.0
            )
            adjusted_concept_score = concept_score * concept_rarity
            review_score = (
                REVIEW_BIGRAM_WEIGHT * bigram_score
                + REVIEW_TRIGRAM_WEIGHT * trigram_score
                + REVIEW_CONCEPT_WEIGHT * adjusted_concept_score
            )
            if strict_pair is None and review_score < review_threshold:
                continue

            common_concepts = sorted(
                left["_concepts"] & right["_concepts"], key=lambda value: (-len(value), value)
            )
            reason_codes, reason_summary = _review_reason_summary(
                common_labels, common_concepts, bigram_score, trigram_score
            )
            if strict_pair is not None:
                reason_codes.insert(0, "strict_similarity_retained")
                reason_summary = f"厳密候補を保持（{strict_pair['method']}）、{reason_summary}"

            eligible[pair_id] = {
                "pairId": pair_id,
                "left": _member(left),
                "right": _member(right),
                "commonLabels": common_labels,
                "sharedLegalConcepts": common_concepts,
                "score": round(
                    max(review_score, strict_pair["score"] if strict_pair is not None else 0.0),
                    6,
                ),
                "reviewScore": round(review_score, 6),
                "scoreBreakdown": {
                    "characterBigramIdfJaccard": round(bigram_score, 6),
                    "characterTrigramJaccard": round(trigram_score, 6),
                    "legalConceptIdfJaccard": round(concept_score, 6),
                    "sharedLegalConceptRarity": round(concept_rarity, 6),
                    "legalConceptRarityAdjustedJaccard": round(adjusted_concept_score, 6),
                },
                "tier": "strict" if strict_pair is not None else "exploratory",
                "method": strict_pair["method"] if strict_pair is not None else "ranked_high_recall",
                "reasonCodes": reason_codes,
                "reasonSummary": reason_summary,
                "reviewed": False,
                "publishable": False,
                "_rankScore": review_score,
            }

    neighbor_pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in eligible.values():
        if pair["tier"] == "strict":
            continue
        neighbor_pairs[pair["left"]["candidateId"]].append(pair)
        neighbor_pairs[pair["right"]["candidateId"]].append(pair)

    selected_by: dict[str, dict[str, int]] = defaultdict(dict)
    for candidate_id in sorted(neighbor_pairs):
        ranked = sorted(
            neighbor_pairs[candidate_id],
            key=lambda pair: (-pair["_rankScore"], pair["pairId"]),
        )
        for rank, pair in enumerate(ranked[:max_neighbors], start=1):
            selected_by[pair["pairId"]][candidate_id] = rank

    review_pairs: list[dict[str, Any]] = []
    for pair_id in sorted(eligible):
        pair = eligible[pair_id]
        ranks = selected_by.get(pair_id, {})
        if pair["tier"] != "strict" and not ranks:
            continue
        pair["selectedByCandidateIds"] = sorted(ranks)
        pair["neighborRanks"] = {candidate_id: ranks[candidate_id] for candidate_id in sorted(ranks)}
        pair.pop("_rankScore")
        review_pairs.append(pair)

    return sorted(
        review_pairs,
        key=lambda pair: (-pair["score"], pair["left"]["candidateId"], pair["right"]["candidateId"]),
    )


def _groups(pairs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = list(pairs)
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for pair in pairs:
        union(pair["left"]["candidateId"], pair["right"]["candidateId"])

    members: dict[str, set[str]] = defaultdict(set)
    component_pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate_id in sorted(parent):
        members[find(candidate_id)].add(candidate_id)
    for pair in pairs:
        component_pairs[find(pair["left"]["candidateId"])].append(pair)

    ordered = sorted(members.values(), key=lambda values: tuple(sorted(values)))
    groups: list[dict[str, Any]] = []
    for index, candidate_ids in enumerate(ordered, start=1):
        root = find(min(candidate_ids))
        related_pairs = component_pairs[root]
        groups.append(
            {
                "groupId": f"similarity-group-{index:04d}",
                "memberCandidateIds": sorted(candidate_ids),
                "pairIds": sorted(pair["pairId"] for pair in related_pairs),
                "pairCount": len(related_pairs),
                "maxScore": max(pair["score"] for pair in related_pairs),
                "reviewed": False,
                "publishable": False,
            }
        )
    return groups


def _unique_candidate_count(pairs: Iterable[Mapping[str, Any]]) -> int:
    candidate_ids: set[str] = set()
    for pair in pairs:
        candidate_ids.add(pair["left"]["candidateId"])
        candidate_ids.add(pair["right"]["candidateId"])
    return len(candidate_ids)


def build_similarity_document(
    inventory: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    max_review_neighbors: int = DEFAULT_MAX_REVIEW_NEIGHBORS,
) -> dict[str, Any]:
    if not 0.0 <= threshold <= 1.0:
        raise SimilarityError("threshold must be between 0 and 1")
    if not 0.0 <= review_threshold <= 1.0:
        raise SimilarityError("review threshold must be between 0 and 1")
    if max_review_neighbors < 0:
        raise SimilarityError("max review neighbors must not be negative")
    choices = _choice_candidates(inventory)
    pairs = _pairs(choices, threshold)
    groups = _groups(pairs)
    review_pairs = _review_pairs(
        choices,
        pairs,
        review_threshold=review_threshold,
        max_neighbors=max_review_neighbors,
    )
    review_groups = _groups(review_pairs)
    method_counts: dict[str, int] = defaultdict(int)
    for pair in pairs:
        method_counts[pair["method"]] += 1
    review_tier_counts: dict[str, int] = defaultdict(int)
    for pair in review_pairs:
        review_tier_counts[pair["tier"]] += 1
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_not_for_web",
        "policy": {
            "threshold": threshold,
            "nearMinimumNormalizedLength": MIN_NEAR_LENGTH,
            "sameQuestionExcluded": True,
            "sharedAdministrativeSubLabelRequired": True,
            "autoPublish": False,
            "highRecallReview": {
                "threshold": review_threshold,
                "maxNeighborsPerCandidate": max_review_neighbors,
                "selectionRule": "union_of_per_candidate_top_k",
                "strictPairsAlwaysRetained": True,
                "scoreWeights": {
                    "characterBigramIdfJaccard": REVIEW_BIGRAM_WEIGHT,
                    "characterTrigramJaccard": REVIEW_TRIGRAM_WEIGHT,
                    "legalConceptRarityAdjustedJaccard": REVIEW_CONCEPT_WEIGHT,
                },
                "humanReviewRequired": True,
                "autoPublish": False,
            },
        },
        "summary": {
            "choiceCandidateCount": len(choices),
            "pairCount": len(pairs),
            "groupCount": len(groups),
            "strictUniqueCandidateCount": _unique_candidate_count(pairs),
            "methodCounts": dict(sorted(method_counts.items())),
            "reviewPairCount": len(review_pairs),
            "reviewGroupCount": len(review_groups),
            "reviewUniqueCandidateCount": _unique_candidate_count(review_pairs),
            "reviewTierCounts": dict(sorted(review_tier_counts.items())),
        },
        "pairs": pairs,
        "groups": groups,
        "reviewPairs": review_pairs,
        "reviewGroups": review_groups,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root() / "curation" / "review_candidates.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "curation" / "similarity_candidates.json",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--review-threshold", type=float, default=DEFAULT_REVIEW_THRESHOLD)
    parser.add_argument(
        "--max-review-neighbors", type=int, default=DEFAULT_MAX_REVIEW_NEIGHBORS
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    document = build_similarity_document(
        load_json(args.input),
        threshold=args.threshold,
        review_threshold=args.review_threshold,
        max_review_neighbors=args.max_review_neighbors,
    )
    atomic_write_json(args.output, document)
    summary = document["summary"]
    print(
        f"choices={summary['choiceCandidateCount']} strict_pairs={summary['pairCount']} "
        f"review_pairs={summary['reviewPairCount']} "
        f"review_coverage={summary['reviewUniqueCandidateCount']} "
        f"groups={summary['reviewGroupCount']} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
