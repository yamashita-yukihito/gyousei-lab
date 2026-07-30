# AGENTS.md — gyousei-lab

このファイルが `gyousei-lab` 配下のAI向けルールの正本です。
`CLAUDE.md` はこのファイルへのシンボリックリンクにし、内容を二重管理しません。

## 1. 作業開始時

- ユーザーへの説明は日本語で、簡潔かつ具体的に行う。
- 最初に `pwd` を確認し、`README.md`、`HANDOFF.md`、`ARCHITECTURE.md`、`docs/migration.md` を読む。
- 変更前に対象ファイル、起動方法、実行中サービス、永続データの場所を確認する。
- 親ディレクトリの `../../AGENTS.md` も適用される。矛盾する場合は、このファイルのプロジェクト固有ルールを優先する。
- このディレクトリはprivate Gitリポジトリ `yamashita-yukihito/gyousei-lab` として管理する。親の `yuki-services` はGitリポジトリではないため、親で `git init` しない。
- `rg` はMacに未導入だった。利用できれば使い、なければ `grep` と `find` を使う。

## 2. このサービスの目的

- 1人の利用者がLAN内で使う行政書士試験の学習サービス。
- 2026年11月8日の受験日まで約3か月、実際に問題を解きながら教材・弱点分析・分野横断学習を育てる長期プロジェクト。
- 過去問、○×学習カード、記述式、解説、類似肢、回答履歴を扱う。
- 会員登録、利用者分離、IP別集計、Basic認証は、ユーザーが改めて求めない限り追加しない。
- 行政法、憲法、民法、商法・会社法、基礎法学、基礎知識の全6科目を同じアプリ・同じデッキで扱う。
- 科目別、全分野頻出、苦手、分野横断は別アプリへ複製せず、共通学習エンジンのfilter preset / `studyView` として作る。
- 構成・データフロー・正本・ロードマップの正本は `ARCHITECTURE.md`。人間向けは `static/architecture.html`。

## 3. 配置と実行環境

- ソース: `~/dev/yuki-services/apps/gyousei-lab/`
- 静的UI: `static/`
- 学習カードの正本: `content/explanation_cards.json`（Git管理。2026-07-30に非公開領域から移した）
- API: `server.py`
- テスト: `tests/`
- 問題生成コード: `authoring/`
- 問題バンドルとSQLite: `~/.local/share/yuki-services/gyousei-lab/`
- 非公開編集データ:
  `~/.local/share/yuki-services/gyousei-lab/authoring/`
- ログ: `~/Library/Logs/yuki-services/gyousei-lab/`
- LAN URL: `http://192.168.10.102:8080/services/gyousei-lab/`
- API直接待受: `127.0.0.1:8817`
- launchd label: `com.yuki.gyousei-lab`
- nginxは `static/` を直接配信し、`/services/gyousei-lab/api/` と `/health` だけをAPIへ転送する。

ソース、実行時データ、ログを混在させない。問題バンドル、SQLite、WAL、ログ、キャッシュをソース側へコピーしない。
例外は**学習カードの正本 `content/explanation_cards.json` だけ**である。これは手で頻繁に書き換える著作物で、変更履歴と差分レビューがそのまま価値になるため、2026-07-30にGit管理へ移した。取得スナップショット、⑤の出典、頻出度監査、SQLiteは従来どおり非公開編集データ配下が正本で、ソース側へ持ち込まない。`content/` はnginxの配信対象外（配信されるのは `static/` だけ）なので、Webへは出ない。

## 4. 絶対に守る互換性

- `production.sqlite3` は利用者の正本データ。削除、初期化、別DBでの上書きをしない。
- 回答イベントは追記型として扱い、既存回答を更新・削除する仕様へ変えない。
- 卒業・絶対覚えた・自信度も追記型の `card_marks` に置く。リセットは「ここより前を習得判定に数えない」区切りであって、回答や過去の卒業回数を消す操作ではない。「絶対覚えた」を全リセットの対象にしない。
- カードID、問題ID、既存のdeck ID `administrative-law-frequent-10` を不用意に変更しない。ID変更は回答履歴との対応を切る。
- 今後の科目も既存の1デッキへ追加し、`subjectId` で絞り込む。科目ごとに別deckへ分割しない。
- 行政法用、民法用、全分野頻出、苦手などの画面でも、同じカードIDと回答履歴を共有する。
- APIの公開用projectionを迂回して、内部パス、provider解説全文、prompt、ログ、archive内部IDをブラウザへ返さない。
- **回答履歴はカードIDだけで引き継ぐ。** A・B・C・正解・法令基準日のどれを直しても、同じカードIDなら過去の回答・卒業回数・「絶対覚えた」・自信度をそのまま数える（2026-07-30に変更）。編集のたびに履歴が消えることを心配しなくてよい。
- `answerRevision`（A・C・正解・法令基準日のハッシュ）は「どの版に対する回答か」の記録として残すが、集計では見ない。**論点そのものが別物になるなら、同じIDを使い回さず新しいカードIDにする。** 同じIDのままなら、その論点の履歴として数え続ける。

## 5. UI・文章の確定方針

- 解説図は「図で整理」タブと、カードの `figures`（⑧）の両方に置ける。2026-07-30に「図で整理タブだけ」の制限を外した。ただし**回答後の解説の中だけ**に出す。A・B1・B2・Cは回答ボタンより上にあるので、そこへ図を出すと答えが割れる。
- `figures` は1枚のカードに**2枚まで**。画像は `static/assets/card-figures/` の下だけに置き、`src` はそこへの相対パスにする。bundle側と画面側の両方でこの形を確かめている。ここを緩めない。`alt` と `caption` は必須で、装飾記法は使わない。
- `placement` で出す位置を選ぶ。`correction`（既定）・`normal`・`deepDiveBackground`・`deepDiveTrap`・`deepDiveExample` の5つで、その説明の直後に出る。**その図がいちばん効く説明の直後**を選ぶ。深掘りの3列のうち図が入った枠は横いっぱいに広がる。
- 同じ図を複数のカードへ重複して置かない。⑦と同じ扱いで、関連するカードからは⑥の `relatedCardId` で結ぶ。
- 図は本文の置き換えではなく理解の補助にする。図があるからといって `correction` や深掘りを削らない。図の中の記述も条文・判例で裏を取る。**間違った図は本文の誤りより害が大きい**（一目で覚えてしまう）。
- **B・CはAと同じ結論の文にする。** A・B1・B2・Cは4つとも回答ボタンより上に表示されるため、B・CがAの逆や正解そのものを書くと○×が割れる。Aが誤りの命題なら、B・Cも同じ誤りの命題を平易にしただけの文にする。断定型で書き、「この命題は〜と言っています」という引用の型は使わない。正解は回答後に出る `correction`・`explanations`・`memoryPoint` へ書く。
- Bは必ず2案を持つ。
  - 1案目: 日常の場面から説明する、やさしい言い換え。
  - 2案目: 問題に応じて「用語からほどく」「条件を並べる」「時間の流れで追う」などを選ぶ。
- Bは、頭が回っていない時でも一度で場面を想像できる文章にする。専門用語を専門用語のまま説明しない。最短要約はCの役割。
- 改行は意味の切れ目だけに使う。1行ごとの過剰な改行や、長い一段落を避ける。
- ⑤には平成28年度以降の実際の過去問肢を原文のまま表示し、本番での聞かれ方を確認できるようにする。取得元が法改正に合わせて文言を直した改題版だけは例外で、`isModified: true` を立てて画面に「法改正に合わせた改題版」と出す。改題扱いにする前に、公式PDFの原文と本当に違うのかを機械照合で確かめる。
- ⑥「似た制度・他分野との違い」は、本当に混同しやすい比較があるカードだけに付ける。データがなければ見出しごと表示しない。
- ⑦「法律ごとの結論くらべ」（`comparisonTable`）は、同じ場面で結論が分かれる論点だけに付ける。行政手続法・行政不服審査法・行政事件訴訟法の三法比較に加え、民法と商法、民法と国家賠償法のように**科目をまたぐ比較**にも使う。2〜4行で、`memoryCue` には並びだけでなく**なぜそう分かれるのか**を書く。⑦の本文には装飾記法を使わない。データがなければ見出しごと表示しない。同じ表を複数のカードへ重複して置かない。
- 深掘り解説は開閉式に戻さず、回答後に普通に表示する。

詳細なカード作成・頻出度ルールは `HANDOFF.md` の「問題追加の正本ルール」を参照する。

## 6. 問題・バンドルを変更する時

- 問題生成コードは`authoring/`、取得スナップショット・抽出結果・⑤の出典・監査データは非公開編集データ配下を正本とする。学習カードの正本だけは `content/explanation_cards.json`（Git管理）である。`gyousei-production-bundle` の `--explanation-cards` の既定はこのパスで、環境変数 `GYOUSEI_CARD_SOURCE` で上書きできる。既定を非公開領域へ戻さない。
- 合格道場の平成18〜令和7年度・全分野1,139問は、非公開編集データの`all_subjects/`にある。直近10年569問は解説558問を含み、旧10年570問は問題と当時の答えだけで解説はない。`all_subjects/README.md`と各期間の`reports/validation.json`を先に確認する。
- `all_subjects/`は次科目の編集資料であり、現行の行政法bundleへ自動投入しない。科目別に正本ルールへ昇格させ、B二案・C・深掘り・常識力は独自に作る。
- 全分野候補を作る時、没問・正解なし・組合せ・個数・多肢・記述を正解番号だけで自動○×化しない。`gyousei-dataset-inventory` のsafe候補数と除外理由を確認する。
- 令和2〜7年度の公式全科目コーパスは`authoring/reference/official-r2-r7/`に隔離されている。論点候補の探索だけに使い、出題当時の正誤を2026年基準の正誤として公開しない。
- リリース済みbundleを手作業で直接つぎはぎしない。候補データ、監査データ、公開bundleを分ける。
- bundleの収録を減らす変更は、`gyousei-production-bundle` が自分で止める。`--compare-to`（既定は稼働中の `gyousei-production.json`）と比べて過去問・カード・⑤の肢・過去問の科目別件数・形式別件数・**カードの科目別件数**のどれかが減っていれば、`--allow-shrink` を付けない限り失敗する。カードを意図的に取り下げる時だけ `--allow-shrink` を付け、何を減らしたかを完了報告に書く。
- 件数のfail closed検証（`--expected-card-count` など）は「指定した数と一致するか」しか見ないため、科目がまるごと落ちる縮小は検出できない。上の収録減チェックと `--question-manifest`（既定 `config/all_subjects_current_target.json`）の2つが、その担保になっている。既定値を行政法だけのパスへ戻さない。カードは全科目が1デッキに同居しているため、総数だけを見ると**ある科目が丸ごと落ちても別の科目の増加で相殺されて気づけない**。`summary.explanationCardSubjectCounts` による科目別の比較を外さない。
- 新しい科目・問題・カードには安定した `subjectId` を付ける。画面の件数を行政法の現件数で固定しない。
- 既存bundleを置換する時は、schema、全参照、件数、内部情報漏洩を検証し、権限 `0600` でatomicに置き換える。
- 合格道場の正答・解説を編集上の第一基準にするが、解説本文をそのまま転載しない。B、C、深掘り、常識力は独自文にする。
- 令和8年度試験向けの法令基準日は `2026-04-01`。取得日や今日の日付と混同しない。

## 7. 編集と検証

- 既存の書き方と構造を確認してから、必要最小限の変更を行う。
- 無関係なリファクタリングや、利用者の既存変更の巻戻しをしない。
- `static/` はnginxが直接配信しているため、UIファイルの変更は保存直後から本番へ反映される。試作コードを置かない。
- JavaScriptやCSSを変更したら、`APP_VERSION` と `index.html` のクエリ版を同時に更新し、古いキャッシュを避ける。
- 全体構成、主要画面、データ正本、分析方式、ロードマップを変更したら、`ARCHITECTURE.md` と `static/architecture.html` を同じ作業で更新する。件数はHTMLへ手入力せず、集計APIから表示する。
- UIのAPI基準は相対URL `api` であり、末尾スラッシュ付きの `/services/gyousei-lab/` とnginxの転送規則が前提。理由なく `/api` の絶対URLへ変えない。
- UI変更ではPC幅とスマホ幅を実ブラウザで確認する。
- 回答POSTを含むブラウザテストは、本番SQLiteへ向けない。別ポート・一時DBで行う。
- `server.py` 変更後は、テスト成功後にlaunchdを再起動する。静的ファイルだけの変更ではAPI再起動は不要。
- nginx設定を変更していないのにnginxを再起動しない。設定変更時は先に `nginx -t` を通す。
- nginxの設定は `~/dev/yuki-services/deploy/macos/nginx/yuki-services.locations.conf` を正本にし、実設定だけを直接編集しない。

基本確認:

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 -m unittest discover -s tests -v
/opt/homebrew/bin/node --check static/app.js
curl -fsS http://127.0.0.1:8817/health
curl -fsS http://192.168.10.102:8080/services/gyousei-lab/health
```

問題生成コードの確認:

```bash
cd ~/dev/yuki-services/apps/gyousei-lab/authoring
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/uv run \
  python -m unittest discover -s tests -v
```

全分野データ件数の再生成:

```bash
cd ~/dev/yuki-services/apps/gyousei-lab/authoring
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
/opt/homebrew/bin/uv run gyousei-dataset-inventory
install -m 0600 \
  "$GYOUSEI_DATA_ROOT/all_subjects/data_inventory.json" \
  "$HOME/.local/share/yuki-services/gyousei-lab/all-subject-inventory.json"
```

`all-subject-inventory.json` は問題文・取得元解説・内部パスを含まない集計専用コピー。rawやprovider解説をruntime直下へ代用コピーしない。

API再起動:

```bash
launchctl kickstart -k "gui/$(id -u)/com.yuki.gyousei-lab"
curl -fsS http://127.0.0.1:8817/health
```

launchd plistを変更する場合は、リポジトリ側
`~/dev/yuki-services/deploy/macos/launchd/com.yuki.gyousei-lab.plist`
とインストール済み
`~/Library/LaunchAgents/com.yuki.gyousei-lab.plist`
の両方を同期し、反映後にhashを比較する。

設定とUIの追加確認:

```bash
plutil -lint ~/dev/yuki-services/deploy/macos/launchd/com.yuki.gyousei-lab.plist
/opt/homebrew/bin/nginx -t
diff -u \
  ~/dev/yuki-services/deploy/macos/nginx/yuki-services.locations.conf \
  /opt/homebrew/etc/nginx/locations/yuki-services.conf
~/dev/yuki-services/scripts/chrome-screenshot-mac.sh \
  http://192.168.10.102:8080/services/gyousei-lab/ \
  /tmp/gyousei-lab.png
```

## 8. SQLiteを扱う時

- 稼働中DBを単純な `cp` でバックアップしない。SQLite backup APIまたは `.backup` を使う。
- Mac外バックアップは `scripts/backup-private-data.sh` が正本。GitHubにはソースしか入っていないため、
  カード正本・頻出度監査・回答履歴はここでしか守られていない。共有未マウント時の中止をやめる、
  世代削除を既定で有効にする、といった緩和をしない。暗号化しない方針は利用者の判断なので、
  勝手に暗号化を戻さない。代わりに、この控えをWeb公開領域や外部ストレージへ置かない。
  詳細は `HANDOFF.md` の3.1。
- schema変更前に整合性確認とバックアップを行い、失敗時の戻し方を明示する。
- `production.sqlite3-wal` と `production.sqlite3-shm` を独立した正本として扱わない。
- 検証後に `PRAGMA quick_check` を実行する。
- 回答件数は利用により増えるため、`HANDOFF.md` の件数を固定期待値としてテストしない。

## 9. AI間の連絡

- 別環境のAIへ設計確認、レビュー、作業指示を渡す時は、チャットだけで終わらせず、このMac側プロジェクト内へMarkdownファイルとして保存する。
- 依頼が `*_REQUEST.md` なら、回答は原則として同じディレクトリの `*_RESPONSE.md` にする。用途が異なる場合も、ファイル名だけで送信元、目的、状態が分かる名前にする。
- 別端末（Windows等）からブラウザで受け取る依頼文は `static/docs/` へ置き、`static/docs/index.html` の一覧へ追記する。nginxが `static/` を直接配信するため、`http://192.168.10.102:8080/services/gyousei-lab/docs/` からダウンロードできる。この置き場所に秘密情報を含むファイルを置かない。
- 連絡文には、更新日、対象依頼、結論、根拠、次に行うこと、変更してはいけないものを書く。
- APIキー、Cookie、provider解説全文、個人情報などの秘密情報を連絡文へ含めない。
- 連絡MarkdownはAI間の受け渡し資料であり、恒久的な設計の正本ではない。採用した判断は `AGENTS.md`、`HANDOFF.md`、`ARCHITECTURE.md` など適切な正本へ反映する。
- ユーザーが別AIへ渡す内容を求めた場合は、回答本文だけで済ませず、Mac側に設置したファイルの絶対パスも報告する。

## 10. 完了報告

- 何を変更したか。
- どの検証を実行し、結果がどうだったか。
- APIやnginxを再起動したか。
- 本番データと回答履歴を変更したか。
- 未解決事項や、次の担当者が判断すべき点。

を日本語で簡潔に報告する。
