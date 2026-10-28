#!/usr/bin/env python3
"""カード動画の声を選ぶための聞きくらべ用サンプルを作る（2026-10-27）。

同じカード1枚ぶんの読み上げ（問題文→答え→やさしい言い換え→理由→覚え方）を、候補の声ごとに音声にする。
出力は ~/.local/share/yuki-services/gyousei-lab/card-video/voice-samples/ の <候補ID>.m4a と samples.json。
nginx の /services/gyousei-lab/media/card-video/ がそのまま配り、static/card-video/voices.html で聞ける。

- macOS の say はそのまま使える。
- VOICEVOX（127.0.0.1:50021）と AivisSpeech（127.0.0.1:10101）は、~/tools/tts/ のエンジンを起動してから実行する。
  どちらもMacの中だけで音声を作り、文章を外へ送らない。起動していないエンジンの候補は飛ばす。
- Irodori-TTS は Windows PC（LAN内）で常駐しているサーバーへ頼む。使い方は IRODORI_TTS_FROM_MAC.md。
  送った文章は Windows 側の secret/jobs/ に残る。Windows がスリープ中なら飛ばす。

使い方:
  python3.12 scripts/card_video/voice_samples.py               # 全候補を作り直す
  python3.12 scripts/card_video/voice_samples.py --only ir-whisper,ir-normal   # 指定した候補だけ作り、一覧へ混ぜる
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_card_video as video  # noqa: E402  読み上げ文の組み方を動画と同じにする

OUT = video.DEFAULT_OUT / "voice-samples"
SAMPLE_CARD = "gyo-aps-scope-001"  # 行政法・最頻出の第1問。×の問題で、B1も理由も入る
RATE = video.RATE
ENGINES = {"voicevox": "http://127.0.0.1:50021", "aivis": "http://127.0.0.1:10101"}
IRODORI = "http://192.168.10.101:8760"  # Windows PC。IPが変わることがある
IRODORI_SEED = 700  # 固定すると声と読み方がそろう

# (候補ID, エンジン, 声, 表示名, ひとこと)。VOICEVOX・AivisSpeech は話者名とスタイル名で引く
CANDIDATES = [
    ("say-kyoko", "say", "Kyoko", "Kyoko（今の声）", "macOS標準。いまの動画の声"),
    # Flo・Reed などの日本語版は say でも AVSpeechSynthesizer でもファイルへ書くと Kyoko の音になったので外した（2026-10-27）
    ("vv-no7-announce", "voicevox", ("No.7", "アナウンス"), "No.7（アナウンス）", "落ち着いた大人の女性。ニュース読みの調子"),
    ("vv-no7-story", "voicevox", ("No.7", "読み聞かせ"), "No.7（読み聞かせ）", "同じ声で、ゆっくりやわらかめ"),
    ("vv-himari", "voicevox", ("冥鳴ひまり", "ノーマル"), "冥鳴ひまり", "低めで落ち着いた女性"),
    ("vv-itako", "voicevox", ("東北イタコ", "ノーマル"), "東北イタコ", "大人の女性。やわらかい"),
    ("vv-sora", "voicevox", ("九州そら", "ノーマル"), "九州そら", "はきはきした女性"),
    ("vv-whitecul", "voicevox", ("WhiteCUL", "ノーマル"), "WhiteCUL", "明るめの女性"),
    ("vv-tsumugi", "voicevox", ("春日部つむぎ", "ノーマル"), "春日部つむぎ", "元気な若い女性"),
    ("vv-metan", "voicevox", ("四国めたん", "ノーマル"), "四国めたん", "定番のキャラクター声"),
    ("vv-zundamon", "voicevox", ("ずんだもん", "ノーマル"), "ずんだもん", "定番のキャラクター声。高め"),
    ("vv-ryusei", "voicevox", ("青山龍星", "ノーマル"), "青山龍星", "低い男性"),
    ("vv-takehiro", "voicevox", ("玄野武宏", "ノーマル"), "玄野武宏", "若い男性"),
    ("vv-suzumatsu", "voicevox", ("雀松朱司", "ノーマル"), "雀松朱司", "落ち着いた男性"),
    ("av-mao", "aivis", ("まお", "ノーマル"), "まお", "AivisSpeech。抑揚が自然な女性"),
    ("av-mao-calm", "aivis", ("まお", "おちつき"), "まお（おちつき）", "同じ声で、落ち着いた調子"),
    ("av-kohaku", "aivis", ("コハク", "ノーマル"), "コハク", "AivisSpeech。やわらかい女性"),
    ("ir-whisper", "irodori", "whisper", "ささやき（whisper）", "Irodori-TTS。ささやき声"),
    ("ir-normal", "irodori", "normal", "ふつう（normal）", "Irodori-TTS。くらべる用の普通の読み上げ"),
]
GROUPS = {"say": "macOS標準（いま入っている声）", "voicevox": "VOICEVOX（無料・Macの中で動く）",
          "aivis": "AivisSpeech（無料・Macの中で動く）", "irodori": "Irodori-TTS（WindowsのPCで動く）"}
CREDIT = {"voicevox": "VOICEVOX:{name}", "aivis": "AivisSpeech:{name}"}


def silence(seconds: float) -> list[int]:
    return [0] * int(RATE * seconds)


def read_pcm(data: bytes) -> list[int]:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(data)
        f.flush()
        with wave.open(f.name, "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
                raise SystemExit(f"想定外のWAV形式: {w.getparams()}")
            raw = w.readframes(w.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def speakers(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/speakers", timeout=5) as r:
        return {(s["name"], t["name"]): t["id"] for s in json.load(r) for t in s["styles"]}


def http_tts(base: str, style_id: int, text: str) -> list[int]:
    q = urllib.parse.urlencode({"text": text, "speaker": style_id})
    req = urllib.request.Request(f"{base}/audio_query?{q}", method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        query = json.load(r)
    query.update({"outputSamplingRate": RATE, "outputStereo": False})
    req = urllib.request.Request(f"{base}/synthesis?speaker={style_id}", data=json.dumps(query).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return read_pcm(r.read())


def irodori_tts(voice: str, text: str) -> list[int]:
    # 1行は30秒で打ち切られるので、文ごとに行を分ける。MP3で返るので ffmpeg でPCMへ戻す
    lines = [x for x in re.split(r"(?<=。)", text) if x.strip()]
    body = json.dumps({"lines": lines, "voice": voice, "seed": IRODORI_SEED, "pause_ms": 250},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{IRODORI}/tts", data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        mp3 = r.read()
    pcm = subprocess.run([video.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                          "-f", "s16le", "-ac", "1", "-ar", str(RATE), "pipe:1"],
                         input=mp3, capture_output=True, check=True).stdout
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def irodori_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{IRODORI}/health", timeout=3) as r:
            return bool(json.load(r).get("ready"))
    except (OSError, ValueError):
        return False


def say_tts(voice: str, text: str) -> list[int]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.wav"
        subprocess.run(["say", "-v", voice, "--file-format=WAVE", "--data-format=LEI16@48000",
                        "-o", str(path), text], check=True)
        return read_pcm(path.read_bytes())


def narration_parts() -> list[list[str]]:
    cards, subjects, _ = video.load_bundle(video.DEFAULT_BUNDLE)
    segs = video.build_segments({"id": "sample", "title": "sample"}, [cards[SAMPLE_CARD]], subjects)
    # 動画と同じく、全角スペースのところで間を置く
    return [[p for p in seg.narration.split("　") if p.strip()] for seg in segs[1:3]]


def synth(engine: str, voice, text: str, ids: dict) -> list[int]:
    if engine == "say":
        return say_tts(voice, text)
    if engine == "irodori":
        return irodori_tts(voice, text)
    return http_tts(ENGINES[engine], ids[engine][voice], text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="作る候補IDをカンマ区切りで。ほかの候補は前回の音声をそのまま一覧に残す")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    unknown = (only or set()) - {c[0] for c in CANDIDATES}
    if unknown:
        raise SystemExit(f"知らない候補ID: {sorted(unknown)}")
    OUT.mkdir(parents=True, exist_ok=True)
    parts = narration_parts()
    targets = [c for c in CANDIDATES if only is None or c[0] in only]
    ids = {}
    for engine, base in ENGINES.items():
        if not any(c[1] == engine for c in targets):
            continue
        try:
            ids[engine] = speakers(base)
        except OSError:
            print(f"{engine}: エンジンが起動していないので飛ばします")
    if any(c[1] == "irodori" for c in targets):
        if irodori_ready():
            ids["irodori"] = {}
        else:
            print("irodori: WindowsのTTSサーバーにつながらないので飛ばします（スリープ中か停止中）")
    samples = []
    for cid, engine, voice, label, note in targets:
        if engine != "say" and engine not in ids:
            continue
        started = time.monotonic()
        audio: list[int] = []
        for seg_parts in parts:
            for text in seg_parts:
                audio += synth(engine, voice, text, ids)
                audio += silence(0.35)
            audio += silence(0.8)
        elapsed = time.monotonic() - started
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(struct.pack(f"<{len(audio)}h", *audio))
            tmp_out = OUT / f".{cid}.tmp.m4a"
            subprocess.run([video.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp_out)], check=True)
            tmp_out.chmod(0o644)
            tmp_out.replace(OUT / f"{cid}.m4a")
        seconds = len(audio) / RATE
        samples.append({
            "id": cid, "engine": engine, "group": GROUPS[engine], "label": label, "note": note,
            "file": f"{cid}.m4a", "seconds": round(seconds, 1),
            # 作るのにかかった時間÷音声の長さ。全283枚（約6時間）を作り直す時間の目安になる
            "synthRatio": round(elapsed / seconds, 3),
            "credit": CREDIT[engine].format(name=voice[0]) if engine in CREDIT else "",
        })
        print(f"{cid}: {seconds:.1f}秒（作成 {elapsed:.1f}秒）", flush=True)
    if only is not None:
        # 作らなかった候補は前回のものを残し、並びは CANDIDATES の順にそろえる
        try:
            previous = json.loads((OUT / "samples.json").read_text(encoding="utf-8"))["samples"]
        except (OSError, ValueError, KeyError):
            previous = []
        made = {s["id"]: s for s in samples}
        kept = {s["id"]: s for s in previous if s["id"] not in made and (OUT / s["file"]).exists()}
        order = [c[0] for c in CANDIDATES]
        samples = sorted([*kept.values(), *made.values()],
                         key=lambda s: order.index(s["id"]) if s["id"] in order else len(order))
    video.write_atomic(OUT / "samples.json", (json.dumps({
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cardId": SAMPLE_CARD, "text": ["　".join(p) for p in parts], "samples": samples,
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
