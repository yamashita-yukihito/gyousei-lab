(function () {
  "use strict";

  const TABS = ["how", "word", "length", "format", "no", "plan"];
  const ALL = "all";

  function showTab(name) {
    TABS.forEach((tab) => {
      document.getElementById(tab + "-section").hidden = tab !== name;
      document.getElementById(tab + "-tab").setAttribute("aria-selected", String(tab === name));
    });
  }

  TABS.forEach((tab) => {
    document.getElementById(tab + "-tab").addEventListener("click", () => showTab(tab));
  });
  showTab("how");

  // スマホでは表を縦に積むので、各欄に見出しの名前を持たせる。
  document.querySelectorAll(".stack-table").forEach((table) => {
    const names = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      [...row.children].forEach((cell, index) => {
        if (index > 0 && names[index]) cell.dataset.label = names[index];
      });
    });
  });

  function ruleCard(rule) {
    const card = document.createElement("article");
    card.className = "tip-card";
    const title = document.createElement("h4");
    title.textContent = rule.label;
    const rate = document.createElement("div");
    rate.className = "rate lean-" + rule.direction;
    const value = document.createElement("strong");
    value.textContent = (rule.direction === "true" ? "正しい " : "誤り ") + rule.hit + "%";
    const detail = document.createElement("span");
    detail.textContent = rule.count + "肢。同じ問題の平均は" + rule.base + "%";
    rate.append(value, detail);
    const note = document.createElement("p");
    note.textContent = rule.note;
    card.append(title, rate, note);
    return card;
  }

  function renderWords(rules) {
    const root = document.getElementById("word-list");
    const names = [];
    rules.forEach((rule) => {
      if (!names.includes(rule.subject)) names.push(rule.subject);
    });
    names.forEach((name) => {
      const group = document.createElement("section");
      group.className = "subject-group";
      group.dataset.subject = name;
      const list = rules.filter((rule) => rule.subject === name);
      const heading = document.createElement("h3");
      heading.textContent = (name === "全科目" ? "全科目共通" : name) + "（" + list.length + "）";
      const grid = document.createElement("div");
      grid.className = "tip-grid";
      list.forEach((rule) => grid.append(ruleCard(rule)));
      group.append(heading, grid);
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

  fetch("word-signals.json?v=20261031-2").then((res) => {
    if (!res.ok) throw new Error("word-signals.json: HTTP " + res.status);
    return res.json();
  }).then((data) => {
    document.getElementById("sim-accuracy").textContent = String(data.simulation.accuracy);
    renderWords(data.rules);
  }).catch((error) => {
    const message = document.getElementById("word-error");
    message.hidden = false;
    message.textContent = "語の表を読み込めませんでした（" + error.message + "）。";
  });
})();
