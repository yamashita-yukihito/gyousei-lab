(function () {
  "use strict";

  const TABS = ["how", "skip", "word", "length", "format", "no", "plan"];
  const ALL = "all";
  // word-signals.json に criteria が無い古い版のときの既定値。正本は scripts/simple_rules/measure.py の MIN_COUNT・MIN_LIFT。
  const DEFAULT_CRITERIA = { minCount: 10, minLift: 5 };

  function showTab(name, remember) {
    TABS.forEach((tab) => {
      document.getElementById(tab + "-section").hidden = tab !== name;
      document.getElementById(tab + "-tab").setAttribute("aria-selected", String(tab === name));
    });
    // 予定表などから「technique/#format」のように直接開けるよう、選んだタブをURLに残す。
    if (remember && window.history && history.replaceState) history.replaceState(null, "", "#" + name);
  }

  function hashTab() {
    const name = location.hash.replace(/^#/, "");
    return TABS.includes(name) ? name : null;
  }

  TABS.forEach((tab) => {
    document.getElementById(tab + "-tab").addEventListener("click", () => showTab(tab, true));
  });
  // 「本文へ移動」（#main-content）のようなタブ以外の # では、開いているタブを変えない。
  window.addEventListener("hashchange", () => {
    const name = hashTab();
    if (name) showTab(name, false);
  });
  showTab(hashTab() || "how", false);

  // HTMLに直接書いた本文へ、カードと同じ色を付ける。語の表の数字（.rate など）は対象にしない。
  TextMarks.decorate(document, ".tip-card > p, .tip-part p, .note-card p, .note-card li, .plain, .numbered li, .stack-table td:not(.num):not(.low):not(.high)");
  document.querySelector(".page-tabs").after(TextMarks.legend());

  // スマホでは表を縦に積むので、各欄に見出しの名前を持たせる。
  document.querySelectorAll(".stack-table").forEach((table) => {
    const names = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      [...row.children].forEach((cell, index) => {
        if (index > 0 && names[index]) cell.dataset.label = names[index];
      });
    });
  });

  // 旧10年・新10年の両方で、その期間の同じ問題の平均より minLift ポイント以上同じ向きに傾き、
  // minCount 肢以上ある語だけを本表に出す。measure.py が stable を書いていればそれを使い、
  // 期間ごとの平均が無い古いJSONでは全体の平均で代わりに比べる（理由の言い方は measure.py と同じ）。
  function stability(rule, criteria) {
    if (typeof rule.stable === "boolean") return { stable: rule.stable, reason: rule.unstableReason || "" };
    const reasons = [];
    if (rule.count < criteria.minCount) reasons.push(rule.count + "肢しかない");
    [["旧10年", rule.early, rule.earlyBase], ["新10年", rule.late, rule.lateBase]].forEach(([name, value, base]) => {
      const against = typeof base === "number" ? base : rule.base;
      if (typeof value !== "number") reasons.push(name + "に肢がない");
      else if (value < against) reasons.push(name + "では平均を下回る");
      else if (value - against < criteria.minLift) reasons.push(name + "では平均との差が小さい");
    });
    return { stable: !reasons.length, reason: reasons.join("・") };
  }

  function periodText(rule) {
    const part = (name, value, base) => {
      if (typeof value !== "number") return name + " 肢なし";
      return name + " " + value + "%" + (typeof base === "number" ? "（平均" + base + "%）" : "");
    };
    return part("旧10年", rule.early, rule.earlyBase) + " ／ " + part("新10年", rule.late, rule.lateBase);
  }

  function ruleCard(rule, check) {
    const card = document.createElement("article");
    card.className = "tip-card" + (check.stable ? "" : " reference");
    const title = document.createElement("h4");
    title.textContent = rule.label;
    const rate = document.createElement("div");
    rate.className = "rate lean-" + rule.direction;
    const value = document.createElement("strong");
    value.textContent = (rule.direction === "true" ? "正しい " : "誤り ") + rule.hit + "%";
    const lift = document.createElement("span");
    lift.className = "lift";
    const diff = rule.hit - rule.base;
    lift.textContent = "平均より" + (diff >= 0 ? "+" : "") + diff + "ポイント";
    const detail = document.createElement("span");
    detail.textContent = rule.count + "肢。同じ問題の平均は" + rule.base + "%";
    rate.append(value, lift, detail);
    const period = document.createElement("p");
    period.className = "period";
    period.textContent = periodText(rule);
    card.append(title, rate, period);
    if (!check.stable) {
      const why = document.createElement("p");
      why.className = "ref-why";
      why.textContent = "参考：" + check.reason;
      card.append(why);
    }
    const note = document.createElement("p");
    TextMarks.append(note, rule.note);
    card.append(note);
    return card;
  }

  function grid(entries) {
    const box = document.createElement("div");
    box.className = "tip-grid";
    entries.forEach((entry) => box.append(ruleCard(entry.rule, entry.check)));
    return box;
  }

  function renderWords(rules, criteria) {
    const root = document.getElementById("word-list");
    const names = [];
    rules.forEach((rule) => {
      if (!names.includes(rule.subject)) names.push(rule.subject);
    });
    names.forEach((name) => {
      const group = document.createElement("section");
      group.className = "subject-group";
      group.dataset.subject = name;
      const list = rules.filter((rule) => rule.subject === name).map((rule) => ({ rule, check: stability(rule, criteria) }));
      const main = list.filter((entry) => entry.check.stable);
      const reference = list.filter((entry) => !entry.check.stable);
      const heading = document.createElement("h3");
      heading.textContent = (name === "全科目" ? "全科目共通" : name) + "（" + main.length + "）";
      group.append(heading);
      if (main.length) {
        group.append(grid(main));
      } else {
        const none = document.createElement("p");
        none.className = "plain";
        none.textContent = "本表の条件を満たす語はありませんでした。";
        group.append(none);
      }
      if (reference.length) {
        const sub = document.createElement("h4");
        sub.className = "reference-head";
        sub.textContent = "参考：決め手にしない語（" + reference.length + "）";
        group.append(sub, grid(reference));
      }
      root.append(group);
    });
    const bar = document.getElementById("word-filter");
    const apply = (name) => {
      root.querySelectorAll(".subject-group").forEach((group) => {
        group.hidden = name !== ALL && group.dataset.subject !== name;
      });
      bar.querySelectorAll(".filter-button").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.subject === name));
      });
    };
    [ALL].concat(names).forEach((name) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter-button";
      button.dataset.subject = name;
      button.textContent = name === ALL ? "すべて" : name === "全科目" ? "全科目共通" : name;
      button.addEventListener("click", () => apply(name));
      bar.append(button);
    });
    apply(ALL);
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node && value !== null && value !== undefined) node.textContent = String(value);
  }

  function renderSimulation(simulation) {
    setText("sim-accuracy", simulation.accuracy);
    setText("sim-signal", simulation.withSignal);
    const pairs = simulation.pairs;
    if (pairs && typeof pairs.decided === "number" && typeof pairs.accuracy === "number") {
      setText("pair-decided", pairs.decided);
      setText("pair-accuracy", pairs.accuracy);
      document.getElementById("pair-note").hidden = false;
    }
  }

  // 「効かなかった手」の割合も measure.py が数えた値で置き換える。JSONに無ければ手で書いた値のまま出す。
  function renderNoEffect(entries) {
    (entries || []).forEach((entry) => {
      document.querySelectorAll('[data-no="' + entry.id + '"]').forEach((node) => {
        node.querySelectorAll("[data-field]").forEach((field) => {
          const value = entry[field.dataset.field];
          if (typeof value === "number") field.textContent = String(value);
        });
        node.hidden = false;
      });
    });
  }

  // 6つの壊し方の説明に、同じ作り方の×のカードをまとめて解く入口を付ける（AGENTS.md §5.9）。読めなければリンクが出ないだけ。
  fetch("../assets/card-hints.json", { cache: "no-store" }).then((res) => {
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }).then((data) => {
    const ids = {};
    Object.entries(data.cards || {}).forEach(([id, hint]) => {
      if (hint.break) (ids[hint.break.kind] = ids[hint.break.kind] || []).push(id);
    });
    document.querySelectorAll(".tip-card").forEach((card) => {
      const title = card.querySelector("h4");
      const entry = Object.entries(data.kinds || {}).find(([, kind]) => title && kind.label === title.textContent.trim());
      if (!entry || !ids[entry[0]]) return;
      const link = document.createElement("a");
      link.className = "solve-link";
      link.href = "../?cardIds=" + ids[entry[0]].join(",") + "&from=technique#study";
      link.textContent = "この形のカードを解く（" + ids[entry[0]].length + "枚）→";
      card.append(link);
    });
  }).catch((error) => console.warn("card-hints.json を読めませんでした", error));

  // 勉強しない3分野。数字は scripts/simple_rules/skip_fields.py が過去問から数えた skip-fields.json だけから読む。
  function range(value) {
    const low = Math.min(value.oldToNew, value.newToOld);
    const high = Math.max(value.oldToNew, value.newToOld);
    return low === high ? low + "%" : low + "〜" + high + "%";
  }

  function periodCell(value) {
    return "旧10年 " + value.old + "% ／ 新10年 " + value.new + "%";
  }

  const SKIP_FIELDS = [
    {
      key: "local",
      title: "地方自治法（問22〜24）",
      steps: (f) => [
        ["番号で塗る", "何も判定できなければ<5番>を塗ります。「正しい／誤っているもの」の問題で" + f.singleNumbers["5"].rate + "%です。行政法の他の分野にこの偏りはなく、理由は説明できません。偶然の可能性もありますが、勘で塗るなら損はありません。"],
        ["語で覆さない", "語で選んでも別の10年で試すと" + range(f.outOfSample.single) + "で、5番に届きません。語は時間が余ったときの補助です。"],
        ["組合せは1肢だけ判定", "語で組合せを選んでも" + range(f.outOfSample.combo) + "で、効きません。知っている肢が無ければ、ここも5番を塗ります。"],
      ],
      methods: (f) => [
        ["5番を塗る（正しい／誤りの問題）", f.singleNumbers["5"], "使う"],
        ["5番を塗る（組合せなども含む全部）", f.numbers["5"], "使う"],
        ["最も長い肢（誤っているものの問題）", f.longestFalse, "使わない。問題が少なく、新10年で落ちます"],
        ["最も長い肢（正しいものの問題）", f.longestTrue, "使わない"],
      ],
    },
    {
      key: "family",
      title: "家族法（問35）",
      steps: (f) => [
        ["組合せは知識で1肢", "22問中" + f.formats.combo + "問が組合せです。民法演習D案の家族法の章の「勘で解くコツ」にある知識を1つ使い、選択肢を減らします。"],
        ["語は当てにしない", "語で選んでも別の10年で試すと、正しい／誤りの問題で" + range(f.outOfSample.single) + "、組合せで" + range(f.outOfSample.combo) + "です。問題が少なく、語で解ける分野ではありません。"],
        ["番号で塗る", "何も分からなければ<3番>を塗ります。" + f.numbers["3"].rate + "%ですが、22問しかないので目安です。"],
      ],
      methods: (f) => [
        ["3番を塗る", f.numbers["3"], "他に手がなければ使う（22問だけの目安）"],
      ],
    },
    {
      key: "company",
      title: "商法と、問37以外の会社法（問36・38〜40）",
      steps: (f) => [
        ["語で選ぶ", "3分野でいちばん語が効きます。「誤っているもの」の問題は誤り寄りの語がある肢を選び、「正しいもの」の問題は誤り寄りの語がある肢を切って、正しい寄りの語がある肢を選びます。別の10年で試して" + range(f.outOfSample.single) + "でした。"],
        ["組合せも語で足し算", "ア〜オのうち正しい寄りの語を+1、誤り寄りの語を-1として、組合せごとに足します。別の10年で試して" + range(f.outOfSample.combo) + "でした。"],
        ["語で決まらなければ長さ", "「誤っているもの」の問題だけ最も長い肢を選びます（" + f.longestFalse.rate + "%）。「正しいもの」の問題で最も長い肢は" + f.longestTrue.rate + "%なので、選びません。"],
        ["最後は番号", "1番を外します（正しい／誤りの問題で" + f.singleNumbers["1"].rate + "%）。何も分からなければ<4番>を塗ります（" + f.numbers["4"].rate + "%）。問37が設立でない年は、その問題もここの手順で解きます。"],
      ],
      methods: (f) => [
        ["最も長い肢（誤っているものの問題）", f.longestFalse, "使う"],
        ["最も長い肢（正しいものの問題）", f.longestTrue, "使わない"],
        ["1番（正しい／誤りの問題）", f.singleNumbers["1"], "外す"],
        ["4番を塗る", f.numbers["4"], "最後の手段"],
      ],
    },
  ];

  function stepCard(number, heading, body) {
    const card = document.createElement("article");
    card.className = "tip-card step";
    const no = document.createElement("div");
    no.className = "step-no";
    no.textContent = number;
    const title = document.createElement("h4");
    title.textContent = heading;
    const text = document.createElement("p");
    // <5番> のように山かっこで囲んだ語だけを太字にする。
    body.split(/(<[^>]+>)/).forEach((part) => {
      if (/^<.+>$/.test(part)) {
        const strong = document.createElement("strong");
        strong.textContent = part.slice(1, -1);
        text.append(strong);
      } else if (part) {
        TextMarks.append(text, part);
      }
    });
    card.append(no, title, text);
    return card;
  }

  function methodTable(rows) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "tech-table stack-table";
    const names = ["手", "当たり", "10年ごと", "判断"];
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    names.forEach((name) => {
      const th = document.createElement("th");
      th.textContent = name;
      headRow.append(th);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    rows.forEach(([name, value, verdict]) => {
      const row = document.createElement("tr");
      [name, value.rate + "%（" + value.count + "問）", periodCell(value), verdict].forEach((cell, index) => {
        const td = document.createElement("td");
        td.textContent = cell;
        if (index > 0) td.dataset.label = names[index];
        row.append(td);
      });
      body.append(row);
    });
    table.append(head, body);
    wrap.append(table);
    return wrap;
  }

  function renderSkip(data) {
    const root = document.getElementById("skip-list");
    SKIP_FIELDS.forEach((def) => {
      const field = data.fields[def.key];
      const group = document.createElement("section");
      group.className = "subject-group";
      const heading = document.createElement("h3");
      heading.textContent = def.title;
      group.append(heading);
      const steps = document.createElement("div");
      steps.className = "tip-grid";
      def.steps(field).forEach(([title, body], index) => steps.append(stepCard(index + 1, title, body)));
      group.append(steps, methodTable(def.methods(field)));
      const wordHead = document.createElement("h3");
      wordHead.className = "subhead";
      wordHead.textContent = "語（両方の10年で同じ向きに傾いたものだけ）";
      group.append(wordHead);
      group.append(grid(field.words.map((rule) => ({ rule, check: { stable: true, reason: "" } }))));
      if (field.notWords.length) {
        const none = document.createElement("p");
        none.className = "plain";
        none.textContent = "傾きが確かめられなかった語（使いません）：" + field.notWords.join("、");
        group.append(none);
      }
      root.append(group);
    });
  }

  fetch("skip-fields.json?v=20261102-1").then((res) => {
    if (!res.ok) throw new Error("skip-fields.json: HTTP " + res.status);
    return res.json();
  }).then(renderSkip).catch((error) => {
    const message = document.getElementById("skip-error");
    message.hidden = false;
    message.textContent = "3分野の表を読み込めませんでした（" + error.message + "）。";
  });

  fetch("word-signals.json?v=20261031-4").then((res) => {
    if (!res.ok) throw new Error("word-signals.json: HTTP " + res.status);
    return res.json();
  }).then((data) => {
    const criteria = Object.assign({}, DEFAULT_CRITERIA, data.criteria || {});
    setText("crit-lift", criteria.minLift);
    setText("crit-count", criteria.minCount);
    renderSimulation(data.simulation);
    renderWords(data.rules, criteria);
    renderNoEffect(data.noEffect);
  }).catch((error) => {
    const message = document.getElementById("word-error");
    message.hidden = false;
    message.textContent = "語の表を読み込めませんでした（" + error.message + "）。";
  });
})();
