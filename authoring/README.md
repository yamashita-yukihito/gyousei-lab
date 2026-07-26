# 行政書士クイズ・データパイプライン

平成18年度（2006年度）から令和7年度（2025年度）までを扱う、本人用クイズの取得・変換コードです。稼働中のクイズは行政法ですが、合格道場の全分野20年分も、次の科目を作るための非公開編集資料として保存しています。

## 現在取得済みの行政法範囲

- 択一等: 問8～26（各年度19問）
- 多肢選択式＋行政法: 問42・43（各年度2問）
- 記述式＋行政法: 問44（各年度1問）
- 各年度22問、10年度合計220問

問題番号を決め打ちして取得せず、合格道場の年度ページに実在するリンクと科目ラベルから目録を作ります。上記件数は行政法の漏れを検知するために使います。アプリ全体の問題数は固定せず、各問題と各学習カードの`subjectId`で科目を分けます。

## データの流れ

```text
年度別リンク目録
  → 改変しない取得スナップショット
  → 形式別の抽出済みraw JSON
  → 公式問題・正答との照合
  → 人が確認した標準命題と関連肢
  → 検証済みWeb用JSON
```

実行時にJavaScriptで問題文を上書きする仕組みは作りません。AIが作った候補と、人が確認した決定データを分け、最後のビルドで一つの表示用JSONへ確定します。

## 保存場所

- コード: `~/dev/yuki-services/apps/gyousei-lab/authoring/`
- 非公開編集データ:
  `~/.local/share/yuki-services/gyousei-lab/authoring/`
- 実行用bundleと回答履歴:
  `~/.local/share/yuki-services/gyousei-lab/`

非公開編集データはnginxの公開対象外です。取得元HTMLや公式PDFを
`static/`へコピーしてはいけません。Web用bundleには、確認済みのクイズ文章と、
表示が必要な出典情報だけを出します。

## 合格道場を基準にする編集方針

- 合格道場の正答と解説を、過去問の正誤および普通の解説を編集する際の第一の基準にします。
- 全肢をAIだけで2026年4月1日時点の法令・判例と再照合する工程は、公開の前提にしません。
- 保存済み解説は非公開の編集資料として構造化しますが、解説本文をクイズへ丸ごとコピーしません。普通の解説は、結論と理由を保った短い独自の言い直しにします。
- 解説の欠落、ページ内の結論矛盾、試験基準日後の改正を明示する注記がある場合だけ、個別の確認対象にします。
- B二案、C、深掘り、常識力は、合格道場にはない学習補助として独自に作成します。
- 公式問題原文のWeb表示許諾は未確認のため、全量rawは非公開のまま扱います。

設定は[config/target.json](config/target.json)にまとめています。

## 全分野20年分の取得結果（2026年7月26日）

既存の行政法専用目録と本番bundleを変更せず、全分野を別データセットとして追加しました。

- 平成28～令和7年度: 569問
  - 解説あり558問
  - 年度ページ内でアーカイブ扱いとなり、解説公開終了11問
- 平成18～27年度: 570問
  - 問題と当時の答えを保存
  - 解説提供なし570問
- 合計: 20年度・1,139問
- 全1,139問が`parsed`、抽出警告0、検証エラー0

設定とコマンドは次のファイルを使います。

- `config/all_subjects_current_target.json`
- `config/all_subjects_archive_target.json`
- `gyousei_pipeline.discover_all_subjects`
- `gyousei_pipeline.validate_all_subjects`

Macでの保存先は
`~/.local/share/yuki-services/gyousei-lab/authoring/all_subjects/`です。
元HTMLと解説は非公開のまま扱い、本番bundleには自動で入りません。

本文を公開せず件数だけを画面に出す集計:

```bash
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
/opt/homebrew/bin/uv run gyousei-dataset-inventory
install -m 0600 \
  "$GYOUSEI_DATA_ROOT/all_subjects/data_inventory.json" \
  "$HOME/.local/share/yuki-services/gyousei-lab/all-subject-inventory.json"
```

この集計は「問題単位」「通常5肢の原肢」「safe ○×候補」「多肢選択の語群・
空欄」「記述」を区別する。没問はsafe候補へ分解しない。生成物には問題文、
取得元解説、URL、内部ID、ローカルパスを含めない。

## 2026年7月18日の取得結果

- 合格道場の実在リンクから行政法220問を取得済み
- 220問すべての保存HTMLに解説本文があり、各肢等の解説区分は合計987件
- 内訳は、通常択一190問、多肢選択式20問、記述式10問
- 各年度とも19問＋2問＋1問＝22問で、平成28年度から令和7年度まで欠落なし
- 抽出結果は220問すべて`parsed`、検証エラー0、警告0
- 通常択一はリスト型176問と組合せ表型14問を分けて保存
- 公式資料は平成30年度から令和7年度まで取得済み
- その8年度では、択一と多肢選択式の正答168件が公式表示と完全一致
- 記述式8件は模範解答の言い回しが異なるため、自動で同一とせず人手確認へ送る
- 平成28・29年度は現在の公式サイトで問題・正答を取得できないため、公式照合は`unavailable`として記録

主な生成物:

- `catalog/questions.json`
- `extracted/`
- `reports/validation.json`
- `reports/corpus.json`
- `reports/answer-reconciliation.json`
- `curation/review_candidates.json`
- `curation/similarity_candidates.json`

## 平成18～27年度の頻度判定専用データ

合格道場の過去問アーカイブから、平成18～27年度も各年22問、計220問を取得済みです。通常択一190問、多肢選択20問、記述10問のすべてが警告なしで解析できています。

- 保存先: `archive_frequency/`
- 頻度判定用corpus: `archive_frequency/corpus.json`（`0600`）
- カード別の判定: `curation/card_frequency_2006_2025.json`（`0600`、全55件を独立再監査済み）
- 用途: 同じ論点が過去に出たかを問題単位で数えることだけ
- 非公開: 旧年度の問題文・当時の答え・URL・内部IDはWeb用bundleへ出さない

同一問題内に関連肢が複数あっても1回と数えます。単に同じ分野というだけの問題は頻度へ入れず、同一命題、逆向き、条件、例外、直接比較に絞ります。平成28年度以降の実際の肢は⑤へ表示しますが、平成18～27年度の文言は表示しません。

一次判定後に全カードを別視点で再監査しています。最初の35件では10件を維持、25件を修正し、追加20件では14件を維持、6件を修正しました。`official-*`と`goukakudojyo:*`が同じ年度・同じ問題を指す場合や、同じ問題内の複数肢は1回に統合しています。現在は最頻出5件、頻出28件、繰り返し出題16件、重要論点6件です。⑤には平成28年度以降の実際の肢を延べ263件、重複を除く211件掲載し、組合せ・記述式の元問題は表示用の肢にせず頻度だけへ反映します。

## 学習カードと将来の科目追加

- Bは、やさしい説明と、問題に応じて条件・時間・用語などからほどく説明の2案です。
- ⑤は平成28年度以降の実際の肢を表示し、本番での聞かれ方を確認できるようにします。
- ⑥は、不服審査法と行政事件訴訟法のように混同しやすい別制度があるカードだけ表示します。現在55件中30件に設定済みです。
- 解説図6枚はカード内へ挿入せず、独立した「図で整理」ページにまとめます。
- 回答履歴を保つためデッキIDは維持し、今後の憲法・民法なども同じデッキへ`subjectId`付きで追加します。

カード作成の正本ルールは[CARD_AUTHORING.md](CARD_AUTHORING.md)です。

大量の候補から⑤と⑥を探す補助として、`learning_index`が同一科目・同一論点・他制度を分けた候補索引を作ります。これは自動確定ではなく、文字n-gramと重要法令語で候補を絞るための非公開ツールです。

`learning_index`は頻出度の正本ではありません。候補の
`frequencyEligible`は未指定なら`false`とし、明示的に許可された候補だけを
旧来の参考集計へ入れます。正式な頻出回数はreview済みのカード×原問監査データを
正本とします。

```bash
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.learning_index \
  --cards "$GYOUSEI_DATA_ROOT/canonical/explanation_cards.json" \
  --review-candidates "$GYOUSEI_DATA_ROOT/curation/review_candidates.json" \
  --output "$GYOUSEI_DATA_ROOT/curation/learning_index.json"
```

`review_candidates.json`はまだ公開用データではありません。単一選択で正誤を安全に決められる655肢と、組合せ・多肢選択・記述式を原形のまま確認する89問を、すべて`reviewed=false`、`publishable=false`で入れています。

`similarity_candidates.json`では、従来の厳密類似9組・7グループ（16肢）を残したまま、文字n-gramと重要法令語による高再現率のレビュー候補を追加しています。現在は588組・363肢（655肢の55.42%）です。同一問題内を除外し、行政法サブラベルの一致と各肢top 4で候補数を制限しています。これは頻出論点を確定した結果ではなく、人が関連性を確認するための候補です。

## 解説から作る非公開○×候補

正解番号だけでは肢ごとの真偽を決められない組合せ・個数問題について、
保存済みの合格道場解説と元問題の正解を厳格に再結合して候補を作ります。

- `gyousei-explanation-ox`: 単一ラベルと明示的な正誤見出しから632肢を生成
- `gyousei-mapping-ox`: 監査済みallowlistから41肢を生成
- `gyousei-explanation-frequency`: 673肢の原問を既存のカード別頻出監査へ照合
- 合計673肢・140原問
- 全候補を`reviewed=false`、`publishable=false`、
  `frequencyEligible=false`で生成
- provider解説本文、監査ルール、sidecarは非公開編集データだけに保存

実問題を含むルールの正本:

```text
$GYOUSEI_DATA_ROOT/all_subjects/current_2016_2025/curation/explanation_mapping_ox_rules.json
```

生成:

```bash
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
/opt/homebrew/bin/uv run gyousei-explanation-ox \
  --expected-candidate-count 632 \
  --expected-corroboration-count 1534 \
  --expected-target-crosscheck-count 132
/opt/homebrew/bin/uv run gyousei-mapping-ox
/opt/homebrew/bin/uv run gyousei-explanation-frequency
```

2つのsidecarは既存`review_candidates.json`を置換しません。
`learning_index`の検索・編集候補には追加できますが、本番bundleへの昇格は
別工程です。

`frequencyEligible`は候補全体に効くため、カードごとに同じ原問を数えるかどうかを
表現できません。そのため673肢は`false`を維持し、頻出回数の正本である
`card_frequency_2006_2025.json`との照合結果を
`explanation_ox_frequency_crosswalk.json`へ保存します。行政法440問・55カードの
独立再監査が完了しているので、行政法の原問は「現行カードに数える／数えない」を
この照合表で確定できます。行政法以外はまだ対応カードがないため、現時点では
`no_current_subject_cards`です。

## AI法令監査の安全境界

令和8年度試験は2026年4月1日現在施行の法令から出題されるため、監査実行日と試験基準日を分離しています。現在のreview schema v3は`ai-legal-review-manifest@3`、`ai-legal-review-batch@3`、`ai-legal-review-response@3`、`ai-legal-review-import@3`です。全655肢を、`targetLegalAsOf=2026-04-01`をIDに含む10肢単位の66バッチへ作り直しました。応答は`targetLawStatus`と`targetTruth`を返し、`legalAsOf`がバッチの基準日と異なればimportできません。

Fableは`claude -p --model fable`で実行し、safe mode、セッション非保存、`WebSearch`と`WebFetch`だけの許可、一次資料限定、JSON Schema検証、実行予算・時間上限、非公開先への追記専用保存を必須にしています。応答と実行ログも別ファイルです。

旧schema v2では2026年7月18日時点の2バッチ・20肢をAI候補として取り込みましたが、これは令和8年度試験の4月1日基準の承認には使いません。旧バッチは監査証跡としてarchiveへ移し、v3バッチを正本にしました。次のClaude実行はレート制限（429）を検知したため停止し、不完全な回答データは保存していません。別工程で優先20肢を4月1日時点の一次資料と照合し、結論差がないことまでは確認しましたが、これも人手確認済みへの昇格ではありません。

AI結果は常に`ai_candidate`であり、AI自身が`human_verified`、`reviewed=true`、`publishable=true`へ昇格させることはできません。人が一次資料と内容を確認するまでWeb用データへ流しません。

## 実行方法

プロジェクト直下で、次の順に実行します。

```bash
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.discover
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.fetch
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.extract
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.validate
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.report
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.candidates
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.reconcile
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.similarity
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.provider_explanations
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.review_batches --legal-as-of 2026-04-01
```

## 非公開production bundle

直近10年の全6科目569問、正答照合、本人用の行政法・頻出論点55問デッキ、学習カード55件、カードから参照する過去問肢211件、旧Claude Fable成功応答20肢と全6件のrun概要、類似候補588組を、本人用API向けの単一JSONへまとめます。run概要には成功2件だけでなく、CLI失敗2件、無効応答1件、rate limit 1件も含めます。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.production_bundle \
  --questions-dir "$GYOUSEI_DATA_ROOT/all_subjects/current_2016_2025/extracted" \
  --reconciliation "$GYOUSEI_DATA_ROOT/all_subjects/current_2016_2025/reports/answer-reconciliation-production.json" \
  --question-manifest config/all_subjects_current_target.json
```

既定の出力は
`$GYOUSEI_DATA_ROOT/builds/releases/gyousei-production.json`です。
編集正本は`$GYOUSEI_DATA_ROOT/canonical/`から読みます。出力先は非公開データ
配下の`builds/`に限定し、ファイルをatomicに置き換えて権限を`0600`にします。
過去問の総数・科目別件数・形式別件数・年度、行政法の年度別問題番号、
正答照合との対応、旧Fable応答と成功runのdigestが一つでも合わなければ
書き込みません。

bundleはホワイトリスト方式で作り、合格道場の解説、Claudeへのprompt・stdout、絶対パスを含めません。`reviewed`と`publishable`は入力値をそのまま保持し、ビルド時に承認済みへ変更しません。`similarityPairs[].pairContentDigest`は、pair IDと左右の出題表示内容から算出します。

主な配列は次のとおりです。

- `questions`: 3形式の問題本文・選択肢・正答。表形式14問の`choiceColumns`と`choices[].cells`も保持
- `officialAnswerChecks`: 569問の正答照合結果。行政法は公式資料と照合し、他科目は未照合状態を明示
- `studyDecks`: 本人用の頻出論点55問デッキ1件（`visibility=private`、2026年4月1日施行法令基準）
- `explanationCards`: B二案、C、深掘り、常識力、必要な⑥を加えた学習カード55件
- `relatedQuestionEvidence`: カードの`relatedPastQuestions[].choiceId`から参照できる平成28年度以降の過去問肢211件
- `claudeReviews` / `claudeRuns`: 旧schema v2のAI候補20肢と、秘密情報を除いた全run概要（状態、時刻、model、`errorKind`のみ）
- `similarityPairs`: 人が確認する類似候補588組

公式資料の取得は年度を明示して行います。平成28・29年度は現在404になるため、再試行して取得済みデータを上書きする必要はありません。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m gyousei_pipeline.official \
  --year 2018 --year 2019 --year 2020 --year 2021 \
  --year 2022 --year 2023 --year 2024 --year 2025
```
