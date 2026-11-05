(function () {
  "use strict";

  const VERSION = "20261105-1";
  const FIELD = /^\s*(?:-\s+)?(法理|別名|正確|やさしく|根拠|過去問|カード)\s*::\s*(.*)$/;
  const ALL = "all";

  function parse(text) {
    const groups = [];
    let group = null;
    let item = null;
    text.replace(/<!--[\s\S]*?-->/g, "").split("\n").forEach((line) => {
      const head = line.match(/^##\s+(.+)$/);
      if (head) {
        group = { name: head[1].trim(), items: [] };
        groups.push(group);
        item = null;
        return;
      }
      const field = line.match(FIELD);
      if (!field || !group) return;
      if (field[1] === "法理") {
        item = {};
        group.items.push(item);
      }
      if (item) item[field[1]] = field[2].trim();
    });
    return groups;
  }

  function examChips(value, links) {
    const wrap = document.createElement("div");
    wrap.className = "exam-list";
    String(value || "").split(/\s+/).filter(Boolean).forEach((token) => {
      const url = links[token];
      const chip = document.createElement(url ? "a" : "span");
      chip.className = "exam-chip";
      chip.textContent = token;
      if (url) {
        chip.href = url;
        chip.target = "_blank";
        chip.rel = "noopener";
      }
      wrap.append(chip);
    });
    return wrap;
  }

  function studyUrl(ids) {
    return "../?cardIds=" + ids.map(encodeURIComponent).join(",") + "&from=memo#study";
  }

  function cardBlock(value, titles) {
    const ids = String(value || "").split(/\s+/).filter(Boolean);
    if (!ids.length) return null;
    const box = document.createElement("div");
    box.className = "card-block";
    const label = document.createElement("span");
    label.className = "part-label";
    label.textContent = "関連カード";
    const list = document.createElement("ul");
    list.className = "card-list";
    ids.forEach((id) => {
      const row = document.createElement("li");
      const link = document.createElement("a");
      link.href = studyUrl([id]);
      link.textContent = titles.get(id) || id;
      row.append(link);
      list.append(row);
    });
    box.append(label, list);
    if (ids.length > 1) {
      const all = document.createElement("a");
      all.className = "solve-all";
      all.href = studyUrl(ids);
      all.textContent = "この" + ids.length + "枚をまとめて解く →";
      box.append(all);
    }
    return box;
  }

  // 過去問の欄（平成28年度以降の分だけ）から、本番でどの問番号に出たかを数えて1行にする。
  // 問番号ごとの出題傾向（question-slots/slots.json）は平成28年度以降の並びなので、それより前の年度は数えない。
  function slotLine(value, slots) {
    const counts = new Map();
    String(value || "").split(/\s+/).forEach((token) => {
      const found = /^(平成(\d+)|令和(元|\d+))年問(\d+)$/.exec(token);
      if (!found || (found[2] && Number(found[2]) < 28)) return;
      counts.set(found[4], (counts.get(found[4]) || 0) + 1);
    });
    const numbers = Array.from(counts.keys()).filter((n) => slots[n])
      .sort((a, b) => counts.get(b) - counts.get(a) || Number(a) - Number(b)).slice(0, 3);
    if (!numbers.length) return null;
    const line = document.createElement("p");
    line.className = "slot-line";
    line.append("本番で出た番号（平成28年度以降）：");
    numbers.forEach((n, i) => {
      if (i) line.append("、");
      const link = document.createElement("a");
      link.href = "../question-slots/#q" + n;
      link.textContent = "問" + n + "（" + slots[n].title + "）" + (counts.get(n) > 1 ? counts.get(n) + "回" : "");
      line.append(link);
    });
    return line;
  }

  function renderDoctrine(item, links, titles, slots) {
    const card = document.createElement("article");
    card.className = "doctrine";
    const title = document.createElement("h4");
    title.textContent = item["法理"];
    card.append(title);
    if (item["別名"]) {
      const alias = document.createElement("p");
      alias.className = "alias";
      alias.textContent = item["別名"];
      card.append(alias);
    }
    const exact = document.createElement("p");
    exact.className = "exact";
    exact.textContent = item["正確"];
    card.append(exact);
    const easy = document.createElement("div");
    easy.className = "easy";
    const easyLabel = document.createElement("span");
    easyLabel.className = "part-label";
    easyLabel.textContent = "わかりやすく";
    easy.append(easyLabel);
    String(item["やさしく"] || "").split("\\n").forEach((paragraph) => {
      const p = document.createElement("p");
      TextMarks.append(p, paragraph);
      easy.append(p);
    });
    card.append(easy);
    if (item["根拠"]) {
      const basis = document.createElement("p");
      basis.className = "basis";
      basis.textContent = "根拠：" + item["根拠"];
      card.append(basis);
    }
    if (item["過去問"]) {
      const line = slotLine(item["過去問"], slots || {});
      if (line) card.append(line);
      card.append(examChips(item["過去問"], links));
    }
    const cards = cardBlock(item["カード"], titles);
    if (cards) card.append(cards);
    return card;
  }

  function applyFilter(name) {
    document.querySelectorAll(".subject-group").forEach((group) => {
      group.hidden = name !== ALL && group.dataset.subject !== name;
    });
    document.querySelectorAll(".filter-button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.subject === name));
    });
  }

  function render(groups, links, titles, slots) {
    const root = document.getElementById("doctrine-list");
    root.replaceChildren();
    const bar = document.getElementById("subject-filter");
    bar.replaceChildren();
    let total = 0;
    [{ name: ALL }].concat(groups).forEach((group) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter-button";
      button.dataset.subject = group.name;
      button.textContent = group.name === ALL ? "すべて" : group.name;
      button.addEventListener("click", () => applyFilter(group.name));
      bar.append(button);
    });
    groups.forEach((group) => {
      total += group.items.length;
      const section = document.createElement("section");
      section.className = "subject-group";
      section.dataset.subject = group.name;
      const heading = document.createElement("h3");
      heading.textContent = group.name + "（" + group.items.length + "）";
      const list = document.createElement("div");
      list.className = "doctrine-list";
      group.items.forEach((item) => list.append(renderDoctrine(item, links, titles, slots)));
      section.append(heading, list);
      root.append(section);
    });
    document.getElementById("doctrine-count").textContent = String(total);
    applyFilter(ALL);
  }

  function load(path, read) {
    return fetch(path).then((res) => {
      if (!res.ok) throw new Error(path + ": HTTP " + res.status);
      return read(res);
    });
  }

  Promise.all([
    load("content.md?v=" + VERSION, (res) => res.text()),
    load("../simple-rules/exam-links.json?v=" + VERSION, (res) => res.json()),
    // カード名が取れなくても、本文とリンクは出す（名前の代わりにカードIDを出す）。
    load("../api/cards", (res) => res.json()).catch(() => null),
    // 問番号ごとの出題傾向が読めなくても、本文は出す。
    load("../question-slots/slots.json?v=" + VERSION, (res) => res.json()).catch(() => null),
  ]).then(([text, links, cards, slotData]) => {
    const titles = new Map(((cards && cards.explanationCards) || []).map((card) => [card.id, card.subtopic || card.id]));
    render(parse(text), links, titles, (slotData && slotData.slots) || {});
    document.getElementById("subject-filter").after(TextMarks.legend());
  }).catch((error) => {
    const message = document.getElementById("load-error");
    message.hidden = false;
    message.textContent = "内容を読み込めませんでした（" + error.message + "）。";
  });
})();
