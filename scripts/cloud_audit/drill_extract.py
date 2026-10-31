"""Extract one drill chapter (+ key fields of linked cards) for an inspector.

usage: python3 drill_extract.py list               # all chapters in audit order
       python3 drill_extract.py <drill> <chapter-id> OUT.json
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")
ORDER = ["gyosei-d", "minpo-d", "kenpo-d", "chishiki-d", "shoho-d", "kiso-d"]


def main(argv):
    if argv[0] == "list":
        for d in ORDER:
            data = json.loads((REPO / "static" / d / "chapters.json").read_text(encoding="utf-8"))
            for ch in data["chapters"]:
                skips = sum(1 for q in ch["questions"] if q.get("skipNote"))
                print(d, ch["id"], ch.get("number"), len(ch["questions"]), "skip", skips, ch.get("title", "")[:40])
        return
    drill, chid, out = argv
    data = json.loads((REPO / "static" / drill / "chapters.json").read_text(encoding="utf-8"))
    ch = next(c for c in data["chapters"] if c["id"] == chid)
    cards = {c["id"]: c for c in json.loads((REPO / "content/explanation_cards.json").read_text(encoding="utf-8"))["items"]}
    linked = {}
    for q in ch["questions"]:
        cid = q.get("cardId")
        if cid and cid in cards:
            c = cards[cid]
            linked[cid] = {"topic": c.get("topic"), "subtopic": c.get("subtopic"), "A": c["variants"]["a"],
                           "correct": c["correct"], "correction": c["correction"], "memoryPoint": c["memoryPoint"]}
    Path(out).write_text(json.dumps({"drill": drill, "cast": data.get("cast"), "chapter": ch, "linkedCards": linked},
                                    ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(out, len(ch["questions"]), "questions, linked cards:", len(linked))


if __name__ == "__main__":
    main(sys.argv[1:])
