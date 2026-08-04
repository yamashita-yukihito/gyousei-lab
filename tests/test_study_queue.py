"""「今日の学習」キュー（py-fsrs）の回帰テスト。

ここで固定したいのは次の4つ。

- 正誤と自信度からFSRSの4段階へ落とす対応
- 「絶対覚えた」はキューから外れるが、履歴も期日も消えない
- リセットより前の回答は次回期日の計算に数えない（行そのものは残す）
- 同じ履歴なら必ず同じ期日になる（fuzzingを切ってある）
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _skip_without_fsrs() -> None:
    try:
        import fsrs  # noqa: F401
    except ImportError:  # pragma: no cover - 環境依存
        raise unittest.SkipTest("fsrs が入っていない環境ではキューを検証しない")


class StudyQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        _skip_without_fsrs()
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.original_db = os.environ.get("GYOUSEI_LAB_DB")
        os.environ["GYOUSEI_LAB_DB"] = str(self.db_path)
        import server

        self.server = importlib.reload(server)
        self.server.init_database()
        self.connection = self.server.connect()
        self.connection.row_factory = sqlite3.Row
        import study_queue

        self.study_queue = study_queue

    def tearDown(self) -> None:
        self.connection.close()
        if self.original_db is None:
            os.environ.pop("GYOUSEI_LAB_DB", None)
        else:
            os.environ["GYOUSEI_LAB_DB"] = self.original_db
        self.temp.cleanup()

    def attempt(self, card_id: str, correct: bool, when: datetime) -> str:
        event_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO card_attempts (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                selected_answer, correct_answer, is_correct, mode,
                answered_at_client, payload_digest, created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id, "s1", "d1", card_id, "rev", 1, 1, int(correct),
                "auto", when.isoformat(), "digest", when.isoformat(),
            ),
        )
        self.connection.commit()
        return event_id

    def mark(
        self,
        card_id: str | None,
        action: str,
        when: datetime,
        *,
        confidence: str | None = None,
        attempt_event_id: str | None = None,
        scope: str = "card",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO card_marks (
                event_id, session_id, study_deck_id, card_id, attempt_event_id,
                action, scope, confidence, marked_at_client, payload_digest,
                created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()), "s1", "d1", card_id, attempt_event_id, action,
                scope, confidence, when.isoformat(), "digest", when.isoformat(),
            ),
        )
        self.connection.commit()

    def states(self, card_ids: list[str], *, days: int = 3) -> dict:
        return self.study_queue.review_states(
            self.connection, card_ids, now=BASE + timedelta(days=days)
        )

    def test_rating_mapping_uses_confidence(self) -> None:
        self.assertEqual(self.study_queue.rating_for(False, "sure"), 1)
        self.assertEqual(self.study_queue.rating_for(True, "guess"), 2)
        self.assertEqual(self.study_queue.rating_for(True, "likely"), 3)
        self.assertEqual(self.study_queue.rating_for(True, "sure"), 4)
        self.assertEqual(self.study_queue.rating_for(True, None), 3)

    def test_confidence_changes_the_next_due_date(self) -> None:
        """同じ「正解」でも、手ごたえが無ければ早く戻ってくる。"""
        sure = self.attempt("card-sure", True, BASE)
        self.mark("card-sure", "confidence", BASE, confidence="sure",
                  attempt_event_id=sure)
        guess = self.attempt("card-guess", True, BASE)
        self.mark("card-guess", "confidence", BASE, confidence="guess",
                  attempt_event_id=guess)
        self.attempt("card-wrong", False, BASE)

        states = self.states(["card-sure", "card-guess", "card-wrong"])
        self.assertEqual(states["card-sure"]["lastRating"], "easy")
        self.assertEqual(states["card-guess"]["lastRating"], "hard")
        self.assertEqual(states["card-wrong"]["lastRating"], "again")
        self.assertLess(states["card-wrong"]["due"], states["card-guess"]["due"])
        self.assertLess(states["card-guess"]["due"], states["card-sure"]["due"])

    def test_certain_cards_leave_the_queue_but_keep_their_history(self) -> None:
        """「絶対覚えた」はキューから外すだけ。期日も回答も消さない。"""
        self.attempt("card-certain", True, BASE)
        self.mark("card-certain", "certain", BASE + timedelta(minutes=1))
        self.attempt("card-plain", True, BASE)

        states = self.states(["card-certain", "card-plain"])
        self.assertTrue(states["card-certain"]["certain"])
        self.assertEqual(states["card-certain"]["reviews"], 1)
        self.assertIsNotNone(states["card-certain"]["due"])

        queue = self.study_queue.build_queue(
            self.connection,
            [{"id": "card-certain"}, {"id": "card-plain"}],
            now=BASE + timedelta(days=3),
        )
        self.assertNotIn("card-certain", queue["cardIds"])
        self.assertIn("card-plain", queue["cardIds"])
        self.assertEqual(queue["counts"]["certain"], 1)

        # 解除すれば、同じ履歴からそのまま戻ってくる。
        self.mark("card-certain", "uncertain", BASE + timedelta(minutes=2))
        queue = self.study_queue.build_queue(
            self.connection,
            [{"id": "card-certain"}, {"id": "card-plain"}],
            now=BASE + timedelta(days=3),
        )
        self.assertIn("card-certain", queue["cardIds"])

    def test_reset_restarts_the_schedule_without_deleting_attempts(self) -> None:
        self.attempt("card-reset", True, BASE)
        self.mark("card-reset", "reset", BASE + timedelta(minutes=2))
        states = self.states(["card-reset"])
        self.assertEqual(states["card-reset"]["state"], "new")
        self.assertIsNone(states["card-reset"]["due"])
        # 回答そのものは残っている。
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM card_attempts WHERE card_id = ?", ("card-reset",)
        ).fetchone()
        self.assertEqual(rows[0], 1)

    def test_deck_reset_applies_to_every_card(self) -> None:
        self.attempt("card-a", True, BASE)
        self.attempt("card-b", False, BASE)
        self.mark(None, "reset", BASE + timedelta(minutes=5), scope="deck")
        states = self.states(["card-a", "card-b"])
        self.assertEqual(states["card-a"]["state"], "new")
        self.assertEqual(states["card-b"]["state"], "new")

    def test_due_cards_come_before_new_cards(self) -> None:
        self.attempt("card-due", False, BASE)
        queue = self.study_queue.build_queue(
            self.connection,
            [{"id": "card-new"}, {"id": "card-due"}],
            now=BASE + timedelta(days=3),
        )
        self.assertEqual(queue["cardIds"], ["card-due", "card-new"])
        self.assertEqual(queue["counts"]["due"], 1)
        self.assertEqual(queue["counts"]["new"], 1)

    def test_schedule_is_stable_for_the_same_history(self) -> None:
        """期日を保存せず毎回計算するので、揺らいでは困る。"""
        self.attempt("card-x", True, BASE)
        first = self.states(["card-x"])["card-x"]["due"]
        second = self.states(["card-x"])["card-x"]["due"]
        self.assertEqual(first, second)

    def test_limit_bounds_the_queue(self) -> None:
        for index in range(5):
            self.attempt(f"card-{index}", False, BASE)
        queue = self.study_queue.build_queue(
            self.connection,
            [{"id": f"card-{index}"} for index in range(5)],
            limit=2,
            now=BASE + timedelta(days=3),
        )
        self.assertEqual(len(queue["cardIds"]), 2)
        self.assertEqual(queue["counts"]["selected"], 2)


if __name__ == "__main__":
    unittest.main()
