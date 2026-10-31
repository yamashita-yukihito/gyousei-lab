#!/usr/bin/env python3
"""「絞り込みテクニック」の語の割合を過去問20年分から数え直し、「法律家の常識」の○×を確かめる。

使い方:
  python3 scripts/simple_rules/measure.py            # 表示だけ
  python3 scripts/simple_rules/measure.py --write    # static/technique/word-signals.json を書き換える
  python3 scripts/simple_rules/measure.py --check    # static/simple-rules/content.md の○×を正解番号から確かめる

読むのは非公開編集データの過去問だけで、書き出すのは割合と肢数だけである。問題文は書き出さない。

肢の正誤は、問いの向きと正解番号から求める。比較の基準は「同じ問題の中で正しい肢の割合」にする。
「正しいものはどれか」の問題は肢の8割が誤りなので、科目の平均と比べると問い方の偏りが混ざるためである。
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
DATA = Path(os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring/all_subjects"))

TRUE_STEM = re.compile(r"(正しい|妥当な|適切な|合致する|適当な)(もの|記述|組合せ)")
FALSE_STEM = re.compile(r"(誤っている|妥当でない|適切でない|合致しない|適当でない|正しくない)(もの|記述|組合せ)")
EXAM = re.compile(r"^(.+?年)問(\d+)(?:肢([^○×]+))?([○×])?$")

# id, 科目（None は全科目）, 語の正規表現, 向き（false=その語があれば誤り寄り）, 除く語, 画面の語, 見方
RULES = [
    ("all-block", None, r"認められない|許されない|ことはない", "false", None, "「認められない」「許されない」「ことはない」", "道をふさぐ文末は、結論を逆にして作りやすい形です。"),
    ("all-exists", None, r"問わず|にかかわらず|かかわりなく", "false", None, "「問わず」「にかかわらず」", "要件を消して制度を広げる形です。事実を述べているだけなら無視します。"),
    ("all-reason", None, r"であるから|ものであるから|ため、|以上、|ので、", "false", None, "「であるから」「ため、」「ので、」", "理屈を足して誤った結論を自然に見せる形です。"),
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
    ("ke-noviolate", "憲法", r"(違反しない|合憲である)。?$", "true", None, "文末が「違反しない」「合憲である」", "判例を知らない問題で残します（平成28年度以降だけの7肢です）。"),
    ("ke-case", "憲法", r"判例である", "false", None, "「とするのが判例である」", "判例にない立場を判例らしく見せる形です。"),
    ("ke-law", "憲法", r"法律の", "false", None, "「法律の」", "「法律の定める〜」の周りは、条文を少し変えて作りやすい場所です。"),
    ("ke-strict", "憲法", r"厳格|必要不可欠|明白かつ現在", "false", None, "「厳格」「必要不可欠」", "学説の言葉を判例として書く形です。"),
    ("ke-allow", "憲法", r"を認め", "true", None, "「を認める」", "権利や権限を肯定する肢を残します。"),
    ("ke-mandate", "憲法", r"憲法上|憲法は", "false", None, "「憲法上」「憲法は」", "憲法の要請を広げる形です。"),
    ("ke-unconst", "憲法", r"違憲|違反する|反する|許されない", "false", r"違反しない|反しない|合憲", "「違憲」「違反する」「許されない」", "合憲の判決を逆にした形です。"),
    ("sh-offering", "商法・会社法", r"募集株式の", "false", None, "「募集株式の」", "募集設立の細かい手続は誤りの材料になります。"),
    ("sh-court", "商法・会社法", r"裁判所", "false", None, "「裁判所」", "肢数が少ないので目安です。"),
    ("sh-cannot", "商法・会社法", r"することができない", "true", None, "「することができない」", "商法では禁止の肢が正しい寄りです。行政法と逆なので混ぜません。"),
    ("sh-charter", "商法・会社法", r"定款で定め", "true", None, "「定款で定める」", "細かい肢でも、正しいと見ます。"),
    ("sh-all", "商法・会社法", r"全員", "true", None, "「全員」", "「過半数」に変えた肢のほうを疑います。"),
    ("ch-basis", "基礎知識", r"に基づいて", "false", None, "「に基づいて」", "根拠の法律を差し替える形です。"),
    ("ch-country", "基礎知識", r"国では", "true", None, "「◯◯国では」", "一般知識で何も分からないときに残します。"),
    ("ch-may", "基礎知識", r"ことがある|場合がある|余地", "true", None, "「ことがある」「場合がある」", "足切り科目なので、この見方でも必ず1肢は切ります。"),
    ("ki-may", "基礎法学", r"ことがある|場合がある|余地", "true", None, "「ことがある」「場合がある」", "肢数が少ないので目安です。"),
    ("ki-can", "基礎法学", r"できる。$", "true", None, "文末が「できる」", "決めつける肢より、こちらを残します。"),
]


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


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(str(DATA / "*" / "extracted" / "*" / "*.json"))):
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


def measure(rows: list[dict]) -> list[dict]:
    result = []
    for rule in RULES:
        rule_id, subject, _, direction, _, label, note = rule
        hit = [row for row in rows if matches(rule, row)]
        if not hit:
            raise SystemExit(f"{rule_id}: 肢が1つもない")

        def as_hit(part: list[dict]) -> int | None:
            if not part:
                return None
            value = share(part, "truth")
            return round((value if direction == "true" else 1 - value) * 100)

        base = share(hit, "base")
        result.append({
            "id": rule_id, "subject": subject or "全科目", "label": label, "note": note,
            "direction": direction, "count": len(hit), "hit": as_hit(hit),
            "base": round((base if direction == "true" else 1 - base) * 100),
            "early": as_hit([row for row in hit if row["year"] < 2016]),
            "late": as_hit([row for row in hit if row["year"] >= 2016]),
        })
    return result


def simulate(rows: list[dict]) -> dict:
    """語の傾向だけで5肢の問題を解いたときの正答率。肢を見ずに選ぶと20%である。"""
    questions = defaultdict(list)
    for row in rows:
        questions[(row["year"], row["number"])].append(row)
    total = hits = with_signal = 0.0
    for block in questions.values():
        if len(block) != 5 or not block[0]["label"].isdigit():
            continue
        count = sum(row["truth"] for row in block)
        if count not in (1, 4):
            continue
        want = count == 1
        scores = []
        for row in block:
            score = sum((1 if rule[3] == "true" else -1) for rule in RULES if matches(rule, row))
            scores.append(score if want else -score)
        best = max(scores)
        picked = [row for row, score in zip(block, scores) if score == best]
        total += 1
        hits += sum(row["truth"] == want for row in picked) / len(picked)
        with_signal += any(score != 0 for score in scores)
    return {"questions": int(total), "accuracy": round(hits / total * 100), "withSignal": round(with_signal / total * 100)}


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
    if args.check:
        raise SystemExit(1 if check(rows) else 0)
    result = {"choices": len(rows), "simulation": simulate(rows), "rules": measure(rows)}
    print(result["simulation"])
    for value in result["rules"]:
        print(value["id"], value["hit"], value["base"], value["count"], value["early"], value["late"])
    if args.write:
        SIGNALS.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
