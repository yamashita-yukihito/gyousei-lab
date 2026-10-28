# Mac から Irodori-TTS（Windows）で音声を生成する

Windows PC で常駐している Irodori-TTS サーバーに、Mac から HTTP で頼んで日本語音声（MP3）を受け取る手順。
2026-10-27 時点の仕様。サーバー側の正本は Windows の `C:\tools\Irodori-TTS-v4\CLAUDE.md` と `handoff.md`。

## 1. 前提

| 項目 | 値 |
|---|---|
| URL | `http://192.168.10.101:8760`（**IP は変わることがある**。つながらなければ Windows 側で確認） |
| モデル | Irodori-TTS v4.1-Small-MF（MeanFlow 版・常に 4 steps） |
| 速さ | モデル常駐で 1行あたり約 0.4 秒。起動直後の最初の1回だけ +2〜3 秒 |
| 返ってくるもの | MP3（192kbps）のバイナリ。WAV は返せない |
| 認証 | なし（LAN 内専用） |

- **Windows PC は45分でスリープする。** スリープ中・サーバー停止中はつながらない。
  呼ぶ側は必ず先に `/health` を見て、だめなら諦める（または Mac 側の別手段に切り替える）作りにする。
- サーバーは Windows で `start_tts_server.cmd` を実行すると起動する。Mac から起動はできない。
- 同時に生成できるのは1件だけ。複数のリクエストは順番に処理される（エラーにはならず待たされる）。
- Mac に ffmpeg は無い。結合済み MP3 がサーバーから返るので、Mac 側で音声を加工する必要はない。

## 2. 生きているか確認する

```bash
curl -s -m 3 http://192.168.10.101:8760/health
```

`"ready":true` なら使える。`"variant":"meanflow"` が現在の版（`full` / `int8-weight-only` のこともある）。
`"busy":true` は別の生成中。

使える声の一覧:

```bash
curl -s http://192.168.10.101:8760/voices
```

普通の読み上げには **`normal`**（ノイズの少ない参照音声）を使う。既定の `whisper` はささやき声。

## 3. 生成する（POST /tts）

```bash
curl -s -o out.mp3 -D headers.txt \
  -H "Content-Type: application/json" \
  --data-binary @request.json \
  http://192.168.10.101:8760/tts
```

`request.json`（**UTF-8 で保存したファイルを `@` で渡す**。日本語をコマンドライン引数に直接書くと崩れることがある）:

```json
{
  "lines": ["行政書士試験の学習を始めましょう。", "次の問題は、行政手続法についてです。"],
  "voice": "normal",
  "seed": 700
}
```

1行＝1回の生成。全行を `pause_ms` の無音でつないだ1本の MP3 が返る。

### リクエストの項目

| 項目 | 型 | 既定 | 説明 |
|---|---|---|---|
| `lines` | 文字列の配列 または `{"text","caption"}` の配列 | — | 1要素＝1行＝1回の生成。`text` とどちらか一方を使う |
| `text` | 文字列 | — | `lines` の代わり。改行で行に分かれる |
| `voice` | 文字列 | `"whisper"` | `/voices` の名前。読み上げなら `"normal"` |
| `seed` | 整数 | `-1` | `-1` は毎回ランダム（実際に使った値が `X-Seed` で返る）。**声や読み方を揃えたいなら固定する** |
| `increment_seed` | 真偽 | `false` | `true` で行ごとに seed を1ずつ増やす。既定は全行同じ seed |
| `caption` | 文字列 | なし | 声の様子の指示（例: `"落ち着いた声で、はっきり読み上げる。"`）。`lines` 内の `caption` が優先 |
| `cfg_scale_caption` | 数値 | caption があれば 3.0 | caption の効き具合 |
| `pause_ms` | 整数 | 300 | 行と行の間の無音（ミリ秒） |
| `blank_line_pause_ms` | 整数 | 700 | 空行（`""`）に入れる無音 |
| `return_lines` | 真偽 | `false` | `true` で行ごとの MP3 も作る（下の 5.） |
| `num_steps` / `fast` | — | — | **今の MeanFlow 版では無視される**（常に 4 steps）。送ってもエラーにはならない |

**知らない項目名を送ると 422 エラー**になる（綴り間違いが黙って無視されないようにしてある）。

### 1行の長さ

1行は最大30秒で打ち切られる。30秒近い生成は崩れやすいので、**句読点で区切って1行10〜20秒（だいたい60〜120字以内）**にする。
長文は Mac 側で文ごとに分けて `lines` に入れる。

### 行ごとに声の様子を変える

```json
{
  "lines": [
    {"text": "では、問題です。", "caption": "明るく元気に話している。"},
    {"text": "正解は、二番です。", "caption": "落ち着いた声で、ゆっくり話している。"}
  ],
  "voice": "normal",
  "seed": 700
}
```

## 4. 返ってくるもの

本文は MP3。応答ヘッダに結果の情報が入る（**ヘッダ名は小文字で届く**。大文字小文字を区別せずに読むこと）。

| ヘッダ | 内容 |
|---|---|
| `X-Job-Id` | ジョブID |
| `X-Duration-Sec` | 音声の長さ（秒） |
| `X-Elapsed-Sec` | サーバーでの生成時間（秒） |
| `X-Seed` | 実際に使った seed |
| `X-Num-Steps` | 実際に使った steps（MeanFlow 版は常に 4） |
| `X-Variant` | サーバーの版（`meanflow` など） |
| `X-Voice` / `X-Caption-Lines` / `X-Line-Count` | 使った声 / caption の付いた行数 / 行ごと MP3 の数 |

ジョブの詳細（本文は含まない）: `GET /jobs/<job_id>`

## 5. 行ごとの MP3 を取る

`"return_lines": true` を付けると、結合済み MP3 に加えて行ごとの MP3 も作られる。

```bash
curl -s -o line1.mp3 http://192.168.10.101:8760/jobs/<job_id>/line/1.mp3
```

番号は **1始まり**で、実際に生成した行（空行・caption だけの行を除く）の順。
カード1枚ずつ再生したい場合などはこちらを使う。

## 6. Python（標準ライブラリのみ）

```python
import json
import urllib.error
import urllib.request

TTS_URL = "http://192.168.10.101:8760"


def tts_available(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{TTS_URL}/health", timeout=timeout) as res:
            return bool(json.load(res).get("ready"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def synthesize(lines: list[str], voice: str = "normal", seed: int = 700, timeout: float = 120.0):
    """Return (mp3 bytes, headers). headers.get() ignores case; the server sends lower-case names."""
    body = json.dumps({"lines": lines, "voice": voice, "seed": seed}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{TTS_URL}/tts",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(), res.headers  # dict() にすると大文字小文字を区別してしまうので変換しない


if tts_available():
    mp3, headers = synthesize(["行政書士試験の学習を始めましょう。"])
    with open("out.mp3", "wb") as f:
        f.write(mp3)
    print(headers.get("X-Duration-Sec"), "秒 /", headers.get("X-Elapsed-Sec"), "秒で生成")
else:
    print("Windows の TTS サーバーにつながらない（スリープ中か停止中）")
```

エラー時は `urllib.error.HTTPError` になる。`e.read()` に `{"detail": ...}` が入っている
（422 = 項目の誤り。detail はどの項目が悪いかの配列。500 = 生成中のエラー。detail は文字列）。

## 7. 注意

- **送った文章は Windows 側の `secret/jobs/<job_id>/` に保存される。** ログ（`logs/jobs.jsonl`）には本文は書かれず、
  SHA-256・行数・字数だけが残る。
- 同じ文・同じ `voice`・同じ `seed` なら、ほぼ同じ音声になる（版を変えると変わる）。
- `/shutdown` は Windows 自身からしか呼べない。Mac から止めることはできない。
- 画面（`GET /`）、台本保存（`/scripts`）、LLM 会話（`/chat` `/chats`）もあるが、音声生成には不要。
