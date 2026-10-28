(() => {
  "use strict";

  // 内容は content.md にだけ持つ。書式は content.md の先頭のコメントを参照。
  const SOURCE = "content.md";
  // 習得状況は学習画面と同じ集計を読むだけ。ここからは何も書き込まない。
  const PROGRESS = "../api/card-progress";
  const STORE_KEY = "memorizeMap:v1";
  const KINDS = { "暗": { label: "覚える", cls: "mem" }, "考": { label: "その場で考える", cls: "think" } };

  const $ = (id) => document.getElementById(id);
  const state = { subjects: [], warnings: [], subject: "all", mode: "all", starOnly: false, todoOnly: false, query: "", progress: null, masteryScore: 3 };

  function loadPrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      if (typeof saved.subject === "string") state.subject = saved.subject;
      if (saved.mode === "all" || KINDS[saved.mode]) state.mode = saved.mode;
      state.starOnly = saved.starOnly === true;
      state.todoOnly = saved.todoOnly === true;
    } catch (error) { /* localStorage が使えなくても表示はできる */ }
  }

  function savePrefs() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ subject: state.subject, mode: state.mode, starOnly: state.starOnly, todoOnly: state.todoOnly }));
    } catch (error) { /* 同上 */ }
  }

  // 本文から {card-id} を抜き出す。
  function splitCards(text) {
    const cards = [];
    const body = text.replace(/\{([a-z0-9-]+)\}/g, (_, id) => {
      if (!cards.includes(id)) cards.push(id);
      return "";
    }).replace(/\s{2,}/g, " ").trim();
    return { text: body, cards };
  }

  function parse(md) {
    const subjects = [];
    const warnings = [];
    let subject = null;
    let field = null;
    let item = null;
    const lines = md.replace(/<!--[\s\S]*?-->/g, (block) => block.replace(/[^\n]/g, "")).split(/\r?\n/);

    function ensureSubject(lineNo) {
      if (!subject) {
        warnings.push(lineNo);
        subject = { name: "（科目なし）", note: "", fields: [] };
        subjects.push(subject);
      }
    }
    function ensureField() {
      if (!field) {
        field = { name: "", note: "", items: [] };
        subject.fields.push(field);
      }
    }

    lines.forEach((raw, index) => {
      const lineNo = index + 1;
      if (!raw.trim()) return;
      let m;
      if ((m = raw.match(/^##\s+(.+)$/))) {
        ensureSubject(lineNo);
        field = { name: m[1].trim(), note: "", items: [] };
        subject.fields.push(field);
        item = null;
      } else if ((m = raw.match(/^#\s+(.+)$/))) {
        subject = { name: m[1].trim(), note: "", fields: [] };
        subjects.push(subject);
        field = null;
        item = null;
      } else if ((m = raw.match(/^>\s?(.*)$/))) {
        ensureSubject(lineNo);
        const target = field || subject;
        target.note = (target.note ? target.note + " " : "") + m[1].trim();
      } else if ((m = raw.match(/^-\s+\[(暗|考)(★?)\]\s*(.+)$/))) {
        ensureSubject(lineNo);
        ensureField();
        item = Object.assign({ kind: m[1], star: m[2] === "★", subs: [] }, splitCards(m[3]));
        field.items.push(item);
      } else if ((m = raw.match(/^\s{2,}-\s+(.+)$/)) && item) {
        item.subs.push(splitCards(m[1]));
      } else {
        warnings.push(lineNo);
      }
    });
    return { subjects, warnings };
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function inline(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function cardsUrl(ids) {
    return "../?cardIds=" + ids.map(encodeURIComponent).join(",") + "&from=memo#study";
  }

  // 習得済み＝学習画面の graduated（リセット後に正解が不正解より3回多い、または「絶対覚えた」）。
  // 残り回数もリセット後の数え方（masteryScore）で出す。全期間の正解数は title にだけ出す。
  function cardState(id) {
    if (!state.progress) return null;
    const p = state.progress[id];
    if (p && p.graduated) return { key: "done", label: "習得済み" };
    if (!p || !p.attempts) return { key: "new", label: "未回答" };
    const rest = Math.max(1, state.masteryScore - (Number(p.masteryScore) || 0));
    return { key: "learning", label: "習得まであと" + rest, note: "これまで " + p.correct + "/" + p.attempts + " 正解" };
  }

  function isGraduated(id) {
    return Boolean(state.progress && state.progress[id] && state.progress[id].graduated);
  }

  function itemCards(item) {
    const ids = [];
    item.cards.concat(...item.subs.map((s) => s.cards)).forEach((id) => {
      if (!ids.includes(id)) ids.push(id);
    });
    return ids;
  }

  // カードに結んだ項目だけ判定できる。結んでいない項目は「未習得」と扱わない。
  function isItemDone(item) {
    const ids = itemCards(item);
    return Boolean(state.progress) && ids.length > 0 && ids.every(isGraduated);
  }

  function cardLinks(ids) {
    return ids.map((id) => {
      const st = cardState(id);
      const title = id + (st && st.note ? "（" + st.note + "）" : "");
      return ' <a class="card-link' + (st ? " is-" + st.key : "") + '" href="' + escapeHtml(cardsUrl([id])) + '" title="' +
        escapeHtml(title) + '">' + (st ? st.label : "カード") + "</a>";
    }).join("");
  }

  function normalize(value) {
    return String(value || "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");
  }

  function matches(item, query) {
    if (state.starOnly && !item.star) return false;
    if (state.todoOnly && isItemDone(item)) return false;
    if (!query) return true;
    const hay = normalize([item.text].concat(item.subs.map((s) => s.text)).join(" "));
    return hay.includes(query);
  }

  function renderItem(item) {
    const cls = [item.star ? "star" : "", isItemDone(item) ? "done" : ""].filter(Boolean).join(" ");
    let html = "<li" + (cls ? ' class="' + cls + '"' : "") + ">" +
      (item.star ? '<span class="star-badge" title="20年で6問以上">頻出</span>' : "") + inline(item.text) + cardLinks(item.cards);
    if (item.subs.length) {
      html += "<ul>" + item.subs.map((s) => "<li>" + inline(s.text) + cardLinks(s.cards) + "</li>").join("") + "</ul>";
    }
    return html + "</li>";
  }

  function renderColumn(kind, items) {
    const meta = KINDS[kind];
    const ids = [];
    items.forEach((item) => itemCards(item).forEach((id) => {
      if (!ids.includes(id)) ids.push(id);
    }));
    const todo = ids.filter((id) => !isGraduated(id));
    let html = '<section class="col ' + meta.cls + '"><h4>' + meta.label + "<span>" + items.length + "件</span></h4>";
    html += "<ul>" + items.map(renderItem).join("") + "</ul>";
    if (state.progress && ids.length && !todo.length) {
      html += '<p class="col-foot">この欄のカードはすべて習得済み</p>';
    } else if (todo.length > 1) {
      const label = state.progress ? "この欄の未習得カードを解く（" : "この欄のカードをまとめて解く（";
      html += '<p class="col-foot"><a href="' + escapeHtml(cardsUrl(todo)) + '">' + label + todo.length + "枚）→</a></p>";
    }
    return html + "</section>";
  }

  function render() {
    const query = normalize(state.query);
    const kinds = state.mode === "all" ? ["暗", "考"] : [state.mode];
    let html = "";
    let shown = 0;
    state.subjects.forEach((subject, index) => {
      if (state.subject !== "all" && state.subject !== String(index)) return;
      let fieldsHtml = "";
      subject.fields.forEach((field) => {
        const byKind = {};
        kinds.forEach((kind) => {
          // 頻出（★）を各欄の先頭に寄せる。それ以外は content.md の順のまま。
          byKind[kind] = field.items.filter((item) => item.kind === kind && matches(item, query))
            .sort((a, b) => Number(b.star) - Number(a.star));
        });
        const count = kinds.reduce((sum, kind) => sum + byKind[kind].length, 0);
        if (!count) return;
        shown += count;
        // 空の欄は出さない。片方だけになったら1列にする。
        const visible = kinds.filter((kind) => byKind[kind].length);
        fieldsHtml += '<article class="field">';
        if (field.name) fieldsHtml += "<h3>" + inline(field.name) + "</h3>";
        if (field.note) fieldsHtml += '<p class="note">' + inline(field.note) + "</p>";
        fieldsHtml += '<div class="cols' + (visible.length === 1 ? " single" : "") + '">';
        fieldsHtml += visible.map((kind) => renderColumn(kind, byKind[kind])).join("");
        fieldsHtml += "</div></article>";
      });
      if (!fieldsHtml) return;
      html += '<section class="subject"><h2>' + inline(subject.name) + "</h2>";
      if (subject.note) html += '<p class="note">' + inline(subject.note) + "</p>";
      html += fieldsHtml + "</section>";
    });
    if (!shown) html = '<p class="status">条件に合う項目がありません。</p>';
    if (state.warnings.length) {
      html += '<div class="warn-box">content.md の書式として読めなかった行：' + state.warnings.slice(0, 20).join(", ") +
        (state.warnings.length > 20 ? " ほか" : "") + "</div>";
    }
    $("content").innerHTML = html;
  }

  function countKind(subjects, kind) {
    return subjects.reduce((sum, s) => sum + s.fields.reduce((n, f) => n + f.items.filter((i) => i.kind === kind).length, 0), 0);
  }

  function renderChips() {
    const chips = [{ key: "all", name: "全科目", count: countKind(state.subjects, "暗") + countKind(state.subjects, "考") }]
      .concat(state.subjects.map((s, index) => ({ key: String(index), name: s.name, count: countKind([s], "暗") + countKind([s], "考") })));
    if (!chips.some((chip) => chip.key === state.subject)) state.subject = "all";
    $("subject-chips").innerHTML = chips.map((chip) =>
      '<button type="button" data-subject="' + chip.key + '" aria-pressed="' + (chip.key === state.subject) + '">' +
      escapeHtml(chip.name) + "<small>" + chip.count + "</small></button>").join("");
  }

  function syncModes() {
    document.querySelectorAll("#mode-buttons button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
    });
    $("star-only").setAttribute("aria-pressed", String(state.starOnly));
    $("todo-only").setAttribute("aria-pressed", String(state.todoOnly));
  }

  function bind() {
    $("subject-chips").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-subject]");
      if (!button) return;
      state.subject = button.dataset.subject;
      document.querySelectorAll("#subject-chips button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      savePrefs();
      render();
    });
    $("mode-buttons").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-mode]");
      if (!button) return;
      state.mode = button.dataset.mode;
      syncModes();
      savePrefs();
      render();
    });
    $("star-only").addEventListener("click", () => {
      state.starOnly = !state.starOnly;
      syncModes();
      savePrefs();
      render();
    });
    $("todo-only").addEventListener("click", () => {
      state.todoOnly = !state.todoOnly;
      syncModes();
      savePrefs();
      render();
    });
    // 学習画面で解いて戻ってきたら、習得状況を読み直す。
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.subjects.length) loadProgress().then(render);
    });
    $("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      render();
    });
  }

  function allItems() {
    return state.subjects.flatMap((s) => s.fields.flatMap((f) => f.items));
  }

  // 頻出（★）の項目に結んだカードのうち、まだ習得していないものをページの並び順でまとめて渡す。
  // ★の項目に一緒に結んだ頻出でないカードも含むので、枚数は頻出カードの数と一致しない。
  // 学習画面の「指定したカード」は200枚で打ち切るので、それを超えるときは先頭200枚だけ渡す。
  function renderStarTodoLink(stars) {
    const link = $("star-todo-link");
    const done = $("star-todo-done");
    const ids = [];
    stars.forEach((item) => itemCards(item).forEach((id) => {
      if (!ids.includes(id) && !isGraduated(id)) ids.push(id);
    }));
    link.hidden = !ids.length;
    done.hidden = !(state.progress && !ids.length);
    if (!ids.length) return;
    const picked = ids.slice(0, 200);
    link.href = cardsUrl(picked);
    link.textContent = (state.progress ? "★の項目の未習得カードをまとめて解く（" : "★の項目のカードをまとめて解く（") + picked.length + "枚）→";
  }

  async function loadProgress() {
    try {
      const response = await fetch(PROGRESS, { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      state.progress = data && typeof data.byCard === "object" ? data.byCard : null;
      if (data && data.stats && Number(data.stats.masteryScore) > 0) state.masteryScore = Number(data.stats.masteryScore);
    } catch (error) {
      state.progress = null;
    }
    const stars = allItems().filter((i) => i.star);
    const note = $("progress-note");
    $("todo-only").hidden = !state.progress;
    renderStarTodoLink(stars);
    if (state.progress) {
      $("count-star-done").textContent = stars.filter(isItemDone).length + " / " + stars.length;
      note.textContent = "習得済み＝学習画面で（リセット後に）正解が不正解より" + state.masteryScore + "回多いカード、または「絶対覚えた」を押したカード。カードに結んでいない項目は判定しません。";
      note.classList.remove("error");
    } else {
      $("count-star-done").textContent = "—";
      note.textContent = "習得状況を読み込めませんでした。項目だけ表示しています。";
      note.classList.add("error");
    }
  }

  async function start() {
    loadPrefs();
    syncModes();
    bind();
    try {
      const response = await fetch(SOURCE, { cache: "no-cache" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const parsed = parse(await response.text());
      state.subjects = parsed.subjects;
      state.warnings = parsed.warnings;
    } catch (error) {
      $("content").innerHTML = '<p class="status error">content.md を読み込めませんでした（' + escapeHtml(error.message) + "）。</p>";
      return;
    }
    $("count-mem").textContent = countKind(state.subjects, "暗") + "件";
    $("count-think").textContent = countKind(state.subjects, "考") + "件";
    $("count-star").textContent = state.subjects.reduce((n, s) => n + s.fields.reduce((m, f) => m + f.items.filter((i) => i.star).length, 0), 0) + "件";
    renderChips();
    render();
    await loadProgress();
    render();
  }

  start();
})();
