"""Build reviewed O/X drafts from non-binary provider classification headings.

``explanation_ox`` deliberately rejects headings such as ``ア．努力義務`` or
``イ、ウ．正しい``.  This follow-up stage consumes only an explicit,
version-controlled allowlist for those rejected mappings.  Each rule is joined
back to the saved source question and its correct answer before a private,
non-publishable O/X draft is emitted.

This is an editorial sidecar.  It never writes a production bundle and never
auto-publishes a card.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .common import atomic_write_json, data_root, load_json, utc_now
from .explanation_ox import _documents


SCHEMA_VERSION = "explanation-mapping-derived-ox@1"
VALIDATION_SCHEMA_VERSION = "explanation-mapping-derived-ox-validation@1"
MANIFEST_SCHEMA_VERSION = "explanation-mapping-derived-ox-manifest@1"
RULES_SCHEMA_VERSION = "explanation-mapping-ox-rules@1"
BASE_SCHEMA_VERSION = "explanation-derived-ox@1"

DEFAULT_SCOPE = data_root() / "all_subjects" / "current_2016_2025"
DEFAULT_INPUT = DEFAULT_SCOPE / "extracted"
DEFAULT_BASE = DEFAULT_SCOPE / "curation" / "explanation_ox_candidates.json"
DEFAULT_RULES = DEFAULT_SCOPE / "curation" / "explanation_mapping_ox_rules.json"
DEFAULT_OUTPUT = (
    DEFAULT_SCOPE / "curation" / "explanation_mapping_ox_candidates.json"
)


class MappingOxError(ValueError):
    """Raised when a mapping rule cannot be proven against saved source data."""


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(text.split())


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(record.get("examYear") or 0),
        int(record.get("questionNumber") or 0),
        str(record.get("rawQuestionId") or ""),
    )


def _unique_by_raw_id(
    values: Iterable[dict[str, Any]], *, source_name: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise MappingOxError(f"{source_name}: item must be an object")
        raw_id = value.get("rawQuestionId")
        if not isinstance(raw_id, str) or not raw_id:
            raise MappingOxError(f"{source_name}: item missing rawQuestionId")
        if raw_id in result:
            raise MappingOxError(
                f"{source_name}: duplicate rawQuestionId: {raw_id}"
            )
        result[raw_id] = value
    return result


def _answer_choice(record: dict[str, Any], expected_option: Any) -> dict[str, Any]:
    raw_id = str(record["rawQuestionId"])
    if isinstance(expected_option, bool) or not isinstance(expected_option, int):
        raise MappingOxError(f"{raw_id}: expectedAnswerOption must be an integer")
    answer = record.get("answer")
    if (
        not isinstance(answer, dict)
        or answer.get("kind") != "option"
        or answer.get("value") != expected_option
    ):
        raise MappingOxError(
            f"{raw_id}: saved answer does not match expected option "
            f"{expected_option}"
        )
    choices = record.get("choices")
    if not isinstance(choices, list):
        raise MappingOxError(f"{raw_id}: choices must be a list")
    matches = [
        choice
        for choice in choices
        if isinstance(choice, dict)
        and str(choice.get("label") or "") == str(expected_option)
    ]
    if len(matches) != 1:
        raise MappingOxError(
            f"{raw_id}: expected option does not identify exactly one choice"
        )
    return matches[0]


def _crosscheck_selected_labels(
    raw_id: str,
    record: dict[str, Any],
    selected_choice: dict[str, Any],
    crosscheck: dict[str, Any],
) -> dict[str, bool]:
    selected_truth = crosscheck.get("selectedTruth")
    if type(selected_truth) is not bool:
        raise MappingOxError(f"{raw_id}: selectedTruth must be boolean")
    raw_vector = crosscheck.get("truthByLabel")
    if not isinstance(raw_vector, dict) or not raw_vector:
        raise MappingOxError(f"{raw_id}: truthByLabel must be a non-empty object")
    truth_by_label: dict[str, bool] = {}
    for label, truth in raw_vector.items():
        if not isinstance(label, str) or not re.fullmatch(r"[アイウエオ]", label):
            raise MappingOxError(f"{raw_id}: invalid truth-vector label {label!r}")
        if type(truth) is not bool:
            raise MappingOxError(f"{raw_id}: truth vector must contain booleans")
        truth_by_label[label] = truth

    all_choice_labels: set[str] = set()
    for choice in record.get("choices") or []:
        if not isinstance(choice, dict):
            raise MappingOxError(f"{raw_id}: combination choice must be an object")
        choice_text = _normalized(choice.get("text"))
        if not re.fullmatch(
            r"[アイウエオ](?:[・、][アイウエオ])+", choice_text
        ):
            raise MappingOxError(f"{raw_id}: combination choice is not parseable")
        all_choice_labels.update(re.findall(r"[アイウエオ]", choice_text))
    if set(truth_by_label) != all_choice_labels:
        raise MappingOxError(f"{raw_id}: truth vector does not cover every label")

    selected_text = _normalized(selected_choice.get("text"))
    if not re.fullmatch(r"[アイウエオ](?:[・、][アイウエオ])+", selected_text):
        raise MappingOxError(f"{raw_id}: selected combination is not parseable")
    selected_labels = set(re.findall(r"[アイウエオ]", selected_text))
    expected_labels = {
        label
        for label, truth in truth_by_label.items()
        if truth is selected_truth
    }
    if selected_labels != expected_labels:
        raise MappingOxError(
            f"{raw_id}: rule truth vector conflicts with saved answer"
        )
    return truth_by_label


def _crosscheck_selected_cells(
    raw_id: str,
    selected_choice: dict[str, Any],
    crosscheck: dict[str, Any],
) -> dict[str, str]:
    raw_values = crosscheck.get("valueByLabel")
    if not isinstance(raw_values, dict) or not raw_values:
        raise MappingOxError(f"{raw_id}: valueByLabel must be a non-empty object")
    expected = {
        str(label): str(value)
        for label, value in raw_values.items()
        if str(label) and str(value)
    }
    if len(expected) != len(raw_values):
        raise MappingOxError(f"{raw_id}: valueByLabel contains an empty value")
    cells = selected_choice.get("cells")
    if not isinstance(cells, list) or not cells:
        raise MappingOxError(f"{raw_id}: selected option has no table cells")
    actual: dict[str, str] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise MappingOxError(f"{raw_id}: selected cell must be an object")
        label = str(cell.get("column") or "")
        value = str(cell.get("text") or "")
        if not label or not value or label in actual:
            raise MappingOxError(f"{raw_id}: invalid selected cell")
        actual[label] = value
    if {
        label: _normalized(value) for label, value in actual.items()
    } != {
        label: _normalized(value) for label, value in expected.items()
    }:
        raise MappingOxError(
            f"{raw_id}: rule classification cells conflict with saved answer"
        )
    return expected


def _mapping_index(base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(base, dict) or base.get("schemaVersion") != BASE_SCHEMA_VERSION:
        raise MappingOxError("base explanation O/X schema mismatch")
    mappings = base.get("editorialMappings")
    if not isinstance(mappings, list):
        raise MappingOxError("base explanation O/X has no editorialMappings")
    result: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise MappingOxError("editorial mapping must be an object")
        mapping_id = mapping.get("mappingId")
        if not isinstance(mapping_id, str) or not mapping_id:
            raise MappingOxError("editorial mapping missing mappingId")
        if mapping_id in result:
            raise MappingOxError(f"duplicate editorial mapping: {mapping_id}")
        result[mapping_id] = mapping
    return result


def _validate_rules(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(value, dict) or value.get("schemaVersion") != RULES_SCHEMA_VERSION:
        raise MappingOxError("mapping rules schema mismatch")
    groups = value.get("groups")
    exclusions = value.get("excludedMappings")
    if not isinstance(groups, list) or not isinstance(exclusions, list):
        raise MappingOxError("mapping rules must contain groups and excludedMappings")
    return groups, exclusions


def _mapping_labels(provider_verdict: str) -> set[str]:
    return set(re.findall(r"[アイウエオ]", _normalized(provider_verdict)))


def _source_match(statement: str, question_text: str) -> str:
    if statement in question_text:
        return "exact_substring"
    if _normalized(statement) in _normalized(question_text):
        return "normalized_substring"
    return "not_found"


def _candidate(
    record: dict[str, Any],
    mapping: dict[str, Any],
    output: dict[str, Any],
    *,
    answer_crosscheck: dict[str, Any],
) -> dict[str, Any]:
    raw_id = str(record["rawQuestionId"])
    label = str(output["statementLabel"])
    source_statement = str(output["sourceStatementText"])
    context = mapping.get("questionContext")
    if not isinstance(context, dict):
        raise MappingOxError(f"{raw_id}: mapping questionContext missing")
    return {
        "candidateId": f"{raw_id}:provider-mapping:{label}",
        "candidateKind": "provider_mapping_composed_proposition",
        "rawQuestionId": raw_id,
        "sourceSnapshotId": str(mapping.get("sourceSnapshotId") or ""),
        "sourceBodySha256": str(mapping.get("sourceBodySha256") or ""),
        "examYear": mapping.get("examYear"),
        "questionNumber": mapping.get("questionNumber"),
        "subjectId": str(mapping.get("subjectId") or ""),
        "subjectLabel": str(mapping.get("subjectLabel") or ""),
        "format": str(mapping.get("format") or ""),
        "task": str(mapping.get("task") or ""),
        "mappingId": str(mapping["mappingId"]),
        "sectionIndex": mapping.get("sectionIndex"),
        "statementLabel": label,
        "choiceLabel": label,
        "sourceStatementText": source_statement,
        "providerMappingStatementText": str(mapping.get("statementText") or ""),
        "statementText": str(output["statementText"]),
        "providerVerdict": str(mapping.get("providerVerdict") or ""),
        "providerExplanationParagraphs": list(
            mapping.get("providerExplanationParagraphs") or []
        ),
        "questionContext": context,
        "sourceTextMatch": _source_match(
            source_statement, str(context.get("questionText") or "")
        ),
        "sourceCitation": mapping.get("sourceCitation"),
        "inferredTruth": output["inferredTruth"],
        "truthBasis": "provider_mapping_composition_and_saved_source_answer",
        "compositionKind": str(output["compositionKind"]),
        "classificationValue": output.get("classificationValue"),
        "reviewNote": str(output.get("reviewNote") or ""),
        "recipeSha256": str(output["_recipeSha256"]),
        "sourceAnswerCrossCheck": answer_crosscheck,
        "currentLawVerified": False,
        "oxEligible": True,
        "contextReviewRequired": True,
        "frequencyEligible": False,
        "reviewed": False,
        "publishable": False,
        "decisionReason": "explicit_editorial_mapping_rule",
    }


def build_inventory(
    records: Iterable[dict[str, Any]],
    base_inventory: dict[str, Any],
    rules_document: dict[str, Any],
    *,
    source_input: str = "",
    source_base: str = "",
    source_rules: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return private O/X drafts after fail-closed source and answer checks."""

    record_map = _unique_by_raw_id(records, source_name="extracted questions")
    mapping_map = _mapping_index(base_inventory)
    groups, exclusions = _validate_rules(rules_document)
    base_summary = base_inventory.get("summary")
    if not isinstance(base_summary, dict):
        raise MappingOxError("base explanation O/X summary missing")

    excluded: dict[str, str] = {}
    for exclusion in exclusions:
        if not isinstance(exclusion, dict):
            raise MappingOxError("excluded mapping must be an object")
        mapping_id = exclusion.get("mappingId")
        reason = exclusion.get("reason")
        if (
            not isinstance(mapping_id, str)
            or mapping_id not in mapping_map
            or not isinstance(reason, str)
            or not reason
        ):
            raise MappingOxError("invalid excluded mapping rule")
        if mapping_id in excluded:
            raise MappingOxError(f"duplicate excluded mapping: {mapping_id}")
        excluded[mapping_id] = reason

    candidates: list[dict[str, Any]] = []
    consumed: dict[str, list[dict[str, Any]]] = {}
    group_ids: set[str] = set()
    answer_methods: Counter[str] = Counter()

    for group in groups:
        if not isinstance(group, dict):
            raise MappingOxError("mapping rule group must be an object")
        raw_id = group.get("rawQuestionId")
        if not isinstance(raw_id, str) or raw_id not in record_map:
            raise MappingOxError(f"unknown rule rawQuestionId: {raw_id!r}")
        if raw_id in group_ids:
            raise MappingOxError(f"duplicate rule group: {raw_id}")
        group_ids.add(raw_id)
        record = record_map[raw_id]
        expected_digest = group.get("expectedSourceBodySha256")
        if (
            not isinstance(expected_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            or record.get("sourceBodySha256") != expected_digest
        ):
            raise MappingOxError(f"{raw_id}: source body digest drift")
        selected_choice = _answer_choice(record, group.get("expectedAnswerOption"))
        crosscheck = group.get("answerCrossCheck")
        if not isinstance(crosscheck, dict):
            raise MappingOxError(f"{raw_id}: answerCrossCheck must be an object")
        method = crosscheck.get("kind")
        if method == "selected_labels":
            truth_by_label = _crosscheck_selected_labels(
                raw_id, record, selected_choice, crosscheck
            )
            value_by_label: dict[str, str] = {}
        elif method == "selected_cells":
            value_by_label = _crosscheck_selected_cells(
                raw_id, selected_choice, crosscheck
            )
            truth_by_label = {}
        else:
            raise MappingOxError(
                f"{raw_id}: unsupported answerCrossCheck kind {method!r}"
            )
        answer_methods[str(method)] += 1
        answer_crosscheck = {
            "result": "matched",
            "method": method,
            "answerOption": group["expectedAnswerOption"],
        }

        outputs = group.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise MappingOxError(f"{raw_id}: outputs must be a non-empty list")
        for output in outputs:
            if not isinstance(output, dict):
                raise MappingOxError(f"{raw_id}: output rule must be an object")
            required = (
                "mappingId",
                "statementLabel",
                "expectedProviderVerdict",
                "expectedMappingStatementText",
                "sourceStatementText",
                "statementText",
                "compositionKind",
            )
            missing = [key for key in required if key not in output]
            if missing:
                raise MappingOxError(f"{raw_id}: output missing {missing}")
            mapping_id = str(output["mappingId"])
            mapping = mapping_map.get(mapping_id)
            if mapping is None:
                raise MappingOxError(f"{raw_id}: unknown mappingId {mapping_id}")
            if mapping.get("rawQuestionId") != raw_id:
                raise MappingOxError(f"{raw_id}: mapping belongs to another question")
            if mapping.get("sourceBodySha256") != expected_digest:
                raise MappingOxError(f"{raw_id}: mapping source body digest drift")
            if mapping.get("providerVerdict") != output["expectedProviderVerdict"]:
                raise MappingOxError(f"{mapping_id}: provider verdict drift")
            if mapping.get("statementText") != output["expectedMappingStatementText"]:
                raise MappingOxError(f"{mapping_id}: mapping statement drift")
            label = str(output["statementLabel"])
            if not re.fullmatch(r"[アイウエオ]", label):
                raise MappingOxError(f"{raw_id}: invalid output label {label!r}")
            if truth_by_label:
                if label not in truth_by_label:
                    raise MappingOxError(f"{raw_id}: label absent from answer vector")
                inferred_truth = truth_by_label[label]
            else:
                classification_value = output.get("classificationValue")
                if (
                    label not in value_by_label
                    or classification_value != value_by_label[label]
                ):
                    raise MappingOxError(
                        f"{raw_id}: output classification conflicts with answer cells"
                    )
                inferred_truth = True
            source_statement = str(output["sourceStatementText"])
            question_text = str(record.get("questionText") or "")
            if _source_match(source_statement, question_text) == "not_found":
                raise MappingOxError(
                    f"{raw_id}: source statement {label} not found in question"
                )
            statement_text = str(output["statementText"]).strip()
            if not statement_text or statement_text == source_statement:
                raise MappingOxError(
                    f"{raw_id}: composed statement must add its classification"
                )
            output = dict(output)
            output["statementText"] = statement_text
            output["inferredTruth"] = inferred_truth
            output["_recipeSha256"] = _canonical_sha256(
                {
                    "rawQuestionId": raw_id,
                    "expectedSourceBodySha256": expected_digest,
                    "expectedAnswerOption": group["expectedAnswerOption"],
                    "answerCrossCheck": crosscheck,
                    "output": {
                        key: value
                        for key, value in output.items()
                        if not key.startswith("_")
                    },
                }
            )
            candidate = _candidate(
                record,
                mapping,
                output,
                answer_crosscheck=answer_crosscheck,
            )
            candidates.append(candidate)
            consumed.setdefault(mapping_id, []).append(candidate)

    for mapping_id, mapping_candidates in consumed.items():
        if mapping_id in excluded:
            raise MappingOxError(f"{mapping_id}: both consumed and excluded")
        if len(mapping_candidates) == 1:
            continue
        provider_labels = _mapping_labels(
            str(mapping_map[mapping_id].get("providerVerdict") or "")
        )
        candidate_labels = {
            str(candidate["statementLabel"]) for candidate in mapping_candidates
        }
        if provider_labels != candidate_labels:
            raise MappingOxError(
                f"{mapping_id}: split labels do not match provider heading"
            )
        mapping_statement = _normalized(mapping_map[mapping_id].get("statementText"))
        if any(
            _normalized(candidate["sourceStatementText"]) not in mapping_statement
            for candidate in mapping_candidates
        ):
            raise MappingOxError(
                f"{mapping_id}: split source is not contained in mapping statement"
            )

    accounted = set(consumed) | set(excluded)
    if accounted != set(mapping_map):
        missing = sorted(set(mapping_map) - accounted)
        extra = sorted(accounted - set(mapping_map))
        raise MappingOxError(
            "rules do not account for every editorial mapping "
            f"(missing={missing[:3]}, extra={extra[:3]})"
        )

    candidate_ids = [candidate["candidateId"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise MappingOxError("generated duplicate candidateId")
    if any(candidate["sourceTextMatch"] == "not_found" for candidate in candidates):
        raise MappingOxError("generated candidate is detached from source text")

    candidates.sort(key=_record_sort_key)
    base_candidates = base_inventory.get("candidates")
    if not isinstance(base_candidates, list):
        raise MappingOxError("base explanation O/X candidates missing")
    base_source_ids = {
        str(candidate.get("rawQuestionId") or "")
        for candidate in base_candidates
        if isinstance(candidate, dict)
    }
    source_ids = {candidate["rawQuestionId"] for candidate in candidates}
    new_source_ids = source_ids - base_source_ids
    by_subject = Counter(candidate["subjectId"] for candidate in candidates)
    by_truth = Counter(
        "true" if candidate["inferredTruth"] else "false"
        for candidate in candidates
    )
    source_match = Counter(candidate["sourceTextMatch"] for candidate in candidates)
    combined_by_subject = Counter(
        {
            str(subject): int(count)
            for subject, count in (
                base_summary.get("additionalCandidatesBySubject") or {}
            ).items()
        }
    )
    combined_by_subject.update(by_subject)
    generated = generated_at or utc_now()
    summary = {
        "rawQuestionCount": len(record_map),
        "baseCandidateCount": len(base_candidates),
        "baseSourceQuestionCount": len(base_source_ids),
        "baseEditorialMappingCount": len(mapping_map),
        "ruleGroupCount": len(groups),
        "consumedEditorialMappingCount": len(consumed),
        "excludedEditorialMappingCount": len(excluded),
        "remainingEditorialMappingCount": len(mapping_map) - len(consumed),
        "additionalCandidateCount": len(candidates),
        "additionalSourceQuestionCount": len(source_ids),
        "newSourceQuestionCount": len(new_source_ids),
        "combinedCandidateCount": len(base_candidates) + len(candidates),
        "combinedSourceQuestionCount": len(base_source_ids | source_ids),
        "additionalCandidatesBySubject": dict(sorted(by_subject.items())),
        "combinedCandidatesBySubject": dict(sorted(combined_by_subject.items())),
        "additionalCandidatesByTruth": dict(sorted(by_truth.items())),
        "candidateSourceTextMatch": dict(sorted(source_match.items())),
        "answerCrossChecksByMethod": dict(sorted(answer_methods.items())),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated,
        "visibility": "private_editorial_candidate_not_for_web",
        "sourceInput": source_input,
        "sourceBaseInventory": source_base,
        "sourceRules": source_rules,
        "rulesDocumentSha256": _canonical_sha256(rules_document),
        "policy": {
            "autoPublish": False,
            "autoFrequency": False,
            "providerNarrativeIsPrivate": True,
            "rulesAreExplicitAllowlist": True,
            "sourceAnswerCrossCheckRequired": True,
            "contextReviewRequiredBeforeCardPromotion": True,
            "frequencyUnitAfterIntegration": "one exam question per card",
        },
        "summary": summary,
        "candidates": candidates,
        "consumedMappingIds": sorted(consumed),
        "excludedMappings": [
            {"mappingId": mapping_id, "reason": reason}
            for mapping_id, reason in sorted(excluded.items())
        ],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)
    os.chmod(path, 0o600)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--base-candidates", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation-output", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--expected-count", type=int, default=569)
    parser.add_argument("--expected-base-candidate-count", type=int, default=632)
    parser.add_argument("--expected-base-mapping-count", type=int, default=47)
    parser.add_argument("--expected-candidate-count", type=int, default=41)
    parser.add_argument("--expected-remaining-mapping-count", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    validation_output = args.validation_output or (
        args.output.parent / "explanation_mapping_ox_validation.json"
    )
    manifest_output = args.manifest_output or (
        args.output.parent / "explanation_mapping_ox_manifest.json"
    )
    try:
        records = _documents(args.input)
        if len(records) != args.expected_count:
            raise MappingOxError(
                f"expected {args.expected_count} records, got {len(records)}"
            )
        base = load_json(args.base_candidates)
        rules = load_json(args.rules)
        generated_at = utc_now()
        inventory = build_inventory(
            records,
            base,
            rules,
            source_input=str(args.input),
            source_base=str(args.base_candidates),
            source_rules=str(args.rules),
            generated_at=generated_at,
        )
        expectations = (
            ("baseCandidateCount", args.expected_base_candidate_count),
            ("baseEditorialMappingCount", args.expected_base_mapping_count),
            ("additionalCandidateCount", args.expected_candidate_count),
            (
                "remainingEditorialMappingCount",
                args.expected_remaining_mapping_count,
            ),
        )
        for key, expected in expectations:
            if inventory["summary"][key] != expected:
                raise MappingOxError(
                    f"expected {expected} for {key}, got "
                    f"{inventory['summary'][key]}"
                )
        _write_private_json(args.output, inventory)
        validation = {
            "schemaVersion": VALIDATION_SCHEMA_VERSION,
            "generatedAt": generated_at,
            "passed": True,
            "checks": {
                "allEditorialMappingsAccountedFor": True,
                "sourceStatementsMatched": True,
                "savedAnswersMatched": True,
                "duplicateCandidateIdCount": 0,
                "autoPublish": False,
                "autoFrequency": False,
            },
            "summary": inventory["summary"],
        }
        _write_private_json(validation_output, validation)
        manifest = {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "generatedAt": generated_at,
            "visibility": "private_authoring_artifact",
            "algorithmSchemaVersion": SCHEMA_VERSION,
            "artifacts": [
                {
                    "name": args.output.name,
                    "sha256": _sha256_file(args.output),
                    "mode": "0600",
                },
                {
                    "name": validation_output.name,
                    "sha256": _sha256_file(validation_output),
                    "mode": "0600",
                },
            ],
            "summary": inventory["summary"],
        }
        _write_private_json(manifest_output, manifest)
    except (MappingOxError, OSError, json.JSONDecodeError) as error:
        print(f"mapping O/X build failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "validation": str(validation_output),
                "manifest": str(manifest_output),
                **inventory["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
