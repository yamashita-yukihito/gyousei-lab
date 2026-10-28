"""D案の章データを組み立てる補助。出力の形は static/minpo-d/chapters.json と同じ。"""

LAW = {
    "行手": "405AC0000000088",
    "行審": "426AC0000000068",
    "行訴": "337AC0000000139",
    "国賠": "322AC0000000125",
    "自治": "322AC0000000067",
    "代執": "323AC0000000043",
    "行組": "323AC0000000120",
    "憲法": "321CONSTITUTION",
    "会社": "417AC0000000086",
    "商法": "132AC0000000048",
    "書士": "326AC1000000004",
    "個情": "415AC0000000057",
    "情公": "411AC0000000042",
    "公文": "421AC0000000066",
    "住基": "342AC0000000081",
    "戸籍": "322AC0000000224",
    "民法": "129AC0000000089",
    "法適": "418AC0000000078",
    "裁判": "322AC0000000059",
    "員法": "416AC0000000063",
    "民訴": "408AC0000000109",
    "刑訴": "323AC0000000131",
}

EX_KEYS = ("summary", "fact", "rule", "conclusion", "protectWho", "why",
           "ifOpposite", "legalSense", "minimum", "next")


def lb(label, law):
    return {"label": label, "url": f"https://laws.e-gov.go.jp/law/{LAW[law]}?occasion_date=20260401"}


def edge(frm, to, label, style="solid"):
    return {"from": frm, "to": to, "label": label, "style": style}


def node(id, label, x, y, cast=True, kind=None):
    row = {"id": id, "label": label, "x": x, "y": y}
    if cast:
        row["castId"] = id
    if kind:
        row["kind"] = kind
    return row


def thing(id, label, x, y):
    return {"id": id, "kind": "thing", "label": label, "x": x, "y": y}


def Q(id, topic, kind, delta, scenario, statement, answer, edges, answer_edges, ex,
      basis, refs, card=None, hidden=(), labels=None, extra=None, remedy=None):
    if len(ex) != len(EX_KEYS):
        raise ValueError(f"{id}: explanation fields {len(ex)}")
    d = {"before": delta[0], "now": delta[1], "same": delta[2]}
    if len(delta) > 3 and delta[3] == "reset":
        d = {"transition": "reset", **d}
    diagram = {"hiddenNodes": list(hidden), "nodeLabels": labels or {}, "edges": edges}
    if extra:
        diagram["extraNodes"] = extra
    explanation = dict(zip(EX_KEYS, ex))
    if remedy:
        explanation["remedy"] = remedy
    q = {
        "id": id, "topic": topic, "kind": kind, "delta": d,
        "currentScenario": scenario, "statement": statement, "answer": answer,
        "diagram": diagram, "answerEdges": answer_edges, "explanation": explanation,
        "legalBasis": basis,
    }
    if card:
        q["cardId"] = card
    q["examRefs"] = list(refs)
    return q
