from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gyousei_pipeline.review_batches import (
    DEFAULT_BATCH_SIZE,
    RESPONSE_SCHEMA_VERSION,
    ReviewBatchError,
    build_argument_parser,
    build_batch,
    build_import_document,
    build_manifest,
    choice_candidates,
    historical_answer_verifications,
    main,
    validate_ai_response,
)


FIXTURE = Path(__file__).parent / "fixtures" / "review_batch_inventory.json"
RECONCILIATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "review_batch_reconciliation.json"
)
LEGAL_AS_OF = "2026-04-01"


def valid_response(batch: dict) -> dict:
    return {
        "schemaVersion": RESPONSE_SCHEMA_VERSION,
        "batchId": batch["batchId"],
        "legalAsOf": batch["targetLegalAsOf"],
        "items": [
            {
                "candidateId": item["candidateId"],
                "targetLawStatus": "confirmed",
                "targetTruth": item["inferredTruth"],
                "legalReviewStatus": "ai_candidate",
                "relationNotes": ["現行法との関係を確認した。"],
                "citationCandidates": [
                    {
                        "citationType": "statute",
                        "title": "行政手続法",
                        "url": "https://elaws.e-gov.go.jp/",
                        "locator": "第1条",
                        "relevance": "根拠条文候補",
                    }
                ],
                "risks": [],
                "reviewed": False,
                "publishable": False,
            }
            for item in batch["items"]
        ],
    }


class ReviewBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.reconciliation = json.loads(
            RECONCILIATION_FIXTURE.read_text(encoding="utf-8")
        )
        self.verifications = historical_answer_verifications(self.reconciliation)

    def build_manifest(self, **kwargs: object) -> dict:
        kwargs.setdefault("target_legal_as_of", LEGAL_AS_OF)
        return build_manifest(
            self.inventory,
            historical_verifications=self.verifications,
            **kwargs,
        )

    def build_batch(self, **kwargs: object) -> dict:
        kwargs.setdefault("target_legal_as_of", LEGAL_AS_OF)
        return build_batch(
            self.inventory,
            historical_verifications=self.verifications,
            **kwargs,
        )

    def test_filters_and_orders_by_sublabel_year_and_natural_ids(self) -> None:
        ordered = choice_candidates(self.inventory)
        self.assertEqual(
            [item["candidateId"] for item in ordered],
            [
                "gkd:100:choice:1",
                "gkd:110:choice:1",
                "gkd:110:choice:2",
                "gkd:90:choice:1",
                "gkd:300:choice:1",
            ],
        )

    def test_manifest_lists_batches_and_embeds_strict_response_schema(self) -> None:
        manifest = self.build_manifest(batch_size=2, source_input=str(FIXTURE))
        self.assertEqual(manifest["choiceCandidateCount"], 5)
        self.assertEqual(manifest["batchCount"], 3)
        self.assertEqual([batch["itemCount"] for batch in manifest["batches"]], [2, 2, 1])
        schema = manifest["responseSchema"]
        self.assertNotIn("$schema", schema)
        self.assertFalse(schema["additionalProperties"])
        item_schema = schema["properties"]["items"]["items"]
        self.assertFalse(item_schema["additionalProperties"])
        self.assertEqual(
            item_schema["properties"]["targetLawStatus"]["enum"],
            ["confirmed", "changed", "uncertain"],
        )
        self.assertEqual(
            item_schema["properties"]["legalReviewStatus"]["enum"],
            ["unreviewed", "ai_candidate", "human_verified"],
        )
        self.assertEqual(
            item_schema["properties"]["targetTruth"]["type"], ["boolean", "null"]
        )
        self.assertIn("legalAsOf", schema["required"])
        self.assertEqual(manifest["targetLegalAsOf"], LEGAL_AS_OF)
        self.assertEqual(item_schema["properties"]["reviewed"], {"const": False})
        self.assertEqual(item_schema["properties"]["publishable"], {"const": False})

    def test_batch_projection_contains_review_context_but_no_provider_explanation(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=2)
        self.assertEqual(batch["batchIndex"], 2)
        self.assertEqual(len(batch["items"]), 2)
        amended = batch["items"][1]
        self.assertEqual(amended["candidateId"], "gkd:90:choice:1")
        self.assertTrue(amended["isAmended"])
        self.assertEqual(amended["questionDirection"]["asksFor"], "incorrect_statement")
        self.assertEqual(amended["historicalAnswerVerification"], "provider_only")
        self.assertIn("statementText", amended)
        serialized = json.dumps(batch, ensure_ascii=False)
        self.assertNotIn("PROVIDER-EXPLANATION-MUST-NOT-LEAK", serialized)
        self.assertNotIn("providerExplanation", serialized)

    def test_batch_index_is_one_based_and_checked(self) -> None:
        with self.assertRaisesRegex(ReviewBatchError, "between 1 and 3"):
            self.build_batch(batch_size=2, batch_index=0)
        with self.assertRaisesRegex(ReviewBatchError, "between 1 and 3"):
            self.build_batch(batch_size=2, batch_index=4)

    def test_valid_response_is_normalized_to_batch_order(self) -> None:
        batch = self.build_batch(batch_size=3, batch_index=1)
        response = valid_response(batch)
        response["items"].reverse()
        normalized = validate_ai_response(response, batch)
        self.assertEqual(
            [item["candidateId"] for item in normalized["items"]],
            [item["candidateId"] for item in batch["items"]],
        )
        imported = build_import_document(
            response, batch, source_response_sha256="a" * 64
        )
        self.assertEqual(imported["trustBoundary"]["source"], "ai_candidate")
        self.assertFalse(imported["trustBoundary"]["humanVerified"])
        self.assertFalse(imported["response"]["items"][0]["publishable"])

    def test_candidate_id_set_must_match_exactly(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=1)
        missing = valid_response(batch)
        missing["items"].pop()
        with self.assertRaisesRegex(ReviewBatchError, "does not exactly match"):
            validate_ai_response(missing, batch)

        extra = valid_response(batch)
        extra["items"].append(copy.deepcopy(extra["items"][0]))
        extra["items"][-1]["candidateId"] = "unexpected"
        with self.assertRaisesRegex(ReviewBatchError, "does not exactly match"):
            validate_ai_response(extra, batch)

        duplicate = valid_response(batch)
        duplicate["items"][1]["candidateId"] = duplicate["items"][0]["candidateId"]
        with self.assertRaisesRegex(ReviewBatchError, "duplicate response candidateId"):
            validate_ai_response(duplicate, batch)

    def test_ai_cannot_escalate_trust_or_add_unknown_fields(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=1)
        cases = []

        unknown_top = valid_response(batch)
        unknown_top["commentary"] = "extra"
        cases.append((unknown_top, "unknown fields"))

        unknown_item = valid_response(batch)
        unknown_item["items"][0]["confidence"] = 1
        cases.append((unknown_item, "unknown fields"))

        unknown_citation = valid_response(batch)
        unknown_citation["items"][0]["citationCandidates"][0]["quote"] = "extra"
        cases.append((unknown_citation, "unknown fields"))

        human = valid_response(batch)
        human["items"][0]["legalReviewStatus"] = "human_verified"
        cases.append((human, "cannot set human_verified"))

        reviewed = valid_response(batch)
        reviewed["items"][0]["reviewed"] = True
        cases.append((reviewed, "leave reviewed false"))

        publishable = valid_response(batch)
        publishable["items"][0]["publishable"] = True
        cases.append((publishable, "leave publishable false"))

        bad_enum = valid_response(batch)
        bad_enum["items"][0]["targetLawStatus"] = "probably"
        cases.append((bad_enum, "invalid value"))

        for response, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ReviewBatchError, message):
                    validate_ai_response(response, batch)

    def test_target_truth_must_match_target_law_status(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=1)

        confirmed_mismatch = valid_response(batch)
        confirmed_mismatch["items"][0]["targetTruth"] = not batch["items"][0][
            "inferredTruth"
        ]
        with self.assertRaisesRegex(ReviewBatchError, "confirmed requires"):
            validate_ai_response(confirmed_mismatch, batch)

        changed_match = valid_response(batch)
        changed_match["items"][0]["targetLawStatus"] = "changed"
        with self.assertRaisesRegex(ReviewBatchError, "changed requires"):
            validate_ai_response(changed_match, batch)

        changed = valid_response(batch)
        changed["items"][0]["targetLawStatus"] = "changed"
        changed["items"][0]["targetTruth"] = not batch["items"][0]["inferredTruth"]
        self.assertEqual(validate_ai_response(changed, batch)["items"][0]["targetLawStatus"], "changed")

        uncertain = valid_response(batch)
        uncertain["items"][0]["targetLawStatus"] = "uncertain"
        uncertain["items"][0]["targetTruth"] = None
        uncertain["items"][0]["legalReviewStatus"] = "unreviewed"
        uncertain["items"][0]["citationCandidates"] = []
        self.assertIsNone(validate_ai_response(uncertain, batch)["items"][0]["targetTruth"])

        uncertain_boolean = valid_response(batch)
        uncertain_boolean["items"][0]["targetLawStatus"] = "uncertain"
        with self.assertRaisesRegex(ReviewBatchError, "must be null"):
            validate_ai_response(uncertain_boolean, batch)

    def test_direct_import_requires_valid_date_and_primary_official_citations(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=1)
        invalid_date = valid_response(batch)
        invalid_date["legalAsOf"] = "2026-02-30"
        with self.assertRaisesRegex(ReviewBatchError, "valid YYYY-MM-DD"):
            validate_ai_response(invalid_date, batch)

        wrong_target = valid_response(batch)
        wrong_target["legalAsOf"] = "2026-07-18"
        with self.assertRaisesRegex(ReviewBatchError, "batch.targetLegalAsOf"):
            validate_ai_response(wrong_target, batch)

        no_citation = valid_response(batch)
        no_citation["items"][0]["citationCandidates"] = []
        with self.assertRaisesRegex(ReviewBatchError, "requires an official citation"):
            validate_ai_response(no_citation, batch)

        third_party = valid_response(batch)
        third_party["items"][0]["citationCandidates"][0]["url"] = (
            "https://example.com/prep-school"
        )
        with self.assertRaisesRegex(ReviewBatchError, "primary official host"):
            validate_ai_response(third_party, batch)

        insecure = valid_response(batch)
        insecure["items"][0]["citationCandidates"][0]["url"] = (
            "http://elaws.e-gov.go.jp/document"
        )
        with self.assertRaisesRegex(ReviewBatchError, "must use https"):
            validate_ai_response(insecure, batch)

    def test_old_batch_and_response_are_rejected(self) -> None:
        batch = self.build_batch(batch_size=2, batch_index=1)
        old_batch = copy.deepcopy(batch)
        old_batch["schemaVersion"] = "ai-legal-review-batch@2"
        with self.assertRaisesRegex(ReviewBatchError, "unsupported review batch schema"):
            validate_ai_response(valid_response(batch), old_batch)

        old_response = valid_response(batch)
        old_response["schemaVersion"] = "ai-legal-review-response@2"
        with self.assertRaisesRegex(ReviewBatchError, "unsupported AI response schema"):
            validate_ai_response(old_response, batch)

    def test_historical_verification_is_required_and_affects_batch_identity(self) -> None:
        missing = dict(self.verifications)
        del missing["gkd:100"]
        with self.assertRaisesRegex(ReviewBatchError, "verification is missing"):
            build_manifest(
                self.inventory,
                historical_verifications=missing,
                target_legal_as_of=LEGAL_AS_OF,
                batch_size=2,
            )
        changed = dict(self.verifications)
        changed["gkd:100"] = "provider_only"
        original = self.build_manifest(batch_size=2)
        modified = build_manifest(
            self.inventory,
            historical_verifications=changed,
            target_legal_as_of=LEGAL_AS_OF,
            batch_size=2,
        )
        self.assertNotEqual(original["inventoryDigest"], modified["inventoryDigest"])

        other_date = self.build_manifest(
            batch_size=2, target_legal_as_of="2026-07-18"
        )
        self.assertNotEqual(original["inventoryDigest"], other_date["inventoryDigest"])

    def test_cli_defaults_and_atomic_private_exports_and_import(self) -> None:
        self.assertEqual(
            build_argument_parser().parse_args(
                ["--legal-as-of", LEGAL_AS_OF]
            ).batch_size,
            DEFAULT_BATCH_SIZE,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_root = root / "review"
            env = {"GYOUSEI_DATA_ROOT": str(root)}
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(
                    [
                        "--input",
                        str(FIXTURE),
                        "--reconciliation",
                        str(RECONCILIATION_FIXTURE),
                        "--legal-as-of",
                        LEGAL_AS_OF,
                        "--batch-size",
                        "2",
                        "--batch-index",
                        "1",
                    ]
                )
            self.assertEqual(status, 0, stderr.getvalue())
            batch_paths = list((review_root / "pending").glob("choice-law-*.json"))
            self.assertEqual(len(batch_paths), 1)
            batch = json.loads(batch_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(list(batch_paths[0].parent.glob(batch_paths[0].name + ".*")), [])

            response_path = root / "claude-response.json"
            response_path.write_text(
                json.dumps(valid_response(batch), ensure_ascii=False), encoding="utf-8"
            )
            import_stdout = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(import_stdout), redirect_stderr(stderr):
                status = main(
                    [
                        "--input",
                        str(FIXTURE),
                        "--reconciliation",
                        str(RECONCILIATION_FIXTURE),
                        "--legal-as-of",
                        LEGAL_AS_OF,
                        "--batch-size",
                        "2",
                        "--batch-index",
                        "1",
                        "--import-response",
                        str(response_path),
                    ]
                )
            self.assertEqual(status, 0, stderr.getvalue())
            self.assertEqual(json.loads(import_stdout.getvalue())["itemCount"], 2)
            imports = list((review_root / "decisions").glob("*.ai.json"))
            self.assertEqual(len(imports), 1)
            imported = json.loads(imports[0].read_text(encoding="utf-8"))
            self.assertEqual(imported["schemaVersion"], "ai-legal-review-import@3")
            self.assertFalse(imported["trustBoundary"]["publishable"])

    def test_cli_rejects_output_outside_private_review_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stderr = io.StringIO()
            with patch.dict(os.environ, {"GYOUSEI_DATA_ROOT": str(root)}), redirect_stderr(stderr):
                status = main(
                    [
                        "--input",
                        str(FIXTURE),
                        "--reconciliation",
                        str(RECONCILIATION_FIXTURE),
                        "--legal-as-of",
                        LEGAL_AS_OF,
                        "--output",
                        str(root / "public.json"),
                    ]
                )
            self.assertEqual(status, 1)
            self.assertIn("private review root", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
