#!/usr/bin/env python3
"""「絞り込みテクニック」の語の割合を過去問20年分から数え直し、「法律家の常識」の○×を確かめる。

使い方:
  python3 scripts/simple_rules/measure.py            # 表示だけ
  python3 scripts/simple_rules/measure.py --write    # static/technique/word-signals.json を書き換える
  python3 scripts/simple_rules/measure.py --check    # static/simple-rules/content.md の○×を正解番号から確かめる

読むのは非公開編集データの過去問だけで、書き出すのは割合と肢数だけである。問題文は書き出さない。
置き場所は authoring と同じ環境変数 GYOUSEI_DATA_ROOT で変えられる（既定は ~/.local/share/yuki-services/gyousei-lab/authoring）。

肢の正誤は、問いの向きと正解番号から求める。比較の基準は「同じ問題の中で正しい肢の割合」にする。
「正しいものはどれか」の問題は肢の8割が誤りなので、科目の平均と比べると問い方の偏りが混ざるためである。

旧10年（平成18〜27年度）と新10年（平成28〜令和7年度）は、それぞれの期間の同じ問題の平均と比べる。
両方の10年で同じ向きに MIN_LIFT ポイント以上傾き、肢が MIN_COUNT 以上ある語だけを stable にし、
画面はそれ以外を「参考」に回す（片方の10年だけの傾きは、総当たりで探した偶然の可能性が高い）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "static" / "simple-rules"
SIGNALS = ROOT / "static" / "technique" / "word-signals.json"
DEFAULT_DATA_ROOT = Path(os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring"))

SPLIT_YEAR = 2016  # この年度から新10年（平成28年度）
MIN_COUNT = 10
MIN_LIFT = 5

TRUE_STEM = re.compile(r"(正しい|妥当な|適切な|合致する|適当な)(もの|記述|組合せ)")
FALSE_STEM = re.compile(r"(誤っている|妥当でない|適切でない|合致しない|適当でない|正しくない)(もの|記述|組合せ)")
EXAM = re.compile(r"^(.+?年)問(\d+)(?:肢([^○×]+))?([○×])?$")

# 憲法の文末で「違反しない」と同じ意味になる言い方。ke-unconst から除き、ke-noviolate で拾う。
NOT_VIOLATE = r"(?:違反|反)しない|(?:違反|反)する(?:もの|こと)(?:で|と)は(?:ない|いえない)|(?:違反|反)するとは(?:いえない|言えない)|違憲(?:で|と)は(?:ない|いえない)"

# id, 科目（None は全科目）, 語の正規表現, 向き（false=その語があれば誤り寄り）, 除く語, 画面の語, 見方
# 除く語は、肢のどこかに当たればその肢を数えない（否定の形など、向きが逆になる言い方を外すため）。
RULES = [
    ("all-block", None, r"認められない|許されない|ことはない", "false", None, "「認められない」「許されない」「ことはない」", "道をふさぐ文末は、結論を逆にして作りやすい形です。"),
    ("all-exists", None, r"問わず|にかかわらず|かかわりなく", "false", None, "「問わず」「にかかわらず」", "要件を消して制度を広げる形です。事実を述べているだけなら無視します。"),
    # 「〜するため、」（目的）と「3分の2以上、」（数の下限）は理屈を足す形ではないので数えない。
    ("all-reason", None, r"であるから|ものであるから|(?:である|た|ない|ある|いる)ため、|(?:る|た|い)以上、|ので、", "false", None, "「であるから」「ため、」「ので、」", "理屈を足して誤った結論を自然に見せる形です。"),
    ("all-notnec", None, r"必ずしも|当然には|直ちには|とは限らない", "true", None, "「必ずしも」「当然には」", "例が1つあれば正しくなるので、誤りに作り変えにくい形です。"),
    ("all-may", None, r"ことがある|場合がある|余地", "true", None, "「ことがある」「場合がある」", "2択で迷ったら、断定している肢よりこちらを残します。"),
    ("all-degree", None, r"著しく|社会通念|信義|相当と認め|合理的", "true", None, "「著しく」「社会通念」「合理的」", "判例をそのまま写した肢に残る言葉です。"),
    ("all-special", None, r"特段の事情|特別の事情", "true", None, "「特段の事情」", "判例が逃げ道を残すときの言葉です。"),
    ("gy-suit", "行政法", r"取消訴訟を提起す", "false", None, "「取消訴訟を提起する」", "提起「できる」も「できない」も、20年で正しい肢がありませんでした。"),
    ("gy-relief", "行政法", r"申し立て|重大な損害", "false", None, "「申し立て」「重大な損害」", "仮の救済は要件が細かく、少し変えるだけで誤りになります。"),
    ("gy-block", "行政法", r"認められない|ことはない", "false", None, "「認められない」「ことはない」", "「正しいもの」を選ぶ問題で先に切ります。"),
    ("gy-limit", "行政法", r"に限られ", "false", None, "「に限られる」", "条文にない条件を足す形です。"),
    ("gy-exists", "行政法", r"規定(がある|している|されている|を置いている)|定められている|定めている|設けられている|明文", "false", None, "「規定している」「定められている」", "ない制度を語るときの形です。"),
    ("gy-fiction", "行政法", r"みなす|みなされ", "false", None, "「みなす」「みなされる」", "自動で結論が決まる仕組みは多くありません。"),
    ("gy-discretion", "行政法", r"合理的|に委ね", "true", None, "「合理的」「に委ねられる」", "裁量を認める判例の言葉です。"),
    ("gy-effort", "行政法", r"努め|努力", "true", None, "「努める」「努力」", "努力義務か法的義務かで迷ったら、努力義務を残します。"),
    ("mi-guardian", "民法", r"被後見人", "false", None, "「被後見人」", "細かい決まりが多く、誤りの材料になります。"),
    ("mi-norescind", "民法", r"解除(することが)?できない", "false", None, "「解除できない」", "解除を否定する肢は誤り寄りです。"),
    ("mi-exists", "民法", r"問わず|にかかわらず|かかわりなく", "false", None, "「問わず」「にかかわらず」", "民法は事情で結論を変えるので、問わない制度は多くありません。"),
    ("mi-joint", "民法", r"共同相続|全員", "false", None, "「共同相続」「全員」", "相続人の間の話は誤りの材料になりやすいです。"),
    ("mi-solo", "民法", r"単独で", "false", None, "「単独で」", "一人でできると広げる形です。"),
    ("mi-register", "民法", r"登記.*(必要|備えなければ|しなければ)", "true", None, "「登記が必要」", "登記なしで勝てる場面は少数です。"),
    ("ke-noviolate", "憲法", r"(?:" + NOT_VIOLATE + r"|合憲である)。?\s*$", "true", None, "文末が「違反しない」「違反するものではない」「合憲である」", "判例を知らない問題で残します。"),
    ("ke-case", "憲法", r"判例である", "false", None, "「とするのが判例である」", "判例にない立場を判例らしく見せる形です。"),
    ("ke-law", "憲法", r"法律の", "false", None, "「法律の」", "「法律の定める〜」の周りは、条文を少し変えて作りやすい場所です。"),
    ("ke-strict", "憲法", r"厳格|必要不可欠|明白かつ現在", "false", None, "「厳格」「必要不可欠」", "学説の言葉を判例として書く形です。ただし、裁判所の事前差止めは判例自身が「厳格かつ明確な要件」と言います。"),
    # 「を認めない」「を認められない」まで数えると、肯定の語として測れない。
    ("ke-allow", "憲法", r"を認め", "true", r"を認め(?:ない|ず|られない|られず|ることは(?:でき)?ない|ることができない|るものではない|る余地はない|るべきではない|ていない|なかった)", "「を認める」", "権利や権限を肯定する肢を残します。"),
    ("ke-mandate", "憲法", r"憲法上|憲法は", "false", None, "「憲法上」「憲法は」", "憲法の要請を広げる形です。"),
    ("ke-unconst", "憲法", r"違憲|違反する|反する|許されない", "false", NOT_VIOLATE + r"|合憲|違憲とまでは", "「違憲」「違反する」「許されない」", "合憲の判決を逆にした形です。"),
    ("sh-offering", "商法・会社法", r"募集株式の", "false", None, "「募集株式の」", "募集設立の細かい手続は誤りの材料になります。"),
    ("sh-court", "商法・会社法", r"裁判所", "false", None, "「裁判所」", "肢数が少ないので目安です。"),
    ("sh-cannot", "商法・会社法", r"することができない", "true", None, "「することができない」", "商法では禁止の肢が正しい寄りです。行政法と逆なので混ぜません。"),
    ("sh-charter", "商法・会社法", r"定款で定め", "true", None, "「定款で定める」", "細かい肢でも、正しいと見ます。"),
    ("sh-all", "商法・会社法", r"全員", "true", None, "「全員」", "株式の数のような骨組みは全員の同意です。設立時取締役の選任は過半数なので、人選びの肢には使いません。"),
    ("ch-basis", "基礎知識", r"に基づいて", "false", None, "「に基づいて」", "根拠の法律を差し替える形です。"),
    ("ch-country", "基礎知識", r"国では", "true", None, "「◯◯国では」", "一般知識で何も分からないときに残します。"),
    ("ch-may", "基礎知識", r"ことがある|場合がある|余地", "true", None, "「ことがある」「場合がある」", "足切り科目なので、この見方でも必ず1肢は切ります。"),
    ("ki-may", "基礎法学", r"ことがある|場合がある|余地", "true", None, "「ことがある」「場合がある」", "肢数が少ないので目安です。"),
    ("ki-can", "基礎法学", r"できる。?\s*$", "true", None, "文末が「できる」", "決めつける肢より、こちらを残します。"),
]

# よく言われるが、測ると効かなかった語。画面の「効かなかった手」に、正しい割合と同じ問題の平均を出す。
# 形は RULES と同じで、向きは使わない（正しい割合をそのまま出す）。
NO_EFFECT = [
    ("no-absolute", None, r"すべて|全て|必ず(?!しも)|一切", "true", None, "「すべて」「必ず」「一切」", "断定の語があれば誤り、とよく言われる。「必ずしも」は逆の向きなので数えない。"),
    ("no-only", None, r"のみ(?!ならず)|(?<!できる)だけ(?!でなく)", "true", None, "「〜のみ」「〜だけ」", "限定の語があれば誤り、とよく言われる。「できるだけ」「だけでなく」は数えない。"),
]


def data_dir() -> Path:
    root = os.environ.get("GYOUSEI_DATA_ROOT")
    return (Path(root) if root else DEFAULT_DATA_ROOT) / "all_subjects"


def statements(question: dict) -> list[tuple[str, str]]:
    found = []
    for part in re.split(r"(?=(?:^|\s)[ア-オ]．)", question.get("questionText") or ""):
        part = part.strip()
        if re.match("[ア-オ]．", part):
            found.append((part[0], part[2:].strip()))
    return found or [(c["label"], c["text"]) for c in question.get("choices") or []]


def stem_kind(question: dict) -> str | None:
    text = re.split(r"\s[ア-オ]．", question.get("questionText") or "")[0]
    text += " " + (question.get("instructionText") or "")
    wrong, right = FALSE_STEM.search(text), TRUE_STEM.search(text)
    if wrong and right:
        return "false" if wrong.start() > right.start() else "true"
    return "false" if wrong else "true" if right else None


def load_rows(data: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(str((data or data_dir()) / "*" / "extracted" / "*" / "*.json"))):
        question = json.loads(Path(path).read_text(encoding="utf-8"))
        answer = question.get("answer") or {}
        kind = stem_kind(question)
        if answer.get("kind") != "option" or question.get("isWithdrawn") or not kind:
            continue
        choices = {str(c["label"]): c["text"] for c in question.get("choices") or []}
        selected = choices.get(str(answer.get("value")))
        if selected is None:
            continue
        items = []
        if re.fullmatch(r"[ア-オ](・[ア-オ])+", selected.strip()):
            chosen = set(selected.strip().split("・"))
            parts = dict(statements(question))
            if not all(label in parts for label in "アイウ"):
                continue
            items = [(label, text, label in chosen) for label, text in parts.items()]
        else:
            if any(re.fullmatch(r"[ア-オ](・[ア-オ])*", value.strip()) for value in choices.values()):
                continue
            if len(choices) < 4 or sum(len(value) for value in choices.values()) < 60:
                continue
            items = [(label, text, label == str(answer.get("value"))) for label, text in choices.items()]
        block = []
        for label, text, in_answer in items:
            block.append({
                "year": question["examYear"], "era": question["eraYear"], "number": question["questionNumber"],
                "label": label, "text": text, "subject": question["subjectLabel"],
                "truth": in_answer if kind == "true" else not in_answer,
            })
        base = sum(row["truth"] for row in block) / len(block)
        for row in block:
            row["base"] = base
        rows.extend(block)
    return rows


def share(rows: list[dict], key: str) -> float:
    return sum(row[key] for row in rows) / len(rows)


def matches(rule: tuple, row: dict) -> bool:
    _, subject, pattern, _, exclude, _, _ = rule
    if subject is not None and row["subject"] != subject:
        return False
    return bool(re.search(pattern, row["text"])) and not (exclude and re.search(exclude, row["text"]))


def percent(part: list[dict], key: str, direction: str) -> int | None:
    """その向きに当たった割合（%）。direction が true なら正しい割合、false なら誤りの割合。"""
    if not part:
        return None
    value = share(part, key)
    return round((value if direction == "true" else 1 - value) * 100)


def rule_stats(rule: tuple, rows: list[dict]) -> dict | None:
    rule_id, subject, _, direction, _, label, note = rule
    hit = [row for row in rows if matches(rule, row)]
    if not hit:
        return None
    early = [row for row in hit if row["year"] < SPLIT_YEAR]
    late = [row for row in hit if row["year"] >= SPLIT_YEAR]
    result = {
        "id": rule_id, "subject": subject or "全科目", "label": label, "note": note,
        "direction": direction, "count": len(hit),
        "hit": percent(hit, "truth", direction), "base": percent(hit, "base", direction),
        "early": percent(early, "truth", direction), "earlyBase": percent(early, "base", direction),
        "late": percent(late, "truth", direction), "lateBase": percent(late, "base", direction),
    }
    reasons = []
    if len(hit) < MIN_COUNT:
        reasons.append(f"{len(hit)}肢しかない")
    for name, key in (("旧10年", "early"), ("新10年", "late")):
        value, base = result[key], result[key + "Base"]
        if value is None:
            reasons.append(f"{name}に肢がない")
        elif value < base:
            reasons.append(f"{name}では平均を下回る")
        elif value - base < MIN_LIFT:
            reasons.append(f"{name}では平均との差が小さい")
    result["stable"] = not reasons
    result["unstableReason"] = "・".join(reasons)
    return result


def measure(rows: list[dict]) -> list[dict]:
    result = []
    for rule in RULES:
        stats = rule_stats(rule, rows)
        if stats is None:
            raise SystemExit(f"{rule[0]}: 肢が1つもない")
        result.append(stats)
    return result


def measure_no_effect(rows: list[dict]) -> list[dict]:
    result = []
    for rule in NO_EFFECT:
        stats = rule_stats(rule, rows)
        if stats is None:
            raise SystemExit(f"{rule[0]}: 肢が1つもない")
        # 正しい割合と平均をそのまま出す。傾かないことを示す欄なので stable は使わない。
        for key in ("stable", "unstableReason", "direction"):
            stats.pop(key)
        stats["truth"] = stats.pop("hit")
        result.append(stats)
    return result


def score(row: dict) -> int:
    """語の傾きで肢に付ける点。正しい寄りの語があれば+1、誤り寄りの語があれば-1。

    同じ向きの語がいくつ当たっても1点にする。全科目の語と科目の語は同じ語を含むことが多く
    （「ことがある」「問わず」など）、足し算すると同じ手がかりを二重に数えるためである。
    """
    directions = {rule[3] for rule in RULES if matches(rule, row)}
    return ("true" in directions) - ("false" in directions)


def simulate(rows: list[dict]) -> dict:
    """語の傾向だけで5肢の問題を解いたときの正答率。肢を見ずに選ぶと20%である。

    pairs は、正解の肢と外れの肢を1つずつ残した2択で、語の点に差がつく組の割合（decided）と、
    差がついたときに点の高いほうが正解だった割合（accuracy）。勘なら50%である。
    語は同じ20年分から選んでいるので、どちらも実力より高めに出る（上限の目安）。
    """
    questions = defaultdict(list)
    for row in rows:
        questions[(row["year"], row["number"])].append(row)
    total = hits = with_signal = 0.0
    late_total = late_hits = 0.0
    pairs = decided = pair_hits = 0
    for block in questions.values():
        if len(block) != 5 or not block[0]["label"].isdigit():
            continue
        count = sum(row["truth"] for row in block)
        if count not in (1, 4):
            continue
        want = count == 1
        scores = [score(row) if want else -score(row) for row in block]
        best = max(scores)
        picked = [row for row, value in zip(block, scores) if value == best]
        gained = sum(row["truth"] == want for row in picked) / len(picked)
        total += 1
        hits += gained
        with_signal += any(value != 0 for value in scores)
        if block[0]["year"] >= SPLIT_YEAR:
            late_total += 1
            late_hits += gained
        answer = next(value for row, value in zip(block, scores) if row["truth"] == want)
        for row, value in zip(block, scores):
            if row["truth"] == want:
                continue
            pairs += 1
            if value != answer:
                decided += 1
                pair_hits += answer > value
    if not total:
        raise SystemExit("5肢の問題が1つもない")
    return {
        "questions": int(total), "accuracy": round(hits / total * 100), "withSignal": round(with_signal / total * 100),
        "lateQuestions": int(late_total), "lateAccuracy": round(late_hits / late_total * 100) if late_total else None,
        "pairs": {
            "pairs": pairs, "decided": round(decided / pairs * 100) if pairs else None,
            "accuracy": round(pair_hits / decided * 100) if decided else None,
        },
    }


def check(rows: list[dict]) -> int:
    index = {(row["era"], row["number"], row["label"]): row for row in rows}
    text = re.sub(r"<!--.*?-->", "", (PAGE / "content.md").read_text(encoding="utf-8"), flags=re.S)
    bad = checked = unknown = 0
    for line in text.splitlines():
        found = re.match(r"\s*過去問 :: (.*)", line)
        if not found:
            continue
        for token in found.group(1).split():
            era, number, label, mark = EXAM.match(token).groups()
            if not label or not mark:
                continue
            row = index.get((era, int(number), label))
            if row is None:
                unknown += 1
                print("正解番号から判定できない:", token)
                continue
            checked += 1
            if row["truth"] != (mark == "○"):
                bad += 1
                print("食い違い:", token, row["text"][:60])
    print(f"確かめた肢 {checked} / 判定できない肢 {unknown} / 食い違い {bad}")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = load_rows()
    print(f"肢 {len(rows)}")
    if not rows:
        raise SystemExit(f"過去問が見つからない: {data_dir()}")
    if args.check:
        raise SystemExit(1 if check(rows) else 0)
    result = {
        "choices": len(rows),
        "criteria": {"splitYear": SPLIT_YEAR, "minCount": MIN_COUNT, "minLift": MIN_LIFT},
        "simulation": simulate(rows),
        "rules": measure(rows),
        "noEffect": measure_no_effect(rows),
    }
    print(result["simulation"])
    for value in result["rules"]:
        print(value["id"], value["hit"], value["base"], value["count"], value["early"], value["earlyBase"],
              value["late"], value["lateBase"], "本表" if value["stable"] else "参考: " + value["unstableReason"])
    for value in result["noEffect"]:
        print(value["id"], "正しい", value["truth"], "平均", value["base"], value["count"])
    if args.write:
        SIGNALS.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
