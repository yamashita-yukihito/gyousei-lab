"""民法D案（static/minpo-d/chapters.json）へ、あとから足した章を差し込む。

使い方: python3 minpo_extra.py [--write]
民法D案の23章は chapters.json を直接直している。ここで扱うのは、23章の番号を動かさずに
条文順の位置へ足した章（第11.5章など）だけ。番号は小数で持ち、画面には「第11.5章」と出る。
"""
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
from build import APP, check, exam_index, ref_key  # noqa: E402
import guides  # noqa: E402
import skip_notes  # noqa: E402

# (章ファイル, 番号, この章の前に来る章ID)
EXTRA = [
    ("mp_transfer_security", 11.5, "statutory-superficies"),
    ("mp_payment", 13.5, "assignment-setoff"),
]
PATH = APP / "static/minpo-d/chapters.json"


def main(argv):
    data = json.loads(PATH.read_text(encoding="utf-8"))
    chapters = data["chapters"]
    new = []
    for mod, number, after in EXTRA:
        ch = importlib.import_module(mod).CHAPTER
        ch = {"id": ch["id"], "number": number, **{k: v for k, v in ch.items() if k != "id"}}
        chapters = [c for c in chapters if c["id"] != ch["id"]]
        at = [c["id"] for c in chapters].index(after) + 1
        chapters.insert(at, ch)
        new.append(ch)
    skip_notes.apply(new)
    guides.apply(new)
    cards = {c["id"] for c in json.loads((APP / "content/explanation_cards.json").read_text(encoding="utf-8"))["items"]}
    errors = check({"cast": data["cast"], "chapters": new}, cards)
    _, urls = exam_index()
    links = dict(data["examLinks"])
    for ch in new:
        for q in ch["questions"]:
            for ref in q["examRefs"]:
                key, _ = ref_key(ref)
                if key not in urls:
                    errors.append(f"{q['id']}: リンクの無い問題 {ref}")
                links[key] = urls.get(key, links.get(key))
    for e in errors:
        print("NG", e)
    print(f"足す章 {len(new)} 全{len(chapters)}章 {sum(len(c['questions']) for c in chapters)}問 誤り {len(errors)}")
    if "--write" in argv and not errors:
        data["chapters"] = chapters
        data["examLinks"] = dict(sorted(links.items()))
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("書き出し", PATH)


if __name__ == "__main__":
    main(sys.argv[1:])
