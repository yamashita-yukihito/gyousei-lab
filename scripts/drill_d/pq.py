"""過去問20年分を語で検索し、肢ごとに出題当時の正誤（推定）と出題箇所を出す。読み取り専用。

使い方: python3 pq.py [-s 行政法] [-y 2016] 語1 語2 ...（すべて含む肢だけ）
"""
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path.home() / ".local/share/yuki-services/gyousei-lab/authoring/all_subjects"
KANA = "アイウエオ"


def era(year: int) -> str:
    if year >= 2019:
        n = year - 2018
        return f"令和{'元' if n == 1 else n}年"
    return f"平成{year - 1988}年"


def limbs(q: dict):
    text = q.get("questionText") or ""
    choices = q.get("choices") or []
    kana = re.split(r"(?:^|\s)([ア-オ])．", text)
    if len(kana) > 3:
        out = []
        for i in range(1, len(kana) - 1, 2):
            out.append((kana[i], kana[i + 1].strip()))
        return "kana", out
    return "num", [(c["label"], c["text"]) for c in choices]


def truth(q: dict, kind: str, items):
    inst = q.get("instructionText") or ""
    ans = (q.get("answer") or {}).get("value")
    tk = (q.get("task") or {}).get("kind")
    neg = tk == "select_false" or bool(re.search(r"(妥当でない|誤っている|正しくない|適切でない|含まれない|当たらない|該当しない)もの", inst))
    res = {}
    if kind == "num":
        if (q.get("task") or {}).get("kind") not in (None, "single", "single_choice", "choose_one"):
            pass
        for label, _ in items:
            if ans is None:
                continue
            hit = str(label) == str(ans)
            res[label] = (not hit) if neg else hit
    else:
        choice = next((c for c in q.get("choices") or [] if str(c["label"]) == str(ans)), None)
        if choice and re.fullmatch(r"[ア-オ・]+", choice["text"].replace(" ", "")):
            chosen = set(choice["text"].replace(" ", "").split("・"))
            for label, _ in items:
                hit = label in chosen
                res[label] = (not hit) if neg else hit
    return res


def main(argv):
    subject = None
    year = None
    words = []
    it = iter(argv)
    for a in it:
        if a == "-s":
            subject = next(it)
        elif a == "-y":
            year = int(next(it))
        else:
            words.append(a)
    for path in sorted(glob.glob(str(ROOT / "*/extracted/*/*.json"))):
        q = json.loads(Path(path).read_text(encoding="utf-8"))
        if subject and subject not in (q.get("subjectLabel") or "") and subject not in " ".join(q.get("labels") or []):
            continue
        if year and q["examYear"] != year:
            continue
        kind, items = limbs(q)
        tv = truth(q, kind, items)
        inst = q.get("instructionText") or ""
        for label, text in items:
            if all(w in text or w in inst for w in words) and any(w in text for w in words):
                mark = {True: "○", False: "×"}.get(tv.get(label), "?")
                ref = f"{era(q['examYear'])}問{q['questionNumber']}肢{label}"
                print(f"{ref} [{mark}] ({(q.get('task') or {}).get('kind')}) {text[:260]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
