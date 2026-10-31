"""Helpers for the cloud audit (not part of the repo).

usage:
  python3 audit_tools.py order                     # print audit order (subject, freq)
  python3 audit_tools.py extract OUT.json ID...    # write selected cards (with figures listed)
  python3 audit_tools.py batch OUT.json START N    # cards START..START+N-1 in audit order
  python3 audit_tools.py validate                  # card_edit.validate_card on all cards
  python3 audit_tools.py apply PATCH.json [--dry]  # apply [{card, path, old, new}] exact replacements
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
CARDS = REPO / "content" / "explanation_cards.json"
sys.path.insert(0, str(REPO))

SUBJECT_ORDER = [
    "administrative-law",
    "civil-law",
    "general-knowledge",
    "constitutional-law",
    "commercial-law",
    "legal-foundations",
]
FREQ_ORDER = ["最頻出", "頻出", "繰り返し出題", "重要論点", "未出題"]


def load():
    raw = CARDS.read_text(encoding="utf-8")
    return raw, json.loads(raw)


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def ordered(doc):
    items = doc["items"]
    index = {c["id"]: n for n, c in enumerate(items)}
    return sorted(
        items,
        key=lambda c: (
            SUBJECT_ORDER.index(c["subjectId"]),
            FREQ_ORDER.index(c["frequency"]["label"]),
            -int(c["frequency"].get("occurrences") or 0),
            index[c["id"]],
        ),
    )


def get_path(card, path):
    node = card
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    return node, (int(last) if isinstance(node, list) else last)


def main(argv):
    cmd = argv[0]
    raw, doc = load()
    if cmd == "order":
        for n, c in enumerate(ordered(doc)):
            print(n, c["subjectId"], c["frequency"]["label"], c["frequency"].get("occurrences"), c["id"], c.get("topic"), c.get("subtopic"))
    elif cmd in ("extract", "batch"):
        out = Path(argv[1])
        if cmd == "batch":
            start, n = int(argv[2]), int(argv[3])
            cards = ordered(doc)[start:start + n]
        else:
            want = argv[2:]
            by_id = {c["id"]: c for c in doc["items"]}
            cards = [by_id[i] for i in want]
        out.write_text(json.dumps(cards, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(out, len(cards), "cards:", " ".join(c["id"] for c in cards))
        figs = [f["src"] for c in cards for f in (c.get("figures") or [])]
        figs += [f["src"] for c in cards for q in (c.get("learnerQa") or []) for f in (q.get("figures") or [])]
        if figs:
            print("figures:", " ".join(figs))
    elif cmd == "validate":
        import card_edit
        ids = {c["id"] for c in doc["items"]}
        problems = []
        for card in doc["items"]:
            card_edit.validate_card(card, ids, set(), False, problems)
        print("cards", len(doc["items"]), "problems", len(problems))
        for p in problems[:50]:
            print(" ", p)
        sys.exit(1 if problems else 0)
    elif cmd == "apply":
        patches = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        dry = "--dry" in argv
        by_id = {c["id"]: c for c in doc["items"]}
        failed = 0
        for p in patches:
            card = by_id[p["card"]]
            node, key = get_path(card, p["path"])
            value = node[key]
            if not isinstance(value, str):
                print("NOT STRING", p["card"], p["path"]); failed += 1; continue
            count = value.count(p["old"])
            if count != 1:
                print(f"OLD FOUND {count} TIMES", p["card"], p["path"], repr(p["old"][:60])); failed += 1; continue
            node[key] = value.replace(p["old"], p["new"])
            print("ok", p["card"], p["path"])
        if failed:
            print("failed", failed, "-> nothing written"); sys.exit(1)
        if not dry:
            CARDS.write_text(dump(doc), encoding="utf-8")
            print("written")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
