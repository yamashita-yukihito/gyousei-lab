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

## 時刻はサーバー側のものを使う

**FSRSへ渡す時刻は `created_at_server` にする。** `answered_at_client` は端末の時計なので、
時計がずれた端末やオフライン保留のぶんが混ざると、DBの並び（`id` 順）と時刻の並びが逆転する。
FSRSは前回からの経過時間で状態を更新するため、逆転すると負の経過が短期復習として扱われ、
誤った stability・difficulty・due を**例外も出さずに**作ってしまう。

`answered_at_client` は画面表示と所要時間の分析に使い、スケジュールの計算には使わない
（2026-08-05のレビュー指摘で変更）。

## 受験日より後へ期日を置かない

`EXAM_AT`（受験日の試験終了時刻）を越える期日を作らない。切らないと、8月に確信ありで
2回正解しただけで次が397日後になり、本番までに二度と出てこないカードができる。

上限は**その回答の時点**から受験日までの残り日数で決める。`now`（画面を開いた時刻）では
決めない。`now` で決めると、新しい回答が1件も無いのに日が進むだけで過去の期日が動いてしまい、
昨日見た期日と今日見た期日が変わる。最後に `EXAM_AT` でも切って二重に保証する。

## ○×から評価への対応

FSRSは Again / Hard / Good / Easy の4段階を受け取る。**Again だけが「思い出せなかった」で、
Hard・Good・Easy はどれも「思い出せた」**である。ここを取り違えると、間隔が桁で狂う。

| 正誤 | 自信度 | FSRSの評価 |
|---|---|---|
| 誤答 | 問わない | Again |
| 正答 | あてずっぽう（guess） | **Again** |
| 正答 | たぶん（likely） | Good |
| 正答 | 確信あり（sure） | **Good** |
| 正答 | 記録なし | Good |

**2026-08-05に、`guess`→Hard と `sure`→Easy をやめた。** 外部レビューの指摘を実測で確かめた結果、

- `Good×3 → Hard → Good` は次が**93日後**（stability 92.8）
- `Good×3 → Again → Good` は次が**2日後**（stability 1.57）

となり、○×のまぐれ当たりを Hard にすると、本来2日で戻るべきカードが93日後になっていた。
○×は2択なので、当てずっぽうの正解は「思い出せた」ではない。Again が正しい。

`sure`→Easy もやめた。Easy は「ほぼ努力せず即答できた」ときの評価で、「確信がある」とは違う。
長く考えた末の確信まで Easy にすると間隔が伸びすぎる（同じ条件で 278日 vs Good の 163日）。

いまは Hard と Easy を使っていない。使うには「どれだけ苦労して思い出したか」を4段階で
選ばせる必要があり、`card_marks.confidence` のCHECK制約（sure/likely/guess）を広げる
schema変更を伴う。利用者の判断待ちとして保留している。

## 「絶対覚えた」との関係

`AGENTS.md` のとおり、「絶対覚えた」はキューから外すが履歴は消さない。
外れるだけなので、解除すれば過去の回答からそのまま期日が復活する。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

MAX_QUEUE_LIMIT = 200
DEFAULT_QUEUE_LIMIT = 20
DEFAULT_NEW_LIMIT = 6

# 令和8年度の受験日。試験は13時〜16時（法令等・基礎知識あわせて180分）なので、
# 終了時刻を越える期日には意味がない。日付だけでUTCの0時にすると日本時間の9時になり、
# 試験前日の夜に答えたぶんが試験時刻より後へ落ちることがあった（2026-08-05に修正）。
EXAM_AT = datetime(2026, 11, 8, 16, 0, tzinfo=timezone(timedelta(hours=9)))
MIN_MAXIMUM_INTERVAL = 1

# 期日を保存せず毎回計算し直すので、ライブラリの版が変わると同じ履歴から別の期日が出る。
# 版を固定し、違う版が入っていたら気づけるようにする（requirements-runtime.txt と対で管理）。
SUPPORTED_FSRS = ("6.",)

# 誤答は Again。正答でも「あてずっぽう」は思い出せていないので Again にする。
# ○×は2択なので、当てずっぽうの正解を「思い出せた」側（Hard）へ入れてはいけない。
RATING_BY_CONFIDENCE = {"guess": 1, "likely": 3, "sure": 3}
RATING_CORRECT_DEFAULT = 3
RATING_INCORRECT = 1

# FSRSの期日を動かさないモード。高速○×は時間を測って同じ日に何度も回す練習なので、
# ここで期日を動かすと、短時間の反復だけで間隔が伸びてしまう（2026-08-05のレビュー指摘）。
MODES_OUTSIDE_SCHEDULE = ("rapid",)

_RATING_LABELS = {1: "again", 2: "hard", 3: "good", 4: "easy"}


class SchedulerVersionError(RuntimeError):
    """入っている py-fsrs が想定外の版のときに投げる。"""


def days_until_exam(now: datetime, exam_at: datetime = EXAM_AT) -> int:
    """受験日までの残り日数。間隔の上限にそのまま使う。

    残りが減るほど上限も縮むので、直前期はどのカードも短い間隔で戻ってくる。
    受験日を過ぎたあとも1日を下回らせない（0を渡すとFSRSが期日を作れない）。
    """
    return max(MIN_MAXIMUM_INTERVAL, (exam_at - now).days)


def fsrs_version() -> str:
    from importlib.metadata import version

    return version("fsrs")


def _fsrs_modules():
    from fsrs import Card, Rating, Scheduler

    installed = fsrs_version()
    if not installed.startswith(SUPPORTED_FSRS):
        raise SchedulerVersionError(
            f"py-fsrs {installed} は想定外です（対応: {', '.join(SUPPORTED_FSRS)}x）。"
            " 期日は保存せず毎回計算するので、版が変わると同じ履歴から別の期日が出ます。"
        )
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


def _reset_points(
    marks: list[sqlite3.Row], study_deck_id: str | None
) -> tuple[list[str], dict[str, list[str]]]:
    """全リセットと個別リセットを、`card_progress_statistics` と同じ形で取り出す。

    **デッキ単位のリセットは、同じデッキのものだけを見る。** いまは1デッキしか無いので
    表には出ないが、`card_marks` は `study_deck_id` を持っているので、デッキが増えた
    ときに他のデッキのリセットが効いてしまう（2026-08-05のレビュー指摘で修正）。
    """
    deck_points: list[str] = []
    card_points: dict[str, list[str]] = {}
    for mark in marks:
        if mark["action"] != "reset":
            continue
        if mark["scope"] == "deck":
            if study_deck_id is not None and mark["study_deck_id"] != study_deck_id:
                continue
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
    exam_at: datetime = EXAM_AT,
    study_deck_id: str | None = None,
) -> dict[str, dict]:
    """カードIDごとに、FSRSの状態と次回期日を計算して返す。

    回答が1件も無いカードは `state: "new"` になり、`due` は None のままにする。
    """
    Card, Rating, Scheduler = _fsrs_modules()
    moment = now or datetime.now(timezone.utc)

    # enable_fuzzing を切って、同じ履歴なら必ず同じ期日になるようにする。
    # 期日を保存せず毎回計算し直すので、揺らぐと表示が回答のたびに変わってしまう。
    # maximum_interval は「その回答の時点から受験日まで」の残り日数にする。
    schedulers: dict[int, object] = {}

    def scheduler_for(reviewed_at: datetime):
        cap = days_until_exam(reviewed_at, exam_at)
        if cap not in schedulers:
            schedulers[cap] = Scheduler(
                desired_retention=desired_retention,
                enable_fuzzing=False,
                maximum_interval=cap,
            )
        return schedulers[cap]

    wanted = set(card_ids)
    marks = connection.execute("SELECT * FROM card_marks ORDER BY id").fetchall()
    deck_points, card_points = _reset_points(marks, study_deck_id)
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

    rows = connection.execute(
        "SELECT * FROM card_attempts ORDER BY created_at_server, id"
    ).fetchall()
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
        if row["mode"] in MODES_OUTSIDE_SCHEDULE:
            # 高速○×の回答は履歴には残すが、次にいつ出すかは動かさない。
            continue
        # スケジュールの計算にはサーバー時刻を使う。端末の時計は信用しない。
        reviewed_at = _parse_moment(row["created_at_server"])
        if reviewed_at is None:
            continue
        rating_value = rating_for(
            bool(row["is_correct"]), confidence.get(row["event_id"])
        )
        card = fsrs_cards.get(card_id) or Card()
        card, _log = scheduler_for(reviewed_at).review_card(
            card, Rating(rating_value), review_datetime=reviewed_at
        )
        fsrs_cards[card_id] = card
        item = states[card_id]
        item["state"] = "review"
        # 最後の砦。learning step のような日未満の間隔は maximum_interval を通らないので、
        # 受験日そのもので必ず切る。
        due = min(card.due.astimezone(timezone.utc), exam_at.astimezone(timezone.utc))
        item["due"] = due.isoformat()
        item["stability"] = round(card.stability, 4) if card.stability else None
        item["difficulty"] = round(card.difficulty, 4) if card.difficulty else None
        item["reviews"] += 1
        item["lastReviewedAt"] = reviewed_at.isoformat()
        # 画面と分析のために、端末が申告した回答時刻も残す（計算には使わない）。
        item["answeredAtClient"] = row["answered_at_client"]
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
    new_limit: int = DEFAULT_NEW_LIMIT,
    desired_retention: float = 0.9,
    now: datetime | None = None,
    exam_at: datetime = EXAM_AT,
    study_deck_id: str | None = None,
) -> dict:
    """今日出す順にカードを並べる。

    期限を過ぎたものを古い順に出し、足りない分を未回答のカードで埋める。
    「絶対覚えた」はどちらにも入れない（履歴は残したまま、出題からだけ外れる）。

    **はじめてのカードには別枠の上限（`new_limit`）を置く。** 期日が来た復習は
    こなさないと溜まる一方だが、はじめてのカードは自分で増やすものなので、
    そこだけ絞れないと1日の分量を調整できない。復習が多い日は新規が自動的に減る。

    `cards` には**画面の絞り込みを通したあとのカード**を渡す。科目や分野で絞っている
    のに全カードからキューを作ると、その日の枠を他の科目に使ってしまい、選んだ科目に
    期日ぎれがあるのに空に見える（2026-08-05のレビュー指摘）。
    """
    moment = now or datetime.now(timezone.utc)
    order = [card["id"] for card in cards]
    states = review_states(
        connection, order, desired_retention=desired_retention, now=moment,
        exam_at=exam_at, study_deck_id=study_deck_id,
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

    # 復習を先に確保し、残り枠のうち new_limit までを新規で埋める。
    limit = max(0, limit)
    chosen_due = due_items[:limit]
    room = limit - len(chosen_due)
    chosen_new = new_items[: min(room, max(0, new_limit))]
    selected = chosen_due + chosen_new
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
        "newLimit": new_limit,
        "examAt": exam_at.astimezone(timezone.utc).isoformat(),
        "maximumIntervalDays": days_until_exam(moment, exam_at),
        "schedulerVersion": fsrs_version(),
        "counts": {
            "eligible": len(order),
            "due": len(due_items),
            "new": len(new_items),
            "certain": sum(1 for s in states.values() if s["certain"]),
            "upcoming": len(upcoming),
            "selected": len(selected),
            "selectedDue": len(chosen_due),
            "selectedNew": len(chosen_new),
        },
        "nextDueAt": upcoming[0]["due"] if upcoming else None,
        "cardIds": [state["cardId"] for state in selected],
        "items": selected,
    }
