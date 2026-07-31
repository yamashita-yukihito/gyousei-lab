#!/usr/bin/env python3.12
"""学習カードを外部AI（ChatGPTなど）へ渡し、編集結果を取り込むための入出力。

正本 `content/explanation_cards.json` を直接渡すと、大きすぎるうえに、
編集してはいけない項目（頻出度監査の数字、⑤の参照、review履歴）まで書き換えられてしまう。
そこで、

    export  編集してよい項目だけを抜き出した「作業用JSON」を書き出す
    import  戻ってきた作業用JSONを検証し、正本へ差し戻す

の2段にする。⑤の肢原文と頻出度は読み取り専用の参考情報として同梱し、
取り込み時に無視する（改ざんされても正本は動かない）。

使い方:

    # 憲法のカードだけ書き出す
    python3.12 authoring/tools/card_exchange.py export --subject constitutional-law

    # 特定のカードだけ書き出す
    python3.12 authoring/tools/card_exchange.py export --ids con-eq-framework-001,con-vote-right-001

    # 戻ってきたファイルを検証だけする（正本は変わらない）
    python3.12 authoring/tools/card_exchange.py import <編集後のJSON>

    # 検証を通ったら正本へ反映する
    python3.12 authoring/tools/card_exchange.py import <編集後のJSON> --apply

`--apply` は正本を書き換えるだけで、本番画面はまだ変わらない。
反映には `gyousei-production-bundle` でのbundle再生成と設置が必要。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_ROOT = Path(
    os.environ.get(
        "GYOUSEI_DATA_ROOT",
        os.path.expanduser("~/.local/share/yuki-services/gyousei-lab/authoring"),
    )
)
# 学習カードの正本はGitリポジトリ側。⑤の肢原文は取得元データなので非公開領域に残す。
CANONICAL = Path(
    os.environ.get(
        "GYOUSEI_CARD_SOURCE",
        Path(__file__).resolve().parents[2] / "content" / "explanation_cards.json",
    )
)
EVIDENCE = DATA_ROOT / "canonical" / "related_question_source.json"
EXCHANGE = DATA_ROOT / "exchange"

EXCHANGE_SCHEMA = "gyousei-card-exchange@1"

# 検証ルールと正本の読み書きは card_edit.py が正本。ここで二重に持たない。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from card_edit import (  # noqa: E402
    EDITABLE_FIELDS,
    FIGURE_DIR,
    FIGURE_LIMIT,
    FIGURE_SRC,
    MARKS,
    MARKUP,
    NEW_CARD_FIELDS,
    VARIANT_FIELDS,
    check_markup,
    strip_markup,
    validate_card,
    write_canonical,
)


def answer_revision(card: dict, law_as_of: str) -> str:
    """server.py の card_answer_revision と同じ計算。回答履歴が生きるかを事前に見るために使う。"""
    variants = card.get("variants") or {}
    source = {
        "a": strip_markup(variants.get("a")),
        "c": strip_markup(variants.get("c")),
        "correct": card.get("correct"),
        "lawAsOf": card.get("lawAsOf") or law_as_of,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_canonical() -> dict:
    return json.loads(CANONICAL.read_text(encoding="utf-8"))


def load_evidence_texts() -> dict[str, dict[str, Any]]:
    document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    records = {r["rawId"]: r for r in document["records"]}
    texts = {}
    for choice in document["choices"]:
        record = records.get(choice["rawQuestionId"], {})
        texts[choice["choiceId"]] = {
            "eraYear": record.get("eraYear"),
            "questionNumber": record.get("questionNumber"),
            "choiceLabel": choice.get("choiceLabel"),
            "text": choice.get("officialOriginalText"),
            "truthAtExam": choice.get("examEvaluation"),
            "truthNow": choice.get("currentEvaluation"),
        }
    return texts


# ---------------------------------------------------------------- export


def build_export(
    document: dict,
    card_ids: list[str],
    include_evidence: bool,
) -> dict:
    law_as_of = document["studyDecks"][0].get("lawAsOf") or document["meta"]["examLawAsOf"]
    by_id = {c["id"]: c for c in document["items"]}
    evidence_texts = load_evidence_texts() if include_evidence else {}

    cards = []
    for card_id in card_ids:
        card = by_id[card_id]
        editable = {field: card[field] for field in EDITABLE_FIELDS if field in card}
        readonly: dict[str, Any] = {
            "subjectId": card["subjectId"],
            "category": card["category"],
            "clusterId": card["clusterId"],
            "frequency": card["frequency"],
            "sourceRefs": card["sourceRefs"],
            "answerRevisionBefore": answer_revision(card, law_as_of),
        }
        if include_evidence:
            readonly["relatedPastQuestions"] = [
                {
                    **ref,
                    **{k: v for k, v in (evidence_texts.get(ref["choiceId"]) or {}).items()},
                }
                for ref in card["relatedPastQuestions"]
            ]
        else:
            readonly["relatedPastQuestions"] = card["relatedPastQuestions"]
        cards.append({"id": card_id, "editable": editable, "readonly": readonly})

    return {
        "schemaVersion": EXCHANGE_SCHEMA,
        "exportedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "examLawAsOf": law_as_of,
        "targetExam": document["meta"]["targetExam"],
        "subjects": document["meta"]["subjects"],
        "howToEdit": [
            "editable の中だけを書き換えてください。readonly は参考情報で、戻しても無視されます。",
            "カードを増やすときは cards の末尾へ、id と subjectId と category と clusterId と "
            "sourceRefs と relatedPastQuestions を含む形で追加してください。",
            "id は既存と重複しない安定した英数字にしてください。あとから変えると回答履歴と切れます。",
            "変更しないカードは、丸ごと削ってかまいません。戻ってきたものだけを見ます。",
        ],
        "cards": cards,
    }


def command_export(args: argparse.Namespace) -> int:
    document = load_canonical()
    by_id = {c["id"]: c for c in document["items"]}

    if args.ids:
        card_ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        unknown = [i for i in card_ids if i not in by_id]
        if unknown:
            print(f"error: 知らないカードID: {', '.join(unknown)}", file=sys.stderr)
            return 1
    elif args.subject:
        card_ids = [c["id"] for c in document["items"] if c["subjectId"] == args.subject]
        if not card_ids:
            known = sorted({c["subjectId"] for c in document["items"]})
            print(f"error: 該当なし。subjectIdは {', '.join(known)}", file=sys.stderr)
            return 1
    else:
        card_ids = [c["id"] for c in document["items"]]

    payload = build_export(document, card_ids, include_evidence=not args.no_evidence)

    EXCHANGE.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = args.subject or ("selected" if args.ids else "all")
    output = Path(args.output) if args.output else EXCHANGE / f"cards-{name}-{stamp}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)

    size_kb = output.stat().st_size / 1024
    print(f"書き出しました: {output}")
    print(f"  カード{len(card_ids)}枚 / {size_kb:.0f} KB")
    if not args.no_evidence:
        print("  ⑤の肢原文（合格道場の過去問）を同梱しています。外部へ渡す前に確認してください。")
        print("  含めたくないときは --no-evidence を付けて書き出し直してください。")
    return 0


# ---------------------------------------------------------------- import


def command_import(args: argparse.Namespace) -> int:
    incoming = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if incoming.get("schemaVersion") != EXCHANGE_SCHEMA:
        print(
            f"error: schemaVersion が {EXCHANGE_SCHEMA} ではありません: "
            f"{incoming.get('schemaVersion')!r}",
            file=sys.stderr,
        )
        return 1

    document = load_canonical()
    law_as_of = document["studyDecks"][0].get("lawAsOf") or document["meta"]["examLawAsOf"]
    by_id = {c["id"]: c for c in document["items"]}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    known_choice_ids = {c["choiceId"] for c in evidence["choices"]}

    entries = incoming.get("cards") or []
    if not entries:
        print("error: cards が空です", file=sys.stderr)
        return 1

    problems: list[str] = []
    updates: list[tuple[dict, dict, bool]] = []  # (対象カード, 新しい中身, 新規か)
    incoming_ids = {e.get("id") for e in entries}
    known_ids = set(by_id) | {i for i in incoming_ids if i}

    for entry in entries:
        card_id = entry.get("id")
        if not card_id:
            problems.append("id の無いカードがあります")
            continue
        editable = entry.get("editable")
        if not isinstance(editable, dict):
            problems.append(f"{card_id}: editable がありません")
            continue

        is_new = card_id not in by_id
        if is_new:
            # 新規カードは editable と、readonly 側に置かれた識別情報を合わせて組み立てる
            merged = dict(editable)
            readonly = entry.get("readonly") or {}
            for field in NEW_CARD_FIELDS:
                if field in editable:
                    merged[field] = editable[field]
                elif field in readonly:
                    merged[field] = readonly[field]
            merged["id"] = card_id
            merged.setdefault("review", {
                "currentLawStatus": "external-draft-pending-review",
                "humanReview": "chatgpt-draft-needs-verification",
            })
            merged.setdefault("frequency", {
                "label": "重要論点",
                "occurrences": 1,
                "yearCount": 1,
                "recentOccurrences": 1,
                "archiveOccurrences": 0,
                "scope": "未監査（取り込み後に頻出度監査が必要）",
                "basis": "外部AIの下書き。取り込み後に原問と突き合わせて数え直すこと",
            })
            merged.setdefault("crossFieldComparisons", [])
            merged.setdefault("evidenceHighlights", [])
            target = merged
        else:
            target = json.loads(json.dumps(by_id[card_id], ensure_ascii=False))
            for field in EDITABLE_FIELDS:
                if field in editable:
                    target[field] = editable[field]

        validate_card(target, known_ids, known_choice_ids, is_new, problems)
        updates.append((by_id.get(card_id), target, is_new))

    if problems:
        print(f"NG: {len(problems)}件")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"検証OK: {len(updates)}件（新規 {sum(1 for _, _, n in updates if n)}件）")
    print()
    revision_changes: list[str] = []
    for before, after, is_new in updates:
        if is_new:
            print(f"  + 新規 {after['id']}  {after.get('subtopic', '')}")
            continue
        changed = [f for f in EDITABLE_FIELDS if before.get(f) != after.get(f)]
        if not changed:
            continue
        old_revision = answer_revision(before, law_as_of)
        new_revision = answer_revision(after, law_as_of)
        mark = "!" if old_revision != new_revision else " "
        print(f"  {mark} {after['id']:<48} 変更: {', '.join(changed)}")
        if old_revision != new_revision:
            revision_changes.append(after["id"])

    if revision_changes:
        print()
        print(f"注意: 回答revisionが変わるカードが {len(revision_changes)} 枚あります。")
        print("      A・C・correct のどれかが変わっており、そのカードの過去の回答は")
        print("      現在の習得判定から外れます（履歴自体は消えません）。")
        for card_id in revision_changes:
            print(f"        {card_id}")

    if not args.apply:
        print()
        print("正本はまだ変えていません。反映するときは --apply を付けて実行してください。")
        return 0

    # 正本はGit管理下にある。未commitの変更があると、取り込みで上書きされて戻せなくなる。
    dirty = os.popen(
        f'git -C "{CANONICAL.parent}" status --porcelain -- "{CANONICAL.name}" 2>/dev/null'
    ).read().strip()
    if dirty and not args.force:
        print()
        print(f"error: {CANONICAL.name} に未commitの変更があります。")
        print("       先に git commit するか、--force を付けてください。")
        return 1

    # 正本はGit管理下なので、履歴はcommitが持つ。退避はリポジトリを汚さないよう外へ置く。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = DATA_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_dir / f"explanation_cards.pre-exchange-{stamp}.json"
    shutil.copy2(CANONICAL, backup)

    index = {c["id"]: i for i, c in enumerate(document["items"])}
    deck = document["studyDecks"][0]
    added = 0
    for _before, after, is_new in updates:
        if is_new:
            document["items"].append(after)
            deck["cardIds"].append(after["id"])
            added += 1
        else:
            document["items"][index[after["id"]]] = after

    if len(set(deck["cardIds"])) != len(deck["cardIds"]):
        print("error: デッキのカードIDが重複しました。正本は書き換えていません", file=sys.stderr)
        return 1
    if len(deck["cardIds"]) != len(document["items"]):
        print("error: デッキ枚数とカード枚数が合いません。正本は書き換えていません", file=sys.stderr)
        return 1

    temporary = CANONICAL.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, CANONICAL)

    print()
    print(f"正本へ反映しました（退避: {backup.name}）")
    print(f"  カード {len(document['items'])}枚（新規 {added}枚）")
    print()
    print("本番画面はまだ変わっていません。次を実行してください:")
    print("  cd ~/dev/yuki-services/apps/gyousei-lab/authoring")
    print('  export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"')
    print("  uv run python -m unittest discover -s tests")
    print(
        f"  uv run gyousei-production-bundle --output \"$GYOUSEI_DATA_ROOT/builds/next.json\" "
        f"--expected-card-count {len(document['items'])} --expected-evidence-count <⑤の件数>"
    )
    if added:
        print("  ※ 新規カードは頻出度が未監査のまま入っています。原問と突き合わせて数え直してください。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="外部AIへ渡す作業用JSONを書き出す")
    group = exporter.add_mutually_exclusive_group()
    group.add_argument("--subject", help="科目で絞る（例: constitutional-law）")
    group.add_argument("--ids", help="カードIDをカンマ区切りで指定する")
    exporter.add_argument("--output", help="出力先。省略すると exchange/ へ日時付きで作る")
    exporter.add_argument(
        "--no-evidence",
        action="store_true",
        help="⑤の肢原文を同梱しない（過去問本文を外部へ出したくないとき）",
    )
    exporter.set_defaults(func=command_export)

    importer = sub.add_parser("import", help="編集済みJSONを検証し、正本へ差し戻す")
    importer.add_argument("file", help="編集済みJSONのパス")
    importer.add_argument("--apply", action="store_true", help="検証を通ったら正本を書き換える")
    importer.add_argument(
        "--force",
        action="store_true",
        help="正本に未commitの変更があっても上書きする",
    )
    importer.set_defaults(func=command_import)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
