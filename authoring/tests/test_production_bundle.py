from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.production_bundle import (
    BundleExpectations,
    ProductionBundleError,
    build_production_bundle,
    expectations_from_question_manifest,
    resolve_expectations,
    write_private_bundle,
)


def raw_question(question_id: str, number: int, question_format: str) -> dict:
    record = {
        "schemaVersion": "raw-question@1",
        "parserVersion": "parser@test",
        "rawQuestionId": question_id,
        "sourceSnapshotId": f"snapshot:{question_id}",
        "sourceBodySha256": "a" * 64,
        "sourceId": "provider",
        "externalQuestionId": str(number),
        "sourceUrl": f"https://example.test/questions/{number}",
        "examYear": 2025,
        "eraYear": "令和7年",
        "questionNumber": number,
        "title": f"令和7年－問{number}",
        "labels": ["行政法", "行政手続法"],
        "listingKind": question_format,
        "isAmended": False,
        "providerUpdatedAt": "2026-07-18T00:00:00+09:00",
        "explanationCaptured": False,
        "extraction": {"status": "parsed", "warnings": []},
        "providerExplanation": f"QUESTION-SECRET-{question_id}",
    }
    if question_format == "regular":
        record.update(
            {
                "instructionText": "正しいものはどれか。",
                "questionText": "行政庁の手続について答えなさい。",
                "choices": [
                    {
                        "label": "1",
                        "text": "理由を示す。",
                        "cells": [{"column": "ア", "text": "理由を示す"}],
                        "providerExplanation": "CHOICE-SECRET",
                    }
                ],
                "choiceFormat": "table",
                "choiceColumns": ["ア"],
                "task": {
                    "kind": "single_choice",
                    "prompt": "正しいものはどれか。",
                    "confidence": "high",
                },
                "answer": {"kind": "option", "value": 1},
            }
        )
    elif question_format == "multiple_blank":
        record.update(
            {
                "instructionText": "空欄を埋めなさい。",
                "passageText": "［ ア ］を選ぶ。",
                "sourceNote": "",
                "blanks": ["ア"],
                "wordBank": [{"number": 1, "text": "行政庁"}],
                "task": {
                    "kind": "fill_four_blanks",
                    "prompt": "空欄を埋めなさい。",
                    "confidence": "high",
                },
                "answer": {"kind": "blank_numbers", "values": {"ア": 1}},
            }
        )
    else:
        record.update(
            {
                "questionText": "40字程度で記述しなさい。",
                "referenceText": "",
                "characterLimit": 40,
                "characterLimitKind": "approximately",
                "modelAnswer": "行政庁が理由を示す。",
                "modelAnswerRaw": "WRITTEN-RAW-SECRET",
                "modelAnswerCharacterCount": 10,
                "task": {
                    "kind": "written_response",
                    "prompt": "40字程度で記述しなさい。",
                    "confidence": "high",
                },
                "answer": {"kind": "model_answer", "value": "行政庁が理由を示す。"},
            }
        )
    return record


def reconciliation(questions: list[dict]) -> dict:
    results = []
    for question in questions:
        answer = question["answer"].get("value", question["answer"].get("values"))
        results.append(
            {
                "rawQuestionId": question["rawQuestionId"],
                "examYear": question["examYear"],
                "questionNumber": question["questionNumber"],
                "format": question["listingKind"],
                "providerAnswer": answer,
                "officialAnswer": answer,
                "status": "exact",
                "reason": "test_exact",
                "providerExplanation": "RECONCILIATION-SECRET",
            }
        )
    return {
        "schemaVersion": "answer-reconciliation@1",
        "summary": {
            "total": len(results),
            "statusCounts": {
                "exact": len(results),
                "match-after-normalization": 0,
                "mismatch": 0,
                "unavailable": 0,
                "unsupported": 0,
            },
        },
        "results": results,
    }


def explanation_cards() -> dict:
    return {
        "schemaVersion": "0.4-prototype",
        "meta": {
            "examLawAsOf": "2026-04-01",
            "targetExam": "令和8年度行政書士試験",
            "subjects": [{"id": "administrative-law", "label": "行政法"}],
        },
        "studyDecks": [
            {
                "id": "deck-1",
                "title": "行政法1問",
                "description": "投影テスト用のprivate deckです。",
                "visibility": "private",
                "lawAsOf": "2026-04-01",
                "cardIds": ["card-1"],
            }
        ],
        "items": [
            {
                "id": "card-1",
                "subjectId": "administrative-law",
                "category": "行政法",
                "topic": "行政手続法",
                "subtopic": "理由提示",
                "clusterId": "reason",
                "variants": {
                    "a": "行政庁は理由を示す。",
                    "b": "行政から理由を教えてもらえます。",
                    "bCasual": "『理由提示』は、なぜそうなったかを教えることです。",
                    "bCasualStyle": "用語からほどく",
                    "c": "処分 → 理由提示",
                },
                "correct": True,
                "correction": "理由を示します。",
                "memoryPoint": "理由を確認する。",
                "frequency": {
                    "label": "頻出",
                    "occurrences": 7,
                    "yearCount": 6,
                    "recentOccurrences": 4,
                    "archiveOccurrences": 3,
                    "scope": "平成18年度〜令和7年度",
                    "basis": "問題単位で集計",
                },
                "explanations": {
                    "normal": "法律が理由提示を求めています。",
                    "deepDive": {
                        "background": "判断の確認に必要です。",
                        "trap": "例外と混同しません。",
                        "example": "申請を断られた場面です。",
                    },
                    "commonSense": "理由の分からない拒否では困ります。",
                },
                "legalBasis": [
                    {"label": "行政手続法", "url": "https://laws.example.test/1"}
                ],
                "sourceRefs": [
                    {
                        "rawId": "official-test-q1",
                        "choiceNumber": "オ",
                        "relationship": "direct-topic",
                    }
                ],
                "relatedPastQuestions": [
                    {"choiceId": "official-test-q1-co", "relation": "same_rule"}
                ],
                "crossFieldComparisons": [
                    {
                        "id": "reason-vs-litigation",
                        "comparedCategory": "行政法",
                        "comparedTopic": "行政事件訴訟法",
                        "title": "申請段階と訴訟段階を分ける",
                        "explanation": "理由提示は行政の判断時、訴訟は判断後の救済です。",
                        "memoryCue": "いつの場面かを見る",
                    }
                ],
                "comparisonTable": {
                    "title": "書面に不備があったとき",
                    "rows": [
                        {
                            "label": "行政手続法",
                            "article": "7条",
                            "rule": "補正を求めるか拒否するかを選ぶ。",
                            "conclusion": "補正させなくてよい",
                        },
                        {
                            "label": "行政不服審査法",
                            "article": "23条",
                            "rule": "審査庁は補正を命じる。",
                            "conclusion": "補正命令が義務",
                        },
                    ],
                    "memoryCue": "入口が最後かどうかで分ける",
                },
                "review": {
                    "currentLawStatus": "sample-reviewed",
                    "humanReview": "prototype",
                    "reviewed": True,
                    "publishable": False,
                },
                "derivedFromWritten": {
                    "questionId": "q:written",
                    "label": "令和7年度 問3",
                    "promptSummary": "理由提示について答える記述式です。",
                    "officialQuestionUrl": "https://official.example.test/question.pdf",
                },
                "reviewed": False,
                "publishable": False,
                "providerExplanation": "CARD-SECRET",
            }
        ],
    }


def related_question_source() -> dict:
    return {
        "schemaVersion": "0.2-prototype",
        "notice": "RELATED-NOTICE-SECRET",
        "records": [
            {
                "rawId": "official-test-q1",
                "examYear": 2025,
                "eraYear": "令和7年",
                "questionNumber": 1,
                "officialQuestionUrl": "https://official.example.test/question.pdf",
                "providerExplanation": "RELATED-RECORD-SECRET",
            }
        ],
        "choices": [
            {
                "choiceId": "official-test-q1-co",
                "rawQuestionId": "official-test-q1",
                "choiceLabel": "オ",
                "officialOriginalText": "行政庁は理由を示さなければならない。",
                "contextSummary": "申請を拒否された場面です。",
                "scopeLabel": "申請拒否",
                "examEvaluation": True,
                "currentEvaluation": True,
                "currentLawAsOf": "2026-07-17",
                "textVersion": "official_original",
                "isModified": False,
                "verification": "official_pdf_and_answer",
                "providerExplanation": "RELATED-CHOICE-SECRET",
            }
        ],
    }


def claude_response() -> dict:
    return {
        "schemaVersion": "ai-legal-review-response@2",
        "batchId": "batch-1",
        "legalAsOf": "2026-07-18",
        "prompt": "CLAUDE-RESPONSE-PROMPT-SECRET",
        "items": [
            {
                "candidateId": "candidate-1",
                "currentLawStatus": "confirmed",
                "currentTruth": True,
                "legalReviewStatus": "ai_candidate",
                "relationNotes": ["条文と一致します。"],
                "citationCandidates": [
                    {
                        "citationType": "statute",
                        "title": "行政手続法",
                        "url": "https://laws.example.test/1",
                        "locator": "第1条",
                        "relevance": "直接の根拠です。",
                    }
                ],
                "risks": ["人手確認前です。"],
                "reviewed": False,
                "publishable": False,
                "providerExplanation": "CLAUDE-ITEM-SECRET",
            }
        ],
    }


def claude_run() -> dict:
    return {
        "schemaVersion": "claude-fable-review-run@2",
        "runId": "run-1",
        "batchId": "batch-1",
        "batchPath": "/private/BATCH-PATH-SECRET",
        "itemCount": 1,
        "targetLegalAsOf": "2026-07-18",
        "status": "completed",
        "startedAt": "2026-07-18T00:00:00Z",
        "finishedAt": "2026-07-18T00:00:01Z",
        "elapsedSeconds": 1.0,
        "prompt": "CLAUDE-RUN-PROMPT-SECRET",
        "command": {
            "modelRequested": "fable",
            "effort": "high",
            "safeMode": True,
            "sessionPersistence": False,
            "tools": ["WebSearch", "WebFetch"],
            "allowedTools": ["WebSearch", "WebFetch"],
            "maxBudgetUsd": 1.0,
        },
        "process": {
            "returnCode": 0,
            "stdoutSha256": "b" * 64,
            "stdoutBytes": 100,
            "stderrSha256": "c" * 64,
            "stderrBytes": 0,
            "stdout": "CLAUDE-STDOUT-SECRET",
        },
        "claude": {
            "subtype": "success",
            "isError": False,
            "durationMs": 900,
            "durationApiMs": 800,
            "numTurns": 1,
            "totalCostUsd": 0.1,
            "terminalReason": "completed",
            "permissionDenialCount": 0,
            "modelUsage": {"claude-fable-5": {"outputTokens": 10}},
        },
        "streamEvidence": {
            "eventCount": 2,
            "toolUseCount": 1,
            "webToolUseCount": 1,
            "successfulToolResultCount": 1,
        },
        "response": {
            "path": "/private/CLAUDE-RESPONSE-PATH-SECRET.json",
            "sha256": "d" * 64,
            "itemCount": 1,
        },
    }


def similarity() -> dict:
    member = {
        "candidateId": "candidate-1",
        "rawQuestionId": "q:regular",
        "examYear": 2025,
        "questionNumber": 1,
        "choiceLabel": "1",
        "inferredTruth": True,
        "statementText": "行政庁は理由を示す。",
    }
    return {
        "schemaVersion": "similarity-candidates@1",
        "reviewPairs": [
            {
                "pairId": "candidate-1--candidate-2",
                "left": member,
                "right": {
                    **member,
                    "candidateId": "candidate-2",
                    "rawQuestionId": "q:multiple",
                    "questionNumber": 2,
                    "statementText": "行政庁は処分の理由を伝える。",
                },
                "commonLabels": ["行政手続法"],
                "sharedLegalConcepts": ["理由提示"],
                "score": 0.8,
                "reviewScore": 0.7,
                "scoreBreakdown": {
                    "characterBigramIdfJaccard": 0.8,
                    "characterTrigramJaccard": 0.7,
                    "legalConceptIdfJaccard": 1.0,
                    "sharedLegalConceptRarity": 0.5,
                    "legalConceptRarityAdjustedJaccard": 0.5,
                },
                "tier": "strict",
                "method": "character_trigram_jaccard",
                "reasonCodes": ["character_ngram_overlap"],
                "reasonSummary": "文字と論点が重なります。",
                "reviewed": True,
                "publishable": False,
                "selectedByCandidateIds": [],
                "neighborRanks": {},
                "providerExplanation": "SIMILARITY-SECRET",
            }
        ],
    }


class ProductionBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = [
            raw_question("q:regular", 1, "regular"),
            raw_question("q:multiple", 2, "multiple_blank"),
            raw_question("q:written", 3, "written"),
        ]
        self.expectations = BundleExpectations(
            question_count=3,
            explanation_card_count=1,
            related_question_evidence_count=1,
            claude_review_count=1,
            claude_run_count=1,
            similarity_pair_count=1,
            target_years=None,
        )

    def build(self, **overrides) -> dict:
        expectations = overrides.pop("expectations", self.expectations)
        values = {
            "questions": self.questions,
            "reconciliation": reconciliation(self.questions),
            "explanation_cards": explanation_cards(),
            "related_question_source": related_question_source(),
            "claude_responses": [claude_response()],
            "claude_runs": [claude_run()],
            "similarity": similarity(),
        }
        values.update(overrides)
        return build_production_bundle(
            **values,
            generated_at="2026-07-18T00:00:00Z",
            expectations=expectations,
        )

    def test_projects_all_api_sections_and_preserves_display_shapes(self) -> None:
        bundle = self.build()
        self.assertEqual("gyousei-production-bundle@1", bundle["schemaVersion"])
        self.assertEqual("private_not_for_web", bundle["visibility"])
        self.assertEqual("2026-04-01", bundle["legalAsOf"])
        self.assertEqual(3, bundle["summary"]["questionCount"])
        self.assertEqual(
            {"administrative-law": 3}, bundle["summary"]["questionSubjectCounts"]
        )
        self.assertEqual(1, bundle["summary"]["studyDeckCount"])
        self.assertEqual(1, bundle["summary"]["relatedQuestionEvidenceCount"])

        regular = bundle["questions"][0]
        self.assertEqual("administrative-law", regular["subjectId"])
        self.assertEqual("行政庁の手続について答えなさい。", regular["content"]["question"])
        self.assertEqual(["ア"], regular["content"]["choiceColumns"])
        self.assertEqual(
            [{"column": "ア", "text": "理由を示す"}],
            regular["content"]["choices"][0]["cells"],
        )
        self.assertEqual("行政庁", bundle["questions"][1]["content"]["wordBank"][0]["text"])
        self.assertEqual(
            "行政庁が理由を示す。",
            bundle["questions"][2]["content"]["modelAnswer"],
        )

    def test_raw_subject_ids_are_canonicalized_without_mutating_inputs(self) -> None:
        questions = copy.deepcopy(self.questions)
        raw_ids = ["administrative_law", "civil_law", "basic_knowledge"]
        for question, raw_id in zip(questions, raw_ids, strict=True):
            question["subjectId"] = raw_id
        questions[1]["providerUpdatedAt"] = None

        bundle = self.build(
            questions=questions,
            reconciliation=reconciliation(questions),
        )

        self.assertEqual(raw_ids, [item["subjectId"] for item in questions])
        self.assertEqual(
            ["administrative-law", "civil-law", "general-knowledge"],
            [item["subjectId"] for item in bundle["questions"]],
        )
        self.assertIsNone(bundle["questions"][1]["source"]["providerUpdatedAt"])

        deck = bundle["studyDecks"][0]
        self.assertEqual("deck-1", deck["id"])
        self.assertEqual("private", deck["visibility"])
        self.assertEqual("2026-04-01", deck["lawAsOf"])
        self.assertEqual(1, deck["cardCount"])
        self.assertEqual(["card-1"], deck["cardIds"])
        self.assertEqual(["administrative-law"], deck["subjectIds"])

        card = bundle["explanationCards"][0]
        self.assertEqual("administrative-law", card["subjectId"])
        self.assertNotIn("visual", card)
        self.assertEqual(
            {"a", "b", "bCasual", "bCasualStyle", "c"},
            set(card["variants"]),
        )
        self.assertEqual(
            "reason-vs-litigation", card["crossFieldComparisons"][0]["id"]
        )
        table = card["comparisonTable"]
        self.assertEqual("書面に不備があったとき", table["title"])
        self.assertEqual(
            ["行政手続法", "行政不服審査法"], [row["label"] for row in table["rows"]]
        )
        self.assertEqual("補正命令が義務", table["rows"][1]["conclusion"])
        self.assertEqual(7, card["frequency"]["occurrences"])
        self.assertEqual(3, card["frequency"]["archiveOccurrences"])
        self.assertEqual("q:written", card["derivedFromWritten"]["questionId"])

        evidence = bundle["relatedQuestionEvidence"][0]
        self.assertEqual("official-test-q1-co", evidence["choiceId"])
        self.assertEqual("オ", evidence["choiceNumber"])
        self.assertTrue(evidence["historicalTruth"])
        self.assertEqual(
            "行政庁は理由を示さなければならない。", evidence["statementText"]
        )
        self.assertTrue(card["review"]["reviewed"])
        self.assertFalse(card["review"]["publishable"])
        self.assertFalse(bundle["claudeReviews"][0]["reviewed"])
        self.assertFalse(bundle["claudeReviews"][0]["publishable"])
        self.assertTrue(bundle["similarityPairs"][0]["reviewed"])
        self.assertFalse(bundle["similarityPairs"][0]["publishable"])
        self.assertRegex(
            bundle["similarityPairs"][0]["pairContentDigest"], r"^[0-9a-f]{64}$"
        )

    def test_combination_format_and_evidence_highlights_reach_the_screen(self) -> None:
        cards = explanation_cards()
        cards["items"][0]["evidenceHighlights"] = ["理由を示す", "申請"]
        source = related_question_source()
        source["records"][0]["questionFormat"] = "combination"

        bundle = self.build(explanation_cards=cards, related_question_source=source)

        card = bundle["explanationCards"][0]
        self.assertEqual(["理由を示す", "申請"], card["evidenceHighlights"])
        self.assertEqual(
            "combination", bundle["relatedQuestionEvidence"][0]["questionFormat"]
        )

    def test_optional_display_fields_stay_absent_when_not_authored(self) -> None:
        bundle = self.build()
        self.assertNotIn("evidenceHighlights", bundle["explanationCards"][0])
        self.assertNotIn("questionFormat", bundle["relatedQuestionEvidence"][0])

    def test_whitelist_excludes_provider_explanations_prompts_and_private_paths(self) -> None:
        bundle = self.build()
        serialized = json.dumps(bundle, ensure_ascii=False)
        for secret in (
            "QUESTION-SECRET",
            "CHOICE-SECRET",
            "WRITTEN-RAW-SECRET",
            "RECONCILIATION-SECRET",
            "CARD-SECRET",
            "RELATED-NOTICE-SECRET",
            "RELATED-RECORD-SECRET",
            "RELATED-CHOICE-SECRET",
            "CLAUDE-RESPONSE-PROMPT-SECRET",
            "CLAUDE-ITEM-SECRET",
            "CLAUDE-RUN-PROMPT-SECRET",
            "CLAUDE-STDOUT-SECRET",
            "CLAUDE-RESPONSE-PATH-SECRET",
            "BATCH-PATH-SECRET",
            "SIMILARITY-SECRET",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(
            {
                "runId",
                "batchId",
                "itemCount",
                "targetLegalAsOf",
                "status",
                "startedAt",
                "finishedAt",
                "elapsedSeconds",
                "modelRequested",
                "models",
                "errorKind",
            },
            set(bundle["claudeRuns"][0]),
        )

    def test_failed_and_rate_limited_runs_are_included_as_safe_summaries(self) -> None:
        limited = claude_run()
        limited.update(
            {
                "runId": "run-rate-limit",
                "batchId": "batch-without-response",
                "status": "rate_limited",
                "response": None,
                "error": {"kind": "rate_limit", "message": "PRIVATE-ERROR-MESSAGE"},
            }
        )
        limited["process"]["returnCode"] = 1
        limited["claude"]["isError"] = True
        limited["claude"]["terminalReason"] = "api_error"
        expectations = BundleExpectations(
            question_count=3,
            explanation_card_count=1,
            related_question_evidence_count=1,
            claude_review_count=1,
            claude_run_count=2,
            similarity_pair_count=1,
            target_years=None,
        )
        bundle = self.build(
            claude_runs=[claude_run(), limited], expectations=expectations
        )
        self.assertEqual(
            {"completed": 1, "rate_limited": 1},
            bundle["summary"]["claudeRunStatusCounts"],
        )
        summary = next(
            run for run in bundle["claudeRuns"] if run["status"] == "rate_limited"
        )
        self.assertEqual("rate_limit", summary["errorKind"])
        self.assertNotIn("PRIVATE-ERROR-MESSAGE", json.dumps(summary, ensure_ascii=False))

    def test_pair_digest_changes_when_displayed_pair_content_changes(self) -> None:
        first = self.build()["similarityPairs"][0]["pairContentDigest"]
        changed = similarity()
        changed["reviewPairs"][0]["right"]["statementText"] += "ただし例外がある。"
        second = self.build(similarity=changed)["similarityPairs"][0][
            "pairContentDigest"
        ]
        self.assertNotEqual(first, second)

    def test_counts_and_review_gate_types_fail_closed(self) -> None:
        with self.assertRaisesRegex(ProductionBundleError, "expected 3 questions"):
            self.build(questions=self.questions[:2])

        subject_expectations = BundleExpectations(
            question_count=3,
            question_subject_counts=(
                ("administrative-law", 2),
                ("civil-law", 1),
            ),
            explanation_card_count=1,
            related_question_evidence_count=1,
            claude_review_count=1,
            claude_run_count=1,
            similarity_pair_count=1,
            target_years=None,
        )
        with self.assertRaisesRegex(ProductionBundleError, "subject counts differ"):
            self.build(expectations=subject_expectations)

        format_expectations = BundleExpectations(
            question_count=3,
            question_format_counts=(("regular", 3),),
            explanation_card_count=1,
            related_question_evidence_count=1,
            claude_review_count=1,
            claude_run_count=1,
            similarity_pair_count=1,
            target_years=None,
        )
        with self.assertRaisesRegex(ProductionBundleError, "format counts differ"):
            self.build(expectations=format_expectations)

        unsafe = similarity()
        unsafe["reviewPairs"][0]["publishable"] = "false"
        with self.assertRaisesRegex(ProductionBundleError, "publishable must be a boolean"):
            self.build(similarity=unsafe)

    def test_all_subject_question_manifest_is_canonical_and_fail_closed(self) -> None:
        manifest = {
            "schemaVersion": "all-subjects-target@1",
            "target": {
                "examYears": [2025],
                "expectedTotal": 3,
                "expectedByFormat": {
                    "regular": 1,
                    "multiple_blank": 1,
                    "written": 1,
                },
                "expectedBySubject": {
                    "administrative_law": 1,
                    "civil_law": 1,
                    "basic_knowledge": 1,
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            expectations = expectations_from_question_manifest(path)

            self.assertEqual(3, expectations.question_count)
            self.assertEqual((2025,), expectations.target_years)
            self.assertEqual(
                (
                    ("administrative-law", 1),
                    ("civil-law", 1),
                    ("general-knowledge", 1),
                ),
                expectations.question_subject_counts,
            )

            manifest["target"]["expectedTotal"] = 4
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                ProductionBundleError, "subject counts do not match"
            ):
                expectations_from_question_manifest(path)

    def test_study_deck_membership_cross_fields_and_written_origins_fail_closed(self) -> None:
        unknown_card = explanation_cards()
        unknown_card["studyDecks"][0]["cardIds"] = ["card-missing"]
        with self.assertRaisesRegex(ProductionBundleError, "unknown cards"):
            self.build(explanation_cards=unknown_card)

        public_deck = explanation_cards()
        public_deck["studyDecks"][0]["visibility"] = "public"
        with self.assertRaisesRegex(ProductionBundleError, "visibility must be 'private'"):
            self.build(explanation_cards=public_deck)

        unexpected_variant = explanation_cards()
        unexpected_variant["items"][0]["variants"]["extra"] = "未定義"
        with self.assertRaisesRegex(ProductionBundleError, "supported fields"):
            self.build(explanation_cards=unexpected_variant)

        unknown_cross_card = explanation_cards()
        unknown_cross_card["items"][0]["crossFieldComparisons"][0][
            "relatedCardId"
        ] = "card-missing"
        with self.assertRaisesRegex(ProductionBundleError, "unknown cards"):
            self.build(explanation_cards=unknown_cross_card)

        non_written_origin = explanation_cards()
        non_written_origin["items"][0]["derivedFromWritten"]["questionId"] = "q:regular"
        with self.assertRaisesRegex(ProductionBundleError, "origin is not a written"):
            self.build(explanation_cards=non_written_origin)

    def test_comparison_table_fails_closed(self) -> None:
        single_row = explanation_cards()
        single_row["items"][0]["comparisonTable"]["rows"] = [
            single_row["items"][0]["comparisonTable"]["rows"][0]
        ]
        with self.assertRaisesRegex(ProductionBundleError, "2 to 4 rows"):
            self.build(explanation_cards=single_row)

        repeated_label = explanation_cards()
        rows = repeated_label["items"][0]["comparisonTable"]["rows"]
        rows[1]["label"] = rows[0]["label"]
        with self.assertRaisesRegex(ProductionBundleError, "must not repeat a label"):
            self.build(explanation_cards=repeated_label)

        missing_conclusion = explanation_cards()
        del missing_conclusion["items"][0]["comparisonTable"]["rows"][0]["conclusion"]
        with self.assertRaisesRegex(ProductionBundleError, "conclusion must be"):
            self.build(explanation_cards=missing_conclusion)

        no_table = explanation_cards()
        del no_table["items"][0]["comparisonTable"]
        bundle = self.build(explanation_cards=no_table)
        self.assertNotIn("comparisonTable", bundle["explanationCards"][0])

    def test_one_private_deck_can_cover_multiple_subjects(self) -> None:
        document = explanation_cards()
        document["meta"]["subjects"].append({"id": "civil-law", "label": "民法"})
        civil_card = copy.deepcopy(document["items"][0])
        civil_card.update(
            {
                "id": "card-civil-1",
                "subjectId": "civil-law",
                "category": "民法",
                "topic": "代理",
                "subtopic": "代理権",
                "clusterId": "civil-agency",
            }
        )
        civil_card["crossFieldComparisons"] = []
        civil_card.pop("derivedFromWritten")
        document["items"].append(civil_card)
        document["studyDecks"][0]["cardIds"].append("card-civil-1")
        expectations = BundleExpectations(
            question_count=3,
            explanation_card_count=2,
            related_question_evidence_count=1,
            claude_review_count=1,
            claude_run_count=1,
            similarity_pair_count=1,
            target_years=None,
        )

        bundle = self.build(explanation_cards=document, expectations=expectations)

        self.assertEqual(
            ["administrative-law", "civil-law"], bundle["studyDecks"][0]["subjectIds"]
        )
        self.assertEqual(
            [
                {"id": "administrative-law", "label": "行政法"},
                {"id": "civil-law", "label": "民法"},
            ],
            bundle["subjects"],
        )

    def test_fixed_timestamp_build_is_deterministic(self) -> None:
        self.assertEqual(self.build(), self.build())

    def test_reconciliation_must_cover_the_same_question_ids(self) -> None:
        report = reconciliation(self.questions)
        report["results"][0]["rawQuestionId"] = "q:unknown"
        with self.assertRaisesRegex(ProductionBundleError, "unknown question"):
            self.build(reconciliation=report)

    def test_resolve_expectations_defaults_are_unchanged_without_overrides(self) -> None:
        self.assertEqual(BundleExpectations(), resolve_expectations(question_manifest=None))

    def test_resolve_expectations_overrides_only_the_three_targeted_fields(self) -> None:
        expectations = resolve_expectations(
            question_manifest=None,
            expected_card_count=10,
            expected_evidence_count=20,
            expected_similarity_count=30,
        )
        self.assertEqual(10, expectations.explanation_card_count)
        self.assertEqual(20, expectations.related_question_evidence_count)
        self.assertEqual(30, expectations.similarity_pair_count)
        # Everything else stays at the fixed default -- overrides are additive,
        # not a replacement of the whole expectations object.
        defaults = BundleExpectations()
        self.assertEqual(defaults.question_count, expectations.question_count)
        self.assertEqual(defaults.study_deck_count, expectations.study_deck_count)
        self.assertEqual(defaults.claude_review_count, expectations.claude_review_count)
        self.assertEqual(defaults.claude_run_count, expectations.claude_run_count)
        self.assertEqual(defaults.target_years, expectations.target_years)

    def test_resolve_expectations_combines_question_manifest_and_overrides(self) -> None:
        manifest = {
            "schemaVersion": "all-subjects-target@1",
            "target": {
                "examYears": [2025],
                "expectedTotal": 3,
                "expectedByFormat": {
                    "regular": 1,
                    "multiple_blank": 1,
                    "written": 1,
                },
                "expectedBySubject": {
                    "administrative_law": 1,
                    "civil_law": 1,
                    "basic_knowledge": 1,
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            expectations = resolve_expectations(
                question_manifest=path,
                expected_card_count=1,
                expected_evidence_count=1,
                expected_similarity_count=1,
            )
        self.assertEqual(3, expectations.question_count)
        self.assertEqual((2025,), expectations.target_years)
        self.assertEqual(1, expectations.explanation_card_count)
        self.assertEqual(1, expectations.related_question_evidence_count)
        self.assertEqual(1, expectations.similarity_pair_count)

    def test_resolve_expectations_rejects_negative_overrides(self) -> None:
        with self.assertRaisesRegex(
            ProductionBundleError, "--expected-card-count must be non-negative"
        ):
            resolve_expectations(question_manifest=None, expected_card_count=-1)
        with self.assertRaisesRegex(
            ProductionBundleError, "--expected-evidence-count must be non-negative"
        ):
            resolve_expectations(question_manifest=None, expected_evidence_count=-1)
        with self.assertRaisesRegex(
            ProductionBundleError, "--expected-similarity-count must be non-negative"
        ):
            resolve_expectations(question_manifest=None, expected_similarity_count=-1)

    def test_cli_accepts_expected_count_override_flags_and_defaults_to_none(self) -> None:
        from gyousei_pipeline.production_bundle import _parser

        args = _parser().parse_args(
            [
                "--expected-card-count",
                "10",
                "--expected-evidence-count",
                "20",
                "--expected-similarity-count",
                "30",
            ]
        )
        self.assertEqual(10, args.expected_card_count)
        self.assertEqual(20, args.expected_evidence_count)
        self.assertEqual(30, args.expected_similarity_count)

        defaulted = _parser().parse_args([])
        self.assertIsNone(defaulted.expected_card_count)
        self.assertIsNone(defaulted.expected_evidence_count)
        self.assertIsNone(defaulted.expected_similarity_count)

    def test_private_writer_atomically_replaces_with_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "bundle.json"
            output.parent.mkdir()
            output.write_text("old", encoding="utf-8")
            os.chmod(output, 0o644)
            bundle = self.build()
            write_private_bundle(output, bundle)
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual(bundle, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual([], list(output.parent.glob(f".{output.name}.*")))

    def test_pair_digest_definition_is_canonical_pair_identity_and_content(self) -> None:
        pair = self.build()["similarityPairs"][0]
        expected = hashlib.sha256(
            json.dumps(
                {"pairId": pair["id"], "left": pair["left"], "right": pair["right"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, pair["pairContentDigest"])


if __name__ == "__main__":
    unittest.main()
