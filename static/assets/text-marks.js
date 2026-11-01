// 学習カードと同じ「色の意味」を、常識・テクニック・演習・マップの文章にも付ける共通スクリプト。
// 文章データは書き換えず、表示のときに規則で色を付ける（2026-11-01に利用者が依頼）。
// データに ==結論== のようなカードの記法が書いてあれば、それを優先する。
// 色の意味はカードと同じ。青＝だれ、下線＝条件・期間、太字＝用語、黄＝結論、赤＝ひっかけ、灰＝根拠。
(function (root) {
  "use strict";

  const MARKUP = /(\*\*[^*\n]+\*\*|__[^_\n]+__|==[^=\n]+==|!![^!\n]+!!|%%[^%\n]+%%|@@[^@\n]+@@)/g;
  const KIND_BY_TOKEN = { "**": "strong", "__": "underline", "==": "mark", "!!": "warn", "%%": "key", "@@": "quiet" };

  // 青：だれ・立場。カードの %% で多く使っている語から選んだ。長い語を先に照合する。
  const WHO = [
    "個人情報保護委員会", "個人情報取扱事業者", "日本行政書士会連合会", "普通地方公共団体", "主たる債務者",
    "命令等制定機関", "内閣総理大臣", "都道府県知事", "地方公共団体", "無権代理人", "審査請求人", "善意の第三者",
    "上級行政庁", "簡易裁判所", "地方裁判所", "最高裁判所", "国会議員", "抵当権者", "行政書士", "市町村長",
    "取締役", "発起人", "株主総会", "保証人", "相手方", "第三者", "転得者", "転借人", "賃借人", "賃貸人",
    "譲受人", "譲渡人", "占有者", "所有者", "債権者", "債務者", "代理人", "買主", "売主", "貸主", "借主",
    "行政庁", "処分庁", "審査庁", "審理員", "裁判所", "申請者", "請求人", "原告", "被告", "主宰者", "参加人",
    "内閣", "国会", "議会", "住民", "知事", "市長", "大臣",
  ];

  // 太字：用語・制度。カードの ** で多く使っている語から選んだ。
  const TERMS = [
    "不作為の違法確認の訴え", "弁明の機会の付与", "背信的悪意者", "法定受託事務", "再調査の請求", "個人識別符号",
    "住民監査請求", "行政指導指針", "法律上の争訟", "発行可能株式総数", "取消訴訟", "無効確認訴訟", "当事者訴訟",
    "審査請求", "執行停止", "仮の差止め", "仮の義務付け", "義務付け訴訟", "差止め訴訟", "自治事務", "公の施設",
    "行政指導", "審査基準", "処分基準", "標準処理期間", "意見公募手続", "占有改定", "相続放棄", "譲渡担保",
    "根抵当権", "住民訴訟", "指定管理者", "即時強制", "代執行", "執行罰", "直接強制", "訴えの利益", "原告適格",
    "処分性", "公定力", "不可争力", "委任命令", "執行命令", "立法事実", "欠格事由", "催告", "追認", "時効", "取得時効",
    "消滅時効", "聴聞", "定款", "商行為", "信義則", "表見代理", "無権代理", "即時取得", "遺留分", "代襲相続",
    "国家賠償", "損失補償", "再議", "法律の留保", "比例原則", "違法性の承継",
  ];

  // 黄：結論。文末の述語だけを見る（日本語は結論が文末に来るので、文中の語に色を付けて賑やかにしない）。
  const CONCLUSION = new RegExp(
    "(?:" + [
      "(?:請求|主張|対抗|行使|解除|提起|取消し|撤回|追認|相殺|利用|申請|審理|選任|設立|決定)?(?:することが)?(?:でき(?:ません|ない|ます|る)|できなくなり(?:ます)?)",
      "取り消せ(?:ません|ない|ます|る)|取り消すことができ(?:ません|ない|ます|る)|争え(?:ません|ない|ます|る)|請求でき(?:ません|ない|ます|る)|認め(?:られ)?(?:ません|ない|ます|る)|認められません",
      "無効(?:です|である|となります|になります|となる|になる)?|有効(?:です|である|となります|になります|となる|になる)?",
      "違憲(?:です|である|となります|になります)?|合憲(?:です|である|となります|になります)?|違法(?:です|である|となります|になります)?|適法(?:です|である)?",
      "不要(?:です|である)?|必要(?:です|である|ありません)?|要りません|要らない",
      "しなければなりません|しなければならない|してはなりません|してはならない|努力義務(?:です|である|にとどまります|にとどまる)?|義務(?:です|である|があります|がある|はありません|はない)",
      // 上の語に当たらない否定の文末（作れません、決まりません、ありません）も結論として扱う。
      "[一-龠々]+[ぁ-ん]{0,4}(?:ません|ない)|(?:では|には|でも)?ありません|(?:では)?ない",
      "(?:適用|準用)(?:されません|されない|されます|される)|含まれ(?:ません|ない|ます|る)|当たり(?:ません|ます)|当たらない|当たる|限られ(?:ません|ます)|限られる|限りません|足ります|足りません|足りる|免れ(?:ません|ます|る)|負い(?:ません|ます)|負わない|負う|成立(?:しません|します|しない|する)|許され(?:ません|ます)|許されない|許される|誤りです|正しい",
    ].join("|") + ")"
  );

  const QUIET_PATTERNS = [
    // 括弧ごと灰色にする条文・判例の根拠。
    /（[^（）\n]{0,40}(?:\d+条|同条|同項|判決|判例|最判|最決|大判|事件|\d+号|ただし書)[^（）\n]{0,24}）/g,
    /(?:最高裁|最大判|最判|最決|大判|大決)(?:大法廷)?(?:昭和|平成|令和|明治|大正)?\d*(?:元)?年?\d*月?\d*日?(?:判決|決定)?/g,
    /(?:令和|平成)(?:\d+|元)年(?:度)?問\d+(?:肢[0-9０-９アイウエオ]+)?/g,
    /(?:日本国憲法|憲法|民法|商法|会社法|行政手続法|行政不服審査法|行政事件訴訟法|国家賠償法|地方自治法|行政代執行法|個人情報保護法|情報公開法|行政書士法|住民基本台帳法|戸籍法|公文書管理法|内閣法|国会法|裁判所法)?\d+条(?:の\d+)?(?:\d+項)?(?:\d+号)?(?:ただし書)?/g,
  ];

  const WARN = /(?:ひっかけ|取り違え(?:やすい|ます|る)?|間違え(?:やすい|ます|る)?|逆(?:に)?(?:すると|なります|になる)|誤解|混同(?:しやすい|します|しない)?)/g;

  // 下線：条件・期間。数字＋単位と、善意悪意などの主観要件、「〜ときは」「〜場合に限り」までの短い節。
  const CONDITION = [
    /(?:(?<![和成治正元月\d])(?:\d+|[一二三四五六七八九十百]+)(?:か月|カ月|ヶ月|箇月|年|週間|日|時間|分の\d+|分の[一二三四五六七八九]|割|%)(?:以内|以上|以下|未満|超|を超える|前|後|間)?|(?<![和成治正元月\d])(?:\d+|[一二三四五六七八九十百]+)(?:人|名)(?:以上|以内|以下|未満)|過半数|半数以上|全員の同意|全員一致)/g,
    /(?:善意無過失|善意かつ無過失|悪意または重大な過失|故意または重大な過失|重大な過失|重過失|無過失|善意|悪意)/g,
    /[^、。（）「」\n]{2,16}(?:場合に限り|場合に限って|ときに限り|ときに限って|がなくても|であっても)/g,
  ];

  const LIMITS = { key: 2, strong: 3, underline: 4, warn: 2, mark: 3 };

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function wordPattern(words) {
    const sorted = [...words].sort((a, b) => b.length - a.length).map(escapeRegExp);
    return new RegExp(sorted.join("|"), "g");
  }

  const WHO_PATTERN = wordPattern(WHO);
  const TERM_PATTERN = wordPattern(TERMS);

  // まだ色の付いていない範囲だけを埋める。早く置いたものが勝つ。
  function place(spans, start, end, kind) {
    if (end <= start) return false;
    if (spans.some((span) => start < span.end && span.start < end)) return false;
    spans.push({ start, end, kind });
    return true;
  }

  function collect(text, options) {
    const spans = [];
    const counts = {};
    const within = (kind) => (counts[kind] || 0) < LIMITS[kind];
    const add = (start, end, kind) => {
      if (kind !== "quiet" && !within(kind)) return;
      if (place(spans, start, end, kind)) counts[kind] = (counts[kind] || 0) + 1;
    };

    QUIET_PATTERNS.forEach((pattern) => {
      for (const match of text.matchAll(pattern)) add(match.index, match.index + match[0].length, "quiet");
    });

    if (options.warn !== false) {
      for (const match of text.matchAll(WARN)) add(match.index, match.index + match[0].length, "warn");
    }

    // 文ごとに、文末の述語を1つだけ黄色にする。事例文（options.mark === false）は事実を書いた文なので塗らない。
    const sentence = /[^。\n]+[。]?/g;
    for (const match of options.mark === false ? [] : text.matchAll(sentence)) {
      // 文末の根拠（31条1項）は飛ばして、その前の述語を見る。
      const body = match[0].replace(/[。]$/, "").replace(/（[^（）]*）$/, "").replace(/[」）)]+$/, "");
      const tail = new RegExp("(?:" + CONCLUSION.source + ")$").exec(body);
      if (tail && tail[0]) add(match.index + tail.index, match.index + tail.index + tail[0].length, "mark");
    }

    CONDITION.forEach((pattern) => {
      for (const match of text.matchAll(pattern)) add(match.index, match.index + match[0].length, "underline");
    });

    // 用語とだれは、同じ語をくり返し塗らない（最初の1回だけ）。
    if (options.terms !== false) {
      [[TERM_PATTERN, "strong"], [WHO_PATTERN, "key"]].forEach(([pattern, kind]) => {
        const seen = new Set();
        for (const match of text.matchAll(pattern)) {
          if (seen.has(match[0])) continue;
          seen.add(match[0]);
          add(match.index, match.index + match[0].length, kind);
        }
      });
    }
    return spans.sort((a, b) => a.start - b.start);
  }

  // 素の文字列を [{text, kind}] に分ける。kind が無い部分は色なし。DOMを使わないのでテストできる。
  function segments(value, options) {
    const opts = options || {};
    const source = String(value === undefined || value === null ? "" : value);
    const output = [];
    const plain = (text) => {
      if (!text) return;
      if (opts.plain) { output.push({ text }); return; }
      let cursor = 0;
      collect(text, opts).forEach((span) => {
        if (span.start > cursor) output.push({ text: text.slice(cursor, span.start) });
        output.push({ text: text.slice(span.start, span.end), kind: span.kind });
        cursor = span.end;
      });
      if (cursor < text.length) output.push({ text: text.slice(cursor) });
    };
    let last = 0;
    for (const match of source.matchAll(MARKUP)) {
      plain(source.slice(last, match.index));
      output.push({ text: match[0].slice(2, -2), kind: KIND_BY_TOKEN[match[0].slice(0, 2)] });
      last = match.index + match[0].length;
    }
    plain(source.slice(last));
    return output;
  }

  const TAGS = {
    strong: ["strong", "mk-strong"],
    underline: ["span", "mk-underline"],
    mark: ["mark", "mk-mark"],
    warn: ["span", "mk-warn"],
    key: ["span", "mk-key"],
    quiet: ["span", "mk-quiet"],
  };

  function nodes(value, options) {
    return segments(value, options).map((part) => {
      if (!part.kind) return document.createTextNode(part.text);
      const [tag, className] = TAGS[part.kind];
      const node = document.createElement(tag);
      node.className = className;
      node.textContent = part.text;
      return node;
    });
  }

  function append(target, value, options) {
    target.append(...nodes(value, options));
    return target;
  }

  // HTMLに直接書いた本文（テクニックのページ）用。リンクや既に強調した語の中は触らない。
  function decorate(rootNode, selector, options) {
    const skip = "a, button, code, strong, b, mark, .star-badge, .low, .high, .mk-strong, .mk-underline, .mk-mark, .mk-warn, .mk-key, .mk-quiet, [data-field]";
    rootNode.querySelectorAll(selector).forEach((block) => {
      const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
      const found = [];
      while (walker.nextNode()) {
        const parent = walker.currentNode.parentElement;
        if (walker.currentNode.nodeValue.trim() && !(parent && parent.closest(skip))) found.push(walker.currentNode);
      }
      found.forEach((text) => text.replaceWith(...nodes(text.nodeValue, options)));
    });
  }

  // カードの「色の意味」と同じ説明。開閉式にして、読むときの邪魔にしない。
  function legend() {
    const box = document.createElement("details");
    box.className = "markup-legend";
    box.innerHTML = '<summary>色の意味</summary>'
      + '<p>青（だれ）→ 下線（条件）→ 黄色（結論）の順に目で追うと、文の意味を組み立てられます。色は規則で付けているので、付いていない大事な語もあります。</p>'
      + '<ul>'
      + '<li><span class="mk-key">保証人</span> だれ・立場</li>'
      + '<li><span class="mk-underline">1か月以上</span> 条件・期間</li>'
      + '<li><strong class="mk-strong">追認</strong> 用語・制度</li>'
      + '<li><mark class="mk-mark">取り消せない</mark> 結論</li>'
      + '<li><span class="mk-warn">ひっかけ</span> ひっかけ</li>'
      + '<li><span class="mk-quiet">民法145条</span> 根拠・補足</li>'
      + '</ul>';
    return box;
  }

  const api = { segments, nodes, append, decorate, legend };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.TextMarks = api;
})(typeof window !== "undefined" ? window : globalThis);
