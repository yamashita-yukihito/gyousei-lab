# 行政書士 過去問ラボ 移行記録

更新日: 2026-07-26 JST

## 移行元

- API: `/home/yuki/services/gyousei-production/server.py`
- UI: `/var/www/portfolio/quiz/gyousei/`
- 問題バンドル:
  `/home/yuki/gyousei_data/builds/releases/gyousei-production.json`
- 回答履歴: `/var/lib/gyousei-quiz-prod/production.sqlite3`
- 旧URL: `/portfolio/quiz/gyousei/`

## 採用したもの

- production版Python APIと18件のテスト
- 現在配信中のHTML、CSS、JavaScript、解説図6枚
- リリース済み問題バンドル
- SQLiteの回答履歴・学習カード履歴・類似問題判断
- 問題取得、抽出、頻出度・類似候補、監査、bundle生成コード
- 合格道場の取得証跡、抽出結果、編集正本、監査データ
- 令和2〜7年度の公式全科目参考コーパス

移行時点の件数:

- 過去問220問
- 学習カード55件
- Claude監査20件
- 類似候補588組
- 過去問回答2件
- 学習カード回答15件
- 類似問題判断588件

## 移行しなかったもの

- 停止済みの`gyousei-progress.service`と`gyousei_sample`
- キャッシュ、`__pycache__`、ruffキャッシュ
- systemd設定、Caddy設定、旧VPSのログ
- SQLiteの古い`pre-ox`バックアップ
- 反映済みの一度限りの変換スクリプトと旧監査資料

旧VPSのサービスと元ファイルは変更していない。

## Mac向け変更

- デフォルトデータパスを`~/.local/share/yuki-services/gyousei-lab/`へ変更
- ポートを未使用の`8817`へ変更
- 静的UIをnginxから直接配信
- APIだけをlaunchdの1プロセスへ転送
- 問題生成コードを`apps/gyousei-lab/authoring/`へ配置
- 非公開編集データを
  `~/.local/share/yuki-services/gyousei-lab/authoring/`へ分離
- Basic認証とcronは追加しない

ブラウザのlocalStorageはオリジン単位のため、旧VPS画面にだけ残っている
未送信回答は自動移行できない。サーバーへ送信済みの履歴はSQLiteで移行する。
