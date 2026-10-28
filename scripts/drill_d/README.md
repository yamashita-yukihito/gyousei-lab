# 演習D案（民法以外）の章データを作る道具

`static/gyosei-d/chapters.json` などは、ここのPythonから組み立てて書き出す。手で直してもよいが、
直したら同じ内容をここの章ファイルにも入れておく（次に書き出したとき戻らないように）。

- `common.py` … `Q()`（1問）、`edge()`、`node()`、`thing()`、`lb()`（e-Govの条文リンク）
- `gy_*.py` … 行政法の章。1ファイル1章で `CHAPTER` を持つ
- `build.py gyosei [--write]` … 章を並べ、○4×4・素直／ひっかけの片寄り・図の線・カードID・
  `examRefs` が過去問データ（非公開編集データの `all_subjects/`）に実在するかを確かめてから書き出す。章の順番は `SUBJECTS` の `modules`
- `pq.py [-s 行政法] 語...` … 20年分の過去問を語で探し、肢ごとに出題当時の正誤（推定）と出題箇所を出す
- `law.py 行手 5 6 36_2` … e-Gov法令API（2026-04-01時点）から条文を引く。応答は `~/.cache/yuki-services/egov/` に置く
- `overlap.py *.html` … Chromeの `--dump-dom` で保存したページの図から、枠とラベルの重なりを数える
- `mshot.mjs URL OUT.png 390 2400` … 端末幅を指定して撮る（ヘッドレスChromeの `--window-size` は狭い幅を再現しない）
- `mp_*.py` … 民法D案にあとから足した章（第11.5章 譲渡担保・第13.5章 弁済）。`minpo_extra.py [--write]` が検査して `static/minpo-d/chapters.json` の条文順の位置へ差し込む（番号は小数のまま。既存23章は触らない）
- `export_text.py` … 全6科目のD案の文章だけを `static/docs/drill-d/` へMarkdownで書き出す（別のAIに読みやすさを見てもらう用。2026-10-28の依頼は終わり、依頼文は片付けた。回答は `static/docs/GEMINI_*` / `ARGON_*`）。章を直したら書き出し直す

過去問の本文はここにもリポジトリにも保存しない（検索結果を見て、出題箇所だけを `examRefs` に書く）。
