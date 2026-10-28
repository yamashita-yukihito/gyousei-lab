"""e-Gov法令API（2026-04-01時点）から条文を引く。使い方: python3 law.py 行手 5 6 33_2"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path.home() / ".cache/yuki-services/egov"  # e-Govの応答はソース側へ置かない
LAWS = {
    "行手": "405AC0000000088",
    "行審": "426AC0000000068",
    "行訴": "337AC0000000139",
    "国賠": "322AC0000000125",
    "自治": "322AC0000000067",
    "代執": "323AC0000000043",
    "憲法": "321CONSTITUTION",
    "会社": "417AC0000000086",
    "商法": "132AC0000000048",
    "書士": "326AC1000000004",
    "個情": "415AC0000000057",
    "情公": "411AC0000000042",
    "公文": "421AC0000000066",
    "住基": "342AC0000000081",
    "戸籍": "322AC0000000224",
    "行組": "323AC0000000120",
    "民法": "129AC0000000089",
}


def load(key):
    law_id = LAWS[key]
    cache = HERE / f"law_{law_id}.json"
    HERE.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = f"https://laws.e-gov.go.jp/api/2/law_data/{law_id}?asof=2026-04-01&response_format=json"
        subprocess.run(["curl", "-sS", "-m", "30", url, "-o", str(cache)], check=True)
    return json.loads(cache.read_text(encoding="utf-8"))


def text(node):
    if isinstance(node, str):
        return node
    out = []
    for child in node.get("children", []):
        t = text(child)
        if isinstance(child, dict) and child.get("tag") in ("Paragraph", "Item", "Subitem1", "Subitem2", "ArticleCaption", "ParagraphNum", "ItemTitle"):
            out.append("\n" + t if child.get("tag") in ("Paragraph", "Item", "Subitem1") else t)
        else:
            out.append(t)
    return "".join(out)


def find(node, num, in_suppl=False):
    if isinstance(node, str):
        return None
    if node.get("tag") == "SupplProvision":
        return None
    if node.get("tag") == "Article" and node.get("attr", {}).get("Num") == num:
        return node
    for child in node.get("children", []):
        hit = find(child, num)
        if hit:
            return hit
    return None


def main(argv):
    data = load(argv[0])
    root = data["law_full_text"]
    for num in argv[1:]:
        art = find(root, num)
        print(f"===== {argv[0]} {num}")
        print(text(art).strip() if art else "(見つからない)")


if __name__ == "__main__":
    main(sys.argv[1:])
