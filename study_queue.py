"""「今日の学習」キュー。py-fsrs で次に解く日を決める。

## なぜ新しいテーブルを作らないのか

FSRSの状態（stability・difficulty・次回期日）は、`card_attempts` と `card_marks` から
**毎回導出する**。専用テーブルへ保存しない。理由は3つある。

- 回答イベントは追記型で、そこがこのラボの正本である。導出値を別に持つと、
  正本と写しの2つができて、ずれたときにどちらが正しいか分からなくなる。
- 「リセット」は「ここより前を習得判定に数えない」という区切りなので、
  区切りが増えるたびに状態を計算し直す必要がある。保存していると作り直しが要る。
- カードは数百枚、回答は数百件の規模なので、毎回replayしても一瞬で終わる。

したがって `production.sqlite3` のschemaは変更しない。

## ○×から4段階の評価への対応

FSRSは Again / Hard / Good / Easy の4段階を受け取る。このラボのカードは○×なので、
**回答ごとの自信度**（`card_marks` の `confidence`）を組み合わせて4段階へ広げる。

| 正誤 | 自信度 | FSRSの評価 |
|---|---|---|
| 誤答 | 問わない | Again |
| 正答 | あてずっぽう（guess） | Hard |
| 正答 | たぶん（likely） | Good |
| 正答 | 確信あり（sure） | Easy |
| 正答 | 記録なし | Good |

自信度は出題対象の判定には効かせない決まりだが、**次にいつ出すか**へ効かせるのは
その決まりに反しない。手ごたえが無いまま当たった問題を早めに戻す、という使い方である。

## 「絶対覚えた」との関係

`AGENTS.md` のとおり、「絶対覚えた」はキューから外すが履歴は消さない。
外れるだけなので、解除すれば過去の回答からそのまま期日が復活する。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

MAX_QUEUE_LIMIT = 200
DEFAULT_QUEUE_LIMIT = 30

# 誤答→Again、正答は自信度で Hard / Good / Easy へ分ける。
RATING_BY_CONFIDENCE = {"guess": 2, "likely": 3, "sure": 4}
RATING_CORRECT_DEFAULT = 3
RATING_INCORRECT = 1

_RATING_LABELS = {1: "again", 2: "hard", 3: "good", 4: "easy"}


def _fsrs_modules():
    from fsrs import Card, Rating, Scheduler

    return Card, Rating, Scheduler


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _reset_points(marks: list[sqlite3.Row]) -> tuple[list[str], dict[str, list[str]]]:
    """全リセットと個別リセットを、`card_progress_statistics` と同じ形で取り出す。"""
    deck_points: list[str] = []
    card_points: dict[str, list[str]] = {}
    for mark in marks:
        if mark["action"] != "reset":
            continue
        if mark["scope"] == "deck":
            deck_points.append(mark["created_at_server"])
        elif mark["card_id"]:
            card_points.setdefault(mark["card_id"], []).append(mark["created_at_server"])
    return sorted(deck_points), card_points


def _confidence_by_attempt(marks: list[sqlite3.Row]) -> dict[str, str]:
    out: dict[str, str] = {}
    for mark in marks:
        if mark["action"] != "confidence":
            continue
        if mark["attempt_event_id"] and mark["confidence"]:
            out[mark["attempt_event_id"]] = mark["confidence"]
    return out


def _certain_cards(marks: list[sqlite3.Row]) -> set[str]:
    """「絶対覚えた」が最後に押されたままのカード。解除（uncertain）で外れる。"""
    state: dict[str, bool] = {}
    for mark in marks:
        if mark["action"] in {"certain", "uncertain"} and mark["card_id"]:
            state[mark["card_id"]] = mark["action"] == "certain"
    return {card_id for card_id, on in state.items() if on}


def rating_for(is_correct: bool, confidence: str | None) -> int:
    if not is_correct:
        return RATING_INCORRECT
    return RATING_BY_CONFIDENCE.get(confidence or "", RATING_CORRECT_DEFAULT)


def review_states(
    connection: sqlite3.Connection,
    card_ids: list[str],
    *,
    desired_retention: float = 0.9,
    now: datetime | None = None,
) -> dict[str, dict]:
    """カードIDごとに、FSRSの状態と次回期日を計算して返す。

    回答が1件も無いカードは `state: "new"` になり、`due` は None のままにする。
    """
    Card, Rating, Scheduler = _fsrs_modules()
    # enable_fuzzing を切って、同じ履歴なら必ず同じ期日になるようにする。
    # 期日を保存せず毎回計算し直すので、揺らぐと表示が回答のたびに変わってしまう。
    scheduler = Scheduler(desired_retention=desired_retention, enable_fuzzing=False)
    moment = now or datetime.now(timezone.utc)

    wanted = set(card_ids)
    marks = connection.execute("SELECT * FROM card_marks ORDER BY id").fetchall()
    deck_points, card_points = _reset_points(marks)
    confidence = _confidence_by_attempt(marks)
    certain = _certain_cards(marks)

    states: dict[str, dict] = {
        card_id: {
            "cardId": card_id,
            "state": "new",
            "due": None,
            "stability": None,
            "difficulty": None,
            "reviews": 0,
            "lastReviewedAt": None,
            "lastRating": None,
            "certain": card_id in certain,
        }
        for card_id in wanted
    }
    fsrs_cards: dict[str, object] = {}

    rows = connection.execute("SELECT * FROM card_attempts ORDER BY id").fetchall()
    for row in rows:
        card_id = row["card_id"]
        if card_id not in wanted:
            continue
        cutoffs = sorted(deck_points + card_points.get(card_id, []))
        cutoff = cutoffs[-1] if cutoffs else None
        if cutoff and row["created_at_server"] <= cutoff:
            # リセットより前の回答は、次にいつ出すかの計算に数えない。
            # 行そのものは残っているので、正答率や卒業回数の集計には効き続ける。
            fsrs_cards.pop(card_id, None)
            item = states[card_id]
            item.update(
                state="new", due=None, stability=None, difficulty=None,
                reviews=0, lastReviewedAt=None, lastRating=None,
            )
            continue
        reviewed_at = _parse_moment(row["answered_at_client"]) or _parse_moment(
            row["created_at_server"]
        )
        if reviewed_at is None:
            continue
        rating_value = rating_for(
            bool(row["is_correct"]), confidence.get(row["event_id"])
        )
        card = fsrs_cards.get(card_id) or Card()
        card, _log = scheduler.review_card(
            card, Rating(rating_value), review_datetime=reviewed_at
        )
        fsrs_cards[card_id] = card
        item = states[card_id]
        item["state"] = "review"
        item["due"] = card.due.astimezone(timezone.utc).isoformat()
        item["stability"] = round(card.stability, 4) if card.stability else None
        item["difficulty"] = round(card.difficulty, 4) if card.difficulty else None
        item["reviews"] += 1
        item["lastReviewedAt"] = reviewed_at.isoformat()
        item["lastRating"] = _RATING_LABELS[rating_value]

    for item in states.values():
        due = _parse_moment(item["due"])
        item["overdueDays"] = (
            round((moment - due).total_seconds() / 86400, 2) if due else None
        )
        item["dueNow"] = bool(due and due <= moment)
    return states


def build_queue(
    connection: sqlite3.Connection,
    cards: list[dict],
    *,
    limit: int = DEFAULT_QUEUE_LIMIT,
    desired_retention: float = 0.9,
    now: datetime | None = None,
) -> dict:
    """今日出す順にカードを並べる。

    期限を過ぎたものを古い順に出し、足りない分を未回答のカードで埋める。
    「絶対覚えた」はどちらにも入れない（履歴は残したまま、出題からだけ外れる）。
    """
    moment = now or datetime.now(timezone.utc)
    order = [card["id"] for card in cards]
    states = review_states(
        connection, order, desired_retention=desired_retention, now=moment
    )
    position = {card_id: index for index, card_id in enumerate(order)}

    due_items = [
        state
        for state in states.values()
        if state["dueNow"] and not state["certain"]
    ]
    due_items.sort(key=lambda s: (s["due"], position[s["cardId"]]))
    new_items = [
        state
        for state in states.values()
        if state["state"] == "new" and not state["certain"]
    ]
    new_items.sort(key=lambda s: position[s["cardId"]])

    selected = (due_items + new_items)[: max(0, limit)]
    upcoming = sorted(
        (
            state
            for state in states.values()
            if state["due"] and not state["dueNow"] and not state["certain"]
        ),
        key=lambda s: s["due"],
    )
    return {
        "generatedAt": moment.isoformat(),
        "desiredRetention": desired_retention,
        "limit": limit,
        "counts": {
            "eligible": len(order),
            "due": len(due_items),
            "new": len(new_items),
            "certain": sum(1 for s in states.values() if s["certain"]),
            "upcoming": len(upcoming),
            "selected": len(selected),
        },
        "nextDueAt": upcoming[0]["due"] if upcoming else None,
        "cardIds": [state["cardId"] for state in selected],
        "items": selected,
    }
