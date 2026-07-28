# セッション引き継ぎ 2026-07-28

作成日: 2026-07-28 JST
対象: 次のセッションのClaude Code（Mac）
先に読む: `AGENTS.md` → このファイル → `ARCHITECTURE.md`（とくに「1.5 得点計画」と「12. 次に行う作業」）

---

## 0. 最初にすること

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
git log --oneline -8
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 -m unittest discover -s tests
curl -fsS http://127.0.0.1:8817/health
```

`health` の `bundle.revision` が
`80c420601b1af772b29ce92916843253a6da3523d2e929f20af782bba73a312e`
なら、2026-07-28終了時点の状態のままである。

---

## 1. いまの状態

本番URL: `http://192.168.10.102:8080/services/gyousei-lab/`

| 項目 | 数 |
|---|---:|
| 過去問（直近10年・全6科目） | 569問 |
| ○×学習カード | 98件 |
| ─ 行政法 | 55 |
| ─ 民法 | 25 |
| ─ 基礎知識 | 18 |
| ⑤の関連過去問肢（重複なし） | 334件 |
| ⑤の延べ表示 | 391件 |
| ⑥のあるカード | 38件 |

- `APP_VERSION` は `20260728-1`。`static/index.html` のクエリ版も同じ。
- 法令基準日は `2026-04-01`（令和8年度試験向け）。取得日・今日の日付と混同しない。
- deck IDは `administrative-law-frequent-10` のまま。**全科目が同じ1デッキ**で、`subjectId` で分ける。

---

## 2. 2026-07-28にやったこと

### 2-1. 基礎知識18枚を追加した

`subjectId: general-knowledge`、`category: 基礎知識`。

**個人情報保護（12枚、topic=個人情報保護、clusterId=general-knowledge-pip）**

| カードID | 論点 |
|---|---|
| `gk-pip-identifier-code-001` | 個人識別符号にあたるもの・あたらないもの |
| `gk-pip-sensitive-data-001` | 要配慮個人情報と諸外国の機微情報 |
| `gk-pip-improper-use-001` | 不適正な利用の禁止と利用目的の変更 |
| `gk-pip-third-party-exception-001` | 第三者提供で本人の同意が要らない場合 |
| `gk-pip-provision-record-001` | 第三者提供の記録義務 |
| `gk-pip-leak-report-001` | 漏えい等の報告と本人への通知 |
| `gk-pip-academic-exception-001` | 学術研究機関の例外は一律の適用除外ではない |
| `gk-pip-public-private-001` | 公的部門と民間部門の一元化 |
| `gk-pip-commission-power-001` | 個人情報保護委員会の組織と監督権限 |
| `gk-pip-commission-scope-001` | 委員会の所掌事務とマイナンバーカード |
| `gk-pip-local-government-001` | 地方公共団体に対する委員会の関わり方 |
| `gk-pip-mosaic-approach-001` | モザイク・アプローチ |

**諸法令＝行政書士法（6枚、topic=諸法令、clusterId=general-knowledge-gyosei-law）**

| カードID | 論点 |
|---|---|
| `gk-gyosei-law-qualification-001` | 行政書士となる資格（公務員経験20年／高卒等17年） |
| `gk-gyosei-law-disqualification-001` | 欠格事由（破産の復権・懲戒免職から3年） |
| `gk-gyosei-law-fee-display-001` | 報酬額の掲示義務 |
| `gk-gyosei-law-specified-appeal-001` | 特定行政書士と不服申立ての代理（⑥あり） |
| `gk-gyosei-law-discipline-001` | 懲戒処分の種類と処分権者 |
| `gk-gyosei-law-registration-erasure-001` | 業務禁止処分と登録の抹消 |

⑤に使った元問題は9問・40肢。

- 個人情報保護: 平成30年問57、令和元年問57、令和2年問57、令和4年問57、令和5年問57、令和6年問57、令和7年問57
- 行政書士法: 令和6年問52、令和7年問53

**採用しなかった問題と理由**

- 平成29年問57、令和2年問56、令和3年問57は、いずれも**旧・行政機関個人情報保護法**が前提。
  令和3年改正で条文が移動し、結論も一部変わるため、2026-04-01基準の肢として出せない。
- 平成30年問56は**没問**なので使わない。
- 令和7年問57の肢1・肢2（罰金の上限、課徴金）は、課徴金制度が検討段階にあり
  2026-04-01時点の施行状況を一次資料で確かめきれなかったため、⑤から外した。
  **次に一次資料（個人情報保護委員会・e-Gov）で確認して、必要なら追加すること。**

### 2-2. ⑤に `contextSummary` を表示できるようにした

平成30年問57の肢は「携帯電話番号」「指紋データ」のように**語句だけ**で、そのままでは何を問われたのか
分からない。bundleにもとからあった `contextSummary`（画面未使用だった）をレンダリングするようにした。

- `static/app.js`: ○×学習の⑤（`renderStudyRelated` 相当）と解説カードの⑤（`renderRelatedEvidence`）
- `static/styles.css`: `.study-related-context`
- 肢の原文は書き換えていない。文脈は肢データ側の別フィールド。

### 2-3. 得点計画を改訂した

`ARCHITECTURE.md`「1.5 得点計画」。根拠は `docs/PASS_STRATEGY_RESEARCH_WITH_RAMUNEGU.md`
（利用者がChatGPTに調べさせた合格者調査。ラムネグというブログが中心）。

改訂点は3つ。

1. 基礎知識を36点 → **40〜44点**（利用者は旧一般知識で56点満点の実績がある）
2. 記述を科目の総点へ埋め込まず、**別枠20〜24点**として数える
3. 総得点の本命を192点 → **196〜200点**

理由は、令和6・7年度の合格者9例で「記述抜き180点以上」が3人、「記述を足して初めて180点」が6人
だったこと。記述を科目に溶かすと、この差が見えなくなる。

### 2-4. ドキュメントを更新した

- `ARCHITECTURE.md`（版数、カード件数、得点計画、「確信のない170点」の節、次に行う作業、Decision log）
- `HANDOFF.md`（件数、bundle revision、冒頭の依頼文）
- `authoring/README.md`（件数、`--expected-card-count 101 --expected-evidence-count 344`）
- `authoring/CARD_AUTHORING.md`（`contextSummary`、改正で結論が変わった肢を⑤へ載せない、装飾の隣接ルールの訂正）
- `static/architecture.html`（版数、8月のロードマップ）

---

## 3. 次にやること（優先順位）

`ARCHITECTURE.md`「12. 次に行う作業」が正本。要約すると次のとおり。

### ① 自信度の記録（最優先。利用者にはまだ提案していない）

調査資料が最優先に挙げた機能。○×の正誤とは別に「理由まで言える／なんとなく／勘」を残し、
**表面得点と確信正解点を分けて表示する**。過去問を回すほど「理由を言えないまま正解できる」状態が
増えるので、早く入れるほど効く。

実装の勘所:

- `card_attempts` へ追記列を足す形にする。**既存行を書き換えない。**
  `answerRevision` の算出式（`server.py` の `card_answer_revision`）は**変えない**。変えると過去の回答が
  習得判定から落ちる。
- SQLite schemaは現在 `user_version=3`。上げる前に `PRAGMA quick_check` とバックアップ（`.backup`）を取る。
- 高速○×モードでは自信度を聞かない（速さが目的なので）。通常モードだけに出すのが自然。
- **着手前に利用者へ「入力の形（3段階か5段階か、キーボードで押せるか）」を確認すること。**

### ② 基礎知識の残り

- **戸籍法・住民基本台帳法**（諸法令枠は令和6年から毎年2問。令和6年に住基法、令和7年に戸籍法が出た）
- **情報通信・情報セキュリティ**（毎年2〜3問。用語問題が多い）
- 政治・経済・社会は範囲が広いので頻出テーマだけ
- 文章理解3問はカードに向かない（解き方の訓練なので過去問演習で扱う）

### ③ 科目横断⑥の整備

調査資料が挙げた比較対象:

許可／認可／特許、取消し／無効／撤回、行政不服申立期間／取消訴訟の出訴期間／民法の消滅時効、
無権代理／表見代理／日常家事代理、民法の表見代理／商法の表見支配人、債権者代位権／詐害行為取消権、
留置権／先取特権／抵当権、行政上の強制執行／即時強制／行政罰、
個人情報／個人データ／保有個人データ、審査請求／再調査の請求／再審査請求。

各比較は「主体・要件・効果・期間・第三者保護」の同じ列で書く。

### ④ 商法・会社法の頻出15枚

設立・株式・機関・商行為・商業使用人に絞る。2/5取れれば十分なので枚数を増やさない。

---

## 4. 作業手順のメモ（今回使ったもの）

### カードを追加するとき

1. 編集正本は `$GYOUSEI_DATA_ROOT/canonical/explanation_cards.json` と
   `related_question_source.json`。`GYOUSEI_DATA_ROOT` は
   `$HOME/.local/share/yuki-services/gyousei-lab/authoring`。
2. 肢の原文は**手で書き写さない**。`all_subjects/current_2016_2025/extracted/<年>/<rawId>.json` から
   機械的に取り出す。組合せ問題のア〜オは `questionText` を `(?=[アイウエオ]．)` で分割して取る。
3. 正誤の基準は `all_subjects/current_2016_2025/curation/provider_explanations.json`（合格道場の解説）。
   **解説本文は転載しない。** A・B二案・C・深掘り・常識力は独自文にする。
4. `crossFieldComparisons` には `id` / `comparedCategory` / `comparedTopic` / `title` / `explanation` /
   `memoryCue` が必須（`relatedCardId` は任意）。ここは今回1回引っかかった。
5. bundleを作る。件数は完全一致のfail closedなので、増えた分を必ず指定する。

```bash
cd ~/dev/yuki-services/apps/gyousei-lab/authoring
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/homebrew/bin/python3.12 -m gyousei_pipeline.production_bundle \
  --questions-dir "$GYOUSEI_DATA_ROOT/all_subjects/current_2016_2025/extracted" \
  --reconciliation "$GYOUSEI_DATA_ROOT/all_subjects/current_2016_2025/reports/answer-reconciliation-production.json" \
  --question-manifest config/all_subjects_current_target.json \
  --expected-card-count 98 --expected-evidence-count 334
```

> 2026-07-29追記。上の3つのパスは既定値になったので省いても同じ束ができる。あわせて、
> 稼働中bundleより収録が減るビルドは `--allow-shrink` を付けない限り失敗するようにした。
> カードを取り下げる時だけ付ける。

6. 本番へ置く前に、旧bundleを退避してからatomicに差し替える。`install` ではなく同一ディレクトリの
   一時ファイル＋`mv` を使う。権限は `0600`。

```bash
RUNTIME="$HOME/.local/share/yuki-services/gyousei-lab"
cp -p "$RUNTIME/gyousei-production.json" "$RUNTIME/gyousei-production.pre-<タグ>.json"
cp "$RUNTIME/authoring/builds/releases/gyousei-production.json" "$RUNTIME/.gyousei-production.json.tmp"
chmod 600 "$RUNTIME/.gyousei-production.json.tmp"
mv "$RUNTIME/.gyousei-production.json.tmp" "$RUNTIME/gyousei-production.json"
```

7. **API再起動は不要**。`server.py` はmtimeとサイズでbundleを読み直す。
   静的ファイルだけの変更でも再起動しない。`server.py` を変えたときだけ
   `launchctl kickstart -k "gui/$(id -u)/com.yuki.gyousei-lab"`。
8. 反映後に必ず確認する: 回答履歴が落ちていないこと。

```bash
curl -fsS "http://127.0.0.1:8817/api/card-progress" \
  | /opt/homebrew/bin/python3.12 -c "import json,sys; print(json.load(sys.stdin)['stats'])"
# staleRevisionAttempts が 0 であること
```

### 装飾を書くとき

- ルールの正本は `authoring/CARD_AUTHORING.md` の「7. 本文の文字装飾」。
- 色＝役割は固定。`%%だれ%%` `__条件・期間__` `**用語・制度**` `==結論==` `!!ひっかけ!!` `@@根拠@@`。
- **Aには `!!` を使わない**（答えが割れる）。
- 記法の入れ子は不可。**同じ**記法を続けて置かない。種類の違う記法が続くのは可
  （`==できる==@@（○○法5条）@@` は既存カードでも使っている）。
- 装飾は表示だけの変更で、回答revisionには影響しない（`server.py` の `strip_display_markup`）。

### ブラウザ確認

CDP経由の検証スクリプトが scratchpad に残っている（セッション終了で消える可能性あり）。
`websocket-client` は Homebrew の python3.12 に入っていない。`~/anaconda3/bin/python` を使う。
回答POSTは必ずスタブで遮断してから触る（本番SQLiteへ書かない）。

```bash
~/dev/yuki-services/scripts/chrome-screenshot-mac.sh \
  http://192.168.10.102:8080/services/gyousei-lab/ /tmp/gyousei-lab.png
```

---

## 5. 未解決・気になっていること

1. **令和7年問57の肢1・2（罰金の上限／課徴金）を⑤へ入れるか。** 課徴金制度の2026-04-01時点の
   施行状況を一次資料で確認していない。確認できたら `gk-pip-commission-power-001` あたりへ足す。
2. **解説の根拠が薄いカード3枚**（2026-07-27から持ち越し）。
   `gyo-act-defect-001`（判例番号なし）、`gyo-written-r7-decision-defect-001`（条文番号なし）、
   `gyo-aps-refusal-hearing-001`（条番号なし）。
3. **基礎知識カードの `frequency`** は、⑤に紐づけた問題数だけを数えた自前集計にしてある
   （`scope` は「平成28年度〜令和7年度」、`archiveOccurrences` は0）。
   行政法・民法のような独立再監査は通していない。信頼度が違う点に注意。
4. **`static/architecture.html` に得点計画の節がない。** ARCHITECTURE.md側だけにある。
   利用者はHTMLをブラウザで見るので、いずれ人間向けにも置いたほうがよい。
5. 民法は25枚で止まっている。調査資料はA/B/Cランクで論点を分ける案を出しているので、
   増やすときはそこを見る。

---

## 6. 触ってはいけないもの

- `production.sqlite3` の既存行。削除・初期化・別DBでの上書きをしない。バックアップは `.backup` で取る。
- カードID、問題ID、deck ID `administrative-law-frequent-10`。
- `card_answer_revision` の算出式。
- 公開projectionの迂回（内部パス、provider解説全文、prompt、ログ、archive内部IDをブラウザへ返さない）。
- `static/docs/` に秘密情報を置かない（nginxが直接配信している）。
- 親ディレクトリ `yuki-services` での `git init`。
