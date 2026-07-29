# 行政書士 過去問ラボ 引き継ぎ

更新日: 2026-07-28 JST

この文書は、VPSで開発していた行政書士クイズをMacBookへ移した時点の設計・判断・現状を、次のCodexまたはClaude Codeへ引き継ぐためのものです。日々の強制ルールは `AGENTS.md` を正本とします。

## 0. 次のMac AIへ最初に依頼する内容

最初に、次の指示をそのまま実行する。

> 直近10年の全6科目569問は「過去問原文」と「記述式」へ掲載済みです。○×学習カードは151件で、**全6科目がそろっています**（行政法55・基礎知識26・民法25・憲法20・商法15・基礎法学10）。まず `AGENTS.md`、`HANDOFF.md`、`ARCHITECTURE.md`、`authoring/README.md`、`docs/SESSION_HANDOFF_20260728.md` を読んでください。
>
> 2026年7月29日に全6科目がそろい、⑦「法律ごとの結論くらべ」も15枚へ広げました。次は実際に解いて、手応えの薄い分野を厚くしていく段階です。**新しいカードを追加したら、必ず○×のバランスを確認してください。** 憲法20枚が最初すべて×になり、×と答え続ければ全問正解できる状態を作ってしまいました。判断基準は `authoring/CARD_AUTHORING.md` の「1.15」です。優先順位の根拠は `ARCHITECTURE.md` の「1.5 得点計画」と「12. 次に行う作業」です。
>
> 新しいカードを作るときは、その論点を直接問う肢が `all_subjects/` の1,139問の中に実在することを先に確かめてください。判断基準は `authoring/CARD_AUTHORING.md` の「1.1 出題された論点だけをカードにする」です。
>
> 既存の問題ID・カードID・deck ID・`production.sqlite3`の行を変更しないでください。このディレクトリはprivate Gitリポジトリとして管理し、親の `yuki-services` では `git init` しないでください。

2026年7月27日に、release manifestの厳密件数検証を加え、直近10年の全6科目569問を本番bundleへ掲載した。行政法220問は旧bundleと完全一致することを確認済み。同日、カード×原問監査schemaを`card-frequency-audit@3`（科目別scope）へ一般化したうえで、民法25論点の○×カードを追加した（起草と独立再監査は別AIで実施、pass 14・修正11・fail 0）。同日さらに、⑤へ組合せ・個数問題の肢も形式を明示して載せる方針へ変更し、⑤が空だった5件（民法4・行政法1）へ肢を補充して全80カードが⑤を持つ状態にした。あわせてカードIDを画面に表示し、本文の文字装飾を「色＝役割」の固定ルール（青＝だれ、緑下線＝条件・期間、太字＝用語、黄＝結論、赤＝ひっかけ、灰＝根拠）で全80カードへ適用した。Aには赤を使わない（答えが割れるため）。装飾は表示だけの変更なので回答revisionには影響させない。ルールの正本は `authoring/CARD_AUTHORING.md` の「7. 本文の文字装飾」。○×学習には科目・分野の絞り込みと併用できることば検索を追加し、カードIDや解説本文からカードを直接呼び出せるようにした。学習ビューには「高速○×」を追加した。Aだけを見て即答し、1問10秒を目安にタイマーとキーボード（←/F＝○、→/J＝×、スペース＝次へ）で進め、セット終了時に正答率・所要時間の中央値・10秒超の問題を出す。解答時間は以前から`card_attempts.response_ms`へ保存していたが表示していなかったため、`card-progress`で中央値を集計してカードごとに出すようにした（5分を超える計測は捨てる）。UI初回はoverview、学習カード、弱点分析projectionを読み、APIは各ページを最後まで取得する。詳細は `ARCHITECTURE.md` の「多科目化のスケーラビリティ基盤」を参照する。

2026年7月28日に、基礎知識18論点の○×カードを追加した（個人情報保護12・行政書士法6、`subjectId: general-knowledge`）。合格道場の正答・解説を編集基準にし、令和3年改正で内容が変わった旧行政機関個人情報保護法の肢は⑤から外して、令和4年度以降と定義が変わっていない平成30年度の個人識別符号だけを採用した。⑤に語句だけの肢（「携帯電話番号」など）が入るため、bundleの`contextSummary`を画面へ出し、何を問われた肢なのかが分かるようにした。同日、得点計画を形式別へ改訂した（基礎知識40〜44点、記述は別枠20〜24点、本命196〜200点）。根拠は `docs/PASS_STRATEGY_RESEARCH_WITH_RAMUNEGU.md`。この日の作業の続きは `docs/SESSION_HANDOFF_20260728.md` にまとめてある。

同日さらに、利用者の指摘でB・CがAの答えを割っていることが分かり、98枚を1枚ずつ点検した。A・B1・B2・Cは4つとも回答ボタンより上に表示されるのに、Aが誤りの命題のときB・Cが正しいルールのほうを書いているカードが44枚（行政法32・基礎知識12）あった。民法8枚は「この命題は〜と言っています」という引用型を使っているのが×のカードだけで、言い回しだけで答えが割れていた。合計52枚を断定型へ書き直した。あわせて、赤（`!!`）の意味を「ひっかけ」から「判定の分かれ目」へ変え（○のカードにも付くので赤の有無で答えが割れない）、条文番号のなかったcorrection 45枚へe-Gov法令APIで原文確認した条文・判例番号を入れ、Cから落ちていた場面を戻した。「1文に黄が2か所」29件はすべて対になった2つの結論だったため、カードではなくルールの側を直した。新たに⑦「法律ごとの結論くらべ」（`comparisonTable`）を追加し、行政手続法・行政不服審査法・行政事件訴訟法で同じ場面の結論が変わるものを表で並べられるようにした（現在2枚）。基礎知識・諸法令へ3枚追加して101枚にした。詳細は `docs/SESSION_HANDOFF_20260728B.md`。

2026年7月29日に、前日追加した`gk-juki-furigana-001`（住民票の記載事項に氏名の振り仮名が含まれる）を取り下げて100枚にした。氏名の振り仮名は令和7年5月26日施行で令和7年度試験の基準日より後にあたり、過去20年で一度も出題されていない。あわせて、住基法7条の号ずれを「ひっかけ」として書いていた説明も誤りだったため、`gk-juki-record-items-001`のcorrectionと深掘りから外し、実際に問われた「それらしいが法律に並んでいない項目」の見分け方へ書き直した。判断基準は `authoring/CARD_AUTHORING.md` の「1.1」へ正本化した。同日、各カードのカードID行に元問題へのリンクを追加した。`sourceRefs`へ`eraYear`・`questionNumber`・`providerUrl`（合格道場）・`officialQuestionUrl`（試験実施機関PDF。合格道場と同一URLのときは落とす）を公開projectionで載せ、100カードすべてから元問題へ飛べる。回答履歴には影響しない。

同日さらに、頻出度の表示バグを直した。画面が「20年の出題傾向」と決め打ちしていたため、**平成28〜令和7年度の10年分しか数えていない71枚（憲法・基礎知識・商法・基礎法学）が、20年分の集計のように見えていた**。`frequency.scope` から年数を出す `frequencySpanLabel()` を入れ、行政法・民法は「20年」、後から足した科目は「10年」と出るようにした。あわせて、**頻出度の根拠に信頼度の差がある**ことを`authoring/CARD_AUTHORING.md`の1.17へ明記した。行政法55・民法25の80枚は非公開の頻出度監査（20年660問・独立再監査済み）を通っているが、残り71枚は出題枠を数えて手で付けた数字である。

同日さらに、⑥「似た制度・他分野との違い」を44枚から61枚へ広げた。商法15・憲法20・基礎法学10・基礎知識26のうち⑥を持つのが1枚だけで、**⑦の説明で「相手側からは⑥のrelatedCardIdで結ぶ」と決めたのに結んでいなかった**ためである。科目をまたぐ関連付けは19件になった。主なものは、基礎法学の一般法・特別法↔商法1条2項、基礎法学の非訟事件↔憲法82条、憲法31条↔行政手続法の聴聞、憲法13条↔個人情報保護法の開示請求権、商法511条↔民法438条以下の絶対効・相対効、民法715条↔国家賠償法1条、基礎法学の法人分類↔会社法の定款・行政書士法人である。

同日さらに、⑦「法律ごとの結論くらべ」を2枚から15枚へ広げた。行政三法の比較6枚（争える期限、不作為の争い方、理由の提示、審理を仕切る人、被告適格、前置の要否）に加え、**科目をまたぐ比較7枚**を新設した。民法と商法（非顕名の代理、分割債務と連帯）、民法と国家賠償法（工作物責任と営造物責任、賠償責任者）、民法と行政行為（取消しと撤回）、民法と行政法（権利を主張できる期限）、会社法と基礎法学（法人設立への行政の関与）である。ルールの正本は`authoring/CARD_AUTHORING.md`の4.1と`AGENTS.md`§5へ反映した。科目をまたぐ⑦は片方のカードにだけ置き、相手側からは⑥の`relatedCardId`で結ぶ。⑦の追加では回答revisionは変わらない。

同日さらに、基礎法学10枚と基礎知識6枚を追加して151枚にし、**全6科目がそろった**。基礎法学は20年間、問1が空欄補充（法史・法思想）で対策しにくく、問2が法令用語・裁判制度・法令の効力で安定しているため、**問2枠へ集中**させた（法令の効力／一般法と特別法／法律要件・法律効果／立法事実／審級制度／控訴審・上告審の構造／簡易裁判所／裁判員制度の評決／訴訟手続の原則／法人の分類）。基礎知識は情報通信・情報セキュリティ6枚（マルウェアとファームウェア／ハルシネーション／EUのDSA・DMA／GDPR／デジタル庁／LGWAN・ガバメントクラウド）。基礎法学と基礎知識は裁判制度・法人・情報法制で重なるので、同じ`clusterId`でつないでいる。

同日さらに、憲法20枚を追加して135枚にした（人権11・統治9、`subjectId: constitutional-law`）。憲法は問3〜5が人権（判例中心）、問6〜7が統治（条文中心）で20年間固定されている。得点計画では憲法・択一の目標が4/5（16点）と高いので、商法と違って作り込む価値がある。統治の条文はe-Gov法令APIで原文確認した。**このとき20枚すべてが×になっており、×と答え続ければ全問正解できる状態を作った。** 10枚のAを実際の正しい肢へ差し替えて○×を10対10にし、あわせて商法（○3/15＝20%）も3枚を○へ振り替えて6/15にした。科目を追加したら○×のバランスを必ず確認する。

同日さらに、商法・会社法の厳選15枚を追加して115枚にした（`subjectId: commercial-law`）。20年分99問を数えたところ、**問36が商法総則・商行為、問37が株式会社の設立**でほぼ固定されているため、この2枠だけへ集中させた（商法10枚・設立5枚）。問38〜40（株式・機関・計算・組織再編）はカード化しない。得点計画の商法4/20点は「1問確実に取る」という意味なので、範囲を広げるより2枠を厚くするほうが効く。⑤の肢は、書き写しでずれないよう`extracted`の原文から機械生成した（新規原問16件・肢62本）。条文は商法・会社法ともe-Gov法令APIで原文確認済み。

同日さらに、自信度の記録と卒業を実装した。追記型の`card_marks`表を足してschemaを`user_version=4`へ上げ（`card_attempts`の行は一切触っていない。移行前に`backups/production.pre-marks-20260729.sqlite3`へbackup APIで退避済み）、回答後に「絶対覚えた」「習得をリセット」「いまの手ごたえ（確信あり／たぶん／あてずっぽう）」を押せるようにした。出題範囲に「卒業済みだけ」を足し、そこを解除の一覧として使う。詳細は下の7.0。

## 1. 現在の到達点

MacBook上の本番URL:

```text
http://192.168.10.102:8080/services/gyousei-lab/
```

2026年7月29日の本番確認時点:

- 過去問: 569問
  - 通常択一: 509問
  - 多肢選択式: 30問
  - 記述式: 30問
- ○×学習カード: 151件（行政法55・基礎知識26・民法25・憲法20・商法・会社法15・基礎法学10）
  - ○×の内訳: ○64件・×87件（行政法36%・民法52%・憲法50%・基礎法学50%・商法40%・基礎知識38%）
- ⑤に使う関連過去問肢: 重複を除いて564件、各カードへの表示は延べ635件
- ⑥「似た制度・他分野との違い」: 61カード（うち科目をまたぐ関連付け19件）
- ⑦「法律ごとの結論くらべ」: 15カード（行政三法9・科目横断6）
- Claude監査候補: 20件
- 類似候補: 588組
- 過去問の収録科目: 基礎法学、憲法、行政法、民法、商法・会社法、基礎知識
- ○×学習カードの収録科目: **全6科目**（行政法、民法、憲法、商法・会社法、基礎法学、基礎知識）
- 法令基準日: 2026-04-01
- bundle revision:
  `ece8960f9cab3d4492f8d5a9b571740f66935eee17743f08edcfd63946458c73`

このプロジェクトは、2026年11月8日の受験日まで約3か月、利用者が実際に問題を解きながら育てる。行政法・民法などの科目別、全分野頻出、回答履歴から作る苦手ページ、行政法と民法などの分野横断比較を、同じ回答履歴と共通学習エンジン上に順次追加する。

次科目用の非公開編集資料:

- 合格道場・平成18〜令和7年度の全分野: 1,139問
- 平成28〜令和7年度: 569問
  - 解説あり558問
  - 解説公開終了11問
- 平成18〜27年度: 570問
  - 問題と当時の答えを保存
  - 解説提供なし
- 全1,139問が抽出警告0・検証エラー0
- 全20年の通常5肢: 5,095肢
- 正解番号だけから安全に○×候補化できる肢: 3,295肢
  - 行政法: 440問、通常5肢1,900肢、safe候補1,375肢
  - 民法: 220問、通常5肢900肢、safe候補555肢
- 多肢選択: 60問、語群1,200項目、空欄240個
- 記述: 60問
- 保存先:
  `~/.local/share/yuki-services/gyousei-lab/authoring/all_subjects/`

直近10年569問は本番の「過去問原文」「記述式」へ組み込み済み。旧10年570問は
引き続き頻出度・論点探索の非公開編集資料としてだけ使う。取得元解説は本番bundleへ
含めない。

年60問×20年なら理論上1,200問である。1,139問なのは、問58〜60の本文が著作権上の理由で20年分60問なく、2017年度問39が取得元の年度一覧にないため。行政法440問は全試験ではなく、行政法22問/年×20年である。

件数の再現可能な集計は、`gyousei-dataset-inventory` で生成する。runtimeの
`all-subject-inventory.json` は問題文・解説・内部パスを含まない集計専用で、
`/api/data-inventory` と「データ状態」「全体構成図」から表示する。

回答履歴の移行時点スナップショット:

- 過去問回答: 2件
- 学習カード回答: 15件
- 類似候補の判断: 588件

これらの回答件数は利用のたびに増えるので、将来の固定期待値ではない。

## 2. Mac上の構成

```text
ブラウザ
  └─ Homebrew nginx :8080
       ├─ /services/gyousei-lab/      → static/ を直接配信
       ├─ /services/gyousei-lab/api/  → 127.0.0.1:8817/api/
       └─ /services/gyousei-lab/health→ 127.0.0.1:8817/health

launchd: com.yuki.gyousei-lab
  └─ /opt/homebrew/bin/python3.12 server.py
       ├─ gyousei-production.json（読取り中心）
       └─ production.sqlite3（回答履歴の正本）
```

主要パス:

| 用途 | パス |
|---|---|
| アプリ | `~/dev/yuki-services/apps/gyousei-lab/` |
| 静的UI | `~/dev/yuki-services/apps/gyousei-lab/static/` |
| API | `~/dev/yuki-services/apps/gyousei-lab/server.py` |
| APIテスト | `~/dev/yuki-services/apps/gyousei-lab/tests/` |
| 問題生成コード | `~/dev/yuki-services/apps/gyousei-lab/authoring/` |
| AI向け全体構成 | `~/dev/yuki-services/apps/gyousei-lab/ARCHITECTURE.md` |
| 人間向け全体構成 | `~/dev/yuki-services/apps/gyousei-lab/static/architecture.html` |
| 非公開編集データ | `~/.local/share/yuki-services/gyousei-lab/authoring/` |
| 全分野20年分 | `~/.local/share/yuki-services/gyousei-lab/authoring/all_subjects/` |
| 安全な件数集計 | `~/.local/share/yuki-services/gyousei-lab/all-subject-inventory.json` |
| bundle | `~/.local/share/yuki-services/gyousei-lab/gyousei-production.json` |
| 回答履歴 | `~/.local/share/yuki-services/gyousei-lab/production.sqlite3` |
| 最新の弱点分析 | `~/.local/share/yuki-services/gyousei-lab/weakness-latest.json` |
| 世代別の弱点分析 | `~/.local/share/yuki-services/gyousei-lab/analytics/snapshots/` |
| ログ | `~/Library/Logs/yuki-services/gyousei-lab/` |
| launchd正本 | `~/dev/yuki-services/deploy/macos/launchd/com.yuki.gyousei-lab.plist` |
| launchd設置先 | `~/Library/LaunchAgents/com.yuki.gyousei-lab.plist` |
| nginx location | `/opt/homebrew/etc/nginx/locations/yuki-services.conf` |

`server.py` はPython標準ライブラリだけで動き、仮想環境は不要。APIは `127.0.0.1:8817` だけで待ち受ける。

既存deck IDは `administrative-law-frequent-10`。名前は行政法由来だが、回答履歴互換のため変更せず、将来の全科目もこのdeckへ追加する。

## 3. ソースと永続データの境界

ソースディレクトリに置くもの:

- `server.py`
- `static/`
- `tests/`
- `authoring/`
- `README.md`
- `AGENTS.md`
- `HANDOFF.md`
- `docs/`

ソースディレクトリへ置かないもの:

- `gyousei-production.json`
- `production.sqlite3`、WAL、SHM
- 合格道場から取得したHTML
- 公式PDF
- provider解説全文
- AIの生prompt・stdout
- ログ、キャッシュ、バックアップ

Mac移行時、リリース済みbundleとSQLiteは
`~/.local/share/yuki-services/gyousei-lab/` へ置き、権限を `0600` にした。

## 4. 現在の画面

- ○×学習
- 図で整理
- 過去問原文
- 記述式
- 解説カード
- Claude監査
- 類似確認
- データ状態
- 全体構成図（独立ページ）

○×学習では次を表示する。

1. そのカードの過去の正解回数・不正解回数と、カードID・元問題リンク
2. A: 標準問題文
3. B: やさしい言い換え
4. Bの別案: 条件、用語、時間の流れなど、問題に合うほどき方
5. C: 短い読み替え
6. 回答後の正解、訂正文、普通の解説、深掘り、根拠、常識力
7. ⑤ 実際の肢・本番での聞かれ方
8. ⑥ 似た制度・他分野との違い（データがある時だけ）
9. ⑦ 法律ごとの結論くらべ（`comparisonTable` があるカードだけ）

深掘り解説は折りたたまず、回答後にそのまま表示する。全体マップなどの画像6枚は「図で整理」だけに置き、各問題内には表示しない。

## 5. 問題追加の正本ルール

### 5.1 基本構造

- `subjectId`: 科目を表す安定した英数字ID。
- `category`: 科目の表示名。
- `topic`: 科目内の分野。
- `subtopic`: 1カードで覚える一つの論点。
- Aは○×判定できる一つの命題にする。独立した二命題を安易に一枚へ詰め込まない。
- `correct`、誤りなら正しい形を示す `correction`、正しい形を覚える `memoryPoint` を持つ。
- Bは `variants.b` と `variants.bCasual` の2案を持つ。
- `variants.bCasualStyle` に、2案目で採用したほどき方を短く記録する。
- Cは `variants.c` に置く。
- 普通の解説、深掘りの背景・ひっかけ・例、常識力を持つ。
- 法令根拠、主資料、review状態、頻出度、⑤、必要な⑥を持つ。

### 5.2 Bの文章

- 頭が回っていない時や集中が乱れている時でも、一度で場面を想像できる言葉にする。
- 1行だけの雑な言い換えで終わらせない。
- 誰が、いつ、何をできるかを日常語で説明する。
- 初めて出る専門用語は、その場で普段の言葉へほどく。
- 1案目は日常の場面から説明する。
- 2案目は、問題に合う型を選ぶ。
  - 用語からほどく
  - 条件を番号で並べる
  - 時間の流れで追う
  - 単純な問題なら自然な2段落
- 番号を振ること自体を目的にしない。条件が一つなら無理に列挙しない。
- 短さだけを狙わない。最短確認はCが担当する。
- 改行は段落の意味が変わる所だけに使う。
- **Aの主語、時期、例外、否定、結論の強さを変えない。B・CはAと同じ結論の文にする。** A・B1・B2・Cは4つとも回答ボタンより上に出るので、B・CがAの逆を書くと答えが割れる。断定型で統一し、「この命題は〜と言っています」という引用の型は使わない。詳細と禁止例は `authoring/CARD_AUTHORING.md` の「2. B・CはAと同じ結論を保つ」を正本とする。

望ましい調子の例:

> 行政から「処分する前に、あなたの言い分を聞きます」と言われたときは、本人が自分で全部話さなくても大丈夫です。
>
> 弁護士など、自分の代わりに説明してくれる人を選べます。

### 5.3 ⑤ 実際の肢・本番での聞かれ方

- 平成28年度以降の実際の過去問肢を、出典付きでそのまま表示する。
- 同じ結論だけでなく、逆向き、条件、例外、直接比較も含める。
- 単に同じ章というだけのものは、学習に本当に役立つ場合だけ表示する。
- 組合せ・個数問題の肢も⑤へ表示し、footerに「組合せ問題の肢」と形式を明示する（2026-07-27にこの方針へ変更した。それ以前は通常択一だけを表示していたため、関連出題があるのに⑤が空のカードが5件あった）。記述式の元問題は⑤の表示用の肢にせず、頻度だけへ反映する。
- 全カードが⑤の肢を1本以上持つ。新しいカードも⑤なしでは公開しない。
- 平成18〜27年度の問題文は⑤へ表示しない。頻出回数の内部判定だけに使う。
- 同じ年度・同じ問題に関連肢が複数あっても、頻出度では1問と数える。
- provider版と公式版が同じ本試験問題を指す場合も1問にまとめる。

### 5.4 頻出度

- 同一命題、逆向き、条件、例外、直接比較だけを数える。
- `same_topic` だけの問題は頻出度へ入れない。
- 問題単位で重複除去する。
- 一次判定後、別のAIまたは別担当が全件を独立再確認する。
- 最初の35カードは10件維持・25件修正、追加20カードは14件維持・6件修正した。AIの最初の類似判定だけでは件数を確定しない。
- 現行ラベル基準:
  - 10回以上: 最頻出
  - 6〜9回: 頻出
  - 3〜5回: 繰り返し出題
  - 1〜2回: 重要論点

### 5.5 ⑥ 似た制度・他分野との違い

- 不服審査法ではこうだが行政事件訴訟法では違う、というような本試験上の混同を防ぐ欄。
- 本当に結論・主体・期間・効果などを取り違えやすい時だけ付ける。
- `title`: 何を混同するか。
- `explanation`: 両制度の差。
- `memoryCue`: 見分ける着眼点。
- 対応するカードが実在する場合だけ `relatedCardId` を付ける。
- 比較がなければ空配列にし、画面では⑥全体を隠す。

## 6. データの出典と法令確認

- 平成28年度〜令和7年度の行政法220問は、合格道場の年度別ページから取得した。
- 平成18〜27年度の行政法220問は頻出度判定専用として取得した。
- 全分野についても、合格道場の実在リンクから平成18〜令和7年度1,139問を取得した。直近10年は解説あり558問・公開終了11問、旧10年は解説提供なし570問である。
- 全分野データの件数・hash・抽出結果は`all_subjects/manifest.json`と、各期間の`reports/validation.json`を正本とする。
- 合格道場の正答・解説を、過去問の正誤と普通の解説の第一基準にした。
- AIには、正誤をゼロから置き換えるより、B二案、C、深掘り、常識力、⑤・⑥の整理を期待する。
- 解説本文は非公開の編集資料であり、bundleへ丸ごとコピーしない。
- 令和8年度試験は2026年4月1日現在施行の法令が基準。取得日や実装日の現行法と混同しない。
- 公式資料の表示許諾は未確認なので、raw一式をLANの静的ディレクトリへ置かない。

## 7. 回答履歴とAPI

主要GET:

- `/health`
- `/api/overview`
- `/api/data-inventory`
- `/api/progress`
- `/api/card-progress`
- `/api/questions`
- `/api/cards`
- `/api/claude-reviews`
- `/api/similarities`
- `/api/export`

主要POST:

- `/api/attempts`
- `/api/card-attempts`
- `/api/similarity-decisions`

回答イベントにはクライアント生成IDがあり、同じ送信の再試行を二重加算しない。学習カードは問題文・正解のrevisionを使い、カード内容が変わった時に古い判定を現在の正答率へ誤って混ぜない設計である。

Bだけの表現変更は回答revisionを変えない。A、C、正解などを変えるとrevisionが変わり、旧回答は履歴として残るが現在の習得判定から外れる。

ブラウザには通信失敗時だけ未送信キューを置く。集計の正本はSQLiteである。localStorageを全回答履歴の正本へ戻さない。

SQLite schemaの `user_version` は4。`answer_attempts`・`card_attempts`・`card_marks`・`similarity_decisions`はDB triggerでもappend-onlyを守る。通常操作でUPDATEやDELETEを行わない。

### 7.0 卒業・絶対覚えた・自信度（`card_marks`）

「習得済み」は自動（`正解 − 不正解 >= 3`）で、おまかせからだけ外れる。「絶対覚えた」は回答後に自分で押すもので、**どの出題範囲でも出さない**。全リセットの対象は習得済みだけで、絶対覚えたは残る。解除はどちらも出題範囲「卒業済みだけ」の一覧から行う。

`card_marks`の`action`は`certain`・`uncertain`・`reset`・`confidence`の4種類で、`scope='deck'`（全リセット）を取れるのは`reset`だけ。`reset`は「ここより前の回答を習得判定に数えない」区切りを置くだけなので、**回答も過去の卒業回数も消えない**。`card-progress`は`correct`/`incorrect`（通算）と`sinceResetCorrect`/`sinceResetIncorrect`（区切り以降）を別々に返し、習得判定には後者を使う。自信度は回答1件につき1つまでで、`attemptEventId`で回答に結び付くだけ。出題対象の判定には効かせない。

localStorageはorigin単位なので、旧VPSのブラウザにだけ残っていた未送信回答はMacへ自動移行されていない。サーバーへ送信済みの履歴はSQLiteで移行済み。

### 7.1 弱点分析snapshot MVP

`weakness_analysis.py`は本番SQLiteを読み取り専用で開き、`card_attempts`のcurrent
`answerRevision`だけをDBの`id`順で集計する。`answer_attempts`はカードの苦手判定へ
混ぜない。

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 weakness_analysis.py
```

生成物:

```text
~/.local/share/yuki-services/gyousei-lab/analytics/snapshots/weakness-<timestamp>.json
~/.local/share/yuki-services/gyousei-lab/weakness-latest.json
```

- schema: `gyousei-weakness-snapshot@1`
- 権限: `0600`
- 保存: atomic
- 必須識別子: `bundleRevision`、`maxAttemptId`、`analyzerVersion`
- 分類: `unlearned`、`learning`、`watch`、`weak`、`recovering`、`mastered`
- 直近窓: 5回
- 苦手: 誤答2回以上かつ、直近2連続誤答、または直近5回中2回以上誤答・正答率50%以下
- 回復中: 過去に苦手で直近2回正解
- 習得復帰: 直近3回正解かつ`correct - incorrect >= 3`
- 時間減衰: 使わない

stale revisionは履歴として残したまま現在判定から除外し、
`stale_revision_ignored`と件数をsnapshotへ記録する。

### 7.2 苦手studyView

学習画面の「学習ビュー」で「苦手・要観察」を選ぶと、`weak`、`watch`の
カードだけを優先度順に表示する。`recovering`は直近2回連続正解の状態として
分析に残すが、このビューから外す。別deckや別回答履歴は作らず、通常学習と同じ
card ID、renderer、`/api/card-attempts`を使う。

`/api/learning-analysis`は非公開snapshotから固定項目だけを返す。
`bundleRevision`または`maxAttemptId`が一致しない場合は、同じ判定器で現在のSQLiteを
読み取り専用集計する。カード回答が保存された時は`weakness-latest.json`を
`0600`でatomic更新する。snapshotの内部パス、provider本文、raw回答は返さない。

## 8. 問題生成・頻出度・類似探索

Macを正本とし、次の境界で管理する。

```text
apps/gyousei-lab/authoring/                 取得・抽出・候補作成・監査・bundle生成コード
~/.local/share/yuki-services/gyousei-lab/
  ├─ gyousei-production.json               実行中bundle
  ├─ production.sqlite3                    回答履歴
  └─ authoring/
      ├─ canonical/                        学習カードと関連肢の編集正本
      ├─ raw/、extracted/、catalog/         取得証跡と抽出結果
      ├─ archive_frequency/                 平成18〜27年度の頻度専用データ
      ├─ curation/、review/、reports/       候補・監査・照合
      ├─ reference/official-r2-r7/          公式全科目の参考コーパス
      └─ builds/                            検証済みbundleの生成先
```

`reference/official-r2-r7/`は令和2〜7年度の1,360肢を含むが、正誤は出題当時基準で、抽出上の欠落もある。本番へ直接流さず、行政法以外の論点候補・類似候補を探す用途に限定する。行政法は既存の合格道場データを優先する。

bundleは`authoring/README.md`の手順で非公開`builds/`へ生成し、現行bundleと件数・digestを比較する。実行中bundleの置換は別工程としてatomicに行う。

`authoring/src/gyousei_pipeline/candidates.py` は `isWithdrawn=true` の問題を
safe ○×候補へ分解せず、問題単位reviewへ残す。全分野の件数集計は
`gyousei-dataset-inventory` で再生成できる。平成30年問56と令和6年問34の
没問を含め、直近10年のsafe候補は1,630肢、20年合計は3,295肢である。

正解番号だけでは分解できない直近10年の組合せ・個数問題には、保存済み解説の
単一ラベル正誤と元の正解を再照合する別工程を用意した。第1段階632肢と
監査allowlistによる第2段階41肢、合計673肢・140原問を非公開sidecarへ生成する。
全候補は`reviewed=false`、`publishable=false`、`frequencyEligible=false`であり、
既存`review_candidates.json`を置換せず、本番bundleへ自動投入しない。
実問題を含むallowlistは
`all_subjects/current_2016_2025/curation/explanation_mapping_ox_rules.json`
を正本とする。

`frequencyEligible`は候補単体のフラグで、カード×原問の頻出判定を表現できないため、
673肢では`false`を維持する。行政法440問・55カードの独立再監査済み正本
`curation/card_frequency_2006_2025.json`へ原問単位で照合し、結果を
`all_subjects/current_2016_2025/curation/explanation_ox_frequency_crosswalk.json`
へ保存する。これにより今回の行政法原問は、現行カードに数えるものと数えないものを
区別済みである。候補全体を`true`にして自動集計してはならない。

`learning_index`は⑤・⑥・新カードの候補探索専用であり、頻出度の正本ではない。
候補の`frequencyEligible`は明示的に`true`と監査された場合以外は`false`とする。
新科目ではカード作成後にカード×原問監査を行い、監査前は未判定として扱う。

## 9. 将来の科目追加

- 現在の安定したdeck IDを維持し、すべての科目を同じ回答履歴へ追加する。
- 学習カード、過去問原文、記述式のすべてに `subjectId` を付ける。
- UIは3か所とも同じ科目カタログを使う。
- 現在の本番件数や行政法の件数をコード上の永続的な前提にしない。
- 過去問の多科目化は完了。○×カードの科目追加では、bundle schema、API、フィルタ、summary、タブ表示、テストを複数科目fixtureで確認する。
- 大量データでは全組合せ比較を避け、科目・topic・重要法令語で候補を絞る補助索引を使う。自動類似度は候補抽出に限り、最終分類は監査データとして保存する。

## 10. 既知の注意点

- このディレクトリはprivate Gitリポジトリ `yamashita-yukihito/gyousei-lab` で管理する。親の `yuki-services` はGitリポジトリではない。
- タブ補足と本番掲載件数はAPI集計から動的表示する。行政法の現件数を固定値へ戻さない。
- `static/app.js` の版は `20260727-3`。UI資産を変更する時はHTMLのクエリ版と一緒に更新する。
- カードの`review.currentLawStatus`は原則`provider-backed`系だが、`min-property-co-ownership-claims-001`だけは取得元解説が未収録の原問があるため`provider-partial`（公式正解からの機械確定で補完済み）。
- UI初回はoverview、cards、learning-analysisを読み、questions、Claude監査、similaritiesはタブ単位で遅延読込する。
- rawのunderscore科目IDは変えず、`authoring/src/gyousei_pipeline/subjects.py`を公開canonical変換の正本にする。
- production bundle builderの過去問件数は`--question-manifest`でrelease manifest由来にできる。総数・科目別・形式別・年度が一致しなければfail closedする。
- `static/app.js` は `const API = "api"` という相対URLを使う。末尾スラッシュ付きサービスURLとnginxの `proxy_pass .../api/` が前提なので、安易に絶対 `/api` へ変えない。
- launchd plistのソースと設置済みファイルは、移行時点ではSHA-256が一致している。
- LAN専用でBasic認証は付けていない。外部公開へ変える場合は、現状の前提をそのまま使わず脅威モデルを見直す。

次の優先順位:

1. 行政法と民法の`learningRelations`（分野横断の比較データ）。
2. 民法の追加論点（次候補: 転貸借、法定地上権、消滅時効期間、錯誤、工作物責任）。
3. 全分野頻出view、憲法・商法等の重点追加。

済み: カード×原問監査schemaの`card-frequency-audit@3`一般化と、民法25論点の追加（2026-07-27）。

弱点分析snapshotはSQLiteを更新せず、未学習と苦手を分け、1回の誤答だけで
不得意と断定しない。科目別・全分野頻出・弱点は別deckの複製ではなく、
共通エンジンの`studyView`として実装する。詳細は`ARCHITECTURE.md`。

## 11. 基本検証

```bash
cd ~/dev/yuki-services/apps/gyousei-lab
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 -m unittest discover -s tests -v
/opt/homebrew/bin/node --check static/app.js
curl -fsS http://127.0.0.1:8817/health
curl -fsS http://192.168.10.102:8080/services/gyousei-lab/health
plutil -lint ~/dev/yuki-services/deploy/macos/launchd/com.yuki.gyousei-lab.plist
/opt/homebrew/bin/nginx -t
```

問題生成コード:

```bash
cd ~/dev/yuki-services/apps/gyousei-lab/authoring
export GYOUSEI_DATA_ROOT="$HOME/.local/share/yuki-services/gyousei-lab/authoring"
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/uv run \
  python -m unittest discover -s tests -v
```

DBを触った場合:

```bash
/opt/homebrew/bin/python3.12 - <<'PY'
import sqlite3
from pathlib import Path

path = Path.home() / ".local/share/yuki-services/gyousei-lab/production.sqlite3"
with sqlite3.connect(path) as db:
    print(db.execute("PRAGMA quick_check").fetchone()[0])
PY
```

サービス状態:

```bash
launchctl print "gui/$(id -u)/com.yuki.gyousei-lab"
```

APIコードの反映:

```bash
launchctl kickstart -k "gui/$(id -u)/com.yuki.gyousei-lab"
curl -fsS http://127.0.0.1:8817/health
```

静的UIはソースをnginxが直接読んでいるため、別のdeployコピー操作はない。
