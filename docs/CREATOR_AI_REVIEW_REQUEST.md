# 行政書士過去問ラボ — 作成AIへの設計確認

更新日: 2026-07-26 JST

この文書の目的は、行政書士過去問ラボを作ったAIに、現在の実装と今後の開発方針を
確認してもらうことです。今回はコードを変更せず、末尾の質問へ回答してください。

## 先に読むもの

```text
/Users/yuki/dev/yuki-services/apps/gyousei-lab/AGENTS.md
/Users/yuki/dev/yuki-services/apps/gyousei-lab/HANDOFF.md
/Users/yuki/dev/yuki-services/apps/gyousei-lab/ARCHITECTURE.md
/Users/yuki/dev/yuki-services/apps/gyousei-lab/authoring/README.md
```

頻出度に関係する実装:

```text
/Users/yuki/dev/yuki-services/apps/gyousei-lab/authoring/src/gyousei_pipeline/learning_index.py
/Users/yuki/dev/yuki-services/apps/gyousei-lab/authoring/src/gyousei_pipeline/explanation_frequency.py
```

非公開編集データ:

```text
/Users/yuki/.local/share/yuki-services/gyousei-lab/authoring/curation/card_frequency_2006_2025.json
/Users/yuki/.local/share/yuki-services/gyousei-lab/authoring/all_subjects/current_2016_2025/curation/explanation_ox_frequency_crosswalk.json
```

## 今回実施したこと

別AIが生成した、解説由来の○×候補をauthoringへ統合した。

- 第1段階: 632肢
- 第2段階: 41肢
- 合計: 673肢・140原問
- 行政法: 207肢・46原問
- 全候補: `reviewed=false`、`publishable=false`
- 本番bundleとSQLite、回答履歴は変更していない

`frequencyEligible`は候補単体へ作用するグローバルなフラグだが、実際の頻出判定は
「どの学習カードに、どの原問を数えるか」というカード×原問の関係である。
候補全体を`true`にした試算では、55カードすべての頻出回数が変わり、
合計784回の過剰加算になったため、その方法は採用しなかった。

行政法については、平成18年度〜令和7年度の440問・55カードを対象とする
`card_frequency_2006_2025.json`が`independent_recheck_complete`になっている。
そこで、673候補の原問をこの監査済みデータへ照合する処理を実装した。

照合結果:

- 行政法46原問のうち、21原問は現行カードの頻出根拠
- 25原問は、現行55カードのどれにも算入しない
- カード×原問の算入関係は40件
- 他科目94原問は、対応する現行カードがないため
  `no_current_subject_cards`
- 候補側の`frequencyEligible`は全673肢で`false`を維持
- 実質的な算入判定は
  `explanation_ox_frequency_crosswalk.json`へ原問単位で保存

検証結果:

- authoring: 150テスト成功
- アプリ/API: 23テスト成功
- JavaScript構文確認: 成功
- ローカル・LAN health: 正常
- 非公開生成物: `0600`

関連コミット:

```text
4a8e2d3 解説由来の○×候補生成を統合
a39647b 解説由来候補を頻出監査へ照合
```

## 現担当AIが考えている今後の開発順

1. **弱点分析MVP**
   - current revisionの回答だけを決定論的に集計する
   - SQLiteは更新せず、分析snapshotを別ファイルへ出す
   - 未学習、一時的な誤答、本当の苦手を分ける

2. **苦手`studyView`**
   - 既存カード、deck ID、回答履歴を共有する
   - 別アプリや別DBは作らない

3. **民法の優先20〜30論点**
   - 現在のデッキへ`subjectId`付きで追加する
   - 行政法と民法の混同ポイントを`learningRelations`として作る

4. **全分野頻出・分野横断**
   - 行政法と民法を横断する頻出view
   - その後、憲法、商法・会社法、基礎法学、基礎知識を追加する

5. **試験前の安定化**
   - 試験前3週間は新規大量追加を止める
   - 弱点回復、直近誤答、記述、全分野混合を中心にする

## 確認したいこと

### 1. 弱点分析へ入れる回答の範囲

カードの苦手判定は`card_attempts`だけで行う想定だったか。
それとも、過去問の`answer_attempts`も、明示的なカード×問題関係を通して
カードの弱点判定へ加える想定だったか。

推奨があれば、次を具体的に示してほしい。

- `card_attempts`と`answer_attempts`の役割分担
- 過去問回答をカードへ反映する場合の対応関係
- 組合せ・記述式など、単純な○×でない回答の扱い

### 2. 苦手判定の基準

次について、元の設計意図または推奨値があるか。

- 苦手と判定する最低回答回数
- 連続誤答と直近誤答の重み
- 古い誤答を時間経過で軽くするか
- 1回だけ誤答したカードを「要観察」として分離するか
- 習得済みへ戻す条件

元の意図がなければ、現担当AIは次を初期案とする。

- 1回の誤答だけでは苦手にしない
- current revisionの採点可能な回答だけを使う
- 最低2回以上の根拠を要求する
- 直近誤答と連続誤答を優先する
- 判定根拠を`reasonCodes`と数値でsnapshotへ残す

### 3. `learning_index.frequency`の位置付け

`learning_index`の`frequency`を、将来も頻出度の正本として使う想定だったか。
それとも、類似・関連候補を絞るための補助値として使う想定だったか。

現担当AIは次の理由から、頻出度の正本を
`card_frequency_2006_2025.json`のようなカード×原問の監査データへ一本化し、
`learning_index`を候補探索専用にすることを推奨している。

- `frequencyEligible`は候補単体のフラグで、カード別の算入可否を表現できない
- `same_topic`だけの候補を誤算入する危険がある
- 同じ年度・同じ原問の複数肢を1回へまとめる必要がある
- 現在の行政法には、全440問・55カードの独立再監査済みデータがある

`learning_index.frequency`を残すなら、監査済み関係と競合しない具体的な用途と
データフローを説明してほしい。

## 回答でほしいもの

- 上記3問への回答
- 現担当AIの開発順に問題がある場合、その理由と修正版
- この文書に書かれていない重要な設計意図
- 次の実装担当が絶対に壊してはいけない追加の互換条件
