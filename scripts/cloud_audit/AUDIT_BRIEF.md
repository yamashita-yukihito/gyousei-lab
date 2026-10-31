# Audit brief (read fully before starting)

You are helping audit study materials for the Japanese 行政書士試験 (exam 2026-11-08). The learner memorizes these
materials, so **a legal error in them is the worst outcome**, and **a wrong "fix" is even worse**. Report only real
errors you can back with the statute text or a well-known Supreme Court case whose name and date you are sure of.

Scratchpad (all helper tools and outputs): `/tmp/claude-0/-home-user-gyousei-lab/ea4cf573-0fd5-5970-81e4-ab2ce3697672/scratchpad`
(below written as `$S`). Repository: `/home/user/gyousei-lab` (public GitHub repo).

## Hard rules

- **Do not edit, create or delete any file inside `/home/user/gyousei-lab`.** Do not run git commands that change
  anything. Write only the output file you are told to write (in `$S`).
- Legal baseline date: **2026-04-01** (laws in force on that day). Check statute text with the e-Gov tool below,
  not from memory. Amendments that matter: 刑法 拘禁刑 (懲役・禁錮 abolished, in force 2025-06-01); 民法 令和6年改正
  (共同親権, 財産分与 5年, 法定養育費; in force 2026-04-01); 民法 令和4年改正 (嫡出推定, 再婚禁止期間 abolished,
  懲戒権 deleted; in force 2024-04-01 / 2022-12-16); 民法・不登法 令和3年改正 (共有・相隣・相続登記義務化 2024-04-01);
  地方自治法 令和6年改正 (補充的な指示 2024-09-26); 行政書士法 令和7年改正 (2026-01-01); 個人情報保護法 一元化
  (行政機関個人情報保護法 abolished 2022-04-01). Always confirm the in-force version via e-Gov.
- Never propose changing `variants.a` (A). A is the original wording of a past exam choice and must stay verbatim.
  If A's conclusion itself changed because of an amendment, report it with kind `a-outdated` (the Mac side handles it).
- Do not report style, readability, "could add more detail", or minor exceptions. The learner targets 60%;
  exceptions are not listed on purpose. Report only: wrong law, wrong numbers (periods, ratios, counts, amounts),
  wrong article numbers, wrong institution names, wrong case holdings, outdated law, and the rule violations below.
- Case law: report a holding as wrong only if you are certain of the case (court, date) and its holding. If you
  suspect a problem but cannot be sure, report with kind `unsure` and say what should be checked. The network is
  open: you may check a judgment on courts.go.jp (裁判例検索; find it with WebSearch, then fetch with curl) and name
  the source in `basis`. Do not fetch past-exam sites.
- Do not judge details of cases such as judges' vote counts or lower-court sentences.

## e-Gov tool (statute text as of 2026-04-01, cached)

```
python3 $S/law.py art 民法 819 766_3      # articles (branch numbers with _ : 766_3 = 766条の3)
python3 $S/law.py grep 行審 審査請求期間   # articles containing all words (main provisions only)
python3 $S/law.py suppl 民法 財産の分与     # supplementary provisions (附則) containing words
python3 $S/law.py info 民法                # version info (last amendment and its enforcement date)
python3 $S/law.py find 道路法              # look up the lawId of any other law by title
```
Law keys: 行手 行審 行訴 国賠 自治 代執 行組 憲法 会社 商法 書士 個情 情公 公文 住基 戸籍 民法 法適 裁判 員法 民訴 刑訴
刑法 国公 地公 不登 借地借家 内閣法 国会法 財政法 公選 警職 番号法 一般法人 非訟 補助金適正化法 建築基準法 都市計画法 土地収用法
家事事件手続法 人事訴訟法 (full names also work, e.g. 地方自治法; any other law works with its e-Gov lawId, e.g. `law.py art 330AC0000000179 24_2`).
Paragraph numbers appear as "２", "３" at line starts; items as "一", "二".

## Learning card format (`content/explanation_cards.json`, you get extracted copies)

- `variants.a` = A (past exam text, the statement the learner judges ○/×). `correct` = true if A is legally correct.
- `variants.b` = B1 (plain retelling), `variants.bCasual` = B2 (second retelling), `variants.c` = C (one-line summary).
  **B1, B2 and C must state the same proposition as A** — if A is a false statement, B/C restate the same false
  statement in plain words. They are shown before the answer, so they must neither state the correct rule (leak)
  nor the opposite of A. A B/C that says the opposite of A, or reveals ○/×, is a rule violation (kind `rule-bc`).
  Note: B/C being "false" when `correct` is false is intended, not an error.
- After answering: `correction` (the correct rule), `memoryPoint`, `explanations.normal`,
  `explanations.deepDive.background|trap|example`, `explanations.commonSense`, `comparisonTable` (rows),
  `crossFieldComparisons[].title|explanation|memoryCue`, `figures` (images), `learnerQa[].answer` (Q&A notes the
  learner asked; report only legal errors there), `legalBasis[].label`.
- `figures[].src` is relative to `/home/user/gyousei-lab/static/` (e.g. `assets/card-figures/x.png`). Look at PNGs with
  the Read tool (it shows images) and read SVG files as text. Check the legal content of figures too.
- Markup inside texts: `**bold**`, `__underline__`, `==highlight==`, `!!red: wrong statement / trap!!`, `%%actor%%`,
  `@@article@@`. Marks never nest and never span lines. Keep the same marks in your replacement text.
- `!!...!!` often marks a WRONG statement being warned against ("!!「…」と言い切る肢は誤り!!"). Read carefully before
  calling it an error.

## Drill format (`static/<x>-d/chapters.json`)

- Chapter: `title`, `lead`, `goal`, `remedyGuide`, `baseScenario`, `summaryTable`, `guide` (tips), `questions`.
- Question: `statement` (the proposition judged ○/×), `answer` (true = ○), `currentScenario`, `delta`,
  `diagram` (shown BEFORE answering — must not show the conclusion), `answerEdges` / `answerDiagram` (after),
  `explanation.summary|fact|rule|conclusion|protectWho|why|ifOpposite|legalSense|minimum|next|remedy`, `legalBasis`,
  `examRefs`, `cardId` (linked learning card; its topic and conclusion must match the question), `skipNote`.

## Output

Write a JSON array to the output file you were given. One object per finding:

```json
{"id": "<your prefix>-1",
 "target": "card" | "drill" | "page",
 "card": "<card id, for cards>", "drill": "<subject dir e.g. gyosei-d>", "question": "<question id or chapter id>",
 "file": "<repo-relative path, for pages>", "line": 0,
 "path": "<dot path inside the card/question, e.g. explanations.deepDive.trap or learnerQa.0.answer>",
 "old": "<exact substring copied from the current text; must occur exactly once in that field>",
 "new": "<minimal replacement text, same style and markup>",
 "kind": "law-error" | "number" | "article-number" | "institution" | "case" | "outdated" | "a-outdated" | "flip" | "rule-bc" | "rule-diagram" | "unsure",
 "basis": "<statute article(s) as checked with law.py, or case name and date>",
 "why": "<one or two sentences>",
 "confidence": "high" | "medium" | "low"}
```

- Keep `old` short but unique (one clause or sentence). Copy it exactly (same characters and marks).
- `new` must change as little as possible and keep the conclusion of the card unless the card is wrong.
- For `flip` (A's ○/× itself is wrong), explain in `why`; do not write replacements for every field.
- If you find nothing wrong in an item you checked, do not list it. Write `[]` if nothing at all.

## Ideas for the two strategy pages (secondary, optional)

The learner also studies two strategy pages: `static/simple-rules/content.md` (法律家の常識: `# sense` = ways of
thinking that decide many choices from the purpose of a rule; `# ok` = rules that may be stated flatly; `# ng` =
oversimplifications that are wrong) and `static/technique/index.html` (絞り込みテクニック: how wrong choices are built,
wording patterns). If, while auditing, you notice a principle that would help judge **many** choices (not a one-off
fact), write it to a separate file `$S/findings/<your prefix>_ideas.json` as a JSON array of
`{"page": "simple-rules" | "technique", "section": "sense" | "ok" | "ng" | "technique", "subject": "<科目>",
"idea": "<Japanese, one or two sentences>", "basis": "<articles / cases>", "examples": ["<card or drill ids>"]}`.
Check first that the idea is not already on the page (grep the file). This is secondary: never let it reduce the
audit's care. Skip the file if you have no idea worth it.

Finally, reply with a short summary (max 15 lines): how many items you checked, how many findings by kind,
and the ids of findings with confidence high. Do not paste the findings themselves into the reply.
