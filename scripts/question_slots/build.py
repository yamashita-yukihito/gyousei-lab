"""問番号ごとの出題傾向ページ（static/question-slots/index.html）を書き出す。

正本は同じフォルダの slots_data.py（テーマと予想）と cards_by_slot.json（平成28年度以降の過去問を出典にしたカード）。
過去問へのリンクは static/simple-rules/exam-links.json から引く。問題文は使わない。

  /opt/homebrew/bin/python3.12 scripts/question_slots/build.py          # 差分の確認だけ
  /opt/homebrew/bin/python3.12 scripts/question_slots/build.py --write  # 書き出す
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import slots_data as D  # noqa: E402

OUT = ROOT / "static/question-slots/index.html"
OUT_JSON = ROOT / "static/question-slots/slots.json"
LINKS = json.loads((ROOT / "static/simple-rules/exam-links.json").read_text())
CARDS = json.loads((HERE / "cards_by_slot.json").read_text())
RECENT = ["平成28年", "平成29年", "平成30年", "令和元年", "令和2年", "令和3年", "令和4年", "令和5年", "令和6年", "令和7年"]
OLD = ["平成%d年" % y for y in range(18, 28)]
SHORT = {"平成28年": "H28", "平成29年": "H29", "平成30年": "H30", "令和元年": "R1"}
CHANCE = [(0.75, "◎", "取れる"), (0.55, "○", "五分より上"), (0.35, "△", "五分以下"), (0.0, "×", "勘")]
ICON = {"kiso": "kiso", "kenpo": "ken", "gyosei": "gyo", "minpo": "min", "shoho": "sho", "tashi": "lab", "kijutsu": "lab", "chishiki": "chi"}


def esc(text):
    return html.escape(str(text), quote=True)


def short(era):
    return SHORT.get(era, era.replace("令和", "R").replace("年", ""))


def ratio(slot):
    return slot["ev"] / slot["max"] if slot["ev"] is not None else slot["p"]


def expected(slot):
    return slot["ev"] if slot["ev"] is not None else slot["p"] * slot["max"]


def chance(slot):
    r = ratio(slot)
    for floor, mark, label in CHANCE:
        if r >= floor:
            return mark, label
    return CHANCE[-1][1:]


def qid(url):
    m = re.search(r"w_mainnendo\.php\?queID=(\d+)$", url)
    return "goukakudojyo:" + m.group(1) if m else None


def slot_html(s):
    n = s["num"]
    mark, label = chance(s)
    ev = expected(s)
    parts = [f'<article class="slot" id="q{n}" data-group="{s["group"]}">']
    parts.append('<header class="slot-head">')
    parts.append(f'<span class="slot-num">問{n}</span>')
    parts.append(f'<h3>{esc(s["title"])}</h3>')
    parts.append(f'<span class="fix">{esc(s["fix"])}</span>')
    parts.append(f'<span class="chance c{CHANCE.index(next(c for c in CHANCE if c[1] == mark))}" title="取れそう度（予想）">{mark} {label}・{ev:.1f}/{s["max"]}点</span>')
    parts.append("</header>")
    parts.append(f'<p class="summary">{esc(s["summary"])}</p>')
    if s["years"]:
        items = []
        for era, theme in zip(RECENT, s["years"].split("／")):
            url = LINKS.get(f"{era}問{n}")
            label_era = esc(short(era))
            if url:
                items.append(f'<li><a href="{esc(url)}" target="_blank" rel="noopener">{label_era}</a> {esc(theme)}</li>')
            else:
                items.append(f'<li><span class="era">{label_era}</span> {esc(theme)}</li>')
        parts.append('<p class="label">直近10年（平成28〜令和7年度）</p>')
        parts.append('<ol class="years">' + "".join(items) + "</ol>")
    if s["old"]:
        old_links = " ".join(
            f'<a href="{esc(LINKS[f"{era}問{n}"])}" target="_blank" rel="noopener">{esc(era.replace("平成", "H").replace("年", ""))}</a>'
            for era in OLD if f"{era}問{n}" in LINKS)
        parts.append(f'<p class="old"><span class="label-inline">旧10年</span>{esc(s["old"])}<span class="old-links">{old_links}</span></p>')
    parts.append(f'<p class="tip"><span class="label-inline">本番</span>{esc(s["tip"])}</p>')
    actions = []
    cards = CARDS.get(str(n), [])
    if cards:
        actions.append(f'<a class="action" href="../?cardIds={esc(",".join(cards[:200]))}&amp;from=slots#study">この番号のカードを解く（{len(cards)}枚）</a>')
    ids = [qid(LINKS[f"{era}問{n}"]) for era in RECENT if f"{era}問{n}" in LINKS]
    ids = [i for i in ids if i]
    if ids:
        actions.append(f'<a class="action ghost" href="../?questionIds={esc(",".join(ids))}#quiz">直近10年を過去問タブで解く（{len(ids)}問）</a>')
    if actions:
        parts.append('<p class="actions">' + "".join(actions) + "</p>")
    parts.append("</article>")
    return "\n".join(parts)


def overview_html():
    cells = []
    for n in range(1, 61):
        s = D.S[n]
        mark, _ = chance(s)
        cells.append(f'<a class="cell g-{s["group"]}" href="#q{n}"><b>問{n}</b><span>{esc(s["title"])}</span><i>{mark}</i></a>')
    return '<div class="overview">' + "".join(cells) + "</div>"


def score_rows():
    rows, legal, general = [], 0.0, 0.0
    for name, key, rng, _ in D.GROUPS:
        slots = [s for s in D.S.values() if s["group"] == key]
        ev = sum(expected(s) for s in slots)
        mx = sum(s["max"] for s in slots)
        if key == "chishiki":
            general += ev
        else:
            legal += ev
        rows.append(f'<tr><td>{esc(name)}</td><td>{esc(rng)}</td><td class="num">{ev:.1f}</td><td class="num">{mx}</td></tr>')
    return rows, legal, general


def build():
    rows, legal, general = score_rows()
    total = legal + general
    sections = []
    for name, key, rng, note in D.GROUPS:
        slots = sorted((s for s in D.S.values() if s["group"] == key), key=lambda s: s["num"])
        body = "\n".join(slot_html(s) for s in slots)
        sections.append(f'''    <section class="section" id="{key}">
      <div class="section-heading">
        <p class="kicker">{esc(rng)}</p>
        <h2>{esc(name)}</h2>
        <p>{esc(note)}</p>
      </div>
{body}
    </section>''')
    return TEMPLATE.format(
        overview=overview_html(),
        score_rows="\n".join(rows),
        legal=f"{legal:.0f}", general=f"{general:.0f}", total=f"{total:.0f}",
        sections="\n\n".join(sections),
    )


TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <link rel="icon" type="image/svg+xml" href="../assets/favicons/tech.svg">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#183a34">
  <title>問番号ごとの出題傾向 | 行政書士 過去問ラボ</title>
  <link rel="stylesheet" href="../architecture.css?v=20260807-1">
  <!-- scripts/question_slots/build.py --write で書き出す。このファイルを直接直さない。 -->
  <style>
    .box {{ margin-top: 14px; padding: 16px 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--paper); font-size: 15px; line-height: 1.8; }}
    .box h3 {{ margin: 0 0 6px; color: var(--deep); font-size: 17px; }}
    .box ul {{ margin: 6px 0 0; padding-left: 20px; }}
    .box li + li {{ margin-top: 4px; }}
    .box p {{ margin: 6px 0 0; }}
    .conclusion {{ border-left: 4px solid var(--deep); background: #fff; }}
    .forecast {{ border-left: 4px solid var(--amber); background: var(--amber-soft); }}
    .note {{ color: var(--muted); font-size: 13px; }}
    .overview {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }}
    .overview .cell {{ position: relative; display: grid; gap: 1px; min-height: 66px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 9px; background: var(--paper); color: var(--ink); text-decoration: none; font-size: 12px; line-height: 1.4; }}
    .overview .cell b {{ color: var(--deep); font-size: 13px; }}
    .overview .cell i {{ position: absolute; top: 6px; right: 8px; font-style: normal; font-weight: 800; }}
    .overview .cell:hover {{ border-color: var(--green); }}
    .g-kiso {{ border-left: 5px solid #8a8f8c !important; }}
    .g-kenpo {{ border-left: 5px solid #9b5c8f !important; }}
    .g-gyosei {{ border-left: 5px solid #1f775b !important; }}
    .g-minpo {{ border-left: 5px solid #376f91 !important; }}
    .g-shoho {{ border-left: 5px solid #8a6412 !important; }}
    .g-tashi {{ border-left: 5px solid #5c6bc0 !important; }}
    .g-kijutsu {{ border-left: 5px solid #b42318 !important; }}
    .g-chishiki {{ border-left: 5px solid #6b7f2a !important; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; color: var(--muted); font-size: 12.5px; }}
    .score {{ width: 100%; min-width: 0; border-collapse: collapse; background: var(--paper); font-size: 14px; margin-top: 10px; }}
    .score th, .score td {{ padding: 7px 11px; border-bottom: 1px solid var(--line); border-left: 0; border-right: 0; text-align: left; }}
    .score .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .score tfoot td {{ font-weight: 800; color: var(--deep); }}
    .slot {{ margin-top: 12px; padding: 14px 18px 12px; border: 1px solid var(--line); border-radius: 12px; background: var(--paper); font-size: 14.5px; line-height: 1.75; scroll-margin-top: 12px; }}
    .slot-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }}
    .slot-num {{ color: var(--green); font-size: 13px; font-weight: 900; }}
    .slot h3 {{ margin: 0; color: var(--deep); font-size: 17px; }}
    .fix {{ padding: 0 8px; border-radius: 999px; background: var(--green-soft); color: var(--green); font-size: 12px; font-weight: 700; }}
    .chance {{ margin-left: auto; font-size: 12.5px; font-weight: 800; white-space: nowrap; }}
    .chance.c0 {{ color: #1f775b; }} .chance.c1 {{ color: #376f91; }} .chance.c2 {{ color: #8a6412; }} .chance.c3 {{ color: #8a8f8c; }}
    .summary {{ margin: 6px 0 0; }}
    .label {{ margin: 8px 0 2px; color: var(--muted); font-size: 12px; font-weight: 700; }}
    .label-inline {{ display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 5px; background: #eef2ef; color: var(--muted); font-size: 11.5px; font-weight: 800; }}
    .years {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; margin: 0; padding: 0; list-style: none; font-size: 13.5px; }}
    .years li a, .years .era {{ display: inline-block; min-width: 2.6em; font-weight: 700; }}
    .old {{ margin: 8px 0 0; color: #4d5b55; font-size: 13.5px; }}
    .old-links {{ display: block; margin-top: 2px; font-size: 12px; }}
    .old-links a {{ margin-right: 6px; }}
    .tip {{ margin: 6px 0 0; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 2px; }}
    .action {{ padding: 5px 12px; border-radius: 8px; background: var(--deep); color: #fff; font-size: 13px; font-weight: 700; text-decoration: none; }}
    .action.ghost {{ background: #fff; color: var(--deep); border: 1px solid var(--line); }}
    .page-foot {{ padding: 24px 0 40px; color: var(--muted); font-size: 12.5px; }}
    @media (max-width: 900px) {{ .overview {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }} }}
    @media (max-width: 600px) {{
      .overview {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .years {{ grid-template-columns: minmax(0, 1fr); }}
      .chance {{ margin-left: 0; }}
    }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">本文へ移動</a>
  <header class="architecture-hero">
    <div class="hero-inner">
      <nav><a href="../index.html">← 過去問ラボへ戻る</a><a href="../mock-exam/">通し演習 →</a></nav>
      <p class="eyebrow">BY QUESTION NUMBER</p>
      <h1>問番号ごとの出題傾向</h1>
      <p class="lead">行政書士試験は、問番号ごとに出る科目と分野がほぼ決まっています。平成18〜令和7年度の20年分（1,139問）を問番号ごとに読み、問1〜60のそれぞれでよく出るテーマを一覧にしました。</p>
    </div>
  </header>

  <main id="main-content">
    <section class="section" id="conclusion">
      <div class="section-heading">
        <p class="kicker">CONCLUSION</p>
        <h2>結論</h2>
      </div>
      <div class="box conclusion">
        <ul>
          <li><strong>問1〜57の科目は、20年間1年も動いていません。</strong>問番号を見れば科目が分かります。</li>
          <li><strong>行政法は平成28年度から並びが1つずれました。</strong>新しい行政不服審査法の施行で、行政不服審査法が2問から3問（問14〜16）に増え、行政事件訴訟法が問17〜19、国家賠償が問20〜21、地方自治法が問22〜24になりました。平成27年度以前の問番号は、いまの番号と1つずれています。</li>
          <li><strong>毎年同じテーマが出る番号：</strong>問11〜13（行政手続法）、問14〜16（行政不服審査法）、問17〜19（行政事件訴訟法）、問20〜21（国家賠償）、問27（民法総則）、問35（家族法）、問37（会社の設立。直近10年で9回）、問57（個人情報保護）。</li>
          <li><strong>テーマが読めない番号：</strong>問5（憲法の人権と統治が半々）、問8〜10（行政法総論が3問の間で入れ替わる）、問25〜26（行政法の総合問題）、問40、問49〜51（一般知識）。</li>
          <li>令和6年度から、基礎知識の問52〜54あたりに諸法令（行政書士法・住民基本台帳法・戸籍法）が1〜2問入っています。</li>
        </ul>
      </div>
    </section>

    <section class="section" id="overview">
      <div class="section-heading">
        <p class="kicker">ALL 60</p>
        <h2>問1〜60の早見表</h2>
        <p>押すと、その番号の詳しい説明へ移ります。右上の記号は、取れそうかどうかの予想です（下の「得点の予想」）。</p>
      </div>
      {overview}
      <div class="legend"><span>◎ 取れる（75%以上）</span><span>○ 五分より上（55%以上）</span><span>△ 五分以下（35%以上）</span><span>× ほぼ勘</span></div>
    </section>

    <section class="section" id="forecast">
      <div class="section-heading">
        <p class="kicker">FORECAST</p>
        <h2>得点の予想（ついで）</h2>
        <p>時間をかけずに付けた粗い予想です。問番号ごとの取れる見込みは、得点設計の科目別の見込み（悪い日と良い日の間）と、今年の重点・捨てる分野の方針から決めました。回答履歴はまだ少ないので、使っていません。</p>
      </div>
      <div class="box forecast">
        <p><strong>期待値は約{total}点</strong>（法令等 約{legal}点／基礎知識 約{general}点）。足切りの法令等122点・基礎知識24点は越えますが、合格点の180点には届かない見込みで、得点設計の「165〜190点のうち上振れを引いたら合格」とそろっています。</p>
        <p>上振れの元は、記述（期待値26点。得点設計では悪い日16点〜良い日40点）と、行政法の択一（問11〜21の11問を取り切れるか）です。地方自治法・家族法・商法（問37以外）は、勉強せずに形だけで解く前提で数えています。</p>
      </div>
      <table class="score">
        <thead><tr><th>区分</th><th>問番号</th><th class="num">期待点</th><th class="num">満点</th></tr></thead>
        <tbody>
{score_rows}
        </tbody>
        <tfoot><tr><td>合計</td><td></td><td class="num">{total}</td><td class="num">300</td></tr></tfoot>
      </table>
      <p class="note">問58〜60（文章理解）はラボに収録していないので、一般的な取りやすさで75%としました。</p>
    </section>

{sections}

    <p class="page-foot">2026-11-05作成。テーマは過去問の問題文を読んで付けた名前で、問題文や取得元の解説は載せていません。年度のリンクは合格道場の問題ページです。データの正本は scripts/question_slots/slots_data.py。</p>
  </main>
</body>
</html>
"""


def slots_json():
    """ほかのページ（本試験形式の練習・法理・法原則）が問番号からテーマを引くための写し。"""
    names = {key: name for name, key, _, _ in D.GROUPS}
    data = {str(n): {"group": names[s["group"]], "title": s["title"], "fix": s["fix"], "tip": s["tip"]}
            for n, s in sorted(D.S.items())}
    return json.dumps({"note": "scripts/question_slots/build.py --write で書き出す。平成28年度以降の並び。", "slots": data},
                      ensure_ascii=False, indent=1) + "\n"


def main():
    text = build()
    data = slots_json()
    if "--write" in sys.argv:
        OUT.write_text(text)
        OUT_JSON.write_text(data)
        print("wrote", OUT, OUT_JSON)
    else:
        current = OUT.read_text() if OUT.exists() else ""
        current_json = OUT_JSON.read_text() if OUT_JSON.exists() else ""
        print("unchanged" if (current, current_json) == (text, data) else "differs (use --write)")
    rows, legal, general = score_rows()
    print(f"expected total {legal + general:.1f} (legal {legal:.1f}, general {general:.1f})")


if __name__ == "__main__":
    main()
