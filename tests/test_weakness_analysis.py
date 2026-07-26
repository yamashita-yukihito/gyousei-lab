from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

import weakness_analysis


DIGEST = "a" * 64


def card(card_id: str, topic: str = "行政手続法") -> dict:
    return {
        "id": card_id,
        "subjectId": "administrative-law",
        "category": "行政法",
        "topic": topic,
        "subtopic": f"{topic}の論点",
    }


def create_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE card_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL,
            answer_revision TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            answered_at_client TEXT NOT NULL,
            response_ms INTEGER
        )
        """
    )
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    return connection


def add_results(
    connection: sqlite3.Connection,
    card_id: str,
    results: list[bool],
    *,
    revision: str = DIGEST,
    timestamps: list[str] | None = None,
) -> None:
    for index, result in enumerate(results):
        timestamp = (
            timestamps[index]
            if timestamps
            else f"2026-07-26T00:{index:02d}:00Z"
        )
        connection.execute(
            """
            INSERT INTO card_attempts (
                card_id, answer_revision, is_correct,
                answered_at_client, response_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (card_id, revision, int(result), timestamp, 1000 + index),
        )
    connection.commit()


class WeaknessAnalysisTests(unittest.TestCase):
    def test_classifies_current_revision_attempts_in_database_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            connection = create_database(path)
            cards = [
                card("unlearned"),
                card("learning"),
                card("watch"),
                card("weak-consecutive"),
                card("weak-window"),
                card("recovering"),
                card("mastered"),
                card("stale"),
            ]
            add_results(connection, "learning", [True, True])
            add_results(connection, "watch", [False])
            add_results(
                connection,
                "weak-consecutive",
                [False, False],
                timestamps=[
                    "2026-07-26T23:00:00Z",
                    "2026-07-26T01:00:00Z",
                ],
            )
            add_results(
                connection, "weak-window", [True, False, True, False]
            )
            add_results(
                connection, "recovering", [False, False, True, True]
            )
            add_results(
                connection,
                "mastered",
                [False, False, True, True, True, True, True],
            )
            add_results(
                connection, "stale", [False], revision="b" * 64
            )

            snapshot = weakness_analysis.build_weakness_snapshot(
                connection,
                cards,
                {item["id"]: DIGEST for item in cards},
                bundle_revision="c" * 64,
                study_deck={"id": "deck-1"},
                generated_at="2026-07-26T12:00:00Z",
            )
            connection.close()

        by_id = {item["cardId"]: item for item in snapshot["cards"]}
        self.assertEqual("unlearned", by_id["unlearned"]["status"])
        self.assertEqual("learning", by_id["learning"]["status"])
        self.assertEqual("watch", by_id["watch"]["status"])
        self.assertEqual(
            "weak", by_id["weak-consecutive"]["status"]
        )
        self.assertEqual(
            [
                "consecutive_incorrect_2",
                "recent_accuracy_lte_50",
            ],
            by_id["weak-consecutive"]["reasonCodes"],
        )
        self.assertEqual("weak", by_id["weak-window"]["status"])
        self.assertEqual(
            ["recent_accuracy_lte_50"],
            by_id["weak-window"]["reasonCodes"],
        )
        self.assertEqual("recovering", by_id["recovering"]["status"])
        self.assertEqual("mastered", by_id["mastered"]["status"])
        self.assertEqual("unlearned", by_id["stale"]["status"])
        self.assertEqual(
            ["stale_revision_ignored"], by_id["stale"]["reasonCodes"]
        )
        self.assertEqual(
            "2026-07-26T01:00:00Z",
            by_id["weak-consecutive"]["lastAnsweredAt"],
        )
        self.assertEqual(1, snapshot["summary"]["staleRevisionAttemptsIgnored"])
        self.assertEqual(4, snapshot["summary"]["targetCount"])
        self.assertEqual(
            ["weak-consecutive", "weak-window", "watch", "recovering"],
            [target["cardId"] for target in snapshot["targets"]],
        )
        self.assertEqual(
            snapshot["summary"]["cardCount"],
            snapshot["bySubject"][0]["cardCount"],
        )

    def test_is_deterministic_and_ignores_outside_and_unknown_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            connection = create_database(path)
            add_results(connection, "card-1", [True, False, True])
            add_results(connection, "card-2", [False])
            add_results(connection, "missing-card", [False])
            private_card = card("card-1")
            private_card["variants"] = {
                "a": "PRIVATE-CARD-TEXT-MUST-NOT-BE-IN-SNAPSHOT"
            }
            private_card["reviewPath"] = "/home/yuki/private/review.json"
            arguments = {
                "connection": connection,
                "cards": [private_card],
                "current_revisions": {"card-1": DIGEST},
                "bundle_revision": "d" * 64,
                "study_deck": {"id": "deck-1"},
                "known_card_ids": {"card-1", "card-2"},
                "generated_at": "2026-07-26T12:00:00Z",
            }
            first = weakness_analysis.build_weakness_snapshot(**arguments)
            second = weakness_analysis.build_weakness_snapshot(**arguments)
            connection.close()

        self.assertEqual(first, second)
        self.assertEqual(1, first["summary"]["outsideDeckAttemptsIgnored"])
        self.assertEqual(1, first["summary"]["unknownCardAttemptsIgnored"])
        self.assertEqual(5, first["maxAttemptId"])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("PRIVATE-CARD-TEXT", serialized)
        self.assertNotIn("/home/yuki/", serialized)

    def test_cli_reads_database_without_modifying_it_and_writes_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "production.sqlite3"
            connection = create_database(database_path)
            bundle = {
                "schemaVersion": "gyousei-production-bundle@1",
                "generatedAt": "2026-07-26T00:00:00Z",
                "visibility": "private",
                "legalAsOf": "2026-04-01",
                "questions": [],
                "explanationCards": [
                    {
                        **card("card-1"),
                        "variants": {
                            "a": "問題文",
                            "b": "やさしい説明",
                            "c": "要点",
                        },
                        "correct": True,
                    }
                ],
                "studyDecks": [
                    {
                        "id": "deck-1",
                        "cardIds": ["card-1"],
                    }
                ],
            }
            bundle_path = root / "bundle.json"
            bundle_path.write_text(
                json.dumps(bundle, ensure_ascii=False), encoding="utf-8"
            )

            import server

            snapshot = server.BundleCatalog(bundle_path).load()
            revision = server.card_answer_revision(
                snapshot.cards["card-1"],
                snapshot,
                snapshot.study_decks["deck-1"],
            )
            add_results(connection, "card-1", [False, False], revision=revision)
            connection.close()
            before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
            snapshot_output = root / "analytics" / "snapshot.json"
            latest_output = root / "weakness-latest.json"

            status_code = weakness_analysis.main(
                [
                    "--db",
                    str(database_path),
                    "--bundle",
                    str(bundle_path),
                    "--snapshot-output",
                    str(snapshot_output),
                    "--latest-output",
                    str(latest_output),
                    "--generated-at",
                    "2026-07-26T12:00:00Z",
                ]
            )

            self.assertEqual(0, status_code)
            self.assertEqual(
                before_hash,
                hashlib.sha256(database_path.read_bytes()).hexdigest(),
            )
            for path in (snapshot_output, latest_output):
                self.assertTrue(path.is_file())
                self.assertEqual(
                    0o600, stat.S_IMODE(path.stat().st_mode)
                )
            self.assertEqual(
                json.loads(snapshot_output.read_text(encoding="utf-8")),
                json.loads(latest_output.read_text(encoding="utf-8")),
            )
            self.assertEqual(
                [],
                list(snapshot_output.parent.glob(".snapshot.json.*")),
            )

    def test_writer_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private" / "snapshot.json"
            output.parent.mkdir()
            output.write_text("old", encoding="utf-8")
            os.chmod(output, 0o644)

            weakness_analysis.atomic_write_private_json(
                output, {"schemaVersion": "test"}
            )

            self.assertEqual(
                0o600, stat.S_IMODE(output.stat().st_mode)
            )
            self.assertEqual(
                {"schemaVersion": "test"},
                json.loads(output.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
