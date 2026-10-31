"""Build a blind verification file from inspector findings (no reasons, no proposed fixes).

usage: python3 make_verify.py OUT.json FINDINGS.json [FINDINGS.json ...]
Each item: id, location, the full field text with the flagged part wrapped in ⟦ ⟧, plus card A/correct context.
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/user/gyousei-lab")


def get(node, path):
    for part in path.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def main(argv):
    out, files = Path(argv[0]), argv[1:]
    cards = {c["id"]: c for c in json.loads((REPO / "content/explanation_cards.json").read_text(encoding="utf-8"))["items"]}
    drills = {}
    items = []
    for f in files:
        for x in json.loads(Path(f).read_text(encoding="utf-8")):
            item = {"id": x["id"], "target": x.get("target")}
            try:
                if x.get("target") == "card":
                    c = cards[x["card"]]
                    text = get(c, x["path"])
                    item.update(card=x["card"], path=x["path"], cardTopic=f"{c.get('topic')} / {c.get('subtopic')}",
                                A=c["variants"]["a"], correct=c["correct"])
                elif x.get("target") == "drill":
                    d = x["drill"]
                    if d not in drills:
                        drills[d] = json.loads((REPO / "static" / d / "chapters.json").read_text(encoding="utf-8"))
                    node = None
                    for ch in drills[d]["chapters"]:
                        if ch.get("id") == x["question"]:
                            node = ch
                        for q in ch.get("questions", []):
                            if q.get("id") == x["question"]:
                                node = q
                    text = get(node, x["path"])
                    item.update(drill=d, question=x["question"], path=x["path"])
                    if "statement" in node:
                        item.update(statement=node["statement"], answer=node["answer"])
                else:
                    text = (REPO / x["file"]).read_text(encoding="utf-8").splitlines()[int(x.get("line") or 1) - 1]
                    item.update(file=x["file"], line=x.get("line"))
            except Exception as e:  # noqa: BLE001
                item["error"] = f"could not locate: {e}"
                items.append(item)
                continue
            old = x.get("old") or ""
            if isinstance(text, str) and old and old in text:
                item["text"] = text.replace(old, "⟦" + old + "⟧", 1)
            else:
                item["text"] = text
                item["flagged"] = old
            if x.get("kind") in ("rule-bc",):
                item["question_for_you"] = "Do A, B1 (variants.b), B2 (variants.bCasual) and C (variants.c) of this card state the same proposition? Is the marked part a leak of the answer or the opposite of A?"
                c = cards[x["card"]]
                item["variants"] = c["variants"]
            items.append(item)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(out, len(items), "items")


if __name__ == "__main__":
    main(sys.argv[1:])
