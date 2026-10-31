"""Check that scripts/drill_d chapter sources and static/*-d/chapters.json agree (without private data).

Builds chapters like build.py (modules + skip_notes + guides) and compares with the JSON chapters.
For minpo-d, compares the chapters made by mp_*.py (via minpo_extra.py's module list) only.
usage: python3 drill_sync_check.py [gyosei kenpo shoho kiso chishiki minpo]
"""
import importlib
import json
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
D = REPO / "scripts/drill_d"
sys.dont_write_bytecode = True
sys.path.insert(0, str(D))


def diff(a, b, path=""):
    if type(a) is not type(b):
        return [f"{path}: type {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: only in json")
            elif k not in b:
                out.append(f"{path}.{k}: only in source")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: len {len(a)} != {len(b)}"]
        out = []
        for n, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}.{n}")
        return out
    return [] if a == b else [f"{path}: {str(a)[:80]!r} != {str(b)[:80]!r}"]


def main(argv):
    import build  # noqa: E402  (imports pq, guides, skip_notes; no private data needed for SUBJECTS)
    import guides
    import skip_notes
    keys = argv or ["gyosei", "kenpo", "shoho", "kiso", "chishiki", "minpo"]
    total = 0
    for key in keys:
        if key == "minpo":
            extra = importlib.import_module("minpo_extra")
            mods = getattr(extra, "MODULES", None) or ["mp_transfer_security", "mp_payment"]
            data = json.loads((REPO / "static/minpo-d/chapters.json").read_text(encoding="utf-8"))
            by_id = {c["id"]: c for c in data["chapters"]}
            for mod in mods:
                ch = importlib.reload(importlib.import_module(mod)).CHAPTER
                ch = dict(ch)
                skip_notes.apply([ch])
                guides.apply([ch])
                js = by_id.get(ch["id"])
                if js is None:
                    print(f"minpo: chapter {ch['id']} missing in json"); total += 1; continue
                js2 = {k: v for k, v in js.items() if k != "number"}
                src = {k: v for k, v in ch.items() if k != "number"}
                d = diff(js2, src, f"minpo:{ch['id']}")
                total += len(d)
                for line in d[:20]:
                    print(line)
            print(f"minpo: checked {len(mods)} extra chapters")
            continue
        conf = build.SUBJECTS[key]
        chapters = []
        for number, mod in enumerate(conf["modules"], 1):
            ch = importlib.reload(importlib.import_module(mod)).CHAPTER
            ch = {"id": ch["id"], "number": number, **{k: v for k, v in ch.items() if k != "id"}}
            chapters.append(ch)
        skip_notes.apply(chapters)
        guides.apply(chapters)
        data = json.loads((REPO / "static" / conf["dir"] / "chapters.json").read_text(encoding="utf-8"))
        d = diff(data["chapters"], chapters, key)
        total += len(d)
        for line in d[:30]:
            print(line)
        print(f"{key}: {len(chapters)} chapters, {len(d)} differences")
    print("TOTAL differences:", total)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
