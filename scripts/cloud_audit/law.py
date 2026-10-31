"""e-Gov 法令API v2 (asof=2026-04-01) から条文を引く。scripts/drill_d/law.py を広げた点検用の版。

usage:
  python3 law.py info 民法                 # 版（最後に入った改正と施行日）
  python3 law.py art 民法 819 766_3        # 条文（枝番は _ で）
  python3 law.py grep 民法 親権者           # 語を含む条を探す（本則のみ）
  python3 law.py suppl 民法 [語]            # 附則（語で絞れる）
キャッシュは ~/.cache/yuki-services/egov/（リポジトリの外）。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path.home() / ".cache/yuki-services/egov"
ASOF = "2026-04-01"
LAWS = {
    "行手": "405AC0000000088", "行政手続法": "405AC0000000088",
    "行審": "426AC0000000068", "行政不服審査法": "426AC0000000068",
    "行訴": "337AC0000000139", "行政事件訴訟法": "337AC0000000139",
    "国賠": "322AC0000000125", "国家賠償法": "322AC0000000125",
    "自治": "322AC0000000067", "地方自治法": "322AC0000000067",
    "代執": "323AC0000000043", "行政代執行法": "323AC0000000043",
    "行組": "323AC0000000120", "国家行政組織法": "323AC0000000120",
    "憲法": "321CONSTITUTION",
    "会社": "417AC0000000086", "会社法": "417AC0000000086",
    "商法": "132AC0000000048",
    "書士": "326AC1000000004", "行政書士法": "326AC1000000004",
    "個情": "415AC0000000057", "個人情報保護法": "415AC0000000057",
    "情公": "411AC0000000042", "情報公開法": "411AC0000000042",
    "公文": "421AC0000000066", "公文書管理法": "421AC0000000066",
    "住基": "342AC0000000081", "住民基本台帳法": "342AC0000000081",
    "戸籍": "322AC0000000224", "戸籍法": "322AC0000000224",
    "民法": "129AC0000000089",
    "法適": "418AC0000000078", "法の適用に関する通則法": "418AC0000000078",
    "裁判": "322AC0000000059", "裁判所法": "322AC0000000059",
    "員法": "416AC0000000063", "裁判員法": "416AC0000000063",
    "民訴": "408AC0000000109", "民事訴訟法": "408AC0000000109",
    "刑訴": "323AC0000000131", "刑事訴訟法": "323AC0000000131",
    "刑法": "140AC0000000045",
    "国公": "322AC0000000120", "国家公務員法": "322AC0000000120",
    "地公": "325AC0000000261", "地方公務員法": "325AC0000000261",
    "不登": "416AC0000000123", "不動産登記法": "416AC0000000123",
    "借地借家": "403AC0000000090", "借地借家法": "403AC0000000090",
    "内閣法": "322AC0000000005",
    "国会法": "322AC1000000079",
    "公選": "325AC1000000100", "公職選挙法": "325AC1000000100",
    "財政法": "322AC0000000034",
    "警職": "323AC0000000136", "警察官職務執行法": "323AC0000000136",
    "行政機関情報公開法": "411AC0000000042",
    "デジタル手続法": "414AC0000000151",
    "番号法": "425AC0000000027",
    "一般法人": "418AC0000000048",
    "手形法": "207AC0000000020",
    "電子署名法": "412AC0000000102",
    "非訟": "423AC0000000051", "非訟事件手続法": "423AC0000000051",
    "補助金適正化法": "330AC0000000179",
    "建築基準法": "325AC0000000201",
    "都市計画法": "343AC0000000100",
    "土地収用法": "326AC0100000219",
    "行政機関の保有する情報の公開に関する法律": "411AC0000000042",
    "家事事件手続法": "423AC0000000052",
    "人事訴訟法": "415AC0000000109",
}


def load(key):
    law_id = LAWS.get(key, key)
    cache = HERE / f"law_{law_id}.json"
    HERE.mkdir(parents=True, exist_ok=True)
    if not cache.exists() or cache.stat().st_size < 1000:
        url = f"https://laws.e-gov.go.jp/api/2/law_data/{law_id}?asof={ASOF}&response_format=json"
        subprocess.run(["curl", "-sS", "-m", "60", url, "-o", str(cache)], check=True)
    return json.loads(cache.read_text(encoding="utf-8"))


def text(node):
    if isinstance(node, str):
        return node
    out = []
    for child in node.get("children", []):
        t = text(child)
        tag = child.get("tag") if isinstance(child, dict) else None
        if tag in ("Paragraph", "Item", "Subitem1", "Subitem2"):
            out.append("\n" + t)
        elif tag in ("ArticleTitle",):
            out.append(t + " ")
        else:
            out.append(t)
    return "".join(out)


def articles(node, suppl=False, inside=False):
    if isinstance(node, str):
        return
    tag = node.get("tag")
    if tag == "SupplProvision":
        if not suppl:
            return
        inside = True
    if tag == "Article" and (inside == suppl):
        yield node
        return
    for child in node.get("children", []):
        yield from articles(child, suppl, inside)


def main(argv):
    cmd, key = argv[0], argv[1]
    if cmd == "find":  # law.py find 土地収用法  -> lawId candidates
        import urllib.parse
        url = "https://laws.e-gov.go.jp/api/2/laws?law_title=" + urllib.parse.quote(key)
        out = subprocess.run(["curl", "-sS", "-m", "30", url], check=True, capture_output=True, text=True).stdout
        for x in json.loads(out).get("laws", [])[:10]:
            print(x["law_info"]["law_id"], x["revision_info"]["law_title"])
        return
    data = load(key)
    if cmd == "info":
        print(json.dumps(data.get("revision_info"), ensure_ascii=False, indent=1))
        return
    root = data["law_full_text"]
    if cmd == "art":
        want = set(argv[2:])
        found = set()
        for art in articles(root):
            num = art.get("attr", {}).get("Num")
            if num in want:
                found.add(num)
                print(f"===== {key} {num}")
                print(text(art).strip())
        for num in want - found:
            print(f"===== {key} {num}\n(見つからない)")
    elif cmd == "grep":
        words = argv[2:]
        for art in articles(root):
            t = text(art)
            if all(w in t for w in words):
                print(f"===== {key} {art.get('attr', {}).get('Num')}")
                print(t.strip()[:1500])
    elif cmd == "suppl":
        words = argv[2:]
        def walk(node, label=""):
            if isinstance(node, str):
                return
            if node.get("tag") == "SupplProvision":
                label = node.get("attr", {}).get("AmendLawNum", "原始附則")
                t = text(node)
                if all(w in t for w in words):
                    print(f"===== 附則 {label}")
                    print(t.strip()[:3000])
                return
            for child in node.get("children", []):
                walk(child, label)
        walk(root)


if __name__ == "__main__":
    main(sys.argv[1:])
