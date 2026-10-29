"""学習カードの⑨質問ノート（learnerQa）へ1件追記し、bundleの差し替えまで行う。

使い方:
  /opt/homebrew/bin/python3.12 scripts/add_learner_qa.py <カードID> <QA.json> --render <DIR>
      図をPNGにしてDIRへ書き、検証だけする（正本は書き換えない）。PNGで文字の重なりを見る。
  /opt/homebrew/bin/python3.12 scripts/add_learner_qa.py <カードID> <QA.json>
      追記 → 全カードへ validate_card → tests → bundle作り直し・差し替え → APIで確認。
  /opt/homebrew/bin/python3.12 scripts/add_learner_qa.py --relink
      全カードの⑨について、本文から過去問リンク（pastExams）を付け直す。流れは上と同じ。

QA.json は {"question": "…", "answer": "…", "figures": [{"src", "alt", "caption"}]}。
id と askedAt は省けば今日の日付で振る（qa-YYYYMMDD-n）。本文に「令和6年問32」のように書いた過去問は、
合格道場の問題ページへのリンク（pastExams）を自動で付ける（URLは非公開編集データの全分野カタログから引く）。図のSVGは先に
static/assets/card-figures/ へ置いておく。途中で落ちたら正本を元へ戻す。
コミットはしない（ほかの差分と混ぜないよう、自分の分だけを手で積む）。
"""
import argparse
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO))
import card_edit  # noqa: E402

DATA_ROOT = Path(os.environ.get(
    "GYOUSEI_DATA_ROOT", Path.home() / ".local/share/yuki-services/gyousei-lab/authoring"
))
CATALOGS = ("current_2016_2025", "archive_2006_2015")
ERA_BASE = {"平成": 1988, "令和": 2018}
EXAM_REF = re.compile(r"(平成|令和)(元|\d+)年度?問(\d+)")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
API = "http://127.0.0.1:8817/api/cards"


def load_exam_urls() -> dict[tuple[int, int], str]:
    urls = {}
    for period in CATALOGS:
        path = DATA_ROOT / "all_subjects" / period / "catalog" / "questions.json"
        for entry in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            urls[(entry["examYear"], entry["questionNumber"])] = entry["url"]
    return urls


def past_exams(answer: str, urls: dict[tuple[int, int], str]) -> list[dict]:
    """本文に出てくる過去問を、出てきた順に1問1リンクで返す。"""
    found: dict[str, str] = {}
    for era, year, number in EXAM_REF.findall(card_edit.strip_markup(answer)):
        label = f"{era}{year}年問{number}"
        key = (ERA_BASE[era] + (1 if year == "元" else int(year)), int(number))
        if key not in urls:
            raise SystemExit(f"{label} のURLがカタログにありません")
        found.setdefault(label, urls[key])
    return [{"label": label, "url": url} for label, url in found.items()]


def build_entry(card: dict, raw: dict) -> dict:
    entry = dict(raw)
    today = datetime.date.today()
    entry.setdefault("askedAt", today.isoformat())
    if "id" not in entry:
        prefix = "qa-" + entry["askedAt"].replace("-", "") + "-"
        used = [q["id"] for q in card.get("learnerQa", []) if q["id"].startswith(prefix)]
        entry["id"] = prefix + str(len(used) + 1)
    # 画面での並びと読みやすさのため、キーの順をそろえる。
    if "pastExams" not in entry:
        entry["pastExams"] = past_exams(entry["answer"], load_exam_urls())
    if not entry["pastExams"]:
        del entry["pastExams"]
    order = ("id", "askedAt", "question", "answer", "pastExams", "figures")
    return {key: entry[key] for key in order if key in entry}


def validate_all(document: dict) -> list[str]:
    ids = {c["id"] for c in document["items"]}
    problems: list[str] = []
    for card in document["items"]:
        card_edit.validate_card(card, ids, set(), False, problems)
    return problems


def render(figures: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for figure in figures:
        svg = card_edit.FIGURE_DIR / Path(figure["src"]).name
        box = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg.read_text(encoding="utf-8"))
        width, height = box.groups() if box else ("640", "640")
        png = out_dir / (svg.stem + ".png")
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={width},{height}", f"--screenshot={png}", svg.as_uri()],
            check=True, capture_output=True,
        )
        print("png:", png)


def published_qa_ids(card_id: str) -> list[str]:
    with urllib.request.urlopen(API, timeout=10) as response:
        cards = json.load(response)["explanationCards"]
    card = next(c for c in cards if c["id"] == card_id)
    return [q["id"] for q in card.get("learnerQa", [])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("card_id", nargs="?")
    parser.add_argument("qa_json", type=Path, nargs="?")
    parser.add_argument("--render", type=Path, metavar="DIR")
    parser.add_argument("--relink", action="store_true")
    args = parser.parse_args()

    original = card_edit.load_canonical()
    document = copy.deepcopy(original)
    if args.relink:
        return relink(original, document)
    if not (args.card_id and args.qa_json):
        parser.error("カードIDとQA.jsonを指定してください")
    card = next((c for c in document["items"] if c["id"] == args.card_id), None)
    if card is None:
        print(f"カード {args.card_id} がありません", file=sys.stderr)
        return 1
    entry = build_entry(card, json.loads(args.qa_json.read_text(encoding="utf-8")))
    if any(q["id"] == entry["id"] for q in card.get("learnerQa", [])):
        print(f"{entry['id']} は既にあります", file=sys.stderr)
        return 1
    card.setdefault("learnerQa", []).append(entry)

    problems = validate_all(document)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if args.render:
        render(entry.get("figures", []), args.render)
        print("検証OK（正本は書き換えていません）:", entry["id"])
        return 0

    revision = publish(original, document)
    published = published_qa_ids(args.card_id)
    if entry["id"] not in published:
        print(f"bundleは差し替えたが、APIに {entry['id']} が出ていません", file=sys.stderr)
        return 1
    print(f"追記・差し替え完了: {args.card_id} {entry['id']} revision={revision[:12]}")
    return 0


def relink(original: dict, document: dict) -> int:
    urls = load_exam_urls()
    changed = 0
    for card in document["items"]:
        for entry in card.get("learnerQa", []):
            links = past_exams(entry["answer"], urls)
            if links != entry.get("pastExams", []):
                entry.pop("pastExams", None)
                rebuilt = {key: entry.pop(key) for key in ("id", "askedAt", "question", "answer") if key in entry}
                if links:
                    rebuilt["pastExams"] = links
                rebuilt.update(entry)
                entry.clear()
                entry.update(rebuilt)
                changed += 1
                print(card["id"], rebuilt["id"], [link["label"] for link in links])
    if not changed:
        print("付け直すものはありません")
        return 0
    problems = validate_all(document)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    revision = publish(original, document)
    print(f"{changed}件を付け直して差し替え完了 revision={revision[:12]}")
    return 0


def publish(original: dict, document: dict) -> str:
    """正本を書き、tests→bundle作り直し・差し替え。失敗したら正本を戻す。"""
    card_edit.write_canonical(document)
    try:
        tests = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=REPO, capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if tests.returncode != 0:
            raise RuntimeError("tests failed:\n" + tests.stderr[-3000:])
        import server  # bundle生成は server と同じ口を通す（件数の検証・収録減の検査ごと）
        revision = server._rebuild_bundle_in_place()
    except BaseException:
        # 画面に出ない内容を正本へ残さない。
        card_edit.write_canonical(original)
        print("失敗したので正本を元へ戻しました", file=sys.stderr)
        raise
    return revision


if __name__ == "__main__":
    sys.exit(main())
