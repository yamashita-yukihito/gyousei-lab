from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gyousei_pipeline.learning_index import (
    LearningIndexError,
    atomic_write_private_json,
    build_learning_index,
    character_ngrams,
    extract_legal_terms,
    main,
    normalize_dimension,
    normalize_subject_id,
    similarity_signals,
)


def card(
    identifier: str,
    *,
    subject: str = "administrative-law",
    category: str = "行政法",
    topic: str = "行政手続法",
    subtopic: str = "弁明の機会",
    statement: str = "弁明の機会の付与を受ける当事者は、代理人を選任できる。",
) -> dict:
    return {
        "id": identifier,
        "subjectId": subject,
        "category": category,
        "topic": topic,
        "subtopic": subtopic,
        "variants": {
            "a": statement,
            "b": "行政に言い分を伝える場では、代理人を頼めます。",
            "bCasual": "本人だけで対応しなくても大丈夫です。",
            "c": "弁明でも代理人を選べる。",
        },
        "correction": "代理人を選任できます。",
        "memoryPoint": "一人で対応しなくてよい。",
        "explanations": {
            "normal": "行政手続法は代理人の選任を認めています。",
            "commonSense": "本人だけに対応を強いる必要はありません。",
        },
        "legalBasis": [{"label": "行政手続法"}],
    }


def candidate(
    identifier: str,
    year: int,
    number: int,
    statement: str,
    *,
    labels: list[str] | None = None,
    subject: str | None = None,
    category: str | None = None,
    topic: str | None = None,
    relation: str | None = None,
    frequency_eligible: bool | None = None,
) -> dict:
    value = {
        "candidateId": identifier,
        "candidateKind": "choice_proposition",
        "rawQuestionId": identifier.rsplit(":choice", 1)[0],
        "examYear": year,
        "questionNumber": number,
        "choiceLabel": identifier.rsplit(":", 1)[-1],
        "statementText": statement,
        "sourceCitation": {
            "title": f"{year}年－問{number}",
            "sourceUrl": f"https://example.test/{year}/{number}",
            "labels": labels or ["行政法", "行政手続法"],
        },
        "providerExplanation": "PRIVATE-PROVIDER-TEXT-MUST-NOT-BE-PROJECTED",
    }
    if subject is not None:
        value["subjectId"] = subject
    if category is not None:
        value["category"] = category
    if topic is not None:
        value["topic"] = topic
    if relation is not None:
        value["relation"] = relation
    if frequency_eligible is not None:
        value["frequencyEligible"] = frequency_eligible
    return value


class LearningIndexTests(unittest.TestCase):
    def test_normalizes_subject_hierarchy_and_character_features(self) -> None:
        self.assertEqual("行政 手続法", normalize_dimension(" 行政　手続法\n"))
        self.assertEqual(
            "administrative-law", normalize_subject_id("Administrative_Law")
        )
        self.assertEqual("civil-law", normalize_subject_id(" 民　法 "))
        self.assertEqual(
            {"行政", "政手", "手続", "続法"}, set(character_ngrams("行政 手続法", 2))
        )
        self.assertIn("取消訴訟", extract_legal_terms("処分の取消訴訟を提起する。"))

    def test_builds_separate_rankings_and_deduplicates_frequency_questions(
        self,
    ) -> None:
        cards = [
            card("admin-card"),
            card(
                "constitution-card",
                subject="憲法",
                category="憲法",
                topic="基本的人権",
                subtopic="表現の自由",
                statement="表現の自由は憲法上保障される。",
            ),
        ]
        candidates = [
            candidate(
                "q-2025-10:choice:1",
                2025,
                10,
                "弁明の機会の付与を受ける当事者は代理人を選任できる。",
                frequency_eligible=True,
            ),
            candidate(
                "q-2025-10:choice:2",
                2025,
                10,
                "弁明では、本人に代わる代理人を選ぶことが認められる。",
                frequency_eligible=True,
            ),
            candidate(
                "q-2024-11:choice:1",
                2024,
                11,
                "行政手続法の弁明でも代理人を選任できる。",
                relation="same-topic",
                frequency_eligible=True,
            ),
            candidate(
                "q-2023-3:choice:1",
                2023,
                3,
                "代理人は本人に代わって意思表示を行うことができる。",
                labels=["民法", "代理"],
                subject="civil_law",
                category="民法",
                topic="代理",
            ),
            candidate(
                "q-2022-1:choice:1",
                2022,
                1,
                "表現の自由は憲法上の基本的人権として保障される。",
                labels=["憲法", "基本的人権"],
            ),
        ]

        result = build_learning_index(
            cards,
            candidates,
            min_score=0.08,
            generated_at="2026-07-19T00:00:00Z",
        )
        self.assertEqual(2, result["summary"]["cardCount"])
        self.assertEqual(2, result["summary"]["subjectCount"])
        self.assertEqual(
            ["administrative-law", "constitutional-law"],
            [row["subjectId"] for row in result["subjects"]],
        )

        admin = next(item for item in result["cards"] if item["cardId"] == "admin-card")
        same_ids = [item["candidateId"] for item in admin["rankings"]["sameField"]]
        cross_ids = [item["candidateId"] for item in admin["rankings"]["crossField"]]
        self.assertIn("q-2025-10:choice:1", same_ids)
        self.assertIn("q-2025-10:choice:2", same_ids)
        self.assertIn("q-2024-11:choice:1", same_ids)
        self.assertIn("q-2023-3:choice:1", cross_ids)

        # Two matching choices from the same examination question count once.
        self.assertEqual(1, admin["frequency"]["questionCount"])
        self.assertEqual(
            [{"examYear": 2025, "questionNumber": 10}],
            admin["frequency"]["questions"],
        )
        # A relation whose only evidence is same_topic is ranked but not added.
        self.assertEqual(1, admin["frequency"]["excludedSameTopicOnlyQuestionCount"])
        self.assertFalse(result["rankingPolicy"]["sameTopicOnlyAutoFrequency"])

        topic_index = result["fieldIndex"]["byTopic"]
        self.assertIn("administrative-law::行政手続法", topic_index)
        self.assertEqual(
            ["admin-card"], topic_index["administrative-law::行政手続法"]["cardIds"]
        )

    def test_private_explanation_candidate_is_ranked_without_frequency_or_leaks(
        self,
    ) -> None:
        private_candidate = candidate(
            "q-2025-10:provider-explanation:ア",
            2025,
            10,
            "弁明の機会の付与を受ける当事者は代理人を選任できる。",
            subject="administrative_law",
        )
        private_candidate.update(
            {
                "candidateKind": "provider_explanation_proposition",
                "frequencyEligible": False,
                "providerExplanationParagraphs": [
                    "PRIVATE-PROVIDER-NARRATIVE-MUST-NOT-BE-PROJECTED"
                ],
                "questionContext": {
                    "prompt": "PRIVATE-PROMPT-MUST-NOT-BE-PROJECTED"
                },
                "sourceInput": "/private/internal/path/must-not-be-projected.json",
            }
        )

        result = build_learning_index(
            [card("admin-card")],
            [private_candidate],
            min_score=0.01,
            generated_at="2026-07-26T00:00:00Z",
        )

        admin = result["cards"][0]
        self.assertEqual(
            ["q-2025-10:provider-explanation:ア"],
            [item["candidateId"] for item in admin["rankings"]["sameField"]],
        )
        self.assertFalse(admin["rankings"]["sameField"][0]["frequencyEligible"])
        self.assertEqual(0, admin["frequency"]["questionCount"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("PRIVATE-PROVIDER-NARRATIVE", serialized)
        self.assertNotIn("PRIVATE-PROMPT", serialized)
        self.assertNotIn("/private/internal/path", serialized)

    def test_same_topic_plus_direct_relation_can_count(self) -> None:
        value = candidate(
            "q-2025-9:choice:1",
            2025,
            9,
            "行政手続法上、弁明では代理人を選任できる。",
        )
        value["relations"] = ["same_topic", "same_rule"]
        value["frequencyEligible"] = True
        result = build_learning_index([card("card-1")], [value], min_score=0.01)
        self.assertEqual(1, result["cards"][0]["frequency"]["questionCount"])

    def test_frequency_is_fail_closed_unless_explicitly_enabled(self) -> None:
        value = candidate(
            "q-2025-9:choice:1",
            2025,
            9,
            "行政手続法上、弁明では代理人を選任できる。",
        )
        result = build_learning_index([card("card-1")], [value], min_score=0.01)

        ranked = result["cards"][0]["rankings"]["sameField"][0]
        self.assertFalse(ranked["frequencyEligible"])
        self.assertEqual(0, result["cards"][0]["frequency"]["questionCount"])
        self.assertFalse(result["rankingPolicy"]["candidateFrequencyDefault"])

    def test_legal_terms_contribute_to_similarity(self) -> None:
        left = card(
            "card-1",
            topic="行政事件訴訟法",
            subtopic="取消訴訟",
            statement="取消訴訟について検討する。",
        )
        right = candidate(
            "q-2025-8:choice:1",
            2025,
            8,
            "処分を争う取消訴訟を提起した。",
            labels=["行政法", "行政事件訴訟法"],
        )
        from gyousei_pipeline.learning_index import normalize_candidate, normalize_card

        signals = similarity_signals(normalize_card(left), normalize_candidate(right))
        self.assertIn("取消訴訟", signals["sharedLegalTerms"])
        self.assertGreater(signals["score"], 0.25)

    def test_rankings_are_bounded_instead_of_exporting_all_pairs(self) -> None:
        cards = [card(f"card-{index}") for index in range(12)]
        candidates = [
            candidate(
                f"q-2025-{index}:choice:1",
                2025,
                index,
                f"行政手続法の弁明では代理人を選任できる。付記{index}",
            )
            for index in range(1, 31)
        ]
        result = build_learning_index(
            cards,
            candidates,
            same_field_limit=3,
            cross_field_limit=2,
            min_score=0.01,
        )
        self.assertTrue(
            all(len(item["rankings"]["sameField"]) <= 3 for item in result["cards"])
        )
        self.assertEqual(
            "bounded_per_card_via_inverted_index",
            result["rankingPolicy"]["pairMaterialization"],
        )
        self.assertNotIn("pairs", result)

    def test_exact_duplicate_inputs_are_collapsed_but_conflicts_fail(self) -> None:
        original = card("card-1")
        result = build_learning_index([original, dict(original)], [])
        self.assertEqual(1, result["summary"]["cardCount"])

        conflict = card("card-1", statement="別の問題文")
        with self.assertRaisesRegex(LearningIndexError, "conflicting duplicate cardId"):
            build_learning_index([original, conflict], [])

    def test_private_writer_atomically_replaces_with_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "index.json"
            output.parent.mkdir()
            output.write_text("old", encoding="utf-8")
            os.chmod(output, 0o644)
            value = {"schemaVersion": "test", "private": True}
            atomic_write_private_json(output, value)
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual(value, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual([], list(output.parent.glob(f".{output.name}.*")))

    def test_cli_accepts_multiple_card_and_candidate_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cards_one = root / "cards-one.json"
            cards_two = root / "cards-two.json"
            candidates_one = root / "candidates-one.json"
            candidates_two = root / "candidates-two.json"
            output = root / "private" / "learning-index.json"

            cards_one.write_text(
                json.dumps({"items": [card("admin-card")]}, ensure_ascii=False),
                encoding="utf-8",
            )
            cards_two.write_text(
                json.dumps(
                    [
                        card(
                            "civil-card",
                            subject="民法",
                            category="民法",
                            topic="代理",
                            subtopic="代理権",
                            statement="代理権の範囲を確認する。",
                        )
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            candidates_one.write_text(
                json.dumps(
                    {
                        "candidates": [
                            candidate(
                                "q-2025-10:choice:1",
                                2025,
                                10,
                                "弁明では代理人を選任できる。",
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            other = candidate(
                "q-2024-3:choice:1",
                2024,
                3,
                "代理権がある者は本人に代わり意思表示をする。",
                labels=["民法", "代理"],
                subject="civil-law",
                category="民法",
                topic="代理",
            )
            candidates_two.write_text(
                json.dumps([other], ensure_ascii=False), encoding="utf-8"
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(
                    [
                        "--cards",
                        str(cards_one),
                        str(cards_two),
                        "--review-candidates",
                        str(candidates_one),
                        "--review-candidates",
                        str(candidates_two),
                        "--output",
                        str(output),
                        "--min-score",
                        "0.01",
                        "--generated-at",
                        "2026-07-19T00:00:00Z",
                    ]
                )

            self.assertEqual(0, status, stderr.getvalue())
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(2, document["summary"]["cardCount"])
            self.assertEqual(2, document["summary"]["candidateCount"])
            self.assertEqual("2026-07-19T00:00:00Z", document["generatedAt"])
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertIn(str(output), stdout.getvalue())
            self.assertNotIn(
                "PRIVATE-PROVIDER-TEXT-MUST-NOT-BE-PROJECTED",
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
