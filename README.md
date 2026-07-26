# 行政書士 過去問ラボ

過去問、学習カード、記述式、AI監査結果、類似問題候補を確認し、
回答履歴と類似問題の整理結果をMac内のSQLiteへ保存するLAN内サービスです。
2026年11月8日の受験日まで、実際の回答履歴を使って科目別・全分野頻出・
苦手・分野横断の教材を約3か月かけて育てます。

## AI向けドキュメント

- `AGENTS.md`: 毎回守る作業ルールの正本
- `CLAUDE.md`: `AGENTS.md`へのシンボリックリンク
- `ARCHITECTURE.md`: AI向けの全体構成・データフロー・3か月ロードマップ
- `HANDOFF.md`: VPS版からの経緯、現行仕様、問題追加ルール、既知の課題
- `authoring/README.md`: 問題取得、頻出度・類似候補、bundle生成

人間向けの全体構成図は、ラボ内の
`/services/gyousei-lab/architecture.html` で確認できます。

## Macでの配置

- ソース: `~/dev/yuki-services/apps/gyousei-lab/`
- 問題バンドル・回答履歴: `~/.local/share/yuki-services/gyousei-lab/`
- 非公開編集データ: `~/.local/share/yuki-services/gyousei-lab/authoring/`
- ログ: `~/Library/Logs/yuki-services/gyousei-lab/`
- URL: `http://192.168.10.102:8080/services/gyousei-lab/`
- API待受: `127.0.0.1:8817`

静的UIはnginxが直接配信し、`/services/gyousei-lab/api/`だけを
標準ライブラリ製Python APIへ転送します。外部Pythonパッケージや仮想環境は
必要ありません。

## データ

`gyousei-production.json`は問題・解説・AI監査・類似候補を含むリリース済み
読み取り専用バンドルです。`production.sqlite3`は回答履歴、学習カード履歴、
類似問題の判断を保存します。

直近10年の過去問は全6科目569問を掲載しています。○×学習カード55件は
現在は行政法です。学習カードには、そのカードの過去の正解回数・不正解回数を
問題文の前に表示します。

`weakness-latest.json`は、`production.sqlite3`を読み取り専用で集計した
非公開の弱点分析snapshotです。current revisionの学習カード回答だけを使い、
回答履歴そのものは変更しません。学習画面の「苦手・要観察」はこのsnapshotの
安全な公開projectionを使い、回答保存後に最新snapshotも自動更新します。
「回復中」は直近2回連続正解の状態として記録しますが、苦手ビューからは外します。

`all-subject-inventory.json`は、全分野20年分について「問題」「通常5肢」
「安全な○×候補」「多肢選択」「記述」を区別した、本文を含まない集計です。

ソースコードと実行時データを混在させないでください。バンドルやSQLiteを
Gitへ追加しません。

## 確認

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 -m unittest discover -s tests -v
/opt/homebrew/bin/node --check static/app.js
curl http://127.0.0.1:8817/health
```

弱点分析snapshotの生成:

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 weakness_analysis.py
```

## 移行記録

採用・除外したものと検証結果は`docs/migration.md`を参照してください。
