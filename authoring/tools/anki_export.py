"""学習カードをAnkiで回せる形へ書き出す。

## 出す形

**タブ区切りのテキスト1枚**にする。Ankiの「ファイルを読み込む」がそのまま受け取れて、
追加のパッケージも要らない。apkgにしないのは、apkgがSQLite＋zipの内部形式で、
Ankiの版が変わると壊れやすいからである。テキストなら中身を目で確かめられる。

列は次の6つ。1行目に `#separator:tab` などのヘッダを付けるので、Anki側で
フィールドの割り当てを毎回やり直す必要がない。

| 列 | 中身 |
|---|---|
| 1 表 | ⑤の肢そのもの（A）と、B1・B2・Cのやさしい言い換え |
| 2 裏 | ○か×か、正しい形、一言暗記、①普通の解説、②深掘り3つ、④常識力 |
| 3 タグ | 科目・頻出度・カードIDをAnkiのタグにしたもの |
| 4 カードID | ラボへ戻るときの手がかり |
| 5 頻出度 | 「最頻出」「頻出」など |
| 6 出典 | ⑤の肢の年度・問・肢 |

## 決めていること

- **Aは原文のまま出す。** ラボの装飾記法（`%%` `__` `**` `==` `!!` `@@`）は
  `card_edit.strip_markup` で外し、素の文字列にする。Ankiに色は持ち込まない。
  記法の意味はラボの画面でしか通じないので、そこへ寄せない。
- **答えは必ず裏に置く。** ラボと同じで、A・B1・B2・Cは表、正誤と解説は裏である。
  表に答えが出ると○×の練習にならない。
- ⑥⑦⑧（比較・表・図）は出さない。Ankiのテキスト取り込みでは表現できず、
  中途半端に崩れるくらいなら落とすほうがよい。図はラボの画面で見る。
- 取得元の解説は入れない。ラボと同じで、本文はすべて自分で書いたものである。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from card_edit import strip_markup  # noqa: E402

DEFAULT_SOURCE = ROOT / "content" / "explanation_cards.json"
HEADER = [
    "#separator:tab",
    "#html:true",
    "#notetype:Basic",
    "#tags column:3",
    "#columns:表\t裏\tタグ\tカードID\t頻出度\t出典",
]


# 改行と列区切りを壊す文字。行内へ入るとTSVの列がずれる。
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _tsv_cell(text: str) -> str:
    """TSVの1セルとして安全な形にする。**全6列をこれに通す。**

    タブ・改行・制御文字が1つでも残ると、その行から先の列がまるごとずれる。
    表と裏だけでなく、タグ・カードID・頻出度・出典も通す。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", " ").replace("\n", " ")
    return _CONTROL.sub("", text).strip()


def _clean(value: object) -> str:
    """装飾記法を外し、HTMLとして解釈されない形にする。

    ヘッダで `#html:true` を宣言しているので、本文に `<` `>` `&` が入ると
    タグとして解釈されてしまう。**教材の本文は必ずエスケープし**、
    段落の区切りに使う `<br>` だけをこちらで足す。
    """
    text = strip_markup(value if isinstance(value, str) else "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(text, quote=False).strip()


def _paragraphs(*chunks: str) -> str:
    """空でない塊だけをHTMLの段落として並べる。"""
    out: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        for line in chunk.split("\n"):
            line = line.strip()
            if line:
                out.append(line)
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return "<br>".join(out)


def _tags(card: dict) -> str:
    parts = ["gyousei-lab"]
    subject = card.get("subjectId")
    if isinstance(subject, str) and subject:
        parts.append(subject)
    topic = card.get("topic")
    if isinstance(topic, str) and topic:
        parts.append(topic.replace(" ", "_"))
    frequency = (card.get("frequency") or {}).get("label")
    if isinstance(frequency, str) and frequency:
        parts.append(frequency)
    parts.append(str(card.get("id") or ""))
    return " ".join(p for p in parts if p)


def _sources(card: dict, evidence: dict[str, dict]) -> str:
    """年度と問番号は肢ではなく問題の側に載っているので、そちらと結んで出す。"""
    labels: list[str] = []
    for ref in card.get("relatedPastQuestions") or []:
        item = evidence.get(ref.get("choiceId", ""))
        if not item:
            continue
        era = item.get("eraYear") or ""
        number = item.get("questionNumber")
        choice = item.get("choiceLabel")
        if not era or number is None:
            continue
        labels.append(f"{era}問{number}肢{choice}")
    return " / ".join(dict.fromkeys(labels))


def front_of(card: dict) -> str:
    variants = card.get("variants") or {}
    return _paragraphs(
        _clean(variants.get("a")),
        "――やさしく言うと――",
        _clean(variants.get("b")),
        _clean(variants.get("bCasual")),
        "――ひとことで――",
        _clean(variants.get("c")),
    )


def back_of(card: dict) -> str:
    explanations = card.get("explanations") or {}
    deep = explanations.get("deepDive") or {}
    verdict = "答え: ○（正しい）" if card.get("correct") else "答え: ×（誤り）"
    return _paragraphs(
        verdict,
        "――正しい形で覚えると――",
        _clean(card.get("correction")),
        "――一言暗記――",
        _clean(card.get("memoryPoint")),
        "――普通の解説――",
        _clean(explanations.get("normal")),
        "――制度の背景・理由――",
        _clean(deep.get("background")),
        "――試験のひっかけ――",
        _clean(deep.get("trap")),
        "――具体的な場面――",
        _clean(deep.get("example")),
        "――常識力で解くと――",
        _clean(explanations.get("commonSense")),
    )


def rows_for(cards: list[dict], evidence: dict[str, dict]) -> list[list[str]]:
    rows: list[list[str]] = []
    for card in cards:
        rows.append(
            [
                _tsv_cell(front_of(card)),
                _tsv_cell(back_of(card)),
                _tsv_cell(_tags(card)),
                _tsv_cell(str(card.get("id") or "")),
                _tsv_cell(str((card.get("frequency") or {}).get("label") or "")),
                _tsv_cell(_sources(card, evidence)),
            ]
        )
    return rows


def render(cards: list[dict], evidence: dict[str, dict]) -> str:
    lines = list(HEADER)
    for row in rows_for(cards, evidence):
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def load_evidence(path: Path | None) -> dict[str, dict]:
    """⑤の出典を読む。肢に年度・問番号は無いので、問題レコードの側から補う。"""
    if path is None or not path.is_file():
        return {}
    decoded = json.loads(path.read_text(encoding="utf-8"))
    records = {
        item["rawId"]: item
        for item in decoded.get("records") or []
        if isinstance(item, dict) and isinstance(item.get("rawId"), str)
    }
    out: dict[str, dict] = {}
    for item in decoded.get("choices") or []:
        if not isinstance(item, dict) or not isinstance(item.get("choiceId"), str):
            continue
        record = records.get(item.get("rawQuestionId", "")) or {}
        out[item["choiceId"]] = {
            **item,
            "eraYear": record.get("eraYear"),
            "questionNumber": record.get("questionNumber"),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--subject", action="append", default=None,
                        help="subjectId で絞る。複数指定できる")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    document = json.loads(args.source.read_text(encoding="utf-8"))
    cards = [c for c in document.get("items") or [] if isinstance(c, dict)]
    if args.subject:
        wanted = set(args.subject)
        cards = [c for c in cards if c.get("subjectId") in wanted]
    if not cards:
        parser.error("書き出すカードがありません")

    evidence = load_evidence(args.evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(cards, evidence), encoding="utf-8")
    args.output.chmod(0o600)
    print(f"{len(cards)}枚を書き出しました -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
