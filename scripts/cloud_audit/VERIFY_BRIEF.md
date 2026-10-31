# Verifier brief (検証役)

Read `$S/AUDIT_BRIEF.md` too (tools, card/drill format, legal baseline 2026-04-01, hard rules: **never edit files in
`/home/user/gyousei-lab`**; write only your output file in `$S`).

You are the independent verifier. Another reviewer flagged the parts marked `⟦ ⟧` in the items of your input file.
You are not told what they think is wrong, or whether they are right. Many flags may be false alarms.
For each item, decide on your own:

- Is the marked statement, read in the context of the whole field (and of the card's A/`correct`, or the drill's
  `statement`/`answer`), legally correct under the law in force on **2026-04-01**? Check the statute with
  `python3 $S/law.py art <law> <article>` — do not rely on memory for article numbers, periods or numbers.
- For case law, say the case (court, date) you rely on; if you are not sure of the case, answer `unsure`.
- For items with `question_for_you`, answer that question (A/B/C consistency rule from the brief).
- Remember `!!…!!` often marks a wrong statement being warned against; that is not an error by itself.

Write a JSON array to your output file, one object per item:

```json
{"id": "<same id>", "verdict": "error" | "ok" | "unsure",
 "correct_statement": "<if error: the correct rule in one or two sentences, in Japanese>",
 "basis": "<statute article(s) you checked with law.py, or case name and date>",
 "note": "<optional, one sentence>"}
```

Then reply with a short summary only: counts of error / ok / unsure, and the ids judged `error`.
