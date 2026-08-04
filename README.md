# 行政書士 過去問ラボ

過去問、学習カード、記述式、AI監査結果、類似問題候補を確認し、
回答履歴と類似問題の整理結果をMac内のSQLiteへ保存するLAN内サービスです。
2026年11月8日の受験日まで、実際の回答履歴を使って科目別・全分野頻出・
苦手・分野横断の教材を育てます。2026年8月は開発を主に進め、9月から
本格的な学習に入る計画です。

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

直近10年の過去問は全6科目569問を掲載しています。○×学習カードも
全6科目238件がそろいました（行政法83、基礎知識41、民法49、憲法26、
商法・会社法24、基礎法学12）。学習カードには、そのカードの過去の正解回数・
不正解回数と、元になった過去問へのリンクを問題文の前に表示します。

頻出度監査の非公開正本は187件で、2026年7月31日に追加した13カードが未統合です。
9月の本格学習前に238件へそろえます。

回答後は、正しい形での覚え方、普通の解説、深掘り、常識力に加えて、
⑤実際の肢、⑥似た制度・他分野との違い、⑦法律ごとの結論くらべを出します。
⑦は行政手続法・行政不服審査法・行政事件訴訟法の三法比較だけでなく、
民法と商法、民法と国家賠償法のように科目をまたぐ比較も置いています。

出題から外す仕組みは2層あります。「習得済み」は正解が不正解より3回多く
なると自動で付き、「絶対覚えた」は自分で押したときだけ付きます。どちらも
通常の「おまかせ」と「苦手・要観察」から外れますが、**「全問題を出す」と「卒業済みだけ」には
含めます。** 「絶対覚えた」は全リセットでは消えません。どちらも「卒業済みだけ」
の一覧から解除でき、解除しても過去の回答と卒業した回数は残ります。

`weakness-latest.json`は、`production.sqlite3`を読み取り専用で集計した
非公開の弱点分析snapshotです。同じカードIDの全回答を使い、`answerRevision`は
回答時の版の記録だけに残します。回答履歴そのものは変更しません。学習画面の「苦手・要観察」はこのsnapshotの
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
