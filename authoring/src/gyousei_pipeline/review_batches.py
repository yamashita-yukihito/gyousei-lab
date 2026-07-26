"""Export and import fail-closed AI review batches for choice propositions.

The batch files contain only the fields an external reviewer needs.  In
particular, provider-authored explanations are never copied.  An imported AI
review remains a candidate: it cannot mark an item as human-verified, reviewed,
or publishable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .common import atomic_write_json, data_root, load_json, utc_now


INPUT_SCHEMA_VERSION = "review-candidate-inventory@1"
RECONCILIATION_SCHEMA_VERSION = "answer-reconciliation@1"
MANIFEST_SCHEMA_VERSION = "ai-legal-review-manifest@3"
BATCH_SCHEMA_VERSION = "ai-legal-review-batch@3"
RESPONSE_SCHEMA_VERSION = "ai-legal-review-response@3"
IMPORT_SCHEMA_VERSION = "ai-legal-review-import@3"
DEFAULT_BATCH_SIZE = 10

TARGET_LAW_STATUSES = ("confirmed", "changed", "uncertain")
LEGAL_REVIEW_STATUSES = ("unreviewed", "ai_candidate", "human_verified")
CITATION_TYPES = ("statute", "case_law", "official_material", "other_official")
HISTORICAL_ANSWER_VERIFICATIONS = (
    "official_exact",
    "official_normalized",
    "provider_only",
    "official_mismatch",
    "official_unsupported",
)
RECONCILIATION_STATUS_TO_VERIFICATION = {
    "exact": "official_exact",
    "match-after-normalization": "official_normalized",
    "unavailable": "provider_only",
    "mismatch": "official_mismatch",
    "unsupported": "official_unsupported",
}

# Pedagogical order used by the existing administrative-law taxonomy.  Any
# future label not in this tuple follows these in Unicode order.
SUBLABEL_ORDER = (
    "行政総論",
    "行政手続法",
    "行政不服審査法",
    "行政事件訴訟法",
    "国家賠償法",
    "地方自治法",
    "情報公開法",
    "公文書管理法",
    "その他",
)


class ReviewBatchError(ValueError):
    """The inventory, batch selection, or AI response is unsafe to accept."""


def _require_object(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewBatchError(f"{context} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, Any], required: Iterable[str], context: str
) -> None:
    expected = set(required)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ReviewBatchError(f"{context} missing fields: {', '.join(missing)}")
    if unknown:
        raise ReviewBatchError(f"{context} has unknown fields: {', '.join(unknown)}")


def _nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewBatchError(f"{context} must be a non-empty string")
    return value


def _iso_date(value: Any, context: str) -> str:
    text = _nonempty_string(value, context)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ReviewBatchError(f"{context} must be a valid YYYY-MM-DD date") from error
    if parsed.isoformat() != text:
        raise ReviewBatchError(f"{context} must use exact YYYY-MM-DD format")
    return text


def _integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewBatchError(f"{context} must be an integer")
    return value


def _string_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list):
        raise ReviewBatchError(f"{context} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_nonempty_string(item, f"{context}[{index}]"))
    return result


def historical_answer_verifications(
    reconciliation: Mapping[str, Any],
) -> dict[str, str]:
    """Build a fail-closed raw-question verification map."""

    if reconciliation.get("schemaVersion") != RECONCILIATION_SCHEMA_VERSION:
        raise ReviewBatchError("unsupported answer reconciliation schema")
    results = reconciliation.get("results")
    if not isinstance(results, list):
        raise ReviewBatchError("answer reconciliation has no results array")
    verifications: dict[str, str] = {}
    for index, value in enumerate(results):
        result = _require_object(value, f"reconciliation.results[{index}]")
        raw_question_id = _nonempty_string(
            result.get("rawQuestionId"),
            f"reconciliation.results[{index}].rawQuestionId",
        )
        if raw_question_id in verifications:
            raise ReviewBatchError(
                f"duplicate reconciliation rawQuestionId: {raw_question_id}"
            )
        status = result.get("status")
        try:
            verification = RECONCILIATION_STATUS_TO_VERIFICATION[status]
        except (KeyError, TypeError) as error:
            raise ReviewBatchError(
                f"{raw_question_id}: unsupported reconciliation status: {status}"
            ) from error
        verifications[raw_question_id] = verification
    if not verifications:
        raise ReviewBatchError("answer reconciliation is empty")
    return verifications


def _validate_historical_verifications(
    value: Mapping[str, str], candidates: Iterable[Mapping[str, Any]]
) -> None:
    for candidate in candidates:
        raw_question_id = str(candidate["rawQuestionId"])
        verification = value.get(raw_question_id)
        if verification not in HISTORICAL_ANSWER_VERIFICATIONS:
            raise ReviewBatchError(
                f"{candidate['candidateId']}: historical answer verification is missing or invalid"
            )


def _sub_label(candidate: Mapping[str, Any]) -> str:
    citation = _require_object(candidate.get("sourceCitation"), "sourceCitation")
    labels = citation.get("labels")
    if not isinstance(labels, list) or not all(
        isinstance(label, str) and label.strip() for label in labels
    ):
        raise ReviewBatchError(
            f"{candidate.get('candidateId', '<unknown>')}: source labels are invalid"
        )
    sublabels = sorted({label.strip() for label in labels if label.strip() != "行政法"})
    if not sublabels:
        raise ReviewBatchError(
            f"{candidate.get('candidateId', '<unknown>')}: administrative sublabel is missing"
        )
    return " / ".join(sublabels)


def _natural(value: Any) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _sub_label_key(value: str) -> tuple[int, Any]:
    try:
        return (0, SUBLABEL_ORDER.index(value))
    except ValueError:
        return (1, value)


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    citation = _require_object(candidate.get("sourceCitation"), "sourceCitation")
    return (
        _sub_label_key(_sub_label(candidate)),
        _natural(candidate.get("examYear")),
        _natural(citation.get("externalQuestionId")),
        _natural(candidate.get("choiceLabel")),
        str(candidate.get("candidateId") or ""),
    )


def choice_candidates(inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and deterministically order only choice-proposition records."""

    if inventory.get("schemaVersion") != INPUT_SCHEMA_VERSION:
        raise ReviewBatchError("unsupported review candidate inventory schema")
    candidates = inventory.get("candidates")
    if not isinstance(candidates, list):
        raise ReviewBatchError("candidate inventory has no candidates array")

    choices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, value in enumerate(candidates):
        if not isinstance(value, dict):
            raise ReviewBatchError(f"candidates[{position}] must be an object")
        if value.get("candidateKind") != "choice_proposition":
            continue
        required = (
            "candidateId",
            "rawQuestionId",
            "sourceSnapshotId",
            "examYear",
            "questionNumber",
            "task",
            "taskMetadata",
            "choiceLabel",
            "statementText",
            "inferredTruth",
            "sourceCitation",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ReviewBatchError(
                f"candidates[{position}] missing fields: {', '.join(missing)}"
            )
        candidate_id = _nonempty_string(value["candidateId"], "candidateId")
        if candidate_id in seen:
            raise ReviewBatchError(f"duplicate candidateId: {candidate_id}")
        seen.add(candidate_id)
        _nonempty_string(value["rawQuestionId"], f"{candidate_id}.rawQuestionId")
        _nonempty_string(value["sourceSnapshotId"], f"{candidate_id}.sourceSnapshotId")
        _integer(value["examYear"], f"{candidate_id}.examYear")
        _integer(value["questionNumber"], f"{candidate_id}.questionNumber")
        if value["task"] not in {"select_true", "select_false"}:
            raise ReviewBatchError(f"{candidate_id}: unsupported question direction")
        task_metadata = _require_object(value["taskMetadata"], f"{candidate_id}.taskMetadata")
        _nonempty_string(
            task_metadata.get("prompt"), f"{candidate_id}.taskMetadata.prompt"
        )
        _nonempty_string(value["choiceLabel"], f"{candidate_id}.choiceLabel")
        _nonempty_string(value["statementText"], f"{candidate_id}.statementText")
        if not isinstance(value["inferredTruth"], bool):
            raise ReviewBatchError(f"{candidate_id}.inferredTruth must be boolean")
        citation = _require_object(value["sourceCitation"], f"{candidate_id}.sourceCitation")
        for field in (
            "sourceId",
            "externalQuestionId",
            "sourceUrl",
            "title",
            "eraYear",
            "sourceBodySha256",
        ):
            _nonempty_string(citation.get(field), f"{candidate_id}.sourceCitation.{field}")
        if not isinstance(citation.get("isAmended"), bool):
            raise ReviewBatchError(
                f"{candidate_id}.sourceCitation.isAmended must be boolean"
            )
        _sub_label(value)
        choices.append(value)

    if not choices:
        raise ReviewBatchError("inventory contains no choice propositions")
    return sorted(choices, key=_candidate_sort_key)


def _project_candidate(
    candidate: Mapping[str, Any], historical_verifications: Mapping[str, str]
) -> dict[str, Any]:
    """Whitelist fields for external review; arbitrary narrative fields stay out."""

    citation = _require_object(candidate["sourceCitation"], "sourceCitation")
    task = str(candidate["task"])
    prompt = _require_object(candidate["taskMetadata"], "taskMetadata")["prompt"]
    return {
        "candidateId": candidate["candidateId"],
        "subLabel": _sub_label(candidate),
        "source": {
            "sourceId": citation["sourceId"],
            "externalQuestionId": citation["externalQuestionId"],
            "sourceUrl": citation["sourceUrl"],
            "title": citation["title"],
            "eraYear": citation["eraYear"],
            "examYear": candidate["examYear"],
            "questionNumber": candidate["questionNumber"],
            "choiceLabel": candidate["choiceLabel"],
            "sourceSnapshotId": candidate["sourceSnapshotId"],
            "sourceBodySha256": citation["sourceBodySha256"],
        },
        "questionDirection": {
            "task": task,
            "asksFor": (
                "correct_statement" if task == "select_true" else "incorrect_statement"
            ),
            "prompt": prompt,
        },
        "statementText": candidate["statementText"],
        "inferredTruth": candidate["inferredTruth"],
        "historicalAnswerVerification": historical_verifications[
            str(candidate["rawQuestionId"])
        ],
        "isAmended": citation["isAmended"],
    }


def response_json_schema() -> dict[str, Any]:
    """Return the exact response contract embedded in every export."""

    citation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["citationType", "title", "url", "locator", "relevance"],
        "properties": {
            "citationType": {"type": "string", "enum": list(CITATION_TYPES)},
            "title": {"type": "string", "minLength": 1},
            "url": {"type": "string", "pattern": "^https://"},
            "locator": {"type": "string", "minLength": 1},
            "relevance": {"type": "string", "minLength": 1},
        },
    }
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "candidateId",
            "targetLawStatus",
            "targetTruth",
            "legalReviewStatus",
            "relationNotes",
            "citationCandidates",
            "risks",
            "reviewed",
            "publishable",
        ],
        "properties": {
            "candidateId": {"type": "string", "minLength": 1},
            "targetLawStatus": {
                "type": "string",
                "enum": list(TARGET_LAW_STATUSES),
            },
            "targetTruth": {"type": ["boolean", "null"]},
            "legalReviewStatus": {
                "type": "string",
                "enum": list(LEGAL_REVIEW_STATUSES),
            },
            "relationNotes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "citationCandidates": {"type": "array", "items": citation},
            "risks": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "reviewed": {"const": False},
            "publishable": {"const": False},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schemaVersion", "batchId", "legalAsOf", "items"],
        "properties": {
            "schemaVersion": {"const": RESPONSE_SCHEMA_VERSION},
            "batchId": {"type": "string", "minLength": 1},
            "legalAsOf": {
                "type": "string",
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
            },
            "items": {"type": "array", "items": item},
        },
    }


def _canonical_digest(
    projected: list[dict[str, Any]], *, target_legal_as_of: str
) -> str:
    encoded = json.dumps(
        {"targetLegalAsOf": target_legal_as_of, "items": projected},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _batch_id(inventory_digest: str, batch_size: int, batch_index: int) -> str:
    # Batch size is identity-bearing: without it, size 10 and size 20 exports
    # would overwrite one another despite containing different candidate sets.
    return f"choice-law-{inventory_digest[:12]}-s{batch_size:03d}-{batch_index:04d}"


def build_manifest(
    inventory: Mapping[str, Any],
    *,
    historical_verifications: Mapping[str, str],
    target_legal_as_of: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    source_input: str = "",
    source_reconciliation: str = "",
) -> dict[str, Any]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ReviewBatchError("batch size must be a positive integer")
    target_legal_as_of = _iso_date(
        target_legal_as_of, "target_legal_as_of"
    )
    ordered = choice_candidates(inventory)
    _validate_historical_verifications(historical_verifications, ordered)
    projected = [
        _project_candidate(candidate, historical_verifications) for candidate in ordered
    ]
    digest = _canonical_digest(
        projected, target_legal_as_of=target_legal_as_of
    )
    batches: list[dict[str, Any]] = []
    for offset in range(0, len(projected), batch_size):
        batch_index = offset // batch_size + 1
        items = projected[offset : offset + batch_size]
        batches.append(
            {
                "batchIndex": batch_index,
                "batchId": _batch_id(digest, batch_size, batch_index),
                "itemCount": len(items),
                "subLabels": sorted({item["subLabel"] for item in items}),
                "candidateIds": [item["candidateId"] for item in items],
            }
        )
    sublabel_counts = Counter(item["subLabel"] for item in projected)
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_not_for_web",
        "sourceInput": source_input,
        "sourceReconciliation": source_reconciliation,
        "targetLegalAsOf": target_legal_as_of,
        "inventoryDigest": digest,
        "batchSize": batch_size,
        "choiceCandidateCount": len(projected),
        "batchCount": len(batches),
        "sortOrder": [
            "administrativeSubLabel:fixed_taxonomy_then_unicode",
            "examYear:ascending",
            "externalQuestionId:natural_ascending",
            "choiceLabel:natural_ascending",
            "candidateId:ascending",
        ],
        "subLabelOrder": list(SUBLABEL_ORDER),
        "choiceCandidatesBySubLabel": dict(sorted(sublabel_counts.items())),
        "historicalAnswerVerificationCounts": dict(
            sorted(Counter(item["historicalAnswerVerification"] for item in projected).items())
        ),
        "aiImportPolicy": {
            "candidateIdSetMustMatch": True,
            "unknownFieldsRejected": True,
            "legalAsOfRequired": True,
            "legalAsOfMustMatchBatchTarget": True,
            "targetTruthMustMatchStatus": True,
            "primaryOfficialCitationsOnly": True,
            "humanVerifiedAllowedFromAi": False,
            "reviewedTrueAllowedFromAi": False,
            "publishableTrueAllowedFromAi": False,
        },
        "responseSchema": response_json_schema(),
        "batches": batches,
    }


def build_batch(
    inventory: Mapping[str, Any],
    *,
    historical_verifications: Mapping[str, str],
    target_legal_as_of: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_index: int,
    source_input: str = "",
    source_reconciliation: str = "",
) -> dict[str, Any]:
    manifest = build_manifest(
        inventory,
        historical_verifications=historical_verifications,
        target_legal_as_of=target_legal_as_of,
        batch_size=batch_size,
        source_input=source_input,
        source_reconciliation=source_reconciliation,
    )
    if isinstance(batch_index, bool) or not isinstance(batch_index, int):
        raise ReviewBatchError("batch index must be an integer")
    if batch_index < 1 or batch_index > manifest["batchCount"]:
        raise ReviewBatchError(
            f"batch index must be between 1 and {manifest['batchCount']}"
        )
    ordered = [
        _project_candidate(candidate, historical_verifications)
        for candidate in choice_candidates(inventory)
    ]
    offset = (batch_index - 1) * batch_size
    items = ordered[offset : offset + batch_size]
    descriptor = manifest["batches"][batch_index - 1]
    return {
        "schemaVersion": BATCH_SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_not_for_web",
        "batchId": descriptor["batchId"],
        "batchIndex": batch_index,
        "batchCount": manifest["batchCount"],
        "batchSize": batch_size,
        "inventoryDigest": manifest["inventoryDigest"],
        "targetLegalAsOf": manifest["targetLegalAsOf"],
        "reviewInstructions": [
            "inferredTruth is the historical answer inference, not proof of the law at targetLegalAsOf.",
            "Use only HTTPS primary official sources for citationCandidates; confirmed/changed requires at least one.",
            "confirmed means targetTruth equals inferredTruth; changed means it differs; uncertain requires null.",
            "Treat amended questions as requiring extra comparison with the original rule.",
            "Return strict JSON matching responseSchema; do not add prose outside JSON.",
            "AI review must leave reviewed and publishable false and cannot claim human_verified.",
            "historicalAnswerVerification=provider_only means the historical answer itself was not checked against an official answer source.",
        ],
        "responseSchema": manifest["responseSchema"],
        "items": items,
    }


def _strict_json_bytes(content: bytes, context: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReviewBatchError(f"{context} has duplicate field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=object_pairs)
    except UnicodeDecodeError as error:
        raise ReviewBatchError(f"{context} is not UTF-8") from error
    except json.JSONDecodeError as error:
        raise ReviewBatchError(f"{context} is not valid JSON: {error}") from error


def _validate_citation(value: Any, context: str) -> dict[str, Any]:
    citation = _require_object(value, context)
    fields = ("citationType", "title", "url", "locator", "relevance")
    _exact_fields(citation, fields, context)
    if citation["citationType"] not in CITATION_TYPES:
        raise ReviewBatchError(f"{context}.citationType has an invalid value")
    result = {"citationType": citation["citationType"]}
    for field in ("title", "url", "locator", "relevance"):
        result[field] = _nonempty_string(citation[field], f"{context}.{field}")
    if not result["url"].startswith("https://"):
        raise ReviewBatchError(f"{context}.url must use https")
    return result


def _validate_response_item(value: Any, context: str) -> dict[str, Any]:
    item = _require_object(value, context)
    fields = (
        "candidateId",
        "targetLawStatus",
        "targetTruth",
        "legalReviewStatus",
        "relationNotes",
        "citationCandidates",
        "risks",
        "reviewed",
        "publishable",
    )
    _exact_fields(item, fields, context)
    candidate_id = _nonempty_string(item["candidateId"], f"{context}.candidateId")
    if item["targetLawStatus"] not in TARGET_LAW_STATUSES:
        raise ReviewBatchError(f"{context}.targetLawStatus has an invalid value")
    target_truth = item["targetTruth"]
    if item["targetLawStatus"] == "uncertain":
        if target_truth is not None:
            raise ReviewBatchError(
                f"{context}.targetTruth must be null when targetLawStatus is uncertain"
            )
    elif not isinstance(target_truth, bool):
        raise ReviewBatchError(
            f"{context}.targetTruth must be boolean when target law is confirmed or changed"
        )
    if item["legalReviewStatus"] not in LEGAL_REVIEW_STATUSES:
        raise ReviewBatchError(f"{context}.legalReviewStatus has an invalid value")
    if item["legalReviewStatus"] == "human_verified":
        raise ReviewBatchError(f"{context}: AI cannot set human_verified")
    if item["reviewed"] is not False:
        raise ReviewBatchError(f"{context}: AI review must leave reviewed false")
    if item["publishable"] is not False:
        raise ReviewBatchError(f"{context}: AI review must leave publishable false")
    relations = _string_list(item["relationNotes"], f"{context}.relationNotes")
    risks = _string_list(item["risks"], f"{context}.risks")
    citations_value = item["citationCandidates"]
    if not isinstance(citations_value, list):
        raise ReviewBatchError(f"{context}.citationCandidates must be an array")
    citations = [
        _validate_citation(citation, f"{context}.citationCandidates[{index}]")
        for index, citation in enumerate(citations_value)
    ]
    return {
        "candidateId": candidate_id,
        "targetLawStatus": item["targetLawStatus"],
        "targetTruth": target_truth,
        "legalReviewStatus": item["legalReviewStatus"],
        "relationNotes": relations,
        "citationCandidates": citations,
        "risks": risks,
        "reviewed": False,
        "publishable": False,
    }


def _primary_official_host(hostname: str) -> bool:
    host = hostname.rstrip(".").casefold()
    return (
        host == "e-gov.go.jp"
        or host.endswith(".e-gov.go.jp")
        or host == "courts.go.jp"
        or host.endswith(".courts.go.jp")
        or host.endswith(".go.jp")
        or host.endswith(".lg.jp")
    )


def validate_primary_official_citations(response: Mapping[str, Any]) -> None:
    """Reject unsupported or third-party legal authorities at every import path."""

    items = response.get("items")
    if not isinstance(items, list):
        raise ReviewBatchError("response.items must be an array")
    for item in items:
        citations = item["citationCandidates"]
        if item["targetLawStatus"] in {"confirmed", "changed"} and not citations:
            raise ReviewBatchError(
                f"{item['candidateId']}: confirmed/changed requires an official citation"
            )
        for citation in citations:
            try:
                parsed_url = urlsplit(citation["url"])
                hostname = parsed_url.hostname or ""
            except ValueError as error:
                raise ReviewBatchError(
                    f"{item['candidateId']}: citation URL is malformed"
                ) from error
            if parsed_url.scheme != "https":
                raise ReviewBatchError(
                    f"{item['candidateId']}: citation URL must use https"
                )
            if not _primary_official_host(hostname):
                raise ReviewBatchError(
                    f"{item['candidateId']}: citation is not an allowed primary official host: {hostname}"
                )


def validate_ai_response(
    response: Any, batch: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate an AI response and normalize it into batch candidate order."""

    if batch.get("schemaVersion") != BATCH_SCHEMA_VERSION:
        raise ReviewBatchError("unsupported review batch schema")
    document = _require_object(response, "response")
    _exact_fields(
        document, ("schemaVersion", "batchId", "legalAsOf", "items"), "response"
    )
    if document["schemaVersion"] != RESPONSE_SCHEMA_VERSION:
        raise ReviewBatchError("unsupported AI response schema")
    if document["batchId"] != batch.get("batchId"):
        raise ReviewBatchError("AI response batchId does not match the batch")
    legal_as_of = _iso_date(document["legalAsOf"], "response.legalAsOf")
    target_legal_as_of = _iso_date(
        batch.get("targetLegalAsOf"), "batch.targetLegalAsOf"
    )
    if legal_as_of != target_legal_as_of:
        raise ReviewBatchError(
            "response.legalAsOf does not match batch.targetLegalAsOf"
        )
    values = document["items"]
    if not isinstance(values, list):
        raise ReviewBatchError("response.items must be an array")
    normalized: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        item = _validate_response_item(value, f"response.items[{index}]")
        candidate_id = item["candidateId"]
        if candidate_id in normalized:
            raise ReviewBatchError(f"duplicate response candidateId: {candidate_id}")
        normalized[candidate_id] = item

    batch_items = batch.get("items")
    if not isinstance(batch_items, list):
        raise ReviewBatchError("batch has no items array")
    batch_by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(batch_items):
        if not isinstance(item, Mapping):
            raise ReviewBatchError(f"batch.items[{index}] must be an object")
        candidate_id = _nonempty_string(
            item.get("candidateId"), f"batch.items[{index}].candidateId"
        )
        if candidate_id in batch_by_id:
            raise ReviewBatchError(f"batch has duplicate candidateId: {candidate_id}")
        if not isinstance(item.get("inferredTruth"), bool):
            raise ReviewBatchError(f"{candidate_id}: batch inferredTruth must be boolean")
        if item.get("historicalAnswerVerification") not in HISTORICAL_ANSWER_VERIFICATIONS:
            raise ReviewBatchError(
                f"{candidate_id}: batch historicalAnswerVerification is invalid"
            )
        batch_by_id[candidate_id] = item

    expected_ids = list(batch_by_id)
    expected_set = set(expected_ids)
    actual_set = set(normalized)
    if actual_set != expected_set:
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ReviewBatchError(
            "response candidateId set does not exactly match batch: "
            + "; ".join(details)
        )

    ordered_items = [normalized[candidate_id] for candidate_id in expected_ids]
    for item in ordered_items:
        inferred_truth = batch_by_id[item["candidateId"]]["inferredTruth"]
        if item["targetLawStatus"] == "confirmed" and item["targetTruth"] != inferred_truth:
            raise ReviewBatchError(
                f"{item['candidateId']}: confirmed requires targetTruth to equal inferredTruth"
            )
        if item["targetLawStatus"] == "changed" and item["targetTruth"] == inferred_truth:
            raise ReviewBatchError(
                f"{item['candidateId']}: changed requires targetTruth to differ from inferredTruth"
            )

    result = {
        "schemaVersion": RESPONSE_SCHEMA_VERSION,
        "batchId": batch["batchId"],
        "legalAsOf": legal_as_of,
        "items": ordered_items,
    }
    validate_primary_official_citations(result)
    return result


def build_import_document(
    response: Any,
    batch: Mapping[str, Any],
    *,
    source_response_sha256: str,
) -> dict[str, Any]:
    normalized = validate_ai_response(response, batch)
    return {
        "schemaVersion": IMPORT_SCHEMA_VERSION,
        "importedAt": utc_now(),
        "visibility": "private_not_for_web",
        "batchId": batch["batchId"],
        "batchIndex": batch["batchIndex"],
        "inventoryDigest": batch["inventoryDigest"],
        "sourceResponseSha256": source_response_sha256,
        "trustBoundary": {
            "source": "ai_candidate",
            "humanVerified": False,
            "reviewed": False,
            "publishable": False,
        },
        "response": normalized,
    }


def _private_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    review_root = (data_root() / "review").expanduser().resolve()
    try:
        resolved.relative_to(review_root)
    except ValueError as error:
        raise ReviewBatchError(f"output must stay under private review root: {review_root}") from error
    web_root = Path("/var/www").resolve()
    try:
        resolved.relative_to(web_root)
    except ValueError:
        pass
    else:
        raise ReviewBatchError("output cannot be written under /var/www")
    return resolved


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root() / "curation" / "review_candidates.json",
        help="private review candidate inventory",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="items per batch (default: 10)",
    )
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=data_root() / "reports" / "answer-reconciliation.json",
        help="official/provider answer reconciliation report",
    )
    parser.add_argument(
        "--legal-as-of",
        required=True,
        help="law date to review in YYYY-MM-DD form (for the 2026 exam: 2026-04-01)",
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        help="1-based batch number; omit to write the complete manifest",
    )
    parser.add_argument(
        "--import-response",
        type=Path,
        help="strict AI response JSON to validate and import (requires --batch-index)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="atomic output path under the private review directory",
    )
    return parser


def _default_output(batch_index: int | None, import_response: Path | None, batch_id: str | None) -> Path:
    if batch_index is None:
        return data_root() / "review" / "pending" / "manifest.json"
    if import_response is not None:
        return data_root() / "review" / "decisions" / f"{batch_id}.ai.json"
    return data_root() / "review" / "pending" / f"{batch_id}.json"


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.import_response is not None and args.batch_index is None:
            raise ReviewBatchError("--import-response requires --batch-index")
        inventory = load_json(args.input)
        if not isinstance(inventory, dict):
            raise ReviewBatchError("candidate inventory must be an object")
        reconciliation = load_json(args.reconciliation)
        if not isinstance(reconciliation, dict):
            raise ReviewBatchError("answer reconciliation must be an object")
        verifications = historical_answer_verifications(reconciliation)
        if args.batch_index is None:
            document = build_manifest(
                inventory,
                historical_verifications=verifications,
                target_legal_as_of=args.legal_as_of,
                batch_size=args.batch_size,
                source_input=str(args.input),
                source_reconciliation=str(args.reconciliation),
            )
            batch_id = None
        else:
            batch = build_batch(
                inventory,
                historical_verifications=verifications,
                target_legal_as_of=args.legal_as_of,
                batch_size=args.batch_size,
                batch_index=args.batch_index,
                source_input=str(args.input),
                source_reconciliation=str(args.reconciliation),
            )
            batch_id = batch["batchId"]
            if args.import_response is None:
                document = batch
            else:
                response_bytes = args.import_response.read_bytes()
                response = _strict_json_bytes(response_bytes, "AI response")
                document = build_import_document(
                    response,
                    batch,
                    source_response_sha256=hashlib.sha256(response_bytes).hexdigest(),
                )
        output = _private_output(
            args.output
            or _default_output(args.batch_index, args.import_response, batch_id)
        )
        atomic_write_json(output, document)
    except (ReviewBatchError, OSError, json.JSONDecodeError) as error:
        print(f"review batch failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(output),
                "schemaVersion": document["schemaVersion"],
                "itemCount": (
                    document.get(
                        "choiceCandidateCount",
                        len(
                            document.get("items")
                            or (document.get("response") or {}).get("items")
                            or []
                        ),
                    )
                    if isinstance(document, dict)
                    else 0
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
