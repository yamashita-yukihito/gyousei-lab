from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.similarity import (
    SimilarityError,
    build_similarity_document,
    main,
    normalize_statement,
)


def choice(
    candidate_id: str,
    raw_id: str,
    text: str,
    *,
    label: str = "行政手続法",
) -> dict:
    return {
        "candidateId": candidate_id,
        "candidateKind": "choice_proposition",
        "rawQuestionId": raw_id,
        "sourceSnapshotId": "snapshot:" + raw_id,
        "examYear": 2025,
        "questionNumber": int(raw_id.rsplit(":", 1)[-1]),
        "choiceLabel": "1",
        "statementText": text,
        "inferredTruth": True,
        "sourceCitation": {"labels": ["行政法", label]},
        "providerExplanation": "must not leak",
    }


def inventory(candidates: list[dict]) -> dict:
    return {
        "schemaVersion": "review-candidate-inventory@1",
        "candidates": candidates,
    }


class SimilarityTests(unittest.TestCase):
    def test_normalization_only_removes_presentation_differences(self) -> None:
        self.assertEqual(
            normalize_statement("行政庁は、処分できる。"),
            normalize_statement("行政庁は 処分できる"),
        )
        self.assertNotEqual(normalize_statement("処分できる"), normalize_statement("処分できない"))

    def test_exact_pair_requires_shared_sub_label_and_different_question(self) -> None:
        candidates = [
            choice("a", "q:1", "行政庁は、処分できる。"),
            choice("b", "q:2", "行政庁は処分できる"),
            choice("same-question", "q:1", "行政庁は処分できる"),
            choice("different-label", "q:3", "行政庁は処分できる", label="地方自治法"),
        ]
        report = build_similarity_document(inventory(candidates))
        self.assertEqual(report["summary"]["pairCount"], 2)
        ids = {
            (pair["left"]["candidateId"], pair["right"]["candidateId"])
            for pair in report["pairs"]
        }
        self.assertIn(("a", "b"), ids)
        self.assertNotIn(("a", "same-question"), ids)
        pair = next(pair for pair in report["pairs"] if pair["left"]["candidateId"] == "a")
        self.assertEqual(pair["method"], "normalized_exact")
        self.assertEqual(pair["commonLabels"], ["行政手続法"])
        self.assertFalse(pair["reviewed"])
        self.assertFalse(pair["publishable"])
        self.assertNotIn("providerExplanation", json.dumps(report, ensure_ascii=False))

    def test_near_pairs_form_a_deterministic_connected_group(self) -> None:
        base = "行政庁は申請を受けた場合には理由を示して処分をしなければならない"
        candidates = [
            choice("a", "q:1", base),
            choice("b", "q:2", base + "こととされる"),
            choice("c", "q:3", base + "のが原則である"),
        ]
        report = build_similarity_document(inventory(candidates), threshold=0.5)
        self.assertGreaterEqual(report["summary"]["pairCount"], 2)
        self.assertEqual(report["summary"]["groupCount"], 1)
        self.assertEqual(report["groups"][0]["memberCandidateIds"], ["a", "b", "c"])

    def test_high_recall_review_finds_a_legal_paraphrase_with_evidence(self) -> None:
        candidates = [
            choice(
                "a",
                "q:1",
                "住民監査請求を経なければ住民訴訟を提起することはできない",
                label="地方自治法",
            ),
            choice(
                "b",
                "q:2",
                "住民訴訟を始めるには、先に住民監査請求を済ませる必要がある",
                label="地方自治法",
            ),
        ]
        report = build_similarity_document(inventory(candidates), threshold=1.0)
        self.assertEqual(report["summary"]["pairCount"], 0)
        self.assertEqual(report["summary"]["reviewPairCount"], 1)
        pair = report["reviewPairs"][0]
        self.assertEqual(pair["tier"], "exploratory")
        self.assertEqual(pair["method"], "ranked_high_recall")
        self.assertEqual(pair["sharedLegalConcepts"], ["住民監査請求", "住民訴訟"])
        self.assertIn("shared_legal_concepts", pair["reasonCodes"])
        self.assertGreater(pair["reviewScore"], 0.18)
        self.assertIn("characterBigramIdfJaccard", pair["scoreBreakdown"])
        self.assertIn("legalConceptRarityAdjustedJaccard", pair["scoreBreakdown"])
        self.assertFalse(pair["reviewed"])
        self.assertFalse(pair["publishable"])

    def test_review_queue_keeps_strict_pairs_outside_top_k(self) -> None:
        candidates = [
            choice("a", "q:1", "行政庁は、処分理由を提示しなければならない"),
            choice("b", "q:2", "行政庁は処分理由を提示しなければならない"),
        ]
        report = build_similarity_document(
            inventory(candidates), review_threshold=1.0, max_review_neighbors=0
        )
        self.assertEqual(report["summary"]["pairCount"], 1)
        self.assertEqual(report["summary"]["reviewPairCount"], 1)
        self.assertEqual(report["reviewPairs"][0]["tier"], "strict")
        self.assertIn("strict_similarity_retained", report["reviewPairs"][0]["reasonCodes"])

    def test_review_queue_is_bounded_by_per_candidate_top_k(self) -> None:
        candidates = [
            choice(
                f"candidate-{number}",
                f"q:{number}",
                f"行政庁による不利益処分の理由を示す手続についての設問その{number}",
            )
            for number in range(1, 7)
        ]
        report = build_similarity_document(
            inventory(candidates),
            threshold=1.0,
            review_threshold=0.0,
            max_review_neighbors=1,
        )
        exploratory = [
            pair for pair in report["reviewPairs"] if pair["tier"] == "exploratory"
        ]
        self.assertLessEqual(len(exploratory), len(candidates))
        self.assertTrue(exploratory)
        for pair in exploratory:
            self.assertTrue(pair["selectedByCandidateIds"])
            self.assertTrue(all(rank == 1 for rank in pair["neighborRanks"].values()))

    def test_high_recall_still_excludes_same_question_and_mismatched_labels(self) -> None:
        candidates = [
            choice("a", "q:1", "住民訴訟では先に住民監査請求が必要", label="地方自治法"),
            choice(
                "same-question",
                "q:1",
                "住民監査請求の後に住民訴訟を提起する",
                label="地方自治法",
            ),
            choice(
                "different-label",
                "q:2",
                "住民監査請求の後に住民訴訟を提起する",
                label="行政事件訴訟法",
            ),
        ]
        report = build_similarity_document(
            inventory(candidates), review_threshold=0.0, max_review_neighbors=4
        )
        self.assertEqual(report["reviewPairs"], [])

    def test_review_output_is_deterministic_for_reordered_input(self) -> None:
        candidates = [
            choice("a", "q:1", "理由の提示をしなければならない"),
            choice("b", "q:2", "理由提示が必要である"),
            choice("c", "q:3", "不利益処分では聴聞を行う"),
        ]
        first = build_similarity_document(inventory(candidates))["reviewPairs"]
        second = build_similarity_document(inventory(list(reversed(candidates))))["reviewPairs"]
        self.assertEqual(first, second)

    def test_invalid_schema_and_threshold_fail_closed(self) -> None:
        with self.assertRaises(SimilarityError):
            build_similarity_document({"schemaVersion": "wrong", "candidates": []})
        with self.assertRaises(SimilarityError):
            build_similarity_document(inventory([]), threshold=1.1)
        with self.assertRaises(SimilarityError):
            build_similarity_document(inventory([]), review_threshold=-0.1)
        with self.assertRaises(SimilarityError):
            build_similarity_document(inventory([]), max_review_neighbors=-1)

    def test_cli_writes_private_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(
                json.dumps(
                    inventory(
                        [
                            choice("a", "q:1", "処分理由を示さなければならない"),
                            choice("b", "q:2", "処分理由を示さなければならない"),
                        ]
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(main(["--input", str(input_path), "--output", str(output_path)]), 0)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["visibility"], "private_not_for_web")
            self.assertEqual(written["summary"]["pairCount"], 1)


if __name__ == "__main__":
    unittest.main()
