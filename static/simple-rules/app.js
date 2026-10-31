(function () {
  "use strict";

  const SECTIONS = {
    ok: { root: "ok-tables", count: "ok-count" },
    ng: { root: "ng-tables", count: "ng-count" },
  };
  const FIELD = /^\s*(?:-\s+)?(概念|解説|具体例|過去問|カード)\s*::\s*(.*)$/;
  const EXAM = /^(.+?年問\d+)(?:肢([^○×]+))?([○×])?$/;

  function parse(text) {
    const groups = { ok: [], ng: [] };
    let table = null;
    let subject = null;
    let item = null;
    text.replace(/<!--[\s\S]*?-->/g, "").split("\n").forEach((line) => {
      const top = line.match(/^#\s+(ok|ng)\s*$/);
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

  function renderSection(key, subjects, links) {
    const root = document.getElementById(SECTIONS[key].root);
    root.replaceChildren();
    let total = 0;
    subjects.forEach((subject) => {
      total += subject.items.length;
      const group = document.createElement("section");
      group.className = "subject-group";
      group.dataset.subject = subject.name;
      const heading = document.createElement("h3");
      heading.textContent = subject.name + "（" + subject.items.length + "）";
      group.append(heading);

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
        appendRich(explain, item["解説"]);
        const example = cell("具体例");
        const sentence = document.createElement("span");
        sentence.className = "example-text";
        appendRich(sentence, item["具体例"]);
        example.append(sentence, examChips(item["過去問"], links));
        row.append(concept, explain, example);
      });
      group.append(table);
      root.append(group);
    });
    document.getElementById(SECTIONS[key].count).textContent = String(total);
  }

  function applyFilter(name) {
    document.querySelectorAll(".subject-group").forEach((group) => {
      group.hidden = name !== "all" && group.dataset.subject !== name;
    });
    document.querySelectorAll(".filter-button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.subject === name));
    });
  }

  function renderFilter(groups) {
    const names = [];
    groups.ok.concat(groups.ng).forEach((subject) => {
      if (!names.includes(subject.name)) names.push(subject.name);
    });
    const bar = document.getElementById("subject-filter");
    bar.replaceChildren();
    ["all"].concat(names).forEach((name) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter-button";
      button.dataset.subject = name;
      button.textContent = name === "all" ? "全科目" : name;
      button.addEventListener("click", () => applyFilter(name));
      bar.append(button);
    });
    applyFilter("all");
  }

  Promise.all([
    fetch("content.md?v=20261031-1").then((res) => {
      if (!res.ok) throw new Error("content.md: HTTP " + res.status);
      return res.text();
    }),
    fetch("exam-links.json?v=20261031-1").then((res) => {
      if (!res.ok) throw new Error("exam-links.json: HTTP " + res.status);
      return res.json();
    }),
  ]).then(([text, links]) => {
    const groups = parse(text);
    renderSection("ok", groups.ok, links);
    renderSection("ng", groups.ng, links);
    renderFilter(groups);
  }).catch((error) => {
    const message = document.getElementById("load-error");
    message.hidden = false;
    message.textContent = "内容を読み込めませんでした（" + error.message + "）。";
  });
})();
