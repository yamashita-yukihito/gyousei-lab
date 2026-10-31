"""Apply exact-text patches to drill chapters.json and the matching scripts/drill_d/*.py sources.

patch: {"drill": "gyosei-d", "question": "<question id or chapter id>", "path": "explanation.summary",
        "old": "...", "new": "..."}
usage: python3 drill_apply.py PATCH.json [--dry]
Rules: old must occur exactly once in the JSON field. In the .py sources the python-escaped old must occur
exactly once in all of scripts/drill_d/*.py (or zero times for minpo-d chapters edited directly in JSON).
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
D = REPO / "scripts/drill_d"
INDENT = {"minpo-d": 2}


def py_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def locate(data, qid):
    for ch in data["chapters"]:
        if ch.get("id") == qid:
            return ch
        for q in ch.get("questions", []):
            if q.get("id") == qid:
                return q
    raise KeyError(qid)


def get_path(node, path):
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    return node, (int(last) if isinstance(node, list) else last)


def main(argv):
    patches = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    dry = "--dry" in argv
    docs, py_texts, failed = {}, {}, 0
    for f in sorted(D.glob("*.py")):
        py_texts[f] = f.read_text(encoding="utf-8")
    for p in patches:
        drill = p["drill"]
        if drill not in docs:
            docs[drill] = json.loads((REPO / "static" / drill / "chapters.json").read_text(encoding="utf-8"))
        node, key = get_path(locate(docs[drill], p["question"]), p["path"])
        value = node[key]
        n = value.count(p["old"]) if isinstance(value, str) else -1
        if n != 1:
            print(f"JSON: old found {n} times", drill, p["question"], p["path"]); failed += 1; continue
        node[key] = value.replace(p["old"], p["new"])
        esc_old, esc_new = py_escape(p["old"]), py_escape(p["new"])
        hits = [(f, t.count(esc_old)) for f, t in py_texts.items() if esc_old in t]
        count = sum(c for _, c in hits)
        if count == 1:
            f = hits[0][0]
            py_texts[f] = py_texts[f].replace(esc_old, esc_new)
            print("ok json+py", drill, p["question"], p["path"], "->", f.name)
        elif count == 0:
            print("ok json only (no .py source found)", drill, p["question"], p["path"])
        else:
            print(f"PY: old found {count} times in", [f.name for f, _ in hits], drill, p["question"]); failed += 1
    if failed:
        print("failed", failed, "-> nothing written"); sys.exit(1)
    if dry:
        print("dry run: nothing written"); return
    for drill, data in docs.items():
        path = REPO / "static" / drill / "chapters.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=INDENT.get(drill, 1)) + "\n", encoding="utf-8")
    for f, t in py_texts.items():
        if f.read_text(encoding="utf-8") != t:
            f.write_text(t, encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main(sys.argv[1:])
