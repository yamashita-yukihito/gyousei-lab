"""演習D案の文章だけをMarkdownへ書き出す（Geminiに読みやすさを見てもらう用）。

使い方: python3 export_text.py [出力先ディレクトリ]（既定は static/docs/drill-d/）
chapters.json の文章をそのまま並べるだけで、図の座標や出典リンクは出さない。
Geminiの1回の回答に収まるよう、50〜60問ずつのファイルに分ける。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
# (ファイル名, 見出し, [(科目ディレクトリ, 章の範囲)])
PARTS = [
    ("minpo-1", "民法（1/3）総則〜物権", [("minpo-d", slice(0, 8))]),
    ("minpo-2", "民法（2/3）担保物権〜債権", [("minpo-d", slice(8, 16))]),
    ("minpo-3", "民法（3/3）債権各論〜親族・相続", [("minpo-d", slice(16, None))]),
    ("gyosei-1", "行政法（1/2）総論・行政手続法・行政不服審査法", [("gyosei-d", slice(0, 8))]),
    ("gyosei-2", "行政法（2/2）行政事件訴訟法・国家賠償法・地方自治法", [("gyosei-d", slice(8, None))]),
    ("kenpo", "憲法", [("kenpo-d", slice(None))]),
    ("small", "商法・会社法／基礎法学／基礎知識", [("shoho-d", slice(None)), ("kiso-d", slice(None)), ("chishiki-d", slice(None))]),
]
KIND = {"straight": "素直", "trap": "ひっかけ"}
FIELDS = [
    ("summary", "答えの説明"),
    ("fact", "決め手の事実"),
    ("rule", "正確なルール"),
    ("conclusion", "この事例の結論"),
    ("protectWho", "誰を守る？"),
    ("why", "誰を守る？（理由）"),
    ("ifOpposite", "反対の結論だと何が困る？"),
    ("legalSense", "法律家の常識へ補正"),
    ("minimum", "最小限の覚え方"),
    ("next", "次の問へのつなぎ"),
]


def line(key, label, value):
    return f"- `{key}`（{label}）: {value}" if value else None


def chapter_md(data, chapter):
    cast = {row["id"]: row for row in data.get("cast", [])}
    out = [f"## 章 `{chapter['id']}` {chapter['title']}", ""]
    if chapter.get("castIds"):
        roles = "、".join(f"{cast[c]['name']}＝{cast[c]['role']}" for c in chapter["castIds"] if c in cast)
        out += [f"配役（固定）: {roles}", ""]
    for key, label in (("lead", "章のねらい"), ("goal", "到達目標"), ("baseScenario", "章の共通事例"), ("remedyGuide", "負ける側に残る救済")):
        row = line(key, label, chapter.get(key))
        if row:
            out.append(row)
    note = (chapter.get("summaryTable") or {}).get("note")
    if note:
        out.append(line("summaryTable.note", "比較表の注", note))
    out.append("")
    for q in chapter["questions"]:
        mark = "○" if q["answer"] else "×"
        out += [f"### 問 `{q['id']}`　{q['topic']}（{KIND.get(q['kind'], q['kind'])}・正解{mark}）", ""]
        delta = q["delta"]
        head = "基準事例へ戻る（いくつかの事実をまとめて変える）" if delta.get("transition") == "reset" else "前問から変えた1点"
        out.append(f"- `delta`（{head}）: 前「{delta['before']}」→ 今回「{delta['now']}」（変えないもの「{delta['same']}」）")
        out.append(line("currentScenario", "今回の事実", q["currentScenario"]))
        out.append(line("statement", "○×を判断する文", q["statement"]))
        ex = q["explanation"]
        for key, label in FIELDS:
            row = line(f"explanation.{key}", label, ex.get(key))
            if row:
                out.append(row)
        if q.get("remedy"):
            out.append(line("remedy", "負ける側に残る救済", q["remedy"]))
        out.append("")
    return out


def main():
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else STATIC / "docs" / "drill-d"
    dest.mkdir(parents=True, exist_ok=True)
    for name, heading, sources in PARTS:
        out = [f"# 演習D案の文章：{heading}", "",
               "依頼文 `gemini-drill-d-readability-request.md` と一緒に読む。`問` の見出しにある問IDで指摘を返すこと。", ""]
        count = 0
        for subject, part in sources:
            data = json.loads((STATIC / subject / "chapters.json").read_text(encoding="utf-8"))
            for chapter in data["chapters"][part]:
                out += chapter_md(data, chapter)
                count += len(chapter["questions"])
        path = dest / f"{name}.md"
        path.write_text("\n".join(out), encoding="utf-8")
        print(f"{path.name}: {count}問 {path.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
