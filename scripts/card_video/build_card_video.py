#!/usr/bin/env python3
"""学習カードから、ご飯や家事の合間に見る○×動画（16:9・1920x1080）を作る。

1枚のカードは「問題と答えを一緒に見る（Bのやさしい言い換えを読み上げ、Cは画面だけ）→ 理由と覚え方」の順に流れる。
答えを隠して解くのはアプリでやるので、動画は最初から答えを見せて読む（2026-10-26に利用者が指定）。
最後に覚え方だけをもう一度読み上げる「まとめ」を付ける。

- 文章は稼働中のbundle（gyousei-production.json）から読む。画面に出ている内容と同じものを使うため。
- 声は VOICEVOX の冥鳴ひまり（2026-10-27に利用者が聞きくらべて選んだ。それまではmacOSのKyoko）。
  エンジンは ~/tools/tts/voicevox/ にあり、作る前に `./run --host 127.0.0.1 --port 50021` で起動しておく。
  Macの中だけで音声を作り、外部サービスへは何も送らない。画面の端に作者の決まりどおりのクレジットを出す。
- スライドは HTML を headless Chrome で撮る（render_slides.mjs）。合成は ffmpeg。
- 出力は ~/.local/share/yuki-services/gyousei-lab/card-video/ の <回のID>.mp4 と manifest.json。
  ソース側（static/）へ動画を置かない。nginx が /services/gyousei-lab/media/card-video/ で配る。
- 動画を見ても回答記録は付かない。production.sqlite3 には触れない。

使い方:
  python3.12 scripts/card_video/build_card_video.py --list
  python3.12 scripts/card_video/build_card_video.py --episode gyo-top-1
  python3.12 scripts/card_video/build_card_video.py --all          # 変わった回だけ作り直す
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT))
import card_edit  # noqa: E402  装飾記法の正規表現を画面と共有する

DATA_ROOT = Path.home() / ".local/share/yuki-services/gyousei-lab"
DEFAULT_BUNDLE = DATA_ROOT / "gyousei-production.json"
DEFAULT_OUT = DATA_ROOT / "card-video"
EPISODES_PATH = HERE / "episodes.json"
RENDERER = HERE / "render_slides.mjs"
NODE = "/opt/homebrew/bin/node"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
# engine が say なら name は macOS の声、voicevox なら (話者名, スタイル名)。credit は画面の端に出す
VOICE = {"engine": "voicevox", "name": ("冥鳴ひまり", "ノーマル"), "label": "冥鳴ひまり", "credit": "VOICEVOX:冥鳴ひまり"}
VOICEVOX = "http://127.0.0.1:50021"
PART_GAP = 0.35  # 読み上げ文の全角スペースのところに置く間。say は自分で間を置くが、VOICEVOX は置かない

# 作り方（スライドの見た目・読み上げの組み方）を変えたら上げる。カードの中身だけでは
# 気づけないため。manifest の digest に入るので、全回が「作り直しが必要」になる。
BUILDER_VERSION = 5
RATE = 48000
FPS = 30
FRAME = RATE // FPS  # 1フレームぶんのサンプル数。区間をこの倍数にそろえて、映像と音のずれを防ぐ

SUBJECT_FALLBACK = {
    "administrative-law": "行政法", "civil-law": "民法", "constitutional-law": "憲法",
    "commercial-law": "商法・会社法", "legal-foundations": "基礎法学", "general-knowledge": "基礎知識",
}


# ---- 読み上げ用の文章 -------------------------------------------------------

ERA = {"明": "明治", "大": "大正", "昭": "昭和", "平": "平成", "令": "令和"}
COURT = {"最判": "最高裁", "最大判": "最高裁大法廷", "大判": "大審院", "最決": "最高裁"}
CASE_DATE = re.compile(r"(最大判|最判|最決|大判)(明|大|昭|平|令)(\d+)\.(\d+)\.(\d+)")
PAREN = re.compile(r"（[^（）]*）|\([^()]*\)")
# Kyoko が読み違えるか、記号のまま黙ってしまうもの。聞いて気づいたらここへ足す。
READINGS = (
    ("○", "まる"), ("×", "ばつ"), ("→", "、"), ("⇒", "、"), ("＝", "、つまり、"),
    ("／", "。"), ("＋", "と"), ("肢", "し"), ("「", ""), ("」", ""),
)
LEADING_VERDICT = re.compile(r"^(誤り|正しい|正しいです|そのとおりです|妥当です|妥当でない)[。、]")


def spoken(text: str | None, *, drop_parens: bool = False) -> str:
    """装飾記法を外し、読み上げで崩れる記号と判例の日付を言葉にする。"""
    out = card_edit.strip_markup(text).replace("\n", "")

    def case_date(m: re.Match) -> str:
        court = COURT[m.group(1)]
        kind = "決定" if m.group(1) == "最決" else "判決"
        return f"{court}、{ERA[m.group(2)]}{m.group(3)}年{m.group(4)}月{m.group(5)}日{kind}"

    out = CASE_DATE.sub(case_date, out)
    if drop_parens:
        out = PAREN.sub("", out)
    for before, after in READINGS:
        out = out.replace(before, after)
    return out.strip()


def reason_sentence(correction: str | None) -> str:
    """解説（correction）の最初の1文を、答えの理由として読み上げる。「誤り。」のような判定だけの文は飛ばす。"""
    text = LEADING_VERDICT.sub("", card_edit.strip_markup(correction).strip())
    sentences = [s for s in re.split(r"(?<=。)", text.split("\n")[0]) if s.strip()]
    return sentences[0].strip() if sentences else ""


def source_label(card: dict) -> str:
    for ref in card.get("sourceRefs") or []:
        if ref.get("eraYear") and ref.get("questionNumber"):
            choice = f"肢{ref['choiceNumber']}" if ref.get("choiceNumber") else ""
            return f"{ref['eraYear']}問{ref['questionNumber']}{choice}"
    return ""


def frequency_label(card: dict) -> str:
    freq = card.get("frequency") or {}
    if isinstance(freq, str):
        return freq
    label = freq.get("label") or ""
    if freq.get("occurrences"):
        label += f"・20年で{freq['occurrences']}問"
    return label


def card_digest(card: dict) -> dict:
    """動画に入る部分だけ。ここが変わったら作り直しが必要。"""
    return {
        "id": card["id"], "topic": card.get("topic"), "subtopic": card.get("subtopic"),
        "a": (card.get("variants") or {}).get("a"), "b": (card.get("variants") or {}).get("b"),
        "c": (card.get("variants") or {}).get("c"), "correct": card.get("correct"),
        "correction": card.get("correction"), "memoryPoint": card.get("memoryPoint"),
        "figures": card.get("figures") or [], "source": source_label(card),
        "frequency": frequency_label(card),
    }


def episode_digest(episode: dict, cards: list[dict]) -> str:
    payload = {"builder": BUILDER_VERSION, "voice": VOICE["label"], "title": episode["title"],
               "description": episode.get("description", ""), "cards": [card_digest(c) for c in cards]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# ---- スライド ---------------------------------------------------------------

MARK_CLASS = {"**": "mk-strong", "__": "mk-underline", "==": "mk-mark",
              "!!": "mk-warn", "%%": "mk-key", "@@": "mk-quiet"}


def rich(text: str | None) -> str:
    """画面（static/app.js の richTextNodes）と同じ装飾をHTMLで付ける。"""
    parts = []
    for piece in card_edit.MARKUP.split(text or ""):
        if card_edit.MARKUP.fullmatch(piece or ""):
            parts.append(f'<span class="{MARK_CLASS[piece[:2]]}">{html.escape(piece[2:-2])}</span>')
        else:
            parts.append(html.escape(piece))
    return "".join(parts).replace("\n\n", "<br><br>").replace("\n", "<br>")


CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; }
body { width: 1920px; height: 1080px; overflow: hidden; display: flex; flex-direction: column;
  background: #f6f3ea; color: #1d2a26;
  font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif; }
.top { flex: 0 0 96px; display: flex; align-items: center; justify-content: space-between;
  padding: 0 64px; background: #183a34; color: #fff; font-size: 30px; font-weight: 700; }
.top .ep { color: #c8ddd4; }
.top .no { font-size: 34px; }
.top .no small { margin-left: 14px; padding: 4px 14px; border-radius: 8px; background: #f6c453; color: #1d2a24; font-size: 26px; }
.meta { display: flex; flex-wrap: wrap; gap: 14px; padding: 34px 64px 0; font-size: 26px; }
.pill { padding: 6px 18px; border-radius: 999px; background: #fff; border: 2px solid #d2ddd6; font-weight: 700; color: #183a34; }
.pill.freq { background: #a3271f; border-color: #a3271f; color: #fff; }
.pill.src { color: #55635d; font-weight: 600; }
.qa { display: flex; gap: 36px; align-items: flex-start; padding: 30px 64px 0; }
.qa .badge { flex-basis: 140px; height: 140px; font-size: 96px; }
.qa .question { flex: 1; font-weight: 600; line-height: 1.7; }
.plain { margin: 26px 64px 0; padding: 20px 28px; border-radius: 16px; background: #fff; border: 2px solid #d2ddd6; line-height: 1.7; }
.plain .label, .short .label { display: inline-block; margin-bottom: 6px; padding: 2px 14px; border-radius: 8px; background: #376f91; color: #fff; font-size: 22px; font-weight: 800; }
.plain .label small, .short .label small { margin-left: 10px; font-size: 20px; font-weight: 700; opacity: .9; }
.short { margin: 18px 64px 0; display: flex; gap: 20px; align-items: baseline; line-height: 1.6; font-weight: 700; }
.short .label { flex: 0 0 auto; margin: 0; background: #55635d; }
.label.ng-note { background: #b23a2e; }
.answer-head { display: flex; gap: 40px; align-items: center; padding: 30px 64px 0; }
.badge { flex: 0 0 190px; height: 190px; border-radius: 50%; display: grid; place-items: center;
  color: #fff; font-size: 130px; font-weight: 800; line-height: 1; }
.badge.ok { background: #2f7d55; } .badge.ng { background: #b23a2e; }
.answer-head .stmt { flex: 1; font-size: 27px; line-height: 1.65; color: #55635d; }
.answer-head .stmt strong { display: block; margin-bottom: 6px; color: #1d2a26; font-size: 34px; }
.answer-body { flex: 1; display: flex; gap: 40px; padding: 26px 64px 0; min-height: 0; }
.answer-body .text { flex: 1; line-height: 1.75; }
.answer-body figure { flex: 0 0 640px; margin: 0; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.answer-body figure img { max-width: 640px; max-height: 470px; object-fit: contain; background: #fff; border-radius: 12px; border: 2px solid #d2ddd6; }
.answer-body figcaption { font-size: 22px; color: #55635d; text-align: center; }
.memory { flex: 0 0 auto; margin: 22px 64px 44px; padding: 22px 30px; border-radius: 16px;
  background: #fff6dc; border: 3px solid #e2c27a; display: flex; gap: 26px; align-items: baseline; }
.memory .label { flex: 0 0 auto; padding: 4px 16px; border-radius: 8px; background: #8a6412; color: #fff; font-size: 26px; font-weight: 800; }
.memory .text { font-size: 36px; font-weight: 800; line-height: 1.6; }
.center { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 0 140px; }
.center h1 { margin: 0; font-size: 92px; color: #183a34; }
.center .lead { margin: 22px 0 0; font-size: 40px; color: #55635d; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; margin-top: 70px; }
.steps div { padding: 30px; border-radius: 18px; background: #fff; border: 2px solid #d2ddd6; font-size: 34px; font-weight: 700; line-height: 1.5; }
.steps b { display: block; color: #8a6412; font-size: 26px; margin-bottom: 8px; }
.summary { flex: 1; display: flex; flex-direction: column; gap: 18px; padding: 36px 64px 44px; }
.summary h2 { margin: 0 0 6px; font-size: 40px; color: #183a34; }
.summary .item { padding: 18px 26px; border-radius: 14px; background: #fff; border: 2px solid #d2ddd6; opacity: .45; }
.summary .item.now { opacity: 1; border-color: #e2a93b; background: #fff8e6; }
.summary .item .h { font-size: 24px; color: #55635d; font-weight: 700; }
.summary .item .t { font-size: 32px; font-weight: 800; line-height: 1.5; }
.summary.dense { gap: 12px; padding-top: 28px; }
.summary.dense .item { padding: 12px 24px; }
.summary.dense .item .t { font-size: 28px; line-height: 1.45; }
.mk-strong { color: #183a34; font-weight: 800; }
.mk-underline { font-weight: 700; text-decoration: underline; text-decoration-color: #2f7d55; text-decoration-thickness: 3px; text-underline-offset: 7px; }
.mk-mark { padding: 0 3px; background: linear-gradient(transparent 56%, #ffe487 56%); font-weight: 700; }
.mk-warn { color: #b23a2e; font-weight: 800; }
.mk-key { padding: 1px 8px; border-radius: 6px; background: #edf6fb; color: #2d6285; font-weight: 800; }
.mk-quiet { color: #62706a; font-size: .92em; }
.credit { position: absolute; right: 24px; bottom: 10px; font-size: 18px; color: #8a958f; }
"""


def page(body: str) -> str:
    credit = f'<div class="credit">{html.escape(VOICE["credit"])}</div>' if VOICE.get("credit") else ""
    return f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}{credit}</body></html>'


def top_bar(episode: dict, right: str) -> str:
    return f'<div class="top"><span class="ep">{html.escape(episode["title"])}</span><span class="no">{right}</span></div>'


def question_font(text: str) -> int:
    n = len(card_edit.strip_markup(text))
    return 54 if n <= 70 else 48 if n <= 110 else 43 if n <= 160 else 38


def plain_font(text: str) -> int:
    n = len(card_edit.strip_markup(text))
    return 34 if n <= 130 else 31 if n <= 190 else 28


def body_font(text: str, has_figure: bool) -> int:
    n = len(card_edit.strip_markup(text)) * (1.7 if has_figure else 1)
    return 34 if n <= 220 else 31 if n <= 330 else 28 if n <= 450 else 25


def card_slide(episode, card, n, total, subject) -> str:
    """問題文と答えを最初から一緒に出す。B・CはAと同じ結論の文なので、Aが×ならB・Cも×だと添える。"""
    meta = [f'<span class="pill">{html.escape(subject)}・{html.escape(card.get("topic") or "")}</span>']
    if card.get("subtopic"):
        meta.append(f'<span class="pill">{html.escape(card["subtopic"])}</span>')
    if frequency_label(card):
        meta.append(f'<span class="pill freq">{html.escape(frequency_label(card))}</span>')
    if source_label(card):
        meta.append(f'<span class="pill src">出典 {html.escape(source_label(card))}</span>')
    variants = card.get("variants") or {}
    a, b, c = variants.get("a") or "", variants.get("b") or "", variants.get("c") or ""
    ok = bool(card.get("correct"))
    mark = "○" if ok else "×"
    note_class = "label" if ok else "label ng-note"
    body = (top_bar(episode, f"第{n}問 / {total}")
            + f'<div class="meta">{"".join(meta)}</div>'
            + f'<div class="qa"><div class="badge {"ok" if ok else "ng"}">{mark}</div>'
            + f'<div class="question" style="font-size:{question_font(a)}px">{rich(a)}</div></div>')
    if b:
        body += (f'<div class="plain" style="font-size:{plain_font(b)}px">'
                 f'<span class="{note_class}">やさしく言い換えると<small>問題文と同じ内容なので、これも{mark}</small></span><br>'
                 f'{rich(b).replace("<br><br>", "<br>")}</div>')
    if c:
        body += (f'<div class="short" style="font-size:31px"><span class="{note_class}">ひとことで<small>これも{mark}</small></span>'
                 f'<span>{rich(c)}</span></div>')
    return page(body)


def answer_slide(episode, card, n, total) -> str:
    ok = bool(card.get("correct"))
    a = (card.get("variants") or {}).get("a") or ""
    figures = card.get("figures") or []
    figure_html = ""
    if figures:
        fig = figures[0]
        src = (REPO_ROOT / "static" / fig["src"]).resolve().as_uri()
        figure_html = (f'<figure><img src="{html.escape(src)}" alt="{html.escape(fig.get("alt", ""))}">'
                       f'<figcaption>{html.escape(fig.get("caption", ""))}</figcaption></figure>')
    correction = card.get("correction") or ""
    return page(
        top_bar(episode, f"第{n}問 / {total}<small>理由と覚え方</small>")
        + '<div class="answer-head">'
        + f'<div class="badge {"ok" if ok else "ng"}">{"○" if ok else "×"}</div>'
        + f'<div class="stmt"><strong>正解は {"○" if ok else "×"}</strong>{html.escape(card_edit.strip_markup(a))}</div>'
        + '</div>'
        + '<div class="answer-body">'
        + f'<div class="text" style="font-size:{body_font(correction, bool(figures))}px">{rich(correction)}</div>'
        + figure_html + '</div>'
        + f'<div class="memory"><span class="label">覚え方</span><span class="text">{rich(card.get("memoryPoint"))}</span></div>'
    )


def intro_slide(episode, total) -> str:
    return page(
        top_bar(episode, f"全{total}問")
        + '<div class="center">'
        + f'<h1>{html.escape(episode["title"])}</h1>'
        + f'<p class="lead">{html.escape(episode.get("description") or "")}　全{total}問</p>'
        + '<div class="steps">'
        + '<div><b>1</b>問題と答えを一緒に見る</div>'
        + '<div><b>2</b>やさしい言い換えで場面をつかむ</div>'
        + '<div><b>3</b>理由と覚え方を聞く</div>'
        + '</div></div>'
    )


def summary_slide(episode, items, current) -> str:
    rows = []
    for i, (n, card) in enumerate(items):
        rows.append(f'<div class="item{" now" if i == current else ""}">'
                    f'<div class="h">第{n}問　{html.escape(card.get("topic") or "")}・{html.escape(card.get("subtopic") or "")}</div>'
                    f'<div class="t">{rich(card.get("memoryPoint"))}</div></div>')
    # 覚え方が長い回は、5問ぶんが1画面に収まるよう文字を詰める（2026-10-27に行政法第6回ではみ出した）
    dense = sum(len(card_edit.strip_markup(card.get("memoryPoint"))) for _, card in items) > 240
    return page(top_bar(episode, "まとめ")
                + f'<div class="summary{" dense" if dense else ""}"><h2>覚え方だけ、もう一度</h2>' + "".join(rows) + '</div>')


def outro_slide(episode, cards) -> str:
    return page(
        top_bar(episode, "おわり")
        + '<div class="center"><h1>おつかれさまでした</h1>'
        + f'<p class="lead">迷ったカードは、動画の下の「この回の{len(cards)}枚をアプリで解く」から、そのまま解けます。</p></div>'
    )


# ---- 区間（1枚のスライドと、その間に流れる音） -------------------------------

@dataclass
class Segment:
    html: str
    narration: str = ""
    sfx: str | None = None          # next（カードの変わり目の合図）
    pad: float = 0.4                # 読み終えたあとの間
    chapter: dict | None = None
    samples: list[int] = field(default_factory=list)


def build_segments(episode: dict, cards: list[dict], subjects: dict) -> list[Segment]:
    total = len(cards)
    segs = [Segment(intro_slide(episode, total),
                    f"{episode['title']}。全{total}問です。問題と答えを一緒に見て、やさしい言い換え、理由、覚え方の順に聞きます。",
                    pad=0.8, chapter={"kind": "intro", "label": "はじめに"})]
    for n, card in enumerate(cards, 1):
        subject = subjects.get(card.get("subjectId"), "")
        variants = card.get("variants") or {}
        ok = bool(card.get("correct"))
        narration = (f"第{n}問。{spoken(card.get('topic'))}。{spoken(variants.get('a'))}"
                     f"　答えは、{'まる' if ok else 'ばつ'}。")
        if variants.get("b"):
            narration += f"　やさしく言うと。{spoken(variants.get('b'))}"
        segs.append(Segment(card_slide(episode, card, n, total, subject), narration, sfx="next", pad=0.8,
                            chapter={"kind": "card", "number": n, "cardId": card["id"],
                                     "label": f"{card.get('topic') or ''}・{card.get('subtopic') or ''}"}))
        narration = f"理由。{spoken(reason_sentence(card.get('correction')), drop_parens=True)}"
        if card.get("memoryPoint"):
            narration += f"　覚え方。{spoken(card.get('memoryPoint'), drop_parens=True)}"
        segs.append(Segment(answer_slide(episode, card, n, total), narration, pad=1.4))
    numbered = list(enumerate(cards, 1))
    for start in range(0, total, 5):
        group = numbered[start:start + 5]
        for i, (n, card) in enumerate(group):
            segs.append(Segment(summary_slide(episode, group, i),
                                ("まとめです。覚え方だけ、もう一度聞きます。" if n == 1 else "")
                                + f"第{n}問、{spoken(card.get('topic'))}。{spoken(card.get('memoryPoint'), drop_parens=True)}",
                                pad=0.7,
                                chapter={"kind": "summary", "label": "まとめ"} if n == 1 else None))
    segs.append(Segment(outro_slide(episode, cards),
                        "おつかれさまでした。迷ったカードは、動画の下のボタンから、アプリでまとめて解けます。",
                        pad=2.0, chapter={"kind": "outro", "label": "おわり"}))
    return segs


# ---- 音 ---------------------------------------------------------------------

def tone(freqs: list[tuple[float, float]], volume: float = 0.35) -> list[int]:
    out: list[int] = []
    for freq, seconds in freqs:
        n = int(RATE * seconds)
        for i in range(n):
            fade = min(1.0, i / 240, (n - i) / 480)
            out.append(int(32767 * volume * fade * math.sin(2 * math.pi * freq * i / RATE)))
    return out


SFX = {
    # 家事中に耳だけで聞いていても、次のカードへ移ったと分かるように鳴らす
    "next": lambda: tone([(660, 0.09), (990, 0.16)], 0.22) + [0] * int(RATE * 0.25),
}


def read_wav(path: Path) -> list[int]:
    with wave.open(str(path)) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise RuntimeError(f"想定外の音声形式です: {path}")
        raw = w.readframes(w.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


_style_id: int | None = None


def voicevox_style() -> int:
    global _style_id
    if _style_id is None:
        try:
            with urllib.request.urlopen(f"{VOICEVOX}/speakers", timeout=5) as r:
                styles = {(s["name"], t["name"]): t["id"] for s in json.load(r) for t in s["styles"]}
        except OSError as e:
            raise SystemExit("VOICEVOXのエンジンにつながりません。~/tools/tts/voicevox/macos-arm64 で "
                             "./run --host 127.0.0.1 --port 50021 を起動してから作ってください") from e
        _style_id = styles[VOICE["name"]]
    return _style_id


def voicevox_wav(text: str, dst: Path) -> None:
    style = voicevox_style()
    q = urllib.parse.urlencode({"text": text, "speaker": style})
    with urllib.request.urlopen(urllib.request.Request(f"{VOICEVOX}/audio_query?{q}", method="POST"), timeout=120) as r:
        query = json.load(r)
    query.update({"outputSamplingRate": RATE, "outputStereo": False})
    req = urllib.request.Request(f"{VOICEVOX}/synthesis?speaker={style}", data=json.dumps(query).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        dst.write_bytes(r.read())


def speak(text: str, work: Path, index: int) -> list[int]:
    if VOICE["engine"] == "say":
        src = work / f"say-{index:04d}.txt"
        dst = work / f"say-{index:04d}.wav"
        src.write_text(text, encoding="utf-8")
        subprocess.run(["say", "-v", VOICE["name"], "--file-format=WAVE", f"--data-format=LEI16@{RATE}",
                        "-o", str(dst), "-f", str(src)], check=True)
        return read_wav(dst)
    samples: list[int] = []
    parts = [p for p in text.split("　") if p.strip()]
    for j, part in enumerate(parts):
        dst = work / f"voice-{index:04d}-{j}.wav"
        voicevox_wav(part, dst)
        samples += read_wav(dst)
        if j < len(parts) - 1:
            samples += [0] * int(RATE * PART_GAP)
    return samples


def segment_audio(seg: Segment, work: Path, index: int) -> list[int]:
    samples: list[int] = []
    if seg.sfx:
        samples += SFX[seg.sfx]()
    if seg.narration:
        samples += speak(seg.narration, work, index)
    samples += [0] * int(RATE * seg.pad)
    samples += [0] * (-len(samples) % FRAME)
    return samples


# ---- 組み立て ---------------------------------------------------------------

def load_bundle(path: Path) -> tuple[dict, dict, str]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    cards = {c["id"]: c for c in bundle["explanationCards"]}
    subjects = dict(SUBJECT_FALLBACK)
    subjects.update({s["id"]: s.get("label") or s["id"] for s in bundle.get("subjects") or []})
    return cards, subjects, bundle.get("generatedAt", "")


def load_episodes() -> list[dict]:
    return json.loads(EPISODES_PATH.read_text(encoding="utf-8"))["episodes"]


def load_manifest(out: Path) -> dict:
    path = out / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"episodes": []}


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def build_episode(episode: dict, cards_by_id: dict, subjects: dict, bundle_at: str,
                  out: Path, keep: Path | None) -> dict:
    missing = [cid for cid in episode["cardIds"] if cid not in cards_by_id]
    if missing:
        raise SystemExit(f"{episode['id']}: bundleに無いカードIDがあります: {missing}")
    cards = [cards_by_id[cid] for cid in episode["cardIds"]]
    segs = build_segments(episode, cards, subjects)

    with tempfile.TemporaryDirectory(prefix="card-video-") as tmp:
        work = keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        jobs = []
        for i, seg in enumerate(segs):
            (work / f"slide-{i:04d}.html").write_text(seg.html, encoding="utf-8")
            jobs.append({"html": str(work / f"slide-{i:04d}.html"), "png": str(work / f"slide-{i:04d}.png")})
        (work / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        print(f"  スライド {len(jobs)}枚を撮影しています…", flush=True)
        subprocess.run([NODE, str(RENDERER), str(work / "jobs.json"), str(Path(tmp) / "chrome")], check=True)
        overflow = json.loads((work / "jobs.json.overflow.json").read_text(encoding="utf-8"))
        for path in overflow:
            print(f"  注意: 文字が画面からはみ出しています: {Path(path).name}", flush=True)

        print(f"  {VOICE['label']}で読み上げています…", flush=True)
        audio: list[int] = []
        chapters = []
        concat = ["ffconcat version 1.0"]
        for i, seg in enumerate(segs):
            seg.samples = segment_audio(seg, work, i)
            if seg.chapter:
                chapters.append({**seg.chapter, "start": round(len(audio) / RATE, 2)})
            audio += seg.samples
            concat += [f"file '{work / f'slide-{i:04d}.png'}'", f"duration {len(seg.samples) / RATE:.6f}"]
        concat.append(f"file '{work / f'slide-{len(segs) - 1:04d}.png'}'")
        (work / "list.ffconcat").write_text("\n".join(concat) + "\n", encoding="utf-8")
        with wave.open(str(work / "audio.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(struct.pack(f"<{len(audio)}h", *audio))

        print("  ffmpegで動画にしています…", flush=True)
        out.mkdir(parents=True, exist_ok=True)
        final = out / f"{episode['id']}.mp4"
        tmp_mp4 = out / f".{episode['id']}.tmp.mp4"
        subprocess.run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(work / "list.ffconcat"),
            "-i", str(work / "audio.wav"),
            "-fps_mode", "cfr", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
            str(tmp_mp4),
        ], check=True)
        os.chmod(tmp_mp4, 0o644)
        os.replace(tmp_mp4, final)

    return {
        "id": episode["id"], "group": episode.get("group", ""), "title": episode["title"],
        "description": episode.get("description", ""), "file": final.name, "cardIds": episode["cardIds"], "cardCount": len(cards),
        "durationSeconds": round(len(audio) / RATE, 1), "chapters": chapters,
        "voice": VOICE["label"], "voiceCredit": VOICE.get("credit", ""),
        "digest": episode_digest(episode, cards), "bundleGeneratedAt": bundle_at,
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": final.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="学習カードの○×動画を作る")
    parser.add_argument("--episode", action="append", default=[], help="作る回のID（複数可）")
    parser.add_argument("--all", action="store_true", help="内容が変わった回をすべて作り直す")
    parser.add_argument("--force", action="store_true", help="変わっていなくても作り直す")
    parser.add_argument("--list", action="store_true", help="各回の状態を表示するだけ")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--keep-work", type=Path, help="スライドと音声をこのフォルダに残す（確認用）")
    args = parser.parse_args(argv)

    cards_by_id, subjects, bundle_at = load_bundle(args.bundle)
    episodes = load_episodes()
    manifest = load_manifest(args.out)
    built = {e["id"]: e for e in manifest.get("episodes", [])}

    def state(ep: dict) -> str:
        if ep["id"] not in built or not (args.out / built[ep["id"]]["file"]).exists():
            return "未作成"
        cards = [cards_by_id[c] for c in ep["cardIds"] if c in cards_by_id]
        return "最新" if built[ep["id"]]["digest"] == episode_digest(ep, cards) else "作り直しが必要"

    if args.list:
        for ep in episodes:
            print(f"{ep['id']}\t{state(ep)}\t{ep['title']}（{len(ep['cardIds'])}枚）")
        return 0

    known = {ep["id"]: ep for ep in episodes}
    unknown = [e for e in args.episode if e not in known]
    if unknown:
        raise SystemExit(f"episodes.json に無い回です: {unknown}")
    targets = [known[e] for e in args.episode] if args.episode else (episodes if args.all else [])
    if not targets:
        parser.error("--episode か --all を指定してください")

    def save_manifest() -> None:
        # episodes.json の並びで書く。group は manifest が古くても今の定義で上書きする
        manifest = {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "episodes": [{**built[ep["id"]], "group": ep.get("group", "")} for ep in episodes if ep["id"] in built],
        }
        write_atomic(args.out / "manifest.json",
                     (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    todo = [ep for ep in targets if args.force or state(ep) != "最新"]
    for ep in targets:
        if ep not in todo:
            print(f"{ep['id']}: 最新なので作り直しません")
    for i, ep in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {ep['id']}: {ep['title']} を作っています", flush=True)
        built[ep["id"]] = build_episode(ep, cards_by_id, subjects, bundle_at, args.out, args.keep_work)
        print(f"  できました: {built[ep['id']]['durationSeconds']}秒", flush=True)
        save_manifest()  # 1本ごとに書く。全回の作成は1時間近くかかるので、途中で止めても出来た分は見られる
    save_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
