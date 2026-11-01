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
    "個人情報保護委員会", "個人情報取扱事業者", "日本行政書士会連合会", "普通地方公共団体", "主たる債務者", "連帯保証人",
    "命令等制定機関", "内閣総理大臣", "都道府県知事", "地方公共団体", "無権代理人", "再審査請求人", "審査請求人", "善意の第三者",
    "上級行政庁", "簡易裁判所", "地方裁判所", "高等裁判所", "最高裁判所", "国会議員", "後順位抵当権者", "抵当権者", "行政書士(?!法|会)", "市町村長",
    "取締役会", "取締役(?!会)", "発起人", "株主総会", "保証人", "相手方", "第三者(?!提供|機関|効|性)", "転得者", "転借人", "賃借人", "賃貸人",
    "譲受人", "譲渡人", "占有者", "所有者", "債権者(?!代位|主義|平等|取消)", "債務者(?!主義)", "復代理人", "代理人", "買主", "売主", "貸主", "借主",
    "行政庁", "処分庁", "審査庁", "審理員", "裁判所(?!法)", "申請者", "請求人", "原告(?!適格)", "被告(?!人|適格)", "主宰者", "参加人",
    "内閣(?!府|法|不信任|提出|官房|総辞職)", "国会(?!法|議員)", "(?:都道府県|市町村|地方)?議会", "住民(?!票|基本|税|監査|訴訟|自治|投票|登録)", "知事", "市長",
    "(?:総務|主任の国務|国務|法務|財務|文部科学|厚生労働|農林水産|経済産業|国土交通|環境|防衛|外務|各|副)?大臣",
  ];

  // 太字：用語・制度。カードの ** で多く使っている語から選んだ。
  const TERMS = [
    "不作為の違法確認の訴え", "弁明の機会の付与", "背信的悪意者", "法定受託事務", "再調査の請求", "個人識別符号",
    "住民監査請求", "行政指導指針", "法律上の争訟", "発行可能株式総数", "取消訴訟", "無効確認訴訟", "当事者訴訟",
    "審査請求(?!人)", "執行停止", "仮の差止め", "仮の義務付け", "義務付け訴訟", "差止め訴訟", "自治事務", "公の施設",
    "行政指導", "審査基準", "処分基準", "標準処理期間", "意見公募手続", "占有改定", "相続放棄", "譲渡担保",
    "根抵当権", "住民訴訟", "指定管理者", "即時強制", "代執行(?!法)", "執行罰", "直接強制", "訴えの利益", "原告適格",
    "処分性", "公定力", "不可争力", "委任命令", "執行命令", "立法事実", "欠格事由", "催告", "追認拒絶", "追認", "時効", "取得時効",
    "消滅時効", "聴聞", "定款", "商行為", "信義則", "表見代理", "無権代理(?!人)", "即時取得", "遺留分", "代襲相続",
    "国家賠償(?!法)", "損失補償", "再議", "法律の留保", "比例原則", "違法性の承継",
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
      // 「ならない」「いえない」は仮名だけなので、「なら｜ない」と切れないよう先に拾う。
      "(?:[一-龠々]+[ぁ-ん]{0,2})?[一-龠々]+[ぁ-ん]{0,3}なければ(?:なりません|ならない)|なければ(?:なりません|ならない)|[一-龠々]+[ぁ-ん]{0,2}(?:ならない|なりません|いえない|いえません|言えない|言えません|されていない|されていません|していない|していません|しない|しません)|ならない|なりません|とはいえ(?:ない|ません)|されていない|されていません|していない|していません|しない|しません",
      // 上の語に当たらない否定の文末（作れません、決まりません、ありません）も結論として扱う。
      "[一-龠々]+[ぁ-ん]{0,4}(?:ません|ない)|(?:では|には|でも)?ありません|(?:では)?ない",
      // 肯定の法律効果。「です」「なります」だけの文は塗らない（どの文にも付いてしまう）。
      "生じ(?:ます|る)|及び(?:ます)|及ぶ|消滅(?:します|する)|失い(?:ます)|失う|取得(?:します|する)|優先(?:します|する)|保護され(?:ます|る)|違反(?:します|する)|反(?:します|する)|勝(?:ちます|つ|てます|てる)|確定(?:します|する)|却下(?:します|する|されます|される)|棄却(?:します|する|されます|される)|対象(?:外)?です|みなされ(?:ます|る)|(?:[一-龠々]+で)?決まり(?:ます)|(?:[一-龠々]+で)?決まる|除かれ(?:ます|る)|除外され(?:ます|る)|残り(?:ます)|残る|承継(?:します|する)|起こせ(?:ます|る)|使え(?:ます|る)|求められ(?:ます|る)",
      "(?:適用|準用)(?:されません|されない|されます|される)|含まれ(?:ません|ない|ます|る)|当たり(?:ません|ます)|当たらない|当たる|限られ(?:ません|ます)|限られる|限りません|足ります|足りません|足りる|免れ(?:ません|ます|る)|負い(?:ません|ます)|負わない|負う|成立(?:しません|します|しない|する)|許され(?:ません|ます)|許されない|許される|誤りです|正しい",
    ].join("|") + ")"
  );

  // 文末の否定でも、法律の結論ではないもの（読み方の助言、統計の説明、日常の言い回し）は黄色にしない。
  const NOT_CONCLUSION = new Set([
    "読まない", "読めない", "決めつけない", "分かりません", "分からない", "効かない", "効きません", "効いていません", "追わない", "追えない", "深追いしない", "追いかけない",
    "困りません", "困らない", "納得できません", "暇がありません", "手になりません", "増えません", "増えない", "増やしません", "偏りはありません", "上がりません", "差がありません",
    "傾きがありません", "見ていません", "混ぜません", "使いません", "使わない", "本人はたまりません", "迷惑をかけません", "気づけません", "見えません", "見られない", "見られません",
    "競争相手になりません", "得にもなりません", "少ない", "多くありません", "意味がありません", "意味がない", "合いません", "合わない", "回りません", "回さない", "減らさない", "直さない",
    "込まない", "訳にならない", "点にならない", "行かない", "下げない", "外さない", "外しません", "着きません", "負けません", "切りません", "数えない", "数えません", "予測できません", "区別できません",
    "対応できません", "保てません", "動けません", "終わらない", "引きずられない", "入りすぎない", "悪くしない", "見通しではない", "崩させない", "言わせません", "頼んでいません", "貸さない",
    "可能性がない", "方がない", "変わりません", "変わらない", "理由はありません", "理由がありません", "理由もありません", "関係ありません", "関係ない", "起きません", "起きない", "続けられません", "思い出せない", "取れない", "読みません", "書きません", "覚えません", "覚えない",
  ]);

  // 手で決めた色。規則では拾えない結論だけをここへ足す（文章そのものは変えない）。文中にこの語句があれば規則より先に塗る。
  // テストが「ページの本文に実在する語句か」を見ている。文章を直して語句が消えたら、ここも直す。
  const MANUAL = [
    // 法律家の常識
    ["何も知らずに信じた人を守ります", "mark"], ["呼び名ではなく実際の効果を見ます", "mark"],
    ["用途地域・水道料金の一律改定は処分でなく", "mark"], ["区画整理事業計画・特定の保育所の廃止は処分です", "mark"],
    ["著しく合理性を欠くか", "underline"], ["規則は5万円以下の過料だけ", "mark"], ["お金は持分の分だけです", "mark"],
    ["違法の原因がどちらにあるかを先に見ます", "mark"], ["学説の言葉を判例の立場として書いた肢を切ります", "mark"],
    ["法律で決めただけのことを憲法の要請にすり替えています", "mark"], ["公序良俗違反として無効になる形", "mark"],
    ["禁止や制限を明確に書きます", "mark"], ["学術研究は、令和3年改正で一律の除外から外れました", "mark"],
    ["学術研究機関は令和3年改正で一律の除外から外れました", "mark"],
    ["報道機関の報道、著述を業とする者の著述、宗教団体の宗教活動、政治団体の政治活動の4つ", "underline"],
    ["「意見はなかった」と公示します", "mark"], ["やめたことを公示します", "mark"], ["完了後も別の効果が続くなら残ります", "mark"],
    ["建築確認は消えます", "mark"], ["土地改良事業の認可は残ります", "mark"], ["請求者の訴えは続きます", "mark"],
    ["取消しを求める利益は残ります", "mark"], ["理由を後で示せます", "mark"], ["同時に理由を示します", "mark"],
    ["「公権力の行使」のほうが広く", "mark"], ["著しく合理性を欠くときだけです", "mark"], ["Cは出ていきます", "mark"],
    ["Cは住み続けられます", "mark"], ["苫米地事件（衆議院の解散）には留保がなく", "mark"],
    // 演習D案の「この章で作る法律家の常識」
    ["被害を受けた側の落ち度の大きさで決める", "mark"], ["不利な契約から守る", "mark"], ["催告などの出口も用意する", "mark"],
    ["本人へ押し付けず", "mark"], ["状況に応じた出口を残す", "mark"], ["登記のルールに従わせる", "mark"],
    ["2年間の回復ルートを残す", "mark"], ["権利の中身を問わず占有の訴えで守る", "mark"], ["守る行為は一人でも", "mark"],
    ["物の姿を大きく変えることは全員の合意で決める", "mark"], ["建物の存立を守る", "mark"], ["引渡しは占有改定で足り", "mark"],
    ["不意打ちの負担を負わないようにする", "mark"], ["知らないうちに不利にならないようにする", "mark"], ["条文の言葉で言い分けられるようにする", "mark"],
    ["身分に関わる行為には立ち入りすぎない", "mark"], ["書面で慎重に決めた後は、約束を守らせる", "mark"], ["その時点で物を支配していた側に寄せる", "mark"],
    ["生活の基盤を守りつつ", "mark"], ["何を約束したかに合わせて", "mark"], ["確実に賠償を受けられるようにしつつ", "mark"],
    ["影響を受ける人の利益が置き去りにならないようにする", "mark"], ["近親者の最低限と家系の連続を、必要な範囲だけ守る", "mark"], ["取り消されるまでは有効", "mark"],
    ["重大で明白な誤りは初めから無効", "mark"], ["一般法は代執行だけ", "mark"], ["個別の法律が要る", "mark"],
    ["義務と努力義務を分けている", "mark"], ["処分が重いほど手続を厚く", "mark"], ["入口で迷った人（教示がない・間違っていた）は救い", "mark"],
    ["判例の線引きを事案の形で覚える", "mark"], ["訴えても処分は止まらないのが原則", "mark"], ["裁判所の決定が要る", "mark"],
    ["使う場面を絞っている", "mark"], ["公務員個人には直接請求させず", "mark"], ["ひどい場合だけ後から求償する", "mark"],
    ["住民監査請求は一人でもできる", "mark"], ["住民訴訟は監査を先に通させる", "mark"], ["法律・政令に根拠があるときだけ", "mark"],
    ["直接ではなく民法の一般条項を通して効かせる", "mark"], ["合理的な理由のない区別を禁じる相対的な平等", "mark"], ["かかわり合いが相当な限度を超えるかで判断する", "mark"],
    ["裁量が広い", "mark"], ["制限には厳しい", "mark"], ["迅速さが要るものは衆議院を優越させ", "mark"],
    ["法律を当てはめて解決できる具体的な争い", "mark"], ["お金以外の出資は発起人だけができ", "mark"], ["免除できるのは株主全員が同意したときだけ", "mark"],
    ["任務を怠ったと推定する", "mark"], ["都道府県知事が懲戒する", "mark"], ["登録は日本行政書士会連合会が管理する", "mark"],
    ["独立した個人情報保護委員会が監督する", "mark"], ["市町村が住民票と戸籍で正確に管理する", "mark"],
    // 絞り込みテクニック
    ["まず主語と相手、次に文末の向きを見ます", "mark"], ["数字の入れ替えはほとんどありませんでした", "mark"],
    ["条件を外す使い方のときに疑ってください", "mark"], ["効くのは憲法・民法・商法だけ", "underline"],
    ["基礎知識では最も短い肢を外してはいけません", "mark"], ["処分をした行政庁が所属する国や公共団体です", "mark"],
    ["自信のない肢は切らずに△にしてください", "mark"], ["この1語があれば肢を切れます", "mark"],
    ["必ずそちらを優先してください", "mark"], ["空欄ごとの候補が5語ずつ混ざっている", "mark"],
    ["法令択一95分・基礎知識40分・多肢選択15分・記述25分・見直し5分", "underline"],
  ];

  const QUIET_PATTERNS = [
    // 括弧ごと灰色にする条文・判例の根拠。
    /（[^（）\n]{0,40}(?:\d+条|同条|同項|判決|判例|最判|最決|大判|事件|\d+号|ただし書)[^（）\n]{0,24}）/g,
    /(?:最高裁|最大判|最判|最決|大判|大決)(?:大法廷)?(?:昭和?|平成?|令和?|明治?|大正?)?(?:元|\d+)?[年.]?\d*[月.]?\d*日?(?:判決|決定)?/g,
    /同(?:条|法\d+条)(?:\d+項)?(?:\d+号)?(?:ただし書)?|同項(?:ただし書)?/g,
    /(?:令和|平成)(?:\d+|元)年(?:度)?問\d+(?:肢[0-9０-９アイウエオ]+)?/g,
    /(?:日本国憲法|憲法|民法|商法|会社法|行政手続法|行政不服審査法|行政事件訴訟法|国家賠償法|地方自治法|行政代執行法|個人情報保護法|情報公開法|行政書士法|住民基本台帳法|戸籍法|公文書管理法|内閣法|国会法|裁判所法)?\d+条(?:の\d+)?(?:\d+項)?(?:\d+号)?(?:ただし書)?/g,
  ];

  const WARN = /(?:ひっかけ|取り違え(?:やすい|ます|る)?|間違え(?:やすい|ます|る)?|逆(?:に)?(?:すると|なります|になる)|誤解|混同(?:しやすい|します|しない)?)/g;

  // 下線：条件・期間。数字＋単位と、善意悪意などの主観要件、「〜ときは」「〜場合に限り」までの短い節。
  // 「過去問20年で」「直近10年のうち9年」は統計なので塗らない。西暦（2026年）も塗らない。
  const CONDITION = [
    /(?:(?<![和成治正元月\d]|直近|過去|旧|新|うち)(?:\d{1,3}|[一二三四五六七八九十百]+)(?:か月|カ月|ヶ月|箇月|年(?![分度連中]|で|のうち)|週間|日|時間|分の\d+|分の[一二三四五六七八九]|割|%)(?:以内|以上|以下|未満|超|を超える|前|後|間)?|(?<![和成治正元月\d])(?:\d+|[一二三四五六七八九十百]+)(?:人|名)(?:以上|以内|以下|未満)|過半数|半数以上|全員の同意|全員一致)/g,
    /(?:善意無過失|善意かつ無過失|悪意または重大な過失|故意または重大な過失|重大な過失|重過失|無過失|善意|(?<!背信的)悪意)/g,
    /(?<=^|[、。（「」）：\s])[^、。（）「」\n]{2,24}(?:場合に限り|場合に限って|ときに限り|ときに限って|がなくても|であっても)/g,
    /(?:場合|とき)に限(?:り|って)/g,
  ];

  const LIMITS = { key: 2, strong: 3, underline: 4, warn: 2, mark: 3 };

  // 語の一覧には「内閣府」を「内閣」と塗らないための先読みが入っているので、正規表現のまま繋ぐ。
  function wordPattern(words) {
    return new RegExp([...words].sort((a, b) => b.length - a.length).join("|"), "g");
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

    MANUAL.forEach(([phrase, kind]) => {
      if (kind === "mark" && options.mark === false) return;
      let index = text.indexOf(phrase);
      while (index >= 0) {
        place(spans, index, index + phrase.length, kind);
        index = text.indexOf(phrase, index + phrase.length);
      }
    });

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
      if (tail && tail[0] && !NOT_CONCLUSION.has(tail[0])) {
        // 「遺言はできない」は主語まで塗らず、述語だけにする。
        const trim = /^[一-龠々]+[はをもが](?=でき)/.exec(tail[0]);
        const start = match.index + tail.index + (trim ? trim[0].length : 0);
        add(start, match.index + tail.index + tail[0].length, "mark");
      }
    }

    // 解説の冒頭（「○。」「×。」で始まる文）は、必ずどこかを結論として塗る。規則に当たらなければ1文目の最後の節を塗る。
    if (options.mark !== false && /^[○×]。/.test(text) && !spans.some((span) => span.kind === "mark")) {
      const first = /^[○×]。([^。\n]+)。?/.exec(text);
      if (first) {
        const body = first[1].replace(/（[^（）]*）$/, "");
        const clause = body.slice(body.lastIndexOf("、") + 1);
        const start = 2 + body.length - clause.length;
        if (clause.length >= 2 && clause.length <= 36) add(start, start + clause.length, "mark");
      }
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
      + '<li><span class="mk-underline">1か月以上</span> 条件・期間・数字</li>'
      + '<li><strong class="mk-strong">追認</strong> 用語・制度</li>'
      + '<li><mark class="mk-mark">取り消せない</mark> 結論</li>'
      + '<li><span class="mk-warn">ひっかけ</span> ひっかけ</li>'
      + '<li><span class="mk-quiet">民法145条</span> 根拠・補足</li>'
      + '</ul>';
    return box;
  }

  const api = { segments, nodes, append, decorate, legend, MANUAL };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.TextMarks = api;
})(typeof window !== "undefined" ? window : globalThis);
