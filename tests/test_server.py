from __future__ import annotations

import copy
import http.client
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gyousei_production_server",
    SERVICE_ROOT / "server.py",
)
assert SPEC is not None and SPEC.loader is not None
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def fixture_bundle() -> dict:
    return {
        "schemaVersion": "gyousei-production@1",
        "generatedAt": "2026-07-18T12:00:00Z",
        "visibility": "private",
        "legalAsOf": "2026-04-01",
        "summary": {"title": "行政法"},
        "sourceDigests": {"questions": "digest-only"},
        "questions": [
            {
                "id": "q-regular",
                "source": {
                    "exam": "令和7年度",
                    "explanation": "PROVIDER_EXPLANATION_SECRET",
                    "localPath": "/home/yuki/private/source.json",
                },
                "exam": {"year": 2025, "number": 8},
                "title": "通常問題",
                "subjectId": "administrative-law",
                "topic": "行政手続法",
                "labels": ["行政手続法"],
                "format": "regular",
                "content": {"stem": "通常問題の本文"},
                "task": {"kind": "single_choice"},
                "answer": {"correct": 2},
            },
            {
                "id": "q-multiple",
                "source": {"exam": "令和6年度"},
                "exam": {"year": 2024, "number": 9},
                "title": "多肢選択",
                "subjectId": "administrative-law",
                "topic": "行政事件訴訟法",
                "labels": ["行政事件訴訟法"],
                "format": "multiple_blank",
                "content": {"stem": "穴埋め問題の本文"},
                "task": {"blanks": ["ア", "イ"]},
                "answer": {"correct": {"ア": 3, "イ": 1}},
            },
            {
                "id": "q-written",
                "source": {"exam": "令和5年度"},
                "exam": {"year": 2023, "number": 44},
                "title": "記述式",
                "subjectId": "civil-law",
                "topic": "代理",
                "labels": ["行政法"],
                "format": "written",
                "content": {"stem": "記述式問題の本文"},
                "task": {"maxChars": 40},
                "answer": {"model": "模範解答"},
            },
        ],
        "officialAnswerChecks": [
            {"id": "check-1", "questionId": "q-regular", "status": "matched"}
        ],
        "explanationCards": [
            {
                "id": "card-1",
                "questionId": "q-regular",
                "subjectId": "administrative-law",
                "category": "行政法",
                "topic": "行政手続法",
                "subtopic": "聴聞",
                "easy": "やさしい説明",
                "lawAsOf": "2026-04-01",
                "variants": {
                    "a": "代理人を選任することができる。",
                    "b": "自分の代わりに説明する人を頼めます。",
                    "c": "代理人を選べる",
                },
                "correct": True,
                "relatedPastQuestions": [
                    {"choiceId": "choice-related-1"},
                    {"choiceId": "choice-missing"},
                ],
                "reviewPath": "/home/yuki/private/review.json",
            },
            {
                "id": "card-2",
                "questionId": "q-written",
                "subjectId": "civil-law",
                "category": "民法",
                "topic": "代理",
                "subtopic": "代理権",
                "variants": {
                    "a": "書面でのみ申立てができる。",
                    "b": "必ず紙で出す、という意味です。",
                    "c": "申立ては書面のみ",
                },
                "correct": False,
                "relatedPastQuestions": [],
            }
        ],
        "studyDecks": [
            {
                "id": "deck-1",
                "title": "確認用deck",
                "lawAsOf": "2026-04-01",
                "cardIds": ["card-1"],
            }
        ],
        "relatedQuestionEvidence": [
            {
                "choiceId": "choice-related-1",
                "questionId": "q-multiple",
                "statement": "関連する実際の肢",
                "providerExplanation": "PROVIDER_EVIDENCE_SECRET",
            },
            {
                "choiceId": "choice-unreferenced",
                "questionId": "q-written",
                "statement": "このカードからは参照されない",
            },
        ],
        "claudeReviews": [
            {
                "id": "review-1",
                "questionId": "q-regular",
                "verdict": "ok",
                "providerResponse": "RAW_PROVIDER_RESPONSE",
            }
        ],
        "claudeRuns": [
            {
                "id": "run-1",
                "model": "claude-fable",
                "responsePath": "/home/yuki/private/response.json",
            }
        ],
        "similarityPairs": [
            {
                "id": "pair-1",
                "left": {"questionId": "q-regular", "text": "左"},
                "right": {"questionId": "q-multiple", "text": "右"},
                "tier": "review",
                "reasonSummary": "同じ論点",
                "pairContentDigest": "a" * 64,
            }
        ],
    }


def fixture_inventory() -> dict:
    metrics = {
        "questionUnits": 3,
        "regularQuestions": 1,
        "regularChoiceCount": 5,
        "safeOxQuestionCount": 1,
        "safeOxChoiceCount": 5,
        "wholeQuestionQueueCount": 2,
        "multipleBlankQuestions": 1,
        "wordBankEntryCount": 20,
        "blankSlotCount": 4,
        "writtenQuestions": 1,
        "withdrawnQuestionCount": 0,
        "amendedQuestionCount": 0,
        "explanationAvailableCount": 3,
        "explanationUnavailableCount": 0,
    }
    return {
        "schemaVersion": "gyousei-data-inventory@1",
        "architectureVersion": "2026-07-26",
        "generatedAt": "2026-07-26T00:00:00Z",
        "examPlan": {
            "examYear": 2026,
            "examDate": "2026-11-08",
            "lawAsOf": "2026-04-01",
            "officialQuestionPlan": {
                "totalQuestions": 60,
                "legalQuestions": 46,
                "basicKnowledgeQuestions": 14,
            },
            "latestConfirmedDetailedFormat": {
                "examYear": 2025,
                "legalRegularQuestions": 40,
                "legalMultipleBlankQuestions": 3,
                "legalWrittenQuestions": 3,
                "basicKnowledgeRegularQuestions": 14,
                "totalPoints": 300,
            },
            "note": "2026年度の細かな形式別配点は事前確定値として扱わない",
        },
        "coverage": {
            "firstExamYear": 2025,
            "lastExamYear": 2025,
            "yearCount": 1,
            "expectedQuestionUnits": 60,
            "storedQuestionUnits": 3,
            "notStoredQuestionUnits": 57,
            "omissions": [
                {
                    "kind": "publicTextUnavailable",
                    "questionNumbers": [58, 59, 60],
                    "years": 1,
                    "questionUnits": 3,
                    "reason": "著作権上の理由で公開本文に含まれない",
                    "localPath": "/home/yuki/private/omissions.json",
                }
            ],
        },
        "scopes": [
            {
                "id": "current",
                "label": "直近",
                "examYears": [2025],
                "historicalUse": "current_editorial_reference",
                "subjects": [
                    {
                        "subjectId": "administrative_law",
                        "subjectLabel": "行政法",
                        **metrics,
                        "rawQuestionText": "PRIVATE INVENTORY QUESTION",
                    }
                ],
                "totals": metrics,
                "safeOxExclusionReasons": {
                    "format_written_requires_question_level_review": 1,
                    "privateProviderReason": 999,
                },
            }
        ],
        "definitions": {
            "questionUnits": "問番号単位",
            "regularChoiceCount": "通常5肢択一の元の選択肢数",
            "safeOxChoiceCount": "安全に真偽を決められる肢数",
            "wholeQuestionQueueCount": "自動分解しない問題数",
            "wordBankEntryCount": "多肢選択の語群項目数",
            "blankSlotCount": "多肢選択の空欄数",
            "publishedCards": "本番公開済みカード数",
        },
        "privacy": {
            "containsQuestionText": False,
            "containsProviderExplanations": False,
            "containsSourceIdentifiers": False,
            "containsLocalPaths": False,
        },
        "privatePath": "/home/yuki/private/inventory.json",
    }


class ProductionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.bundle_path = self.root / "bundle.json"
        self.inventory_path = self.root / "inventory.json"
        self.database_path = self.root / "state" / "production.sqlite3"
        self.weakness_path = self.root / "state" / "weakness-latest.json"
        self.write_bundle(fixture_bundle())
        self.inventory_path.write_text(
            json.dumps(fixture_inventory(), ensure_ascii=False),
            encoding="utf-8",
        )

        self.original_database_path = server.DB_PATH
        self.original_catalog = server.CATALOG
        self.original_inventory_path = server.INVENTORY_PATH
        self.original_weakness_path = server.WEAKNESS_PATH
        server.DB_PATH = self.database_path
        server.CATALOG = server.BundleCatalog(self.bundle_path)
        server.INVENTORY_PATH = self.inventory_path
        server.WEAKNESS_PATH = self.weakness_path
        server.init_database()

    def tearDown(self) -> None:
        server.DB_PATH = self.original_database_path
        server.CATALOG = self.original_catalog
        server.INVENTORY_PATH = self.original_inventory_path
        server.WEAKNESS_PATH = self.original_weakness_path
        self.temp_directory.cleanup()

    def write_bundle(self, payload: dict) -> None:
        self.bundle_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        stat = self.bundle_path.stat()
        os.utime(
            self.bundle_path,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
        )

    def attempt_payload(
        self,
        *,
        event_id: str = "event-1",
        question_id: str = "q-regular",
        question_format: str = "regular",
        selected_answer: object = 2,
        is_correct: bool | None = True,
    ) -> dict:
        payload = {
            "eventId": event_id,
            "sessionId": "session-1",
            "questionId": question_id,
            "format": question_format,
            "isCorrect": is_correct,
            "mode": "review",
            "answeredAt": "2026-07-18T12:01:00Z",
            "responseMs": 1250,
            "questionPosition": 1,
            "appVersion": "test",
        }
        if question_format == "written":
            payload["answerText"] = "処分性が認められるため。"
        else:
            payload["selectedAnswer"] = selected_answer
        return payload

    def decision_payload(
        self,
        *,
        decision_id: str = "decision-1",
        decision: str = "related",
        relation_type: str | None = "same_topic",
        digest: str = "a" * 64,
        supersedes: str | None = None,
    ) -> dict:
        payload = {
            "decisionId": decision_id,
            "pairId": "pair-1",
            "decision": decision,
            "pairContentDigest": digest,
            "decidedAt": "2026-07-18T12:02:00Z",
        }
        if relation_type is not None:
            payload["relationType"] = relation_type
        if supersedes is not None:
            payload["supersedes"] = supersedes
        return payload

    def card_attempt_payload(
        self,
        *,
        event_id: str = "card-event-1",
        card_id: str = "card-1",
        selected_answer: bool = True,
    ) -> dict:
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        card = snapshot.cards[card_id]
        return {
            "eventId": event_id,
            "sessionId": "session-card-1",
            "studyDeckId": deck["id"] if deck else None,
            "cardId": card_id,
            "answerRevision": server.card_answer_revision(card, snapshot, deck),
            "selectedAnswer": selected_answer,
            "mode": "review",
            "orderMode": "sequence",
            "topicFilter": "all",
            "answeredAt": "2026-07-18T12:05:00Z",
            "shownAt": "2026-07-18T12:04:55Z",
            "responseMs": 5000,
            "questionPosition": 1,
            "appVersion": "test",
        }

    def test_bundle_cache_reload_and_public_projection(self) -> None:
        first = server.CATALOG.load()
        serialized = json.dumps(first.bundle, ensure_ascii=False)
        self.assertNotIn("PROVIDER_EXPLANATION_SECRET", serialized)
        self.assertNotIn("RAW_PROVIDER_RESPONSE", serialized)
        self.assertNotIn("/home/yuki/", serialized)
        self.assertEqual(first.questions["q-regular"]["format"], "regular")

        changed = fixture_bundle()
        changed["generatedAt"] = "2026-07-18T13:00:00Z"
        self.write_bundle(changed)
        second = server.CATALOG.load()
        self.assertNotEqual(first.revision, second.revision)
        self.assertEqual(second.bundle["generatedAt"], "2026-07-18T13:00:00Z")

        self.bundle_path.write_text("{broken", encoding="utf-8")
        stale = server.CATALOG.load()
        self.assertEqual(stale.revision, second.revision)
        self.assertTrue(server.CATALOG.status()["stale"])

    def test_macos_internal_paths_are_not_public(self) -> None:
        projected = server.public_projection(
            {
                "userPathInTextField": "/Users/yuki/private/source.json",
                "privatePathInTextField": "/private/var/tmp/source.json",
                "safe": "公開してよい値",
            }
        )
        self.assertEqual(projected, {"safe": "公開してよい値"})

        for path in (
            "/Users/yuki/private/inventory.json",
            "/private/var/tmp/inventory.json",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "internal path"):
                    server._inventory_text(path)

    def test_missing_bundle_is_reported_without_internal_path(self) -> None:
        missing = server.BundleCatalog(self.root / "secret" / "missing.json")
        with self.assertRaises(server.ApiError) as context:
            missing.load()
        self.assertEqual(context.exception.status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(context.exception.message, "bundle unavailable")

    def test_data_inventory_is_sanitized_and_missing_file_is_nonfatal(self) -> None:
        inventory = server.data_inventory()
        self.assertTrue(inventory["available"])
        self.assertEqual(inventory["coverage"]["storedQuestionUnits"], 3)
        serialized = json.dumps(inventory, ensure_ascii=False)
        self.assertNotIn("PRIVATE INVENTORY QUESTION", serialized)
        self.assertNotIn("/home/yuki/", serialized)
        self.assertNotIn("privateProviderReason", serialized)

        server.INVENTORY_PATH = self.root / "private" / "missing.json"
        unavailable = server.data_inventory()
        self.assertFalse(unavailable["available"])
        self.assertNotIn(str(server.INVENTORY_PATH), json.dumps(unavailable))

    def test_attempts_support_every_format_and_nullable_correctness(self) -> None:
        first, inserted = server.add_attempt(self.attempt_payload())
        self.assertTrue(inserted)
        self.assertEqual(first["selectedAnswer"], 2)

        multiple = self.attempt_payload(
            event_id="event-2",
            question_id="q-multiple",
            question_format="multiple_blank",
            selected_answer={"ア": 3, "イ": 1},
            is_correct=None,
        )
        second, inserted = server.add_attempt(multiple)
        self.assertTrue(inserted)
        self.assertEqual(second["selectedAnswer"], {"ア": 3, "イ": 1})
        self.assertIsNone(second["isCorrect"])

        written = self.attempt_payload(
            event_id="event-3",
            question_id="q-written",
            question_format="written",
            is_correct=False,
        )
        third, inserted = server.add_attempt(written)
        self.assertTrue(inserted)
        self.assertIn("処分性", third["answerText"])

        duplicate, inserted = server.add_attempt(copy.deepcopy(written))
        self.assertFalse(inserted)
        self.assertEqual(duplicate["eventId"], "event-3")

        with server.connect() as connection:
            progress = server.progress_statistics(connection)
        self.assertEqual(
            progress["overall"],
            {
                "attempts": 3,
                "correct": 1,
                "incorrect": 1,
                "ungraded": 1,
                "accuracy": 0.5,
            },
        )
        self.assertEqual(progress["byQuestion"]["q-multiple"]["ungraded"], 1)

        exported = server.export_data()
        self.assertEqual(
            exported["attempts"][1]["selectedAnswer"],
            {"ア": 3, "イ": 1},
        )

    def test_attempt_idempotency_conflict_and_catalog_validation(self) -> None:
        original = self.attempt_payload()
        server.add_attempt(original)
        changed = copy.deepcopy(original)
        changed["selectedAnswer"] = 4
        with self.assertRaises(server.ApiError) as context:
            server.add_attempt(changed)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        unknown = self.attempt_payload(event_id="event-unknown")
        unknown["questionId"] = "q-unknown"
        with self.assertRaises(server.ApiError) as context:
            server.add_attempt(unknown)
        self.assertEqual(context.exception.message, "unknown questionId")

    def test_answer_history_is_append_only_at_database_level(self) -> None:
        server.add_attempt(self.attempt_payload())
        with self.assertRaises(sqlite3.IntegrityError):
            with server.connect() as connection:
                connection.execute(
                    "UPDATE answer_attempts SET mode = 'all' WHERE event_id = 'event-1'"
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with server.connect() as connection:
                connection.execute(
                    "DELETE FROM answer_attempts WHERE event_id = 'event-1'"
                )

    def test_similarity_decision_requires_relation_digest_and_supersedes(self) -> None:
        missing_relation = self.decision_payload(relation_type=None)
        with self.assertRaises(server.ApiError) as context:
            server.add_similarity_decision(missing_relation)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

        wrong_digest = self.decision_payload(digest="b" * 64)
        with self.assertRaises(server.ApiError) as context:
            server.add_similarity_decision(wrong_digest)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        first, inserted = server.add_similarity_decision(self.decision_payload())
        self.assertTrue(inserted)
        self.assertTrue(first["matchesCurrentContent"])
        duplicate, inserted = server.add_similarity_decision(self.decision_payload())
        self.assertFalse(inserted)
        self.assertEqual(duplicate["decisionId"], "decision-1")

        no_supersedes = self.decision_payload(
            decision_id="decision-2",
            decision="reject",
            relation_type=None,
        )
        with self.assertRaises(server.ApiError) as context:
            server.add_similarity_decision(no_supersedes)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        replacement = self.decision_payload(
            decision_id="decision-2",
            decision="reject",
            relation_type=None,
            supersedes="decision-1",
        )
        latest, inserted = server.add_similarity_decision(replacement)
        self.assertTrue(inserted)
        self.assertEqual(latest["supersedes"], "decision-1")

        with server.connect() as connection:
            pairs = server.similarities_with_latest(connection)
        self.assertEqual(pairs[0]["latestDecision"]["decision"], "reject")
        self.assertEqual(pairs[0]["decisionState"], "current")

    def test_changed_pair_digest_marks_latest_decision_stale(self) -> None:
        server.add_similarity_decision(self.decision_payload())
        changed = fixture_bundle()
        changed["similarityPairs"][0]["pairContentDigest"] = "b" * 64
        self.write_bundle(changed)
        snapshot = server.CATALOG.load()

        with server.connect() as connection:
            pairs = server.similarities_with_latest(connection, snapshot)
        self.assertEqual(pairs[0]["decisionState"], "stale")
        self.assertFalse(
            pairs[0]["latestDecision"]["matchesCurrentContent"]
        )

        old_digest = self.decision_payload(
            decision_id="decision-2",
            supersedes="decision-1",
        )
        with self.assertRaises(server.ApiError) as context:
            server.add_similarity_decision(old_digest)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        current_digest = self.decision_payload(
            decision_id="decision-2",
            digest="b" * 64,
            supersedes="decision-1",
        )
        decision, inserted = server.add_similarity_decision(current_digest)
        self.assertTrue(inserted)
        self.assertTrue(decision["matchesCurrentContent"])

    def test_ui_similarity_aliases_and_merge_relation_are_supported(self) -> None:
        payload = {
            "eventId": "decision-ui-1",
            "pairId": "pair-1",
            "decision": "merge",
            "relationType": "same_proposition",
            "pairContentDigest": "a" * 64,
            "reviewedAt": "2026-07-18T12:03:00Z",
        }
        decision, inserted = server.add_similarity_decision(payload)
        self.assertTrue(inserted)
        self.assertEqual(decision["eventId"], "decision-ui-1")
        self.assertEqual(decision["reviewedAt"], "2026-07-18T12:03:00Z")
        self.assertEqual(decision["relationType"], "same_proposition")

        replacement = {
            "eventId": "decision-ui-2",
            "pairId": "pair-1",
            "decision": "defer",
            "pairContentDigest": "a" * 64,
            "reviewedAt": "2026-07-18T12:04:00Z",
            "supersedesEventId": "decision-ui-1",
        }
        decision, inserted = server.add_similarity_decision(replacement)
        self.assertTrue(inserted)
        self.assertEqual(decision["supersedesEventId"], "decision-ui-1")

    def test_cards_return_only_joined_related_evidence(self) -> None:
        snapshot = server.CATALOG.load()
        payload = server.cards_payload(snapshot, {})
        self.assertEqual(payload["subjects"], [])
        self.assertEqual(len(payload["explanationCards"]), 1)
        self.assertEqual(payload["studyDeck"]["id"], "deck-1")
        self.assertEqual(
            payload["explanationCards"][0]["answerRevision"],
            server.card_answer_revision(
                snapshot.cards["card-1"],
                snapshot,
                snapshot.study_decks["deck-1"],
            ),
        )
        self.assertEqual(len(payload["relatedQuestionEvidence"]), 1)
        self.assertEqual(
            payload["relatedQuestionEvidence"][0]["choiceId"],
            "choice-related-1",
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PROVIDER_EVIDENCE_SECRET", serialized)
        self.assertNotIn("/home/yuki/", serialized)

    def test_large_multi_subject_fixture_filters_and_pages_without_truncation(
        self,
    ) -> None:
        bundle = fixture_bundle()
        template = bundle["questions"][0]
        questions = []
        for index in range(1205):
            question = copy.deepcopy(template)
            question["id"] = f"q-large-{index:04d}"
            question["exam"] = {
                "year": 2024 if index % 3 == 0 else 2025,
                "number": index % 60 + 1,
            }
            question["format"] = "multiple_blank" if index % 10 == 0 else "regular"
            if index % 2:
                question["subjectId"] = "civil-law"
                question["topic"] = "代理"
                question["labels"] = ["民法", "代理"]
            else:
                question["subjectId"] = "administrative-law"
                question["topic"] = "行政手続法"
                question["labels"] = ["行政法", "行政手続法"]
            questions.append(question)
        bundle["questions"] = questions
        bundle["officialAnswerChecks"] = []
        self.write_bundle(bundle)
        snapshot = server.CATALOG.load()

        first = server.paginated(server.question_items(snapshot, {}), {}, snapshot)
        self.assertEqual(1205, first["page"]["total"])
        self.assertEqual(1000, first["page"]["returned"])
        self.assertTrue(first["page"]["hasMore"])

        second_query = {"offset": ["1000"]}
        second = server.paginated(
            server.question_items(snapshot, second_query),
            second_query,
            snapshot,
        )
        self.assertEqual(205, second["page"]["returned"])
        self.assertFalse(second["page"]["hasMore"])

        filter_query = {
            "subjectId": ["civil-law"],
            "year": ["2024"],
            "topic": ["代理"],
            "format": ["regular"],
            "limit": ["37"],
        }
        expected = [
            question
            for question in questions
            if question["subjectId"] == "civil-law"
            and question["exam"]["year"] == 2024
            and question["topic"] == "代理"
            and question["format"] == "regular"
        ]
        filtered = server.paginated(
            server.question_items(snapshot, filter_query),
            filter_query,
            snapshot,
        )
        self.assertEqual(len(expected), filtered["page"]["total"])
        self.assertEqual(min(37, len(expected)), filtered["page"]["returned"])
        self.assertTrue(
            all(item["subjectId"] == "civil-law" for item in filtered["items"])
        )

    def test_card_and_similarity_filters_use_canonical_question_metadata(self) -> None:
        snapshot = server.CATALOG.load()
        cards = server.cards_payload(
            snapshot,
            {
                "subjectId": ["administrative-law"],
                "topic": ["行政手続法"],
            },
        )
        self.assertEqual(1, cards["page"]["total"])
        self.assertEqual("card-1", cards["explanationCards"][0]["id"])

        no_cards = server.cards_payload(
            snapshot,
            {"subjectId": ["civil-law"]},
        )
        self.assertEqual(0, no_cards["page"]["total"])

        with server.connect() as connection:
            pairs = server.similarities_with_latest(connection, snapshot)
        admin_ids = {
            question["id"]
            for question in snapshot.questions.values()
            if server._matches_question_filters(
                question,
                server._question_filters(
                    {
                        "subjectId": ["administrative-law"],
                        "year": ["2025"],
                        "topic": ["行政手続法"],
                        "format": ["regular"],
                    }
                ),
            )
        }
        filtered_pairs = [
            pair
            for pair in pairs
            if server._referenced_question_ids(pair) & admin_ids
        ]
        self.assertEqual(["pair-1"], [pair["id"] for pair in filtered_pairs])

        with self.assertRaisesRegex(server.ApiError, "invalid year"):
            server.question_items(snapshot, {"year": ["not-a-year"]})
        with self.assertRaisesRegex(server.ApiError, "not supported for cards"):
            server.cards_payload(snapshot, {"format": ["regular"]})

    def test_card_answer_revision_ignores_b_but_tracks_answer(self) -> None:
        first = server.CATALOG.load()
        first_revision = server.card_answer_revision(
            first.cards["card-1"],
            first,
            first.study_decks["deck-1"],
        )

        b_change = fixture_bundle()
        b_change["explanationCards"][0]["variants"]["b"] = "やさしい説明を変更"
        b_change["explanationCards"][0]["variants"]["bCasual"] = (
            "別のほどき方へ変更"
        )
        self.write_bundle(b_change)
        second = server.CATALOG.load()
        self.assertEqual(
            first_revision,
            server.card_answer_revision(
                second.cards["card-1"],
                second,
                second.study_decks["deck-1"],
            ),
        )

        answer_change = copy.deepcopy(b_change)
        answer_change["explanationCards"][0]["variants"]["a"] += " 原則として"
        self.write_bundle(answer_change)
        third = server.CATALOG.load()
        self.assertNotEqual(
            first_revision,
            server.card_answer_revision(
                third.cards["card-1"],
                third,
                third.study_decks["deck-1"],
            ),
        )
        for field in ("c",):
            changed_card = copy.deepcopy(first.cards["card-1"])
            changed_card["variants"][field] += " 変更"
            self.assertNotEqual(
                first_revision,
                server.card_answer_revision(
                    changed_card,
                    first,
                    first.study_decks["deck-1"],
                ),
            )
        changed_card = copy.deepcopy(first.cards["card-1"])
        changed_card["correct"] = not changed_card["correct"]
        self.assertNotEqual(
            first_revision,
            server.card_answer_revision(
                changed_card,
                first,
                first.study_decks["deck-1"],
            ),
        )
        changed_card = copy.deepcopy(first.cards["card-1"])
        changed_card["lawAsOf"] = "2027-04-01"
        self.assertNotEqual(
            first_revision,
            server.card_answer_revision(
                changed_card,
                first,
                first.study_decks["deck-1"],
            ),
        )

    def test_response_time_median_ignores_outliers_and_missing_values(self) -> None:
        self.assertIsNone(server._median_ms([]))
        self.assertEqual(4000, server._median_ms([4000]))
        self.assertEqual(5000, server._median_ms([9000, 1000, 5000]))
        self.assertEqual(3000, server._median_ms([2000, 4000]))

        for elapsed in (1000, 4000, None, 900_000):
            payload = self.card_attempt_payload()
            payload["attemptId"] = f"card-attempt-{elapsed}"
            payload["eventId"] = payload["attemptId"]
            payload["responseMs"] = elapsed
            server.add_card_attempt(payload)

        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
        card = progress["byCard"]["card-1"]
        # 5分を超える計測と未記録は捨てるので、残る標本は1000と4000だけ
        self.assertEqual(2, card["responseSamples"])
        self.assertEqual(2500, card["medianResponseMs"])
        self.assertEqual(4000, card["lastResponseMs"])

    def test_display_markup_does_not_change_the_answer_revision(self) -> None:
        first = server.CATALOG.load()
        first_revision = server.card_answer_revision(
            first.cards["card-1"],
            first,
            first.study_decks["deck-1"],
        )

        decorated = copy.deepcopy(first.cards["card-1"])
        variants = decorated["variants"]
        variants["a"] = f"**{variants['a']}**"
        variants["c"] = f"=={variants['c']}=="
        self.assertEqual(
            first_revision,
            server.card_answer_revision(
                decorated,
                first,
                first.study_decks["deck-1"],
            ),
        )

        reworded = copy.deepcopy(first.cards["card-1"])
        reworded["variants"]["a"] += "重要"
        self.assertNotEqual(
            first_revision,
            server.card_answer_revision(
                reworded,
                first,
                first.study_decks["deck-1"],
            ),
        )

    def test_card_progress_counts_every_attempt_of_the_same_card_id(self) -> None:
        # 2026-07-30に方針を変えた。A・C・正解・法令基準日を直しても、
        # 同じカードIDなら過去の回答をそのまま数える。
        server.add_card_attempt(self.card_attempt_payload())

        b_only = fixture_bundle()
        b_only["explanationCards"][0]["variants"]["b"] = "口調だけ変更"
        self.write_bundle(b_only)
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
        self.assertEqual(progress["overall"]["attempts"], 1)

        answer_change = copy.deepcopy(b_only)
        answer_change["explanationCards"][0]["variants"]["a"] += "（変更）"
        answer_change["explanationCards"][0]["variants"]["c"] += "（変更）"
        answer_change["explanationCards"][0]["correct"] = not answer_change[
            "explanationCards"
        ][0]["correct"]
        self.write_bundle(answer_change)
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
        self.assertEqual(progress["overall"]["attempts"], 1)
        self.assertEqual(progress["byCard"]["card-1"]["attempts"], 1)
        self.assertEqual(len(server.export_data()["cardAttempts"]), 1)

    def test_card_attempts_compute_result_and_idempotency(self) -> None:
        first, inserted, snapshot, deck = server.add_card_attempt(
            self.card_attempt_payload()
        )
        self.assertTrue(inserted)
        self.assertTrue(first["correctAnswer"])
        self.assertTrue(first["isCorrect"])
        self.assertEqual(first["scopeMode"], "review")
        self.assertEqual(first["mode"], "review")

        second_payload = self.card_attempt_payload(
            event_id="card-event-2",
            selected_answer=False,
        )
        second, inserted, _, _ = server.add_card_attempt(second_payload)
        self.assertTrue(inserted)
        self.assertFalse(second["isCorrect"])

        replayed = copy.deepcopy(second_payload)
        replayed["clientMetadata"] = {"cached": True}
        duplicate, inserted, _, _ = server.add_card_attempt(replayed)
        self.assertFalse(inserted)
        self.assertEqual(duplicate["eventId"], "card-event-2")

        conflicting = copy.deepcopy(second_payload)
        conflicting["selectedAnswer"] = True
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(conflicting)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
            raw_count = connection.execute(
                "SELECT COUNT(*) FROM answer_attempts"
            ).fetchone()[0]
        self.assertEqual(raw_count, 0)
        self.assertEqual(
            progress["overall"],
            {
                "attempts": 2,
                "correct": 1,
                "incorrect": 1,
                "accuracy": 0.5,
                "medianResponseMs": 5000,
            },
        )
        self.assertEqual(progress["byCard"]["card-1"]["streak"], 0)
        self.assertEqual(progress["byCard"]["card-1"]["maxStreak"], 1)
        self.assertEqual(len(server.export_data()["cardAttempts"]), 2)

    def card_mark_payload(
        self,
        *,
        event_id: str = "mark-1",
        action: str = "certain",
        scope: str = "card",
        card_id: str | None = "card-1",
        confidence: str | None = None,
        attempt_event_id: str | None = None,
    ) -> dict:
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        payload = {
            "eventId": event_id,
            "sessionId": "session-card-1",
            "studyDeckId": deck["id"] if deck else None,
            "action": action,
            "scope": scope,
            "markedAt": "2026-07-18T12:10:00Z",
            "appVersion": "test",
        }
        if card_id is not None:
            payload["cardId"] = card_id
        if confidence is not None:
            payload["confidence"] = confidence
        if attempt_event_id is not None:
            payload["attemptEventId"] = attempt_event_id
        return payload

    def test_certain_mark_survives_a_deck_wide_reset(self) -> None:
        for index in range(3):
            server.add_card_attempt(
                self.card_attempt_payload(event_id=f"card-event-{index}")
            )
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        with server.connect() as connection:
            before = server.card_progress_statistics(connection, snapshot, deck)
        self.assertTrue(before["byCard"]["card-1"]["mastered"])
        self.assertTrue(before["byCard"]["card-1"]["graduated"])
        self.assertEqual(before["byCard"]["card-1"]["graduatedTimes"], 1)

        server.add_card_mark(self.card_mark_payload())
        server.add_card_mark(
            self.card_mark_payload(
                event_id="mark-reset", action="reset", scope="deck", card_id=None
            )
        )
        with server.connect() as connection:
            after = server.card_progress_statistics(connection, snapshot, deck)
        item = after["byCard"]["card-1"]
        # 全リセットで習得判定は数え直しになるが、絶対覚えたと過去の卒業事実は残る
        self.assertFalse(item["mastered"])
        self.assertEqual(item["masteryScore"], 0)
        self.assertEqual(item["sinceResetAttempts"], 0)
        self.assertTrue(item["certain"])
        self.assertTrue(item["graduated"])
        self.assertEqual(item["graduatedTimes"], 1)
        self.assertEqual(item["correct"], 3)
        self.assertEqual(item["attempts"], 3)

        server.add_card_mark(
            self.card_mark_payload(event_id="mark-off", action="uncertain")
        )
        with server.connect() as connection:
            released = server.card_progress_statistics(connection, snapshot, deck)
        self.assertFalse(released["byCard"]["card-1"]["certain"])
        self.assertFalse(released["byCard"]["card-1"]["graduated"])
        self.assertEqual(released["byCard"]["card-1"]["graduatedTimes"], 1)
        self.assertEqual(released["stats"]["masteryScore"], server.MASTERY_SCORE)

        with server.connect() as connection:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM card_attempts"
            ).fetchone()[0]
        self.assertEqual(attempts, 3)
        self.assertEqual(len(server.export_data()["cardMarks"]), 3)

    def test_marks_are_carried_over_after_a_card_revision(self) -> None:
        server.add_card_mark(self.card_mark_payload())
        server.add_card_attempt(self.card_attempt_payload())
        server.add_card_mark(
            self.card_mark_payload(
                event_id="mark-confidence",
                action="confidence",
                confidence="sure",
                attempt_event_id="card-event-1",
            )
        )
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        with server.connect() as connection:
            before = server.card_progress_statistics(connection, snapshot, deck)
        self.assertTrue(before["byCard"]["card-1"]["certain"])
        self.assertEqual(before["byCard"]["card-1"]["confidenceCounts"]["sure"], 1)

        # 2026-07-30に方針を変えた。A を直して回答revisionが変わっても、
        # 同じカードIDなら「絶対覚えた」と自信度をそのまま引き継ぐ。
        revised = fixture_bundle()
        revised["explanationCards"][0]["variants"]["a"] += " ただし例外がある"
        self.write_bundle(revised)
        changed = server.CATALOG.load()
        changed_deck = server.default_study_deck(changed)
        with server.connect() as connection:
            after = server.card_progress_statistics(connection, changed, changed_deck)
        item = after["byCard"]["card-1"]
        self.assertTrue(item["certain"])
        self.assertEqual(item["confidenceCounts"]["sure"], 1)
        # 印そのものは消さない。追記型なので行は残る。
        with server.connect() as connection:
            stored = connection.execute("SELECT COUNT(*) FROM card_marks").fetchone()[0]
        self.assertEqual(stored, 2)

    def test_deck_reset_still_applies_after_a_card_revision(self) -> None:
        for index in range(3):
            server.add_card_attempt(
                self.card_attempt_payload(event_id=f"card-event-{index}")
            )
        server.add_card_mark(
            self.card_mark_payload(
                event_id="mark-reset", action="reset", scope="deck", card_id=None
            )
        )
        revised = fixture_bundle()
        revised["explanationCards"][0]["variants"]["a"] += " 改訂"
        self.write_bundle(revised)
        changed = server.CATALOG.load()
        with server.connect() as connection:
            after = server.card_progress_statistics(
                connection, changed, server.default_study_deck(changed)
            )
        # リセットは版に依存しない区切りなので、改訂後も効き続ける
        self.assertEqual(after["byCard"]["card-1"]["resetCount"], 1)

    def test_confidence_is_recorded_per_answer_without_changing_selection(self) -> None:
        server.add_card_attempt(self.card_attempt_payload())
        mark, inserted, snapshot, deck = server.add_card_mark(
            self.card_mark_payload(
                event_id="mark-confidence",
                action="confidence",
                confidence="guess",
                attempt_event_id="card-event-1",
            )
        )
        self.assertTrue(inserted)
        self.assertEqual(mark["confidence"], "guess")
        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
        item = progress["byCard"]["card-1"]
        self.assertEqual(item["confidenceCounts"]["guess"], 1)
        self.assertEqual(item["lastConfidence"], "guess")
        # 自信度は出題対象の判定には効かない
        self.assertFalse(item["mastered"])
        self.assertEqual(item["correct"], 1)

        with self.assertRaises(server.ApiError) as context:
            server.add_card_mark(
                self.card_mark_payload(
                    event_id="mark-confidence-2",
                    action="confidence",
                    confidence="sure",
                    attempt_event_id="card-event-1",
                )
            )
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

    def test_card_mark_validation_and_append_only_storage(self) -> None:
        for payload, status in (
            (self.card_mark_payload(action="graduate"), HTTPStatus.BAD_REQUEST),
            (
                self.card_mark_payload(
                    event_id="m2", action="certain", scope="deck", card_id=None
                ),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                self.card_mark_payload(
                    event_id="m3", action="reset", scope="deck", card_id="card-1"
                ),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                self.card_mark_payload(event_id="m4", confidence="sure"),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                self.card_mark_payload(
                    event_id="m5",
                    action="confidence",
                    confidence="sure",
                    attempt_event_id="missing-attempt",
                ),
                HTTPStatus.BAD_REQUEST,
            ),
            (
                self.card_mark_payload(event_id="m6", card_id="card-unknown"),
                HTTPStatus.BAD_REQUEST,
            ),
        ):
            with self.assertRaises(server.ApiError) as context:
                server.add_card_mark(payload)
            self.assertEqual(context.exception.status, status)

        server.add_card_mark(self.card_mark_payload(event_id="mark-keep"))
        replayed, inserted, _, _ = server.add_card_mark(
            self.card_mark_payload(event_id="mark-keep")
        )
        self.assertFalse(inserted)
        self.assertEqual(replayed["eventId"], "mark-keep")
        with self.assertRaises(server.ApiError) as context:
            server.add_card_mark(
                self.card_mark_payload(event_id="mark-keep", action="uncertain")
            )
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        with server.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM card_marks")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE card_marks SET action = 'uncertain'")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(card_marks)")
            }
        self.assertEqual(
            columns,
            {
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
            },
        )

    def test_individual_reset_only_clears_that_card(self) -> None:
        # 実時計が同一ミリ秒を返しても、回答→リセット→回答の追記順を保つ。
        with mock.patch.object(
            server, "utc_now", return_value="2026-07-18T12:00:00.000Z"
        ):
            for index in range(3):
                server.add_card_attempt(
                    self.card_attempt_payload(event_id=f"a-{index}", card_id="card-1")
                )
            snapshot = server.CATALOG.load()
            deck = server.default_study_deck(snapshot)
            server.add_card_mark(
                self.card_mark_payload(
                    event_id="reset-1", action="reset", card_id="card-1"
                )
            )
            server.add_card_attempt(
                self.card_attempt_payload(event_id="a-after", card_id="card-1")
            )
        with server.connect() as connection:
            progress = server.card_progress_statistics(connection, snapshot, deck)
        item = progress["byCard"]["card-1"]
        self.assertEqual(item["attempts"], 4)
        self.assertEqual(item["sinceResetAttempts"], 1)
        self.assertEqual(item["masteryScore"], 1)
        self.assertFalse(item["mastered"])
        self.assertEqual(item["resetCount"], 1)
        self.assertEqual(item["graduatedTimes"], 1)

    def test_database_schema_version_and_card_attempt_columns(self) -> None:
        with server.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(card_attempts)")
            }
        self.assertEqual(version, server.DATABASE_SCHEMA_VERSION)
        self.assertEqual(
            columns,
            {
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
            },
        )

    def test_card_attempt_scope_mode_aliases_are_normalized(self) -> None:
        official = self.card_attempt_payload(event_id="card-scope-mode-1")
        official.pop("mode")
        official["scopeMode"] = "review"
        saved, inserted, _, _ = server.add_card_attempt(official)
        self.assertTrue(inserted)
        self.assertEqual(saved["scopeMode"], "review")
        self.assertEqual(saved["mode"], "review")

        legacy = copy.deepcopy(official)
        legacy.pop("scopeMode")
        legacy["mode"] = "review"
        duplicate, inserted, _, _ = server.add_card_attempt(legacy)
        self.assertFalse(inserted)
        self.assertEqual(duplicate["scopeMode"], "review")

        both = copy.deepcopy(official)
        both["mode"] = "review"
        duplicate, inserted, _, _ = server.add_card_attempt(both)
        self.assertFalse(inserted)
        self.assertEqual(duplicate["scopeMode"], "review")

        conflicting_event = copy.deepcopy(legacy)
        conflicting_event["mode"] = "all"
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(conflicting_event)
        self.assertEqual(context.exception.status, HTTPStatus.CONFLICT)

        disagreeing_aliases = self.card_attempt_payload(event_id="card-scope-mode-2")
        disagreeing_aliases["scopeMode"] = "all"
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(disagreeing_aliases)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

        exported = server.export_data()["cardAttempts"]
        self.assertEqual(exported[0]["scopeMode"], "review")
        self.assertEqual(exported[0]["mode"], "review")

    def test_card_attempt_validation_and_study_deck_membership(self) -> None:
        unknown = self.card_attempt_payload()
        unknown["cardId"] = "card-unknown"
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(unknown)
        self.assertEqual(context.exception.message, "unknown cardId")

        outside_deck = self.card_attempt_payload(card_id="card-2")
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(outside_deck)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertIn("not in the study deck", context.exception.message)

        # 2026-07-30に方針を変えた。カードを直したあとに届いた回答も受け取る。
        # 画面が出していた版はそのまま記録し、集計では見ない。
        older_revision = self.card_attempt_payload(event_id="card-event-older")
        older_revision["answerRevision"] = "b" * 64
        recorded, inserted, _, _ = server.add_card_attempt(older_revision)
        self.assertTrue(inserted)
        self.assertEqual(recorded["answerRevision"], "b" * 64)

        invalid_revision = self.card_attempt_payload()
        invalid_revision["answerRevision"] = None
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(invalid_revision)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

        malformed_revision = self.card_attempt_payload()
        malformed_revision["answerRevision"] = "not-a-sha256"
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(malformed_revision)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

        wrong_type = self.card_attempt_payload()
        wrong_type["selectedAnswer"] = 1
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(wrong_type)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

        false_client_result = self.card_attempt_payload()
        false_client_result["isCorrect"] = False
        with self.assertRaises(server.ApiError) as context:
            server.add_card_attempt(false_client_result)
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

    def test_learning_analysis_projects_fresh_snapshot_and_falls_back_live(self) -> None:
        initial = server.learning_analysis()
        self.assertTrue(initial["available"])
        self.assertEqual(initial["analysis"]["source"], "live")
        self.assertEqual(initial["summary"]["targetCount"], 0)
        self.assertIn("missing", initial["freshness"]["reasonCodes"])

        server.add_card_attempt(
            self.card_attempt_payload(
                event_id="card-weakness-1",
                selected_answer=False,
            )
        )
        server.add_card_attempt(
            self.card_attempt_payload(
                event_id="card-weakness-2",
                selected_answer=False,
            )
        )
        snapshot = server.CATALOG.load()
        deck = server.default_study_deck(snapshot)
        server.refresh_weakness_latest(snapshot, deck)
        self.assertEqual(self.weakness_path.stat().st_mode & 0o777, 0o600)

        private_snapshot = json.loads(
            self.weakness_path.read_text(encoding="utf-8")
        )
        private_snapshot["providerExplanation"] = "PRIVATE_EXPLANATION"
        private_snapshot["localPath"] = "/home/yuki/private/weakness.json"
        private_snapshot["targets"][0]["rawResponse"] = "PRIVATE_RESPONSE"
        self.weakness_path.write_text(
            json.dumps(private_snapshot, ensure_ascii=False),
            encoding="utf-8",
        )

        projected = server.learning_analysis()
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertEqual(projected["analysis"]["source"], "stored")
        self.assertTrue(projected["freshness"]["storedSnapshotFresh"])
        self.assertEqual(projected["summary"]["targetCount"], 1)
        self.assertEqual(projected["targets"][0]["cardId"], "card-1")
        self.assertEqual(projected["targets"][0]["status"], "weak")
        self.assertNotIn("PRIVATE_EXPLANATION", serialized)
        self.assertNotIn("PRIVATE_RESPONSE", serialized)
        self.assertNotIn("/home/yuki/", serialized)

        server.add_card_attempt(
            self.card_attempt_payload(
                event_id="card-weakness-3",
                selected_answer=True,
            )
        )
        current = server.learning_analysis()
        self.assertEqual(current["analysis"]["source"], "live")
        self.assertIn("attempts", current["freshness"]["reasonCodes"])

    def test_card_attempt_table_is_append_only(self) -> None:
        server.add_card_attempt(self.card_attempt_payload())
        with self.assertRaises(sqlite3.IntegrityError):
            with server.connect() as connection:
                connection.execute(
                    "UPDATE card_attempts SET topic_filter = 'changed' "
                    "WHERE event_id = 'card-event-1'"
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with server.connect() as connection:
                connection.execute(
                    "DELETE FROM card_attempts WHERE event_id = 'card-event-1'"
                )

    def test_legacy_bundle_without_study_deck_accepts_all_cards(self) -> None:
        legacy = fixture_bundle()
        legacy.pop("studyDecks")
        self.write_bundle(legacy)
        snapshot = server.CATALOG.load()
        payload = server.cards_payload(snapshot, {})
        self.assertIsNone(payload["studyDeck"])
        self.assertEqual(len(payload["explanationCards"]), 2)

        attempt = self.card_attempt_payload(card_id="card-2")
        saved, inserted, _, deck = server.add_card_attempt(attempt)
        self.assertTrue(inserted)
        self.assertIsNone(deck)
        self.assertEqual(saved["cardId"], "card-2")

    def request(
        self,
        http_server: ThreadingHTTPServer,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        body = None
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            http_server.server_port,
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            decoded = json.loads(response.read().decode("utf-8"))
            return response.status, decoded
        finally:
            connection.close()

    def test_http_endpoints_and_write_request_defenses(self) -> None:
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.ProductionHandler)
        http_server.daemon_threads = True
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        try:
            status, payload = self.request(http_server, "GET", "/api/overview")
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(payload["catalog"]["questions"], 3)
            self.assertIn("overall", payload)
            self.assertIn("byQuestion", payload)
            self.assertTrue(payload["dataInventory"]["available"])

            status, inventory = self.request(
                http_server,
                "GET",
                "/api/data-inventory",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(inventory["coverage"]["storedQuestionUnits"], 3)
            self.assertNotIn(
                "/home/yuki/",
                json.dumps(inventory, ensure_ascii=False),
            )

            status, cards = self.request(http_server, "GET", "/api/cards")
            self.assertEqual(status, HTTPStatus.OK)
            self.assertIn("explanationCards", cards)
            self.assertIn("relatedQuestionEvidence", cards)
            self.assertIn("answerRevision", cards["explanationCards"][0])

            status, questions = self.request(
                http_server,
                "GET",
                "/api/questions?subjectId=administrative-law&year=2025&format=regular&limit=1",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(questions["page"]["total"], 1)
            self.assertEqual(questions["items"][0]["id"], "q-regular")

            status, card_progress = self.request(
                http_server,
                "GET",
                "/api/card-progress",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(card_progress["overall"]["attempts"], 0)
            self.assertIn("card-1", card_progress["byCard"])

            status, learning_analysis = self.request(
                http_server,
                "GET",
                "/api/learning-analysis",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertTrue(learning_analysis["available"])
            self.assertEqual(
                learning_analysis["studyView"]["id"],
                "weakness",
            )

            status, reviews = self.request(
                http_server,
                "GET",
                "/api/claude-reviews",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertIn("items", reviews)
            self.assertIn("claudeRuns", reviews)
            serialized_reviews = json.dumps(reviews, ensure_ascii=False)
            self.assertNotIn("RAW_PROVIDER_RESPONSE", serialized_reviews)
            self.assertNotIn("/home/yuki/", serialized_reviews)

            attempt = self.attempt_payload(event_id="event-http")
            status, payload = self.request(
                http_server,
                "POST",
                "/api/attempts",
                attempt,
            )
            self.assertEqual(status, HTTPStatus.FORBIDDEN)

            port = http_server.server_port
            protected_headers = {
                "X-Gyousei-Client": "web-v1",
                "X-Forwarded-Host": f"example.test:{port}",
                "X-Forwarded-Proto": "https",
                "Origin": "https://evil.test",
            }
            status, payload = self.request(
                http_server,
                "POST",
                "/api/attempts",
                attempt,
                protected_headers,
            )
            self.assertEqual(status, HTTPStatus.FORBIDDEN)

            card_attempt = self.card_attempt_payload(event_id="card-event-http")
            card_attempt.pop("mode")
            card_attempt["scopeMode"] = "review"
            status, payload = self.request(
                http_server,
                "POST",
                "/api/card-attempts",
                card_attempt,
                protected_headers,
            )
            self.assertEqual(status, HTTPStatus.FORBIDDEN)

            protected_headers["Origin"] = f"https://example.test:{port}"

            oversized_headers = dict(protected_headers)
            oversized_headers["Content-Type"] = "application/json"
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                http_server.server_port,
                timeout=5,
            )
            try:
                connection.request(
                    "POST",
                    "/api/card-attempts",
                    body=b"x" * (server.MAX_BODY_BYTES + 1),
                    headers=oversized_headers,
                )
                oversized_response = connection.getresponse()
                oversized_response.read()
                self.assertEqual(
                    oversized_response.status,
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            finally:
                connection.close()

            status, payload = self.request(
                http_server,
                "POST",
                "/api/attempts",
                attempt,
                protected_headers,
            )
            self.assertEqual(status, HTTPStatus.CREATED)
            self.assertTrue(payload["saved"])
            self.assertEqual(payload["overall"]["attempts"], 1)

            status, payload = self.request(
                http_server,
                "POST",
                "/api/card-attempts",
                card_attempt,
                protected_headers,
            )
            self.assertEqual(status, HTTPStatus.CREATED)
            self.assertTrue(payload["saved"])
            self.assertTrue(payload["attempt"]["isCorrect"])
            self.assertEqual(payload["attempt"]["scopeMode"], "review")
            self.assertEqual(payload["attempt"]["mode"], "review")
            self.assertEqual(payload["overall"]["attempts"], 1)
            self.assertTrue(payload["learningAnalysisUpdated"])
            self.assertTrue(self.weakness_path.is_file())

            status, similarities = self.request(
                http_server,
                "GET",
                "/api/similarities?questionId=q-regular&subjectId=administrative-law&year=2025&format=regular",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(similarities["page"]["total"], 1)
            self.assertEqual(
                similarities["items"][0]["decisionState"],
                "unreviewed",
            )

            status, exported = self.request(http_server, "GET", "/api/export")
            self.assertEqual(status, HTTPStatus.OK)
            serialized = json.dumps(exported, ensure_ascii=False)
            self.assertEqual(len(exported["cardAttempts"]), 1)
            self.assertNotIn("payload_digest", serialized)
            self.assertNotIn("/home/yuki/", serialized)
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()


def explanation_card_document() -> dict:
    """正本 content/explanation_cards.json と同じ形の、最小の1枚。"""
    return {
        "schemaVersion": "0.4-prototype",
        "meta": {"examLawAsOf": "2026-04-01", "targetExam": "令和8年度行政書士試験"},
        "studyDecks": [{"id": "deck-1", "lawAsOf": "2026-04-01", "cardIds": ["card-1"]}],
        "items": [
            {
                "id": "card-1",
                "subjectId": "administrative-law",
                "category": "行政法",
                "topic": "行政手続法",
                "subtopic": "理由提示",
                "correct": True,
                "variants": {
                    "a": "行政庁は理由を示す。",
                    "b": "行政から理由を教えてもらえます。",
                    "bCasual": "「理由提示」は、なぜそうなったかを教えることです。",
                    "bCasualStyle": "用語からほどく",
                    "c": "処分 → 理由提示",
                },
                "correction": "@@行政手続法14条@@は理由を示すよう求めています。",
                "memoryPoint": "処分には理由を付ける。",
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
                    {"label": "行政手続法14条", "url": "https://laws.e-gov.go.jp/law/405AC0000000088"}
                ],
            }
        ],
    }


class CardEditTest(unittest.TestCase):
    """画面からのカード編集。bundleの作り直しへ進む前に止まる経路だけを見る。

    作り直しそのものは全科目の取得データを必要とするので、ここでは検証しない。
    正本を書き換えてよいかの判断が、`card_edit.py` の1か所に集まっていることを確かめる。
    """

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.canonical_path = root / "explanation_cards.json"
        self.document = explanation_card_document()
        self.canonical_path.write_text(
            json.dumps(self.document, ensure_ascii=False), encoding="utf-8"
        )

        import card_edit

        self.card_edit = card_edit
        self.original_canonical = card_edit.CANONICAL_PATH
        card_edit.CANONICAL_PATH = self.canonical_path

        self.bundle_path = root / "bundle.json"
        self.bundle_path.write_text(
            json.dumps(fixture_bundle(), ensure_ascii=False), encoding="utf-8"
        )
        self.original_catalog = server.CATALOG
        server.CATALOG = server.BundleCatalog(self.bundle_path)

    def tearDown(self) -> None:
        self.card_edit.CANONICAL_PATH = self.original_canonical
        server.CATALOG = self.original_catalog
        self.temp_directory.cleanup()

    def stored(self) -> dict:
        return json.loads(self.canonical_path.read_text(encoding="utf-8"))

    def test_rejects_unknown_card_and_unknown_fields(self) -> None:
        with self.assertRaises(server.ApiError) as context:
            server.save_card_edit({"cardId": "missing-card", "editable": {}})
        self.assertEqual(context.exception.status, HTTPStatus.NOT_FOUND)

        with self.assertRaises(server.ApiError) as context:
            server.save_card_edit({"cardId": "card-1", "editable": {"frequency": {}}})
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)
        self.assertIn("frequency", context.exception.message)

        with self.assertRaises(server.ApiError) as context:
            server.save_card_edit({"cardId": "card-1", "editable": "not-an-object"})
        self.assertEqual(context.exception.status, HTTPStatus.BAD_REQUEST)

    def test_rejects_edits_that_break_the_card_rules(self) -> None:
        # Aに赤は使えない。card_exchange.py と同じ判定がここでも働く。
        with self.assertRaises(server.ApiError) as context:
            server.save_card_edit(
                {
                    "cardId": "card-1",
                    "editable": {
                        "variants": dict(
                            self.document["items"][0]["variants"],
                            a="行政庁は!!理由!!を示す。",
                        )
                    },
                }
            )
        self.assertIn("赤", context.exception.message)

        with self.assertRaises(server.ApiError) as context:
            server.save_card_edit({"cardId": "card-1", "editable": {"memoryPoint": " "}})
        self.assertIn("memoryPoint", context.exception.message)

        # どちらも正本へは書かない
        self.assertEqual(self.document, self.stored())

    def test_unchanged_edit_does_not_touch_the_canonical(self) -> None:
        before = self.canonical_path.read_bytes()
        result = server.save_card_edit(
            {"cardId": "card-1", "editable": {"topic": self.document["items"][0]["topic"]}}
        )
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["saved"])
        self.assertEqual(before, self.canonical_path.read_bytes())

    def test_failed_rebuild_puts_the_canonical_back(self) -> None:
        # 作り直しが失敗したら、画面に出ない内容が正本へ残ってはいけない。
        original = server._rebuild_bundle_in_place
        server._rebuild_bundle_in_place = lambda: (_ for _ in ()).throw(
            server.ApiError(HTTPStatus.BAD_REQUEST, "bundle rebuild failed: test")
        )
        try:
            with self.assertRaises(server.ApiError) as context:
                server.save_card_edit(
                    {"cardId": "card-1", "editable": {"memoryPoint": "書き換えた要点。"}}
                )
            self.assertIn("bundle rebuild failed", context.exception.message)
        finally:
            server._rebuild_bundle_in_place = original
        self.assertEqual(self.document, self.stored())


class SharedRuleDriftTest(unittest.TestCase):
    """同じルールを別々に書いている箇所が、ずれていないことを確かめる。

    ⑧解説図の src は、bundle生成（最後の砦）・card_edit（取り込み前の検証）・
    画面（<img>へ載せる前）の3か所で確かめている。生成側は単体で動くべきなので
    import では束ねず、代わりにここでずれを検出する。
    """

    def test_figure_source_rule_matches_everywhere(self) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "authoring" / "src"))
        import card_edit
        from gyousei_pipeline import production_bundle

        self.assertEqual(
            card_edit.FIGURE_SRC.pattern,
            production_bundle.CARD_FIGURE_SRC_PATTERN.pattern,
        )
        self.assertEqual(card_edit.FIGURE_LIMIT, production_bundle.CARD_FIGURE_LIMIT)

        app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        match = re.search(r"const CARD_FIGURE_SRC = /\^(.+)\$/;", app_js)
        self.assertIsNotNone(match, "app.js に CARD_FIGURE_SRC が見つからない")
        self.assertEqual(
            match.group(1).replace("\\/", "/"), card_edit.FIGURE_SRC.pattern
        )

    def test_card_exchange_uses_the_shared_rules(self) -> None:
        # 取り込みツールが自前の検証を持ち直していないこと
        source = (
            Path(__file__).resolve().parents[1]
            / "authoring"
            / "tools"
            / "card_exchange.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from card_edit import", source)
        for owned_by_card_edit in ("def validate_card(", "def check_markup(", "MARKUP = re.compile"):
            self.assertNotIn(owned_by_card_edit, source)
