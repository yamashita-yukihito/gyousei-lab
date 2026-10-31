"""T4: pull numeric expressions (periods, ratios, counts, amounts) out of all study materials.

usage: python3 numbers_extract.py OUT.json
Each hit: {value, unit, norm, src, loc, ctx, articles}. `norm` normalizes 3ヶ月/3箇月/三箇月 -> 3か月, etc.
"""
import glob
import json
import re
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def kanji_to_int(s):
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in KANJI:
            cur = KANJI[ch]
        elif ch == "十":
            total += (cur or 1) * 10; cur = 0
        elif ch == "百":
            total += (cur or 1) * 100; cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000; cur = 0
    return total + cur


NUM = r"([0-9０-９]+|[一二三四五六七八九十百千〇]+)"
PATTERNS = [
    (re.compile(NUM + r"\s*分の\s*" + NUM), "ratio"),
    (re.compile(NUM + r"\s*(?:か月|ヶ月|ケ月|箇月|カ月|ヵ月|月以内|月を)"), "month"),
    (re.compile(NUM + r"\s*(?:日以内|日を|日間|日前|日まで|日の|日以上|日から|日経過|日)"), "day"),
    (re.compile(NUM + r"\s*(?:年以内|年間|年を|年以上|年以下|年が|年で|年の|年)(?!度|問|[0-9０-９])"), "year"),
    (re.compile(NUM + r"\s*週間"), "week"),
    (re.compile(NUM + r"\s*時間"), "hour"),
    (re.compile(NUM + r"\s*(?:人以上|人以下|人|名)"), "person"),
    (re.compile(NUM + r"\s*(?:回|期)"), "count"),
    (re.compile(NUM + r"\s*(?:万円|円)"), "yen"),
    (re.compile(NUM + r"\s*(?:％|%|パーセント|割)"), "percent"),
    (re.compile(r"過半数|半数|全員|3分の2以上|三分の二以上"), "majority"),
    (re.compile(NUM + r"\s*歳"), "age"),
]
ART = re.compile(r"(?:[^\s@、。（）()「」]{1,12}法)?\s*[0-9０-９]+条(?:の[0-9０-９]+)?(?:[0-9０-９]+項)?(?:[0-9０-９]+号)?")
FW = str.maketrans("０１２３４５６７８９", "0123456789")


def walk(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for n, v in enumerate(node):
            yield from walk(v, f"{path}.{n}")


SKIP_PATHS = re.compile(r"(^|\.)(id|src|url|frequency|sourceRefs|relatedPastQuestions|examRefs|examLinks|review|x|y|"
                        r"number|askedAt|choiceId|pastExams|cardId|castId|clusterId|style|color|emoji)(\.|$)")


def sources():
    cards = json.loads((REPO / "content/explanation_cards.json").read_text(encoding="utf-8"))
    for c in cards["items"]:
        for p, s in walk(c):
            if not SKIP_PATHS.search(p):
                yield "card", f"{c['id']}:{p}", s, f"{c.get('topic')}/{c.get('subtopic')}"
    for f in sorted(glob.glob(str(REPO / "static/*-d/chapters.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        sub = Path(f).parent.name
        for ch in d["chapters"]:
            for p, s in walk({k: v for k, v in ch.items() if k != "questions"}):
                if not SKIP_PATHS.search(p):
                    yield "drill", f"{sub}:{ch['id']}:{p}", s, ch.get("topic", "")
            for q in ch.get("questions", []):
                for p, s in walk(q):
                    if not SKIP_PATHS.search(p):
                        yield "drill", f"{sub}:{q['id']}:{p}", s, q.get("topic", "")
    for f in sorted(glob.glob(str(REPO / "static/*/content.md"))):
        head = ""
        for n, line in enumerate(Path(f).read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("#"):
                head = line.strip("# ")
            yield "page", f"{Path(f).parent.name}/content.md:{n}", line, head
    for f in [REPO / "static/technique/index.html", REPO / "static/tools/quiz3.html"]:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            yield "page", f"{f.parent.name}/{f.name}:{n}", re.sub(r"<[^>]+>", " ", line), ""


def main(argv):
    out = []
    for src, loc, text, topic in sources():
        if not isinstance(text, str):
            continue
        for rx, unit in PATTERNS:
            for m in rx.finditer(text):
                before = text[max(0, m.start() - 3):m.start()]
                after = text[m.end():m.end() + 3]
                if unit == "day" and re.search(r"月\s*$", before):
                    continue
                if unit == "year" and (re.search(r"(平成|令和|昭和|大正|明治)\s*$", before) or re.match(r"\s*[0-9０-９一二三四五六七八九十]+\s*月", after)
                                       or re.match(r"[0-9０-９]{4}$", m.group(1) or "")):
                    continue
                a, b = max(0, m.start() - 50), min(len(text), m.end() + 50)
                ctx = text[a:b].replace("\n", " ")
                sent_a = max(text.rfind("。", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
                sent_b_candidates = [i for i in (text.find("。", m.end()), text.find("\n", m.end())) if i != -1]
                sent_b = min(sent_b_candidates) if sent_b_candidates else len(text)
                sentence = text[sent_a:sent_b]
                arts = sorted(set(a2.translate(FW).strip() for a2 in ART.findall(sentence)))
                g = m.groups()
                if unit == "ratio":
                    norm = f"{kanji_to_int(g[0].translate(FW))}分の{kanji_to_int(g[1].translate(FW))}"
                elif unit == "majority":
                    norm = m.group(0)
                else:
                    suffix = {"month": "か月", "day": "日", "year": "年", "week": "週間", "hour": "時間", "person": "人",
                              "count": "回", "yen": "円", "percent": "%", "age": "歳"}[unit]
                    raw = m.group(0)
                    if unit == "yen" and "万円" in raw:
                        suffix = "万円"
                    norm = f"{kanji_to_int(g[0].translate(FW))}{suffix}"
                out.append({"norm": norm, "unit": unit, "src": src, "loc": loc, "topic": topic, "ctx": ctx,
                            "sentence": sentence[:300], "articles": arts})
    Path(argv[0]).write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    from collections import Counter
    print(len(out), "hits")
    print(Counter(h["unit"] for h in out).most_common())


if __name__ == "__main__":
    main(sys.argv[1:])
