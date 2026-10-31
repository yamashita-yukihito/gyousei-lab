"""T1: search all study materials for amendment-sensitive terms; print per-term hits with context."""
import glob
import json
import re
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
TERMS = sys.argv[1:] or [
    "懲役", "禁錮", "禁固", "拘禁刑",
    "親権", "養育費", "財産分与", "離婚",
    "嫡出", "再婚", "懲戒",
    "共有", "管理者", "隣地", "相続登記",
    "補充的な指示", "指示", "関与",
    "行政書士法", "業務の制限", "両罰",
    "行政機関個人情報保護法", "独立行政法人等個人情報", "行政機関の保有する個人情報",
]


def walk(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for n, v in enumerate(node):
            yield from walk(v, f"{path}.{n}")


def sources():
    cards = json.loads((REPO / "content/explanation_cards.json").read_text(encoding="utf-8"))
    for c in cards["items"]:
        for p, s in walk(c):
            yield f"card:{c['id']}:{p}", s
    for f in sorted(glob.glob(str(REPO / "static/*-d/chapters.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        sub = Path(f).parent.name
        for ch in d["chapters"]:
            for q in ch.get("questions", []):
                for p, s in walk(q):
                    yield f"{sub}:{q['id']}:{p}", s
            for key in ("guide", "baseScenario", "intro", "title", "hint"):
                if key in ch:
                    for p, s in walk(ch[key]):
                        yield f"{sub}:{ch.get('id')}:{key}.{p}", s
    for f in sorted(glob.glob(str(REPO / "static/*/content.md"))):
        for n, line in enumerate(Path(f).read_text(encoding="utf-8").splitlines(), 1):
            yield f"{Path(f).parent.name}/content.md:{n}", line
    for f in [REPO / "static/technique/index.html", REPO / "static/tools/quiz3.html"]:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            yield f"{f.parent.name}/{f.name}:{n}", line
    for f in sorted(glob.glob(str(REPO / "static/assets/card-figures/*.svg"))):
        text = re.sub(r"<[^>]+>", " ", Path(f).read_text(encoding="utf-8"))
        yield f"svg:{Path(f).name}", text


hits = {t: [] for t in TERMS}
for loc, text in sources():
    for t in TERMS:
        for m in re.finditer(re.escape(t), text):
            a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
            hits[t].append((loc, text[a:b].replace("\n", " ")))
for t in TERMS:
    print(f"\n######## {t}: {len(hits[t])}")
    seen = set()
    for loc, ctx in hits[t]:
        key = (loc, ctx)
        if key in seen:
            continue
        seen.add(key)
        print(f"- {loc} | {ctx}")
