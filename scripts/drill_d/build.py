"""科目ごとのD案 chapters.json を組み立て、形と出題箇所を確かめてから書き出す。

使い方: python3 build.py gyosei [--write]
"""
import glob
import importlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
from pq import era, limbs  # noqa: E402
import guides  # noqa: E402
import skip_notes  # noqa: E402

APP = Path.home() / "dev/yuki-services/apps/gyousei-lab"
DATA = Path.home() / ".local/share/yuki-services/gyousei-lab/authoring/all_subjects"

CAST_STYLE = {
    "nobita": ("のび太", "🟡", "#b57a00"),
    "shizuka": ("しずか", "🟣", "#8a4f90"),
    "gian": ("ジャイアン", "🔴", "#b6443c"),
    "suneo": ("スネ夫", "🔵", "#3472a3"),
    "dekisugi": ("出木杉", "🟢", "#37815e"),
    "doraemon": ("ドラえもん", "⚪", "#287db3"),
}

SUBJECTS = {
    "gyosei": {
        "dir": "gyosei-d",
        "title": "行政法・法律家の常識ドリル D案",
        "description": "最初の2周は答えと理由を読み、3周目から解く連続事例教材（行政法）",
        "roles": {
            "nobita": "申請する人・処分を受ける人・訴える人",
            "gian": "行政庁・処分庁（権限を持つ側）",
            "suneo": "近隣住民・取引先などの第三者",
            "dekisugi": "審理員・裁判所・代理人（中立・きちんとした側）",
            "doraemon": "上級行政庁・大臣・国（上から関わる側）",
            "shizuka": "住民・議会（地域の側）",
        },
        "modules": [
            "gy_act_effect", "gy_discretion", "gy_enforcement",
            "gy_application", "gy_adverse", "gy_guidance",
            "gy_appeal_entry", "gy_appeal_review",
            "gy_suit_gate", "gy_suit_procedure", "gy_suit_other",
            "gy_state_comp1", "gy_state_comp2",
            "gy_local_residents", "gy_local_structure",
        ],
    },
    "kenpo": {
        "dir": "kenpo-d",
        "title": "憲法・法律家の常識ドリル D案",
        "description": "最初の2周は答えと理由を読み、3周目から解く連続事例教材（憲法）",
        "roles": {
            "nobita": "人権を主張する個人",
            "gian": "国・国会・規制する側",
            "suneo": "外国人・法人・団体など立場が特殊な人",
            "dekisugi": "裁判所",
        },
        "modules": [
            "kp_holders", "kp_equality", "kp_religion", "kp_expression", "kp_economy", "kp_social",
            "kp_diet", "kp_cabinet", "kp_judiciary", "kp_finance",
        ],
    },
    "shoho": {
        "dir": "shoho-d",
        "title": "商法・会社法・法律家の常識ドリル D案",
        "description": "最頻出の設立と機関に絞った連続事例教材（商法・会社法）",
        "roles": {
            "nobita": "発起人・株主",
            "suneo": "発起人・取締役",
            "gian": "代表取締役",
            "shizuka": "発起人以外の株式引受人",
            "dekisugi": "弁護士・監査役（きちんとした側）",
        },
        "modules": ["sh_incorporation", "sh_organs"],
    },
    "kiso": {
        "dir": "kiso-d",
        "title": "基礎法学・法律家の常識ドリル D案",
        "description": "最頻出の法の効力と法令用語に絞った連続事例教材（基礎法学）",
        "roles": {
            "nobita": "法律に従う国民",
            "gian": "国会（法律を作る側）",
            "dekisugi": "法律に詳しい人",
        },
        "modules": ["kj_law"],
    },
    "chishiki": {
        "dir": "chishiki-d",
        "title": "基礎知識・法律家の常識ドリル D案",
        "description": "最頻出の諸法令に絞った連続事例教材（基礎知識）",
        "roles": {
            "nobita": "国民・本人・住民",
            "gian": "行政機関・都道府県知事・市町村長",
            "suneo": "事業者・外国人など",
            "dekisugi": "行政書士",
            "doraemon": "個人情報保護委員会",
        },
        "modules": ["kc_shoshi", "kc_privacy", "kc_records"],
    },
}

REF = re.compile(r"^(平成|令和)(元|\d+)年問(\d+)(?:肢([0-9ア-オ]))?")


def exam_index():
    limbs_by_q = {}
    urls = {}
    for era_dir in ("current_2016_2025", "archive_2006_2015"):
        for e in json.loads((DATA / era_dir / "catalog/questions.json").read_text(encoding="utf-8"))["entries"]:
            urls[f"{e['examYear']}-q{int(e['questionNumber'])}"] = e["url"]
    for path in glob.glob(str(DATA / "*/extracted/*/*.json")):
        q = json.loads(Path(path).read_text(encoding="utf-8"))
        _, items = limbs(q)
        limbs_by_q[f"{q['examYear']}-q{q['questionNumber']}"] = {str(label) for label, _ in items}
    return limbs_by_q, urls


def ref_key(ref):
    m = REF.match(ref)
    if not m:
        raise ValueError(f"出題箇所の書き方が違う: {ref}")
    n = 1 if m.group(2) == "元" else int(m.group(2))
    year = (1988 if m.group(1) == "平成" else 2018) + n
    return f"{year}-q{int(m.group(3))}", m.group(4)


def check(data, cards):
    errors = []
    limbs_by_q, _ = exam_index()
    cast_ids = {c["id"] for c in data["cast"]}
    qids = set()
    for ch in data["chapters"]:
        qs = ch["questions"]
        if len(qs) != 8:
            errors.append(f"{ch['id']}: {len(qs)}問")
        ans = [q["answer"] for q in qs]
        if sum(ans) != 4:
            errors.append(f"{ch['id']}: ○が{sum(ans)}問")
        for kind in ("straight", "trap"):
            ks = {q["answer"] for q in qs if q["kind"] == kind}
            if ks != {True, False}:
                errors.append(f"{ch['id']}: {kind}の○×が片寄り {ks}")
        base = {n["id"] for n in ch["diagram"]["nodes"]}
        for n in ch["diagram"]["nodes"]:
            if n.get("castId") and n["castId"] not in cast_ids:
                errors.append(f"{ch['id']}: 配役 {n['castId']}")
            if n.get("castId") and n["castId"] not in ch["castIds"]:
                errors.append(f"{ch['id']}: 図の配役 {n['castId']} が castIds に無い")
        if qs[0]["delta"].get("transition") or qs[0]["delta"]["before"] != "章の共通事例":
            errors.append(f"{qs[0]['id']}: 1問目の差分")
        for q in qs:
            if q["id"] in qids:
                errors.append(f"重複ID {q['id']}")
            qids.add(q["id"])
            hidden = set(q["diagram"]["hiddenNodes"])
            extra = {n["id"] for n in q["diagram"].get("extraNodes", [])}
            visible = (base - hidden) | extra
            if not hidden <= base:
                errors.append(f"{q['id']}: hidden {hidden - base}")
            for e in q["diagram"]["edges"] + q["answerEdges"]:
                for end in (e["from"], e["to"]):
                    if end not in visible:
                        errors.append(f"{q['id']}: 見えない枠への線 {end}")
            if not set(q["diagram"]["nodeLabels"]) <= base | extra:
                errors.append(f"{q['id']}: nodeLabels")
            if q.get("cardId") and q["cardId"] not in cards:
                errors.append(f"{q['id']}: 無いカード {q['cardId']}")
            if not q["examRefs"]:
                errors.append(f"{q['id']}: examRefs が無い")
            for ref in q["examRefs"]:
                key, limb = ref_key(ref)
                if key not in limbs_by_q:
                    errors.append(f"{q['id']}: 無い問題 {ref}")
                elif limb and limb not in limbs_by_q[key]:
                    errors.append(f"{q['id']}: 無い肢 {ref}")
            if len(q["currentScenario"]) < 24 or len(q["statement"]) < 18:
                errors.append(f"{q['id']}: 文が短い")
    return errors


def build(key):
    conf = SUBJECTS[key]
    chapters = []
    for number, mod in enumerate(conf["modules"], 1):
        ch = importlib.reload(importlib.import_module(mod)).CHAPTER
        ch = {"id": ch["id"], "number": number, **{k: v for k, v in ch.items() if k != "id"}}
        chapters.append(ch)
    skip_notes.apply(chapters)
    guides.apply(chapters)
    cast = [
        {"id": cid, "name": CAST_STYLE[cid][0], "emoji": CAST_STYLE[cid][1], "color": CAST_STYLE[cid][2], "role": role}
        for cid, role in conf["roles"].items()
    ]
    _, urls = exam_index()
    links = {}
    for ch in chapters:
        for q in ch["questions"]:
            for ref in q["examRefs"]:
                k, _ = ref_key(ref)
                if k in urls:
                    links[k] = urls[k]
    data = {
        "schemaVersion": "drill-d@1",
        "asOf": "2026-04-01",
        "title": conf["title"],
        "description": conf["description"],
        "cast": cast,
        "chapters": chapters,
        "examLinks": dict(sorted(links.items())),
    }
    return conf, data


def main(argv):
    key = argv[0]
    conf, data = build(key)
    cards = {c["id"] for c in json.loads((APP / "content/explanation_cards.json").read_text(encoding="utf-8"))["items"]}
    errors = check(data, cards)
    for e in errors:
        print("NG", e)
    n = sum(len(c["questions"]) for c in data["chapters"])
    print(f"{len(data['chapters'])}章 {n}問 examLinks {len(data['examLinks'])} 誤り {len(errors)}")
    if "--write" in argv and not errors:
        out = APP / "static" / conf["dir"] / "chapters.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("書き出し", out)


if __name__ == "__main__":
    main(sys.argv[1:])
