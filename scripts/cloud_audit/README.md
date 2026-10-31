# クラウド点検の道具（`docs/CLOUD_AUDIT_TASK_20261031.md` 用）

2026-10-31〜11-01 のクラウドセッションが使ったもの。続きのセッションが同じ手順で再開できるように置いた。
アプリからは使わない。リポジトリの場所は `/home/user/gyousei-lab`（クラウドの既定）を前提にしている。

- `AUDIT_BRIEF.md` … 点検役のエージェントへの指示（英語）。`$S` は作業用の一時フォルダに読み替える
- `VERIFY_BRIEF.md` … 検証役への指示。点検役の理由と直し方を伏せて判定させる
- `law.py` … e-Gov 法令API v2（`asof=2026-04-01`）で条文を引く（`art`・`grep`・`suppl`・`info`・`find`）。応答は `~/.cache/yuki-services/egov/`
- `audit_tools.py` … カードの点検順（`order`）、バッチの切り出し（`batch`）、全カード検証（`validate`）、文字列の置き換え（`apply`。JSON を整形し直さない）
- `make_verify.py` … 点検役の指摘から、検証役に渡す入力（理由を伏せたもの）を作る
- `drill_apply.py` … 演習D案の `chapters.json` と `scripts/drill_d/` の章ファイルを同じ文に直す
- `drill_sync_check.py` … 非公開データ無しで章ファイルを組み立て、`chapters.json` と一致するかを見る
- `drill_extract.py` … 演習の章を1つ切り出す（結んだカードの結論も付ける）
- `t1_grep.py` … 法改正に関わる語を全教材から探す
- `numbers_extract.py` … T4 の数字の台帳の下書き（期間・割合・人数を全教材から抜き出す）
