"""学習カードの編集ルールと、正本の読み書き。

`authoring/tools/card_exchange.py`（外部AIとのやり取り）と `server.py`（画面からの編集）の
両方がここを使う。**検証ルールを二重に持たない**ためのモジュールで、片方だけ緩めば
片方の入口から不正なカードが入ってしまう。

正本 `content/explanation_cards.json` はGit管理下にある。ここでの書き込みは
同じディレクトリへ一時ファイルを作ってから `os.replace` で置き換える。書きかけの
JSONが正本として残らないようにするためで、権限は 0600 のままにする。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent

CANONICAL_PATH = Path(
    os.environ.get(
        "GYOUSEI_CARD_SOURCE",
        REPO_ROOT / "content" / "explanation_cards.json",
    )
)

# 外部AIと画面の編集フォームが書き換えてよい項目。ここに無いものは受け取っても捨てる。
EDITABLE_FIELDS = (
    "topic",
    "subtopic",
    "correct",
    "variants",
    "correction",
    "memoryPoint",
    "explanations",
    "legalBasis",
    "evidenceHighlights",
    "crossFieldComparisons",
    "comparisonTable",
    "figures",
)
VARIANT_FIELDS = ("a", "b", "bCasual", "bCasualStyle", "c")

# 新しいカードを作るときだけ必要な項目。既存カードでは触らせない。
NEW_CARD_FIELDS = (
    "id",
    "subjectId",
    "category",
    "clusterId",
    "sourceRefs",
    "relatedPastQuestions",
)

# ⑧解説図。画像ファイルはMac側にしか置けないので、既にあるものを参照する形だけを許す。
FIGURE_DIR = REPO_ROOT / "static" / "assets" / "card-figures"
FIGURE_SRC = re.compile(
    r"assets/card-figures/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|svg|webp)"
)
FIGURE_LIMIT = 2

MARKUP = re.compile(
    r"(\*\*[^*\n]+\*\*|__[^_\n]+__|==[^=\n]+==|!![^!\n]+!!|%%[^%\n]+%%|@@[^@\n]+@@)"
)
MARKS = ("**", "__", "==", "!!", "%%", "@@")


def strip_markup(text: str | None) -> str:
    return MARKUP.sub(lambda m: m.group(0)[2:-2], text or "")


def check_markup(label: str, text: str, problems: list[str], *, allow_red: bool) -> None:
    rest = MARKUP.sub("", text or "")
    for mark in MARKS:
        if mark in rest:
            problems.append(f"{label}: 閉じていない装飾記法 {mark} が残っています")
    if MARKUP.search(strip_markup(text)):
        problems.append(f"{label}: 装飾の入れ子があります")
    if not allow_red and any(
        m.group(0).startswith("!!") for m in MARKUP.finditer(text or "")
    ):
        problems.append(f"{label}: Aに赤（!!）は使えません")
    for sentence in filter(None, re.split(r"(?<=。)", text or "")):
        highlights = [
            m for m in MARKUP.finditer(sentence) if m.group(0).startswith("==")
        ]
        if len(highlights) > 2:
            problems.append(f"{label}: 1文に黄（==）が{len(highlights)}か所あります")


def validate_card(
    card: dict,
    known_ids: set[str],
    known_choice_ids: set[str],
    is_new: bool,
    problems: list[str],
) -> None:
    card_id = card.get("id", "(id無し)")
    variants = card.get("variants") or {}
    for field in VARIANT_FIELDS:
        if not isinstance(variants.get(field), str) or not variants[field].strip():
            problems.append(f"{card_id}: variants.{field} が空です")
    for field in ("correction", "memoryPoint"):
        if not isinstance(card.get(field), str) or not card[field].strip():
            problems.append(f"{card_id}: {field} が空です")
    if not isinstance(card.get("correct"), bool):
        problems.append(f"{card_id}: correct は true / false のどちらかにしてください")

    explanations = card.get("explanations") or {}
    if (
        not isinstance(explanations.get("normal"), str)
        or not explanations["normal"].strip()
    ):
        problems.append(f"{card_id}: explanations.normal が空です")
    deep = explanations.get("deepDive") or {}
    for field in ("background", "trap", "example"):
        if not isinstance(deep.get(field), str) or not deep[field].strip():
            problems.append(f"{card_id}: explanations.deepDive.{field} が空です")
    if (
        not isinstance(explanations.get("commonSense"), str)
        or not explanations["commonSense"].strip()
    ):
        problems.append(f"{card_id}: explanations.commonSense が空です")

    for field in VARIANT_FIELDS:
        if field == "bCasualStyle":
            continue
        check_markup(
            f"{card_id}.{field}",
            variants.get(field, ""),
            problems,
            allow_red=field != "a",
        )
    check_markup(f"{card_id}.correction", card.get("correction", ""), problems, allow_red=True)
    check_markup(f"{card_id}.memoryPoint", card.get("memoryPoint", ""), problems, allow_red=True)

    for index, basis in enumerate(card.get("legalBasis") or []):
        if not isinstance(basis, dict):
            problems.append(f"{card_id}: legalBasis[{index}] はオブジェクトにしてください")
            continue
        if not str(basis.get("label") or "").strip():
            problems.append(f"{card_id}: legalBasis[{index}] に label が必要です")
        url = str(basis.get("url") or "")
        if not url.startswith("https://"):
            problems.append(f"{card_id}: legalBasis[{index}] の url は https:// で始めてください")

    # ⑦は素の文字列で表示するので装飾記法を書かせない
    table = card.get("comparisonTable")
    if isinstance(table, dict):
        for key in ("title", "memoryCue"):
            if MARKUP.search(str(table.get(key) or "")):
                problems.append(f"{card_id}: ⑦の{key}に装飾記法は使えません")
        rows = table.get("rows") or []
        if not 2 <= len(rows) <= 4:
            problems.append(f"{card_id}: ⑦の rows は2〜4行にしてください（今は{len(rows)}行）")
        labels = [str(r.get("label") or "") for r in rows]
        if len(set(labels)) != len(labels):
            problems.append(f"{card_id}: ⑦の label が重複しています")
        for row in rows:
            for key in ("label", "article", "rule", "conclusion"):
                if MARKUP.search(str(row.get(key) or "")):
                    problems.append(f"{card_id}: ⑦の行（{key}）に装飾記法は使えません")

    for comparison in card.get("crossFieldComparisons") or []:
        target = comparison.get("relatedCardId")
        if target and target not in known_ids:
            problems.append(f"{card_id}: ⑥のrelatedCardId {target} が存在しません")

    figures = card.get("figures")
    if figures is not None:
        if not isinstance(figures, list):
            problems.append(f"{card_id}: ⑧figures は配列にしてください")
            figures = []
        if len(figures) > FIGURE_LIMIT:
            problems.append(f"{card_id}: ⑧figures は{FIGURE_LIMIT}枚までです")
        for figure in figures:
            if not isinstance(figure, dict):
                problems.append(f"{card_id}: ⑧figures の要素はオブジェクトにしてください")
                continue
            src = str(figure.get("src") or "")
            if not FIGURE_SRC.fullmatch(src):
                problems.append(
                    f"{card_id}: ⑧figures の src {src!r} は assets/card-figures/ の下だけです"
                )
            elif not (FIGURE_DIR / Path(src).name).is_file():
                # 画像そのものはMac側で置く。存在しないパスを指すと、画面に壊れた画像が出る。
                problems.append(f"{card_id}: ⑧figures の画像 {src} がまだ置かれていません")
            for key in ("alt", "caption"):
                if not str(figure.get(key) or "").strip():
                    problems.append(f"{card_id}: ⑧figures には {key} が必要です")
                elif MARKUP.search(str(figure.get(key))):
                    problems.append(f"{card_id}: ⑧figures の {key} に装飾記法は使えません")

    if is_new:
        for field in NEW_CARD_FIELDS:
            if field not in card:
                problems.append(f"{card_id}: 新規カードには {field} が必要です")
        refs = card.get("relatedPastQuestions") or []
        if not refs:
            problems.append(f"{card_id}: ⑤（relatedPastQuestions）が空です。1本以上必要です")
        for ref in refs:
            if ref.get("choiceId") not in known_choice_ids:
                problems.append(f"{card_id}: ⑤の肢 {ref.get('choiceId')} が⑤正本にありません")


def load_canonical(path: Path | None = None) -> dict:
    return json.loads((path or CANONICAL_PATH).read_text(encoding="utf-8"))


def write_canonical(document: dict, path: Path | None = None) -> None:
    """同じディレクトリへ書いてから置き換える。書きかけが正本として残らないようにする。"""
    target = path or CANONICAL_PATH
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=".explanation_cards.", suffix=".json"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def merge_editable(current: dict, editable: dict) -> dict:
    """編集してよい項目だけを差し替えた新しいカードを返す。元のdictは変えない。"""
    merged = dict(current)
    for field in EDITABLE_FIELDS:
        if field in editable:
            merged[field] = editable[field]
    return merged


def editable_of(card: dict) -> dict:
    return {field: card[field] for field in EDITABLE_FIELDS if field in card}
