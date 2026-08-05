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
        """FSRSの4段階のうち、Again だけが「思い出せなかった」。

        2026-08-05に guess→Hard と sure→Easy をやめた。Hard は成功側の評価なので、
        ○×のまぐれ当たりを入れると間隔が桁で伸びる。Easy は「即答できた」ときだけ。
        """
        self.assertEqual(self.study_queue.rating_for(False, "sure"), 1)
        self.assertEqual(self.study_queue.rating_for(True, "guess"), 1)
        self.assertEqual(self.study_queue.rating_for(True, "likely"), 3)
        self.assertEqual(self.study_queue.rating_for(True, "sure"), 3)
        self.assertEqual(self.study_queue.rating_for(True, None), 3)
        # 2026-08-05に4評価を直接選ばせる形にした。again/hard/good/easy が現在の値。
        self.assertEqual(self.study_queue.rating_for(True, "again"), 1)
        self.assertEqual(self.study_queue.rating_for(True, "hard"), 2)
        self.assertEqual(self.study_queue.rating_for(True, "good"), 3)
        self.assertEqual(self.study_queue.rating_for(True, "easy"), 4)
        # 誤答は自己申告に関係なく Again。画面でも誤答には評価を選ばせない。
        self.assertEqual(self.study_queue.rating_for(False, "easy"), 1)

    def test_confidence_changes_the_next_due_date(self) -> None:
        """同じ「正解」でも、当てずっぽうなら誤答と同じ扱いで早く戻ってくる。"""
        sure = self.attempt("card-sure", True, BASE)
        self.mark("card-sure", "confidence", BASE, confidence="sure",
                  attempt_event_id=sure)
        guess = self.attempt("card-guess", True, BASE)
        self.mark("card-guess", "confidence", BASE, confidence="guess",
                  attempt_event_id=guess)
        self.attempt("card-wrong", False, BASE)

        states = self.states(["card-sure", "card-guess", "card-wrong"])
        self.assertEqual(states["card-sure"]["lastRating"], "good")
        self.assertEqual(states["card-guess"]["lastRating"], "again")
        self.assertEqual(states["card-wrong"]["lastRating"], "again")
        # 当てずっぽうの正解は、誤答と同じ期日になる。
        self.assertEqual(states["card-guess"]["due"], states["card-wrong"]["due"])
        # 思い出せた側は、それより先になる。
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

    def test_new_cards_have_their_own_limit(self) -> None:
        """復習は溜まるとこなすしかないが、はじめてのカードは自分で絞れる。"""
        for index in range(4):
            self.attempt(f"due-{index}", False, BASE)
        cards = [{"id": f"due-{i}"} for i in range(4)] + [
            {"id": f"new-{i}"} for i in range(10)
        ]
        queue = self.study_queue.build_queue(
            self.connection, cards, limit=20, new_limit=3,
            now=BASE + timedelta(days=1),
        )
        self.assertEqual(queue["counts"]["selectedDue"], 4)
        self.assertEqual(queue["counts"]["selectedNew"], 3)
        self.assertEqual(queue["counts"]["selected"], 7)

        # 0にすれば復習だけになる。
        queue = self.study_queue.build_queue(
            self.connection, cards, limit=20, new_limit=0,
            now=BASE + timedelta(days=1),
        )
        self.assertEqual(queue["counts"]["selectedNew"], 0)
        self.assertEqual(queue["counts"]["selected"], 4)

    def test_reviews_win_the_slots_when_the_total_limit_is_small(self) -> None:
        for index in range(5):
            self.attempt(f"due-{index}", False, BASE)
        cards = [{"id": f"due-{i}"} for i in range(5)] + [{"id": "new-0"}]
        queue = self.study_queue.build_queue(
            self.connection, cards, limit=3, new_limit=6,
            now=BASE + timedelta(days=1),
        )
        self.assertEqual(queue["counts"]["selectedDue"], 3)
        self.assertEqual(queue["counts"]["selectedNew"], 0)

    def test_intervals_never_run_past_the_exam_date(self) -> None:
        """受験日を越える期日を作らない。越えると本番までに二度と出てこない。

        確信ありで正解を重ねると、上限を切らない既定では
        8日 → 66日 → 397日 → 1875日 と伸び、3回目以降は受験日のはるか先になる。
        """
        # 受験日は EXAM_AT（試験終了時刻）で持っている。ここに定数を書き写すと、
        # 本体を変えたときにテストだけ古くなる。
        exam = self.study_queue.EXAM_AT.astimezone(timezone.utc)
        moment = BASE
        rounds = 0
        while moment < exam and rounds < 12:
            event_id = self.attempt("card-easy", True, moment)
            self.mark("card-easy", "confidence", moment, confidence="sure",
                      attempt_event_id=event_id)
            state = self.study_queue.review_states(
                self.connection, ["card-easy"], now=moment
            )["card-easy"]
            due = self.study_queue._parse_moment(state["due"])
            self.assertLessEqual(
                due, exam,
                f"受験日より後の期日が出た: {state['due']}（{rounds + 1}回目）",
            )
            moment = due
            rounds += 1
        self.assertGreaterEqual(rounds, 3, "受験日まで3回以上は戻ってくるはず")

    def test_days_until_exam_shrinks_and_never_reaches_zero(self) -> None:
        far = self.study_queue.days_until_exam(datetime(2026, 8, 5, tzinfo=timezone.utc))
        near = self.study_queue.days_until_exam(datetime(2026, 11, 1, tzinfo=timezone.utc))
        after = self.study_queue.days_until_exam(datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(far, 95)
        self.assertEqual(near, 7)
        self.assertEqual(after, self.study_queue.MIN_MAXIMUM_INTERVAL)
        self.assertGreaterEqual(after, 1)

    def test_schedule_uses_server_time_not_client_time(self) -> None:
        """端末の時計がずれても、期日の計算が壊れない。

        `answered_at_client` は端末が申告した時刻なので、時計のずれや
        オフライン保留で保存順と逆転しうる。FSRSは前回からの経過で状態を作るため、
        逆転すると誤った期日を例外も出さずに作ってしまう。
        """
        # 1件目: 端末が「1年後」を申告している（時計が進んだ端末）
        self.connection.execute(
            """
            INSERT INTO card_attempts (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                selected_answer, correct_answer, is_correct, mode,
                answered_at_client, payload_digest, created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "e1", "s1", "d1", "card-skew", "rev", 1, 1, 1, "auto",
                (BASE + timedelta(days=365)).isoformat(), "digest", BASE.isoformat(),
            ),
        )
        # 2件目: 時計を直したあと。保存順は後ろだが、端末時刻は前になる。
        self.connection.execute(
            """
            INSERT INTO card_attempts (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                selected_answer, correct_answer, is_correct, mode,
                answered_at_client, payload_digest, created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "e2", "s1", "d1", "card-skew", "rev", 1, 1, 1, "auto",
                BASE.isoformat(), "digest",
                (BASE + timedelta(hours=1)).isoformat(),
            ),
        )
        self.connection.commit()
        state = self.states(["card-skew"])["card-skew"]
        self.assertEqual(state["reviews"], 2)
        # サーバー時刻で並べているので、最後の回答は2件目（BASE+1時間）になる。
        self.assertEqual(
            state["lastReviewedAt"], (BASE + timedelta(hours=1)).isoformat()
        )
        # 端末の申告値は残すが、計算には使わない。
        self.assertEqual(state["answeredAtClient"], BASE.isoformat())
        # 期日が1年後の申告に引きずられていない。
        self.assertLess(state["due"], (BASE + timedelta(days=200)).isoformat())

    def test_deck_reset_does_not_cross_decks(self) -> None:
        """デッキ単位のリセットは、同じデッキのものだけを見る。"""
        self.attempt("card-a", True, BASE)
        self.connection.execute(
            """
            INSERT INTO card_marks (
                event_id, session_id, study_deck_id, card_id, attempt_event_id,
                action, scope, confidence, marked_at_client, payload_digest,
                created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "m-other", "s1", "other-deck", None, None, "reset", "deck", None,
                (BASE + timedelta(minutes=5)).isoformat(), "digest",
                (BASE + timedelta(minutes=5)).isoformat(),
            ),
        )
        self.connection.commit()
        # 別デッキのリセットなので、d1 のカードは new へ戻らない。
        states = self.study_queue.review_states(
            self.connection, ["card-a"], now=BASE + timedelta(days=3),
            study_deck_id="d1",
        )
        self.assertEqual(states["card-a"]["state"], "review")
        # 同じデッキを指定すれば効く。
        states = self.study_queue.review_states(
            self.connection, ["card-a"], now=BASE + timedelta(days=3),
            study_deck_id="other-deck",
        )
        self.assertEqual(states["card-a"]["state"], "new")

    def test_due_never_passes_the_exam_moment_even_just_before(self) -> None:
        """試験直前に答えたぶんが、試験の後ろへ落ちない。

        maximum_interval は日単位なので、残り1日のときに夜遅く答えると
        24時間後＝試験の後ろになりうる。最後に EXAM_AT で切っている。
        """
        exam = self.study_queue.EXAM_AT
        just_before = exam - timedelta(hours=6)
        for rating_correct, confidence in (
            (False, None), (True, "guess"), (True, "likely"), (True, "sure"),
        ):
            card_id = f"card-{rating_correct}-{confidence}"
            event_id = self.attempt(card_id, rating_correct, just_before)
            if confidence:
                self.mark(card_id, "confidence", just_before,
                          confidence=confidence, attempt_event_id=event_id)
            state = self.study_queue.review_states(
                self.connection, [card_id], now=just_before
            )[card_id]
            self.assertLessEqual(
                state["due"], exam.astimezone(timezone.utc).isoformat(),
                f"{card_id} の期日が試験時刻より後: {state['due']}",
            )

    def test_schedule_does_not_drift_as_days_pass(self) -> None:
        """新しい回答が無いのに、日が進むだけで過去の期日が動かない。

        上限を「画面を開いた時刻」から数えると、replay全体が組み直されて
        昨日見た期日と今日見た期日が変わってしまう。回答時点から数えている。
        """
        self.attempt("card-stable", True, BASE)
        first = self.states(["card-stable"], days=1)["card-stable"]["due"]
        later = self.states(["card-stable"], days=40)["card-stable"]["due"]
        self.assertEqual(first, later)

    def test_lucky_guess_counts_as_a_lapse_not_a_hard_success(self) -> None:
        """まぐれ当たりは Again。Hard（＝思い出せた）にしない。

        FSRSは Again だけが「思い出せなかった」で、Hard・Good・Easy はどれも成功である。
        ○×は2択なので、当てずっぽうの正解を Hard にすると
        「苦労したが思い出せた」と誤認され、間隔が桁で伸びる。
        """
        self.assertEqual(self.study_queue.rating_for(True, "guess"), 1)
        self.assertEqual(self.study_queue.rating_for(True, "likely"), 3)
        # 「確信あり」も Easy にしない。Easy は「ほぼ努力せず即答」のときだけ。
        self.assertEqual(self.study_queue.rating_for(True, "sure"), 3)
        self.assertEqual(self.study_queue.rating_for(True, None), 3)
        self.assertEqual(self.study_queue.rating_for(False, "sure"), 1)

    def test_guess_and_wrong_answers_reach_the_same_schedule(self) -> None:
        wrong = self.attempt("card-wrong", False, BASE)
        guess = self.attempt("card-guess", True, BASE)
        self.mark("card-guess", "confidence", BASE, confidence="guess",
                  attempt_event_id=guess)
        states = self.states(["card-wrong", "card-guess"])
        self.assertEqual(states["card-wrong"]["lastRating"], "again")
        self.assertEqual(states["card-guess"]["lastRating"], "again")
        self.assertEqual(states["card-wrong"]["due"], states["card-guess"]["due"])

    def test_rapid_mode_answers_do_not_move_the_schedule(self) -> None:
        """高速○×は履歴には残すが、次にいつ出すかは動かさない。"""
        self.connection.execute(
            """
            INSERT INTO card_attempts (
                event_id, session_id, study_deck_id, card_id, answer_revision,
                selected_answer, correct_answer, is_correct, mode,
                answered_at_client, payload_digest, created_at_server
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "rapid-1", "s1", "d1", "card-rapid", "rev", 1, 1, 1, "rapid",
                BASE.isoformat(), "digest", BASE.isoformat(),
            ),
        )
        self.connection.commit()
        state = self.states(["card-rapid"])["card-rapid"]
        self.assertEqual(state["state"], "new")
        self.assertIsNone(state["due"])
        # 行そのものは残っている（正答率の集計には効き続ける）。
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM card_attempts WHERE card_id = ?", ("card-rapid",)
        ).fetchone()
        self.assertEqual(rows[0], 1)

    def test_rating_previews_show_each_choice(self) -> None:
        """ボタンへ出す「この評価を選ぶと次はいつか」。選ぶ前に見えるようにする。"""
        preview = self.study_queue.rating_previews(
            self.connection, "card-new", now=BASE
        )
        self.assertEqual(sorted(preview), ["again", "easy", "good", "hard"])
        for label, text in preview.items():
            self.assertTrue(text, f"{label} の目安が空")
        # 評価が上がるほど先になる。
        self.assertEqual(preview["again"], "1分")
        self.assertIn("日", preview["easy"])

    def test_scheduler_version_is_pinned(self) -> None:
        version = self.study_queue.fsrs_version()
        self.assertTrue(
            version.startswith(self.study_queue.SUPPORTED_FSRS),
            f"想定外の py-fsrs {version}。requirements-runtime.txt と揃えてください",
        )

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
