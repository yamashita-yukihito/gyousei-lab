#!/usr/bin/env python3
"""「法律家の常識」のページに出す数字を、過去問20年分から数え直す。

使い方:
  python3 scripts/simple_rules/measure.py            # 数字を画面へ出さず表示だけ
  python3 scripts/simple_rules/measure.py --write    # static/simple-rules/measured.json を書き換える
  python3 scripts/simple_rules/measure.py --check    # content.md の○×を正解番号から確かめる

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
DATA = Path(os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring/all_subjects"))

TRUE_STEM = re.compile(r"(正しい|妥当な|適切な|合致する|適当な)(もの|記述|組合せ)")
FALSE_STEM = re.compile(r"(誤っている|妥当でない|適切でない|合致しない|適当でない|正しくない)(もの|記述|組合せ)")
EXAM = re.compile(r"^(.+?年)問(\d+)(?:肢([^○×]+))?([○×])?$")

# id, 科目（None は全科目）, 語の正規表現, 向き（false=その語があれば誤り寄り）, 除く語
RULES = [
    ("all-exists", None, r"問わず|にかかわらず|かかわりなく", "false", None),
    ("all-block", None, r"認められない|許されない|ことはない", "false", None),
    ("all-reason", None, r"であるから|ものであるから|ため、|以上、|ので、", "false", None),
    ("all-may", None, r"ことがある|場合がある|余地", "true", None),
    ("all-notnec", None, r"必ずしも|当然には|直ちには|とは限らない", "true", None),
    ("all-degree", None, r"著しく|社会通念|信義|相当と認め|合理的", "true", None),
    ("all-special", None, r"特段の事情|特別の事情", "true", None),
    ("gy-limit", "行政法", r"に限られ", "false", None),
    ("gy-block", "行政法", r"認められない|ことはない", "false", None),
    ("gy-suit", "行政法", r"取消訴訟を提起す", "false", None),
    ("gy-relief", "行政法", r"申し立て|重大な損害", "false", None),
    ("gy-exists", "行政法", r"規定(がある|している|されている|を置いている)|定められている|定めている|設けられている|明文", "false", None),
    ("gy-fiction", "行政法", r"みなす|みなされ", "false", None),
    ("gy-effort", "行政法", r"努め|努力", "true", None),
    ("gy-discretion", "行政法", r"合理的|に委ね", "true", None),
    ("mi-exists", "民法", r"問わず|にかかわらず|かかわりなく", "false", None),
    ("mi-guardian", "民法", r"被後見人", "false", None),
    ("mi-solo", "民法", r"単独で", "false", None),
    ("mi-joint", "民法", r"共同相続|全員", "false", None),
    ("mi-register", "民法", r"登記.*(必要|備えなければ|しなければ)", "true", None),
    ("mi-norescind", "民法", r"解除(することが)?できない", "false", None),
    ("ke-unconst", "憲法", r"違憲|違反する|反する|許されない", "false", r"違反しない|反しない|合憲"),
    ("ke-const", "憲法", r"合憲|違反しない|反しない|許される", "true", None),
    ("ke-noviolate", "憲法", r"(違反しない|合憲である)。?$", "true", None),
    ("ke-case", "憲法", r"判例である", "false", None),
    ("ke-mandate", "憲法", r"憲法上|憲法は", "false", None),
    ("ke-strict", "憲法", r"厳格|必要不可欠|明白かつ現在", "false", None),
    ("ke-allow", "憲法", r"を認め", "true", None),
    ("ke-law", "憲法", r"法律の", "false", None),
    ("sh-charter", "商法・会社法", r"定款で定め", "true", None),
    ("sh-cannot", "商法・会社法", r"することができない", "true", None),
    ("sh-all", "商法・会社法", r"全員", "true", None),
    ("sh-offering", "商法・会社法", r"募集株式の", "false", None),
    ("sh-court", "商法・会社法", r"裁判所", "false", None),
    ("ch-country", "基礎知識", r"国では", "true", None),
    ("ch-basis", "基礎知識", r"に基づいて", "false", None),
    ("ch-may", "基礎知識", r"ことがある|場合がある|余地", "true", None),
    ("ki-can", "基礎法学", r"できる。$", "true", None),
    ("ki-may", "基礎法学", r"ことがある|場合がある|余地", "true", None),
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


def measure(rows: list[dict]) -> dict:
    result = {}
    for rule_id, subject, pattern, direction, exclude in RULES:
        word, skip = re.compile(pattern), re.compile(exclude) if exclude else None
        hit = [
            row for row in rows
            if word.search(row["text"]) and not (skip and skip.search(row["text"]))
            and (subject is None or row["subject"] == subject)
        ]
        if not hit:
            raise SystemExit(f"{rule_id}: 肢が1つもない")
        early = [row for row in hit if row["year"] < 2016]
        late = [row for row in hit if row["year"] >= 2016]

        def as_hit(part: list[dict]) -> int | None:
            if not part:
                return None
            value = share(part, "truth")
            return round((value if direction == "true" else 1 - value) * 100)

        base = share(hit, "base")
        result[rule_id] = {
            "direction": direction,
            "count": len(hit),
            "hit": as_hit(hit),
            "base": round((base if direction == "true" else 1 - base) * 100),
            "early": as_hit(early),
            "late": as_hit(late),
        }
    return result


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
    result = measure(rows)
    for rule_id, value in result.items():
        print(rule_id, value)
    if args.write:
        (PAGE / "measured.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
