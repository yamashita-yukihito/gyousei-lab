#!/usr/bin/env python3
"""勉強しない3分野（地方自治法・家族法・商法と問37以外の会社法）を、形だけで解くための数字を過去問20年分から数える。

使い方:
  python3 scripts/simple_rules/skip_fields.py           # 表示だけ
  python3 scripts/simple_rules/skip_fields.py --write   # static/technique/skip-fields.json を書き換える

読むのは非公開編集データの過去問だけで、書き出すのは割合と問題数だけである。問題文は書き出さない。

measure.py の RULES へ足さないのは、あちらの語は科目単位で学習カードのヒントにも使われるため。
語を同じデータで選ぶと当たりが甘く出るので、「旧10年で選んだ語を新10年で試す」とその逆も出す。
語の候補は CANDIDATES に固定し、ここから MIN_COUNT 肢以上、両方の10年で MIN_LIFT ポイント以上同じ向きに傾いた語だけを本表に出す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure as M  # noqa: E402

OUT = M.ROOT / "static" / "technique" / "skip-fields.json"
SPLIT = M.SPLIT_YEAR
MIN_COUNT = M.MIN_COUNT
MIN_LIFT = M.MIN_LIFT
SELECT_COUNT = 5  # 片方の10年で語を選ぶときの最低肢数
SELECT_LIFT = 0.10

LOCAL_WORDS = r"地方自治法|普通地方公共団体|地方公共団体の(長|議会)|住民監査請求|住民訴訟|公の施設|直接請求|条例|特別区|法定受託事務|自治事務|議会"
FAMILY_EXTRA = {(2010, 34), (2018, 34)}  # 問35以外で家族法を問うた問題
NUMBER_WORD = "[0-9０-９一二三四五六七八九十]+(日|年|か月|箇月|ヶ月)"
EXEMPT = "問わず|にかかわらず|かかわりなく"

# 語の候補（正規表現, 画面の語, 見方）。
CANDIDATES = {
    "local": [
        ("であり、", "「〜であり、」", "理屈を足して誤った結論を自然に見せる形です。"),
        ("ならない。", "文末が「〜ならない」", "義務を足して重くする形です。"),
        ("することができる", "「することができる」", "「誤っているもの」の問題では選ばず、「正しいもの」の問題で残します。"),
        ("有する者", "「◯◯を有する者」", "選挙権を有する者の署名の話に多い形です。"),
        ("監査委員", "「監査委員」", "監査委員の肢は条文どおりのことが多めです。"),
        ("規則", "「規則」", ""), ("条例", "「条例」", ""), ("議決", "「議決」", ""), ("必要", "「必要」", ""),
        ("大臣", "「大臣」", ""), ("知事", "「知事」", ""), ("委員会", "「委員会」", ""), ("裁判所", "「裁判所」", ""),
        ("許可", "「許可」", ""), ("認可", "「認可」", ""), ("同意", "「同意」", ""), ("できない。", "文末が「できない」", ""),
        ("できるが", "「できるが」", ""), ("のみ|に限", "「のみ」「に限る」", ""), ("過半数|分の", "「過半数」「◯分の◯」", "数字を変えて誤りにする定番です。"),
        ("すべて|全て|一切|必ず(?!しも)", "「すべて」「一切」「必ず」", ""), (EXEMPT, "「問わず」「にかかわらず」", ""),
        ("ため|ために", "「ため」", "理由を足して誤った結論を自然に見せる形です。"), ("対象とな", "「対象となる」", ""), (NUMBER_WORD, "日数・年数", ""),
    ],
    "family": [
        ("配偶者", "「配偶者」", "相続分や権利を認める肢に多い語です。"),
        ("家庭裁判所", "「家庭裁判所」", "手続を足して誤りにする形です。"),
        ("原則|ただし|除き", "「原則」「ただし」「除き」", "例外まで書いた肢は、条文を写したものが多めです。"),
        ("前に", "「◯◯の前に」", ""), ("ため", "「ため」", ""), ("請求", "「請求」", ""), ("取り消|取消", "「取消し」", ""),
        ("無効", "「無効」", ""), ("当然", "「当然」", ""), ("同意", "「同意」", ""), ("必要", "「必要」", ""),
        ("できない。", "文末が「できない」", ""), ("できる。", "文末が「できる」", "できないことをできると広げる形です。"), ("ならない。", "文末が「ならない」", ""),
        ("認知", "「認知」", ""), ("養子", "「養子」", ""), ("相続", "「相続」", ""), ("遺言", "「遺言」", "肢数が少ないので目安です。"),
        (EXEMPT, "「問わず」「にかかわらず」", ""), ("のみ|に限", "「のみ」「に限る」", ""),
        ("すべて|全て|一切|必ず(?!しも)", "「すべて」「一切」「必ず」", ""), ("であっても|でも", "「であっても」", ""),
    ],
    "company": [
        ("株主総会の決議|総会の決議", "「株主総会の決議」", "軽い手続を重い手続へ書き換える定番です。"),
        ("しなければならない", "「しなければならない」", "義務を足して重くする形です。"),
        (EXEMPT + "|によらず|らず、", "「問わず」「にかかわらず」「〜らず、」", "要件を消して制度を広げる形です。"),
        ("から", "「から」", "理屈を足す形、手続の起点をずらす形です。"),
        ("場合に", "「場合に」", "肢が多いぶん差は小さめです。"),
        ("発行", "「発行」", "株式発行の手続を細かく変えて作りやすい場所です。"),
        ("または|もしくは", "「または」「もしくは」", "条文を写すと残る言葉です。"),
        ("種類", "「種類」", "種類株式の肢は条文どおりのことが多めです。"),
        ("定款で定", "「定款で定める」", "「定款で別段の定めができる」は正しいことが多めです。"),
        ("原則|ただし|除き", "「原則」「ただし」「除き」", "例外まで書いた肢は、条文を写したものが多めです。"),
        ("全員", "「全員」", ""), ("定款", "「定款」（定める以外も含む）", ""), ("特別決議", "「特別決議」", ""),
        ("取締役会", "「取締役会」", ""), ("裁判所", "「裁判所」", ""), ("することができない|できない。", "「できない」", ""),
        ("できる。", "文末が「できる」", ""), ("責任", "「責任」", ""), ("請求", "「請求」", ""), ("旨", "「旨」", ""),
        ("登記", "「登記」", ""), ("必要", "「必要」", ""), ("当然", "「当然」", ""), ("許可|認可", "「許可」「認可」", ""),
        ("過半数|分の", "「過半数」「◯分の◯」", "数字を変えて誤りにする定番です。"), ("のみ|に限", "「のみ」「に限る」", ""),
        ("すべて|全て|一切|必ず(?!しも)", "「すべて」「一切」「必ず」", ""), (NUMBER_WORD, "日数・年数", ""),
    ],
}


def question_text(q: dict) -> str:
    return (q.get("questionText") or "") + " " + " ".join(c["text"] for c in q.get("choices") or [])


def field_of(q: dict) -> str | None:
    subject, year, number = q["subjectLabel"], q["examYear"], q["questionNumber"]
    labels = q.get("labels") or []
    if subject == "行政法":
        if not 21 <= number <= 25:
            return None
        if year >= SPLIT:
            return "local" if "地方自治法" in labels else None
        text = question_text(q)
        return "local" if len(re.findall(LOCAL_WORDS, text)) >= 3 or "地方自治法" in text[:120] else None
    if subject == "民法":
        return "family" if number == 35 or (year, number) in FAMILY_EXTRA else None
    if subject == "商法・会社法" and number <= 40:
        stem = (q.get("questionText") or "")[:200]
        if re.search("設立|発起人", stem) or len(re.findall("発起人|設立時", question_text(q))) >= 3:
            return None  # 問37の設立は勉強して解く
        return "company"
    return None


def format_of(q: dict) -> str:
    choices = [c["text"].strip() for c in q.get("choices") or []]
    text = (q.get("questionText") or "") + (q.get("instructionText") or "")
    if (q.get("answer") or {}).get("kind") != "option" or not choices:
        return "other"
    if any(re.fullmatch(r"[ア-オ](・[ア-オ])+", c) for c in choices):
        return "combo"
    if re.search("いくつ", text[:300]) or all(re.fullmatch("[一二三四五]つ|ゼロ|なし", c) for c in choices):
        return "count"
    if re.search("空欄", text[:120]) or not M.stem_kind(q) or sum(map(len, choices)) < 60:
        return "other"
    return "single"


def load_questions() -> dict[str, list[dict]]:
    fields: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(M.data_dir().glob("*/extracted/*/*.json")):
        q = json.loads(path.read_text(encoding="utf-8"))
        field = None if q.get("isWithdrawn") else field_of(q)
        if field:
            q["fmt"] = format_of(q)
            fields[field].append(q)
    return fields


def answer_index(q: dict) -> int:
    return [str(c["label"]) for c in q["choices"]].index(str(q["answer"]["value"]))


def rate(part: float, whole: int) -> int | None:
    return round(part / whole * 100) if whole else None


def periods(questions: list[dict], hits) -> dict:
    old = [q for q in questions if q["examYear"] < SPLIT]
    new = [q for q in questions if q["examYear"] >= SPLIT]
    return {
        "count": len(questions), "rate": rate(sum(map(hits, questions)), len(questions)),
        "oldCount": len(old), "old": rate(sum(map(hits, old)), len(old)),
        "newCount": len(new), "new": rate(sum(map(hits, new)), len(new)),
    }


def longest(q: dict) -> float:
    lengths = [len(c["text"]) for c in q["choices"]]
    top = [i for i, value in enumerate(lengths) if value == max(lengths)]
    return (answer_index(q) in top) / len(top)


def is_number(number: int):
    return lambda q: answer_index(q) == number - 1


def word_stats(rows: list[dict], pattern: str, label: str, note: str) -> dict | None:
    hit = [r for r in rows if re.search(pattern, r["text"])]
    if not hit:
        return None
    old = [r for r in hit if r["year"] < SPLIT]
    new = [r for r in hit if r["year"] >= SPLIT]
    if not (old and new):
        lift_old = lift_new = 0.0
    else:
        lift_old = sum(r["truth"] - r["base"] for r in old) / len(old)
        lift_new = sum(r["truth"] - r["base"] for r in new) / len(new)
    direction = "true" if sum(r["truth"] - r["base"] for r in hit) >= 0 else "false"
    sign = 1 if direction == "true" else -1
    stable = len(hit) >= MIN_COUNT and min(sign * lift_old, sign * lift_new) * 100 >= MIN_LIFT
    return {
        "label": label, "note": note, "direction": direction, "count": len(hit),
        "hit": M.percent(hit, "truth", direction), "base": M.percent(hit, "base", direction),
        "early": M.percent(old, "truth", direction), "earlyBase": M.percent(old, "base", direction),
        "late": M.percent(new, "truth", direction), "lateBase": M.percent(new, "base", direction),
        "stable": stable,
    }


def select_words(rows: list[dict], candidates: list[tuple]) -> tuple[list[str], list[str]]:
    good, bad = [], []
    for pattern, _, _ in candidates:
        hit = [r for r in rows if re.search(pattern, r["text"])]
        if len(hit) < SELECT_COUNT:
            continue
        lift = sum(r["truth"] - r["base"] for r in hit) / len(hit)
        if lift >= SELECT_LIFT:
            good.append(pattern)
        elif lift <= -SELECT_LIFT:
            bad.append(pattern)
    return good, bad


def score(text: str, good: list[str], bad: list[str]) -> int:
    return sum(bool(re.search(w, text)) for w in good) - sum(bool(re.search(w, text)) for w in bad)


def word_pick_single(q: dict, good: list[str], bad: list[str]) -> float:
    texts = [c["text"] for c in q["choices"]]
    kind = M.stem_kind(q)
    scores = [score(t, good, bad) for t in texts]
    target = max(scores) if kind == "true" else min(scores)
    picks = [i for i, s in enumerate(scores) if s == target]
    if kind == "false":  # 語で決まらないとき、誤りを選ぶ問題だけ最も長い肢へ寄せる
        most = max(len(texts[i]) for i in picks)
        picks = [i for i in picks if len(texts[i]) == most]
    return (answer_index(q) in picks) / len(picks)


def word_pick_combo(q: dict, good: list[str], bad: list[str]) -> float:
    parts = dict(M.statements(q))
    options = {str(c["label"]): c["text"].strip().split("・") for c in q["choices"]}
    scores = {k: sum(score(parts.get(x, ""), good, bad) for x in v) for k, v in options.items()}
    target = min(scores.values()) if M.stem_kind(q) == "false" else max(scores.values())
    picks = [k for k, s in scores.items() if s == target]
    return (str(q["answer"]["value"]) in picks) / len(picks)


def out_of_sample(rows: list[dict], candidates: list[tuple], questions: list[dict]) -> dict:
    """片方の10年で語を選び、もう片方の10年の問題へ当てた正答率。"""
    result = {}
    for name, fmt, pick in (("single", "single", word_pick_single), ("combo", "combo", word_pick_combo)):
        for train_old, key in ((True, "oldToNew"), (False, "newToOld")):
            good, bad = select_words([r for r in rows if (r["year"] < SPLIT) == train_old], candidates)
            test = [q for q in questions if q["fmt"] == fmt and (q["examYear"] < SPLIT) != train_old]
            result.setdefault(name, {})[key] = rate(sum(pick(q, good, bad) for q in test), len(test))
            result[name][key + "Count"] = len(test)
    return result


def build() -> dict:
    fields = load_questions()
    keys = {(q["examYear"], q["questionNumber"]): f for f, qs in fields.items() for q in qs}
    rows_by_field: dict[str, list[dict]] = defaultdict(list)
    for row in M.load_rows():
        field = keys.get((row["year"], row["number"]))
        if field:
            rows_by_field[field].append(row)
    result = {}
    for field, questions in fields.items():
        rows = rows_by_field[field]
        selectable = [q for q in questions if (q.get("answer") or {}).get("kind") == "option"]
        single = [q for q in questions if q["fmt"] == "single"]
        numbers = {str(n): periods(selectable, is_number(n)) for n in range(1, 6)}
        single_numbers = {str(n): periods(single, is_number(n)) for n in range(1, 6)}
        words = [word_stats(rows, *candidate) for candidate in CANDIDATES[field]]
        words = [w for w in words if w]
        for word in words:
            word["hasNote"] = bool(word["note"])
        result[field] = {
            "questions": len(questions),
            "formats": dict(Counter(q["fmt"] for q in questions)),
            "numbers": numbers,
            "singleNumbers": single_numbers,
            "longestTrue": periods([q for q in single if M.stem_kind(q) == "true"], longest),
            "longestFalse": periods([q for q in single if M.stem_kind(q) == "false"], longest),
            "words": sorted([w for w in words if w["stable"]], key=lambda w: (w["direction"], -w["hit"])),
            "notWords": [w["label"] for w in words if not w["stable"] and w["count"] >= MIN_COUNT],
            "outOfSample": out_of_sample(rows, CANDIDATES[field], questions),
        }
    return {"criteria": {"minCount": MIN_COUNT, "minLift": MIN_LIFT}, "fields": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print("書き出した:", OUT)
    for field, value in data["fields"].items():
        print("==", field, value["questions"], value["formats"])
        print(" 番号", {k: (v["rate"], v["old"], v["new"]) for k, v in value["numbers"].items()})
        print(" 単一番号", {k: (v["rate"], v["old"], v["new"]) for k, v in value["singleNumbers"].items()})
        print(" 最長 正しい問", value["longestTrue"], "\n 最長 誤り問", value["longestFalse"])
        print(" 語", [(w["label"], w["direction"], w["count"], w["hit"], w["base"], w["early"], w["late"]) for w in value["words"]])
        print(" 語にならない", value["notWords"])
        print(" 期間外", value["outOfSample"])


if __name__ == "__main__":
    main()
