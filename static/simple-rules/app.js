(function () {
  "use strict";

  const FIELD = /^\s*(?:-\s+)?(概念|解説|具体例|過去問|カード|目安|理由|使い方)\s*::\s*(.*)$/;
  const EXAM = /^(.+?年問\d+)(?:肢([^○×]+))?([○×])?$/;
  const TABS = ["sense", "ok", "ng"];
  const ALL = "all";

  function parse(text) {
    const groups = { sense: [], ok: [], ng: [] };
    let table = null;
    let subject = null;
    let item = null;
    text.replace(/<!--[\s\S]*?-->/g, "").split("\n").forEach((line) => {
      const top = line.match(/^#\s+(sense|ok|ng)\s*$/);
      if (top) {
        table = groups[top[1]];
        subject = null;
        item = null;
        return;
      }
      const sub = line.match(/^##\s+(.+)$/);
      if (sub && table) {
        subject = { name: sub[1].trim(), items: [] };
        table.push(subject);
        item = null;
        return;
      }
      const field = line.match(FIELD);
      if (!field || !subject) return;
      if (field[1] === "概念") {
        item = {};
        subject.items.push(item);
      }
      if (item) item[field[1]] = field[2].trim();
    });
    return groups;
  }

  function appendRich(parent, text) {
    String(text || "").split(/\*\*/).forEach((part, index) => {
      if (!part) return;
      if (index % 2) {
        const strong = document.createElement("strong");
        strong.textContent = part;
        parent.append(strong);
      } else {
        parent.append(part);
      }
    });
  }

  function examChips(value, links) {
    const wrap = document.createElement("span");
    wrap.className = "exam-list";
    String(value || "").split(/\s+/).filter(Boolean).forEach((token) => {
      const found = token.match(EXAM);
      const url = found && links[found[1]];
      const label = found ? found[1] + (found[2] ? "肢" + found[2] : "") : token;
      const chip = document.createElement(url ? "a" : "span");
      chip.className = "exam-chip";
      chip.textContent = label;
      if (url) {
        chip.href = url;
        chip.target = "_blank";
        chip.rel = "noopener";
      }
      if (found && found[3]) {
        const mark = document.createElement("b");
        mark.className = found[3] === "○" ? "mark-true" : "mark-false";
        mark.textContent = found[3];
        mark.setAttribute("aria-label", found[3] === "○" ? "この肢は正しい" : "この肢は誤り");
        chip.append(" ", mark);
      }
      wrap.append(chip);
    });
    return wrap;
  }

  function cardLink(value) {
    const ids = String(value || "").split(/\s+/).filter(Boolean);
    if (!ids.length) return null;
    const link = document.createElement("a");
    link.className = "card-link";
    link.href = "../?cardIds=" + ids.map(encodeURIComponent).join(",") + "&from=memo#study";
    link.textContent = "カードで解く（" + ids.length + "枚）→";
    return link;
  }

  function cell(label, className) {
    const td = document.createElement("td");
    td.dataset.label = label;
    if (className) td.className = className;
    return td;
  }

  function groupSection(subject, count) {
    const group = document.createElement("section");
    group.className = "subject-group";
    group.dataset.subject = subject;
    const heading = document.createElement("h3");
    heading.textContent = subject + "（" + count + "）";
    group.append(heading);
    return group;
  }

  function renderTable(key, subjects, links) {
    const root = document.getElementById(key + "-tables");
    root.replaceChildren();
    let total = 0;
    subjects.forEach((subject) => {
      total += subject.items.length;
      const group = groupSection(subject.name, subject.items.length);
      const table = document.createElement("table");
      table.className = "rule-table " + key;
      const head = table.createTHead().insertRow();
      ["概念", "解説", "具体例"].forEach((name) => {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = name;
        head.append(th);
      });
      const body = table.createTBody();
      subject.items.forEach((item) => {
        const row = body.insertRow();
        const concept = cell("概念", "concept");
        appendRich(concept, item["概念"]);
        const link = cardLink(item["カード"]);
        if (link) concept.append(link);
        const explain = cell("解説");
        TextMarks.append(explain, item["解説"]);
        const example = cell("具体例");
        const sentence = document.createElement("span");
        sentence.className = "example-text";
        TextMarks.append(sentence, item["具体例"]);
        example.append(sentence, examChips(item["過去問"], links));
        row.append(concept, explain, example);
      });
      group.append(table);
      root.append(group);
    });
    document.getElementById(key + "-count").textContent = String(total);
  }

  function hint(text) {
    const box = document.createElement("p");
    box.className = "evidence";
    box.textContent = text || "";
    return box;
  }

  function labelled(label, text) {
    const block = document.createElement("div");
    block.className = "sense-part";
    const name = document.createElement("span");
    name.className = "sense-label";
    name.textContent = label;
    const body = document.createElement("p");
    TextMarks.append(body, text);
    block.append(name, body);
    return block;
  }

  function renderSense(subjects, links) {
    const root = document.getElementById("sense-list");
    root.replaceChildren();
    let total = 0;
    subjects.forEach((subject) => {
      total += subject.items.length;
      const group = groupSection(subject.name, subject.items.length);
      const list = document.createElement("div");
      list.className = "sense-grid";
      subject.items.forEach((item) => {
        const card = document.createElement("article");
        card.className = "sense-card";
        const title = document.createElement("h4");
        appendRich(title, item["概念"]);
        card.append(title);
        if (item["目安"]) card.append(hint(item["目安"]));
        if (item["理由"]) card.append(labelled("なぜそう考えるか", item["理由"]));
        if (item["使い方"]) card.append(labelled("本番での使い方", item["使い方"]));
        if (item["過去問"]) card.append(examChips(item["過去問"], links));
        const link = cardLink(item["カード"]);
        if (link) card.append(link);
        list.append(card);
      });
      group.append(list);
      root.append(group);
    });
    document.getElementById("sense-count").textContent = String(total);
  }

  function applyFilter(name) {
    document.querySelectorAll(".subject-group").forEach((group) => {
      group.hidden = name !== ALL && group.dataset.subject !== name;
    });
    // 「試験全体」は考え方にしかない。表で選んだときに画面が空にならないよう案内を出す。
    ["sense-list", "ok-tables", "ng-tables"].forEach((id) => {
      const root = document.getElementById(id);
      let note = root.querySelector(".empty-note");
      if (!note) {
        note = document.createElement("p");
        note.className = "empty-note";
        note.textContent = "この表には、選んだ区分の項目がありません。「考え方」のタブか、別の科目を選んでください。";
        root.append(note);
      }
      note.hidden = [...root.querySelectorAll(".subject-group")].some((group) => !group.hidden);
    });
    document.querySelectorAll(".filter-button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.subject === name));
    });
  }

  function renderFilter(groups) {
    const names = [];
    groups.sense.concat(groups.ok, groups.ng).forEach((subject) => {
      if (!names.includes(subject.name)) names.push(subject.name);
    });
    const bar = document.getElementById("subject-filter");
    bar.replaceChildren();
    [ALL].concat(names).forEach((name) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter-button";
      button.dataset.subject = name;
      button.textContent = name === ALL ? "すべて" : name;
      button.addEventListener("click", () => applyFilter(name));
      bar.append(button);
    });
    applyFilter(ALL);
  }

  function showTab(name, remember) {
    TABS.forEach((tab) => {
      document.getElementById(tab + "-section").hidden = tab !== name;
      const button = document.getElementById(tab + "-tab");
      button.setAttribute("aria-selected", String(tab === name));
    });
    // 「simple-rules/#ng」のように直接開けるよう、選んだタブをURLに残す。
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
  showTab(hashTab() || "sense", false);

  function load(path, read) {
    return fetch(path + "?v=20261101-3").then((res) => {
      if (!res.ok) throw new Error(path + ": HTTP " + res.status);
      return read(res);
    });
  }

  Promise.all([
    load("content.md", (res) => res.text()),
    load("exam-links.json", (res) => res.json()),
  ]).then(([text, links]) => {
    const groups = parse(text);
    renderSense(groups.sense, links);
    renderTable("ok", groups.ok, links);
    renderTable("ng", groups.ng, links);
    renderFilter(groups);
    document.getElementById("subject-filter").after(TextMarks.legend());
  }).catch((error) => {
    const message = document.getElementById("load-error");
    message.hidden = false;
    message.textContent = "内容を読み込めませんでした（" + error.message + "）。";
  });
})();
