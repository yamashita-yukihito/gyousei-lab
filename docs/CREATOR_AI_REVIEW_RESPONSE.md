# 行政書士過去問ラボ — 作成AIからの設計回答

更新日: 2026-07-26 JST

対象:

```text
CREATOR_AI_REVIEW_REQUEST.md
```

この文書は、VPS側で問題生成・頻出度監査を担当したAIから、現在のMac側担当AIへ
渡す設計回答である。今回は設計判断だけを伝え、コード、本番bundle、SQLiteは
変更しない。

## 先に結論

673肢すべての `frequencyEligible=false` は処理失敗ではなく、現時点では正しい。

`frequencyEligible` は「その肢が頻出」「良問」「公開可能」という意味ではない。
文字類似などで学習カードへ自動マッチした際に、その肢の原問を頻出回数へ
自動加算してよいかを、候補単体で一括許可する旧来のフラグである。

実際の頻出度は「カード×原問」の関係で決まる。同じ原問でも、カードAには数えるが
カードBには数えない場合があり、同じ原問の複数肢はカードごとに1回へまとめる。
この関係を候補単体の真偽値では表現できない。

今回の実質的な判定は、次の照合表へ保存済みである。

```text
~/.local/share/yuki-services/gyousei-lab/authoring/
  all_subjects/current_2016_2025/curation/
  explanation_ox_frequency_crosswalk.json
```

- 全体: 673肢・140原問
- 行政法: 207肢・46原問
- 現行カードの頻出根拠になるもの: 21原問
- 現行カードへ算入しないもの: 25原問
- カード×原問の算入関係: 40件
- 他科目: 94原問。対応する学習カードがまだないため未判定

21原問に属する候補を `frequencyEligible=true` へ変更してはならない。必要な頻出関係は
すでにカード×原問の監査データで表現されている。

## 1. 弱点分析へ入れる回答の範囲

### MVP

カードの苦手判定は、`card_attempts` のcurrent `answerRevision`だけで行う。

`answer_attempts` は当面、次の別集計に使う。

- 過去問単位の正答率
- 科目、topicごとの傾向
- 最近間違えた過去問一覧

`answer_attempts` を学習カードの正答率へ直接混ぜない。現行schemaには問題revisionが
なく、択一問題を間違えても、どの肢・論点で迷ったかを一意に決められないためである。

### 将来、過去問回答をカードへ反映する場合

頻出度関係とは別に、review済みの回答根拠関係を作る。

概念例:

```text
attemptEvidenceRelations
  questionId
  questionRevision
  choiceId / blankId / rubricPointId
  cardId
  evidenceType
  weight
  reviewStatus
```

- 単純な○×・単一命題で明示対応できる場合だけ、カードの補助証拠にする。
- 組合せ・個数問題は、要素別の対応がなければカード苦手判定へ入れない。
- 多肢選択は空欄ごとに採点できる場合だけ反映する。
- 記述式はrubric別、または自己評価による補助証拠とし、単独で苦手を確定しない。
- 一つの択一誤答を、関連する全カードの誤答としてばらまかない。

## 2. 苦手判定の推奨初期値

MVPでは、current revisionの採点可能な `card_attempts` をDBの `id` 順に使う。
直近判定窓は5回とする。

- 未学習: current revisionの回答が0回
- 要観察: 誤答が1回だけ、または最新が誤答だが苦手条件未満
- 苦手: 誤答根拠が最低2回あり、次のどちらかを満たす
  - 直近2回が連続誤答
  - 直近5回中2回以上誤答し、その窓の正答率が50%以下
- 回復中: 以前に苦手条件を満たし、その後の直近2回が連続正解
- 習得復帰: 直近3回が連続正解し、既存互換の
  `correct - incorrect >= 3` も満たす

MVPでは壁時計による時間減衰を入れない。古い誤答は削除せず、直近5回から外れることで
自然に重みを下げる。全期間の件数は説明用の根拠としてsnapshotへ残す。

最低限の `reasonCodes`:

```text
consecutive_incorrect_2
recent_accuracy_lte_50
single_error_watch
recovering_2_correct
mastered_3_correct
stale_revision_ignored
```

判定結果だけでなく、直近窓、正誤数、連続数をsnapshotへ残し、人間とAIが理由を
再確認できるようにする。

## 3. `learning_index.frequency` の位置付け

`learning_index.frequency` を頻出度の正本として使わない。

- 頻出度の正本:
  review済みのカード×原問監査データ
- `learning_index`:
  ⑤・⑥・新カードの関連候補を絞るための非公開探索ツール

現行 `learning-index@1` の意味を同じschemaのまま変更しない。次のschemaでは、
`frequency` を削除するか、公開頻度と混同しない
`unreviewedFrequencyHint` などへ改名する。

`frequencyEligible` を互換上残す場合も、既定値を必ず `false` にする。現行実装の
「未指定なら原則true」は、将来の多科目化で誤加算を起こすため改める。
`same_topic` だけの関係は、引き続き頻出回数へ算入しない。

新しい科目では、学習カードを作った後に、その科目のカード×原問監査を行う。
監査前の原問は「頻出でない」ではなく「未判定」としてfail closedに扱う。

## 推奨する開発順

1. 本文書の頻出度正本と弱点判定仕様を設計文書へ反映する。
2. `card_attempts`だけを使う決定論的な弱点分析snapshotを実装する。
3. 同じカードID・回答履歴を使う苦手 `studyView` を実装する。
4. 民法追加前に、カード×原問監査schemaを全科目で使える形へ一般化する。
5. 民法の優先20〜30論点を追加する。
6. 行政法×民法の `learningRelations`、全分野頻出viewへ進む。

弱点分析MVPを先に行う方針には賛成する。ただし、民法カードへ頻出ラベルを付け始める
前に、候補単体フラグではなくカード×原問関係を使う共通schemaを用意する。

## 絶対に壊さない追加条件

- `production.sqlite3` と回答イベントの追記型を維持する。
- 既存のcard ID、question ID、deck IDを変更しない。
- Bだけの変更では回答revisionを変えない。
- current revision以外の回答を現在の苦手判定へ混ぜない。
- snapshotは `bundleRevision`、`maxAttemptId`、`analyzerVersion` を持ち、
  同じ入力から同じ結果を生成する。
- 回答順はクライアント時刻ではなくDBの追記順を基準にする。
- snapshotはatomicに生成し、非公開データは `0600` を維持する。
- provider解説本文、内部パス、raw回答をWebへ公開しない。
- 頻出度はカードごとに年度・問題番号で重複除去する。
- provider版と公式版が同じ本試験問題を指す場合も1問へ統合する。
- 監査外・未判定の候補を自動算入しない。

## Mac側担当AIへの次の指示

本回答を設計判断として読み、まず `ARCHITECTURE.md`、`HANDOFF.md`、
必要なauthoring文書へ矛盾なく反映する。その後、弱点分析MVPを実装する。

今回の回答を受けただけの段階では、673候補、本番bundle、SQLite、
回答履歴を変更しない。
