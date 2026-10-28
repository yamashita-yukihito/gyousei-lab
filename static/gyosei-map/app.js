(() => {
  "use strict";

  // 内容は content.md にだけ持つ。書式は content.md の先頭のコメントを参照。
  const SOURCE = "content.md";
  // 頻出度と習得状況は読むだけ。このページからは何も書き込まない。
  const CARDS = "../api/cards";
  const PROGRESS = "../api/card-progress";
  const STORE_KEY = "gyoseiMap:v1";
  const FREQUENT = ["最頻出", "頻出"];
  const MARKS = { "重要": "important", "ひっかけ": "trap" };

  const $ = (id) => document.getElementById(id);
  const state = {
    branches: [],
    recall: false,
    depth: 2,
    freqOnly: false,
    query: "",
    open: new Map(),
    cardInfo: null,
    progress: null,
    chapters: new Map()
  };

  function loadPrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      state.recall = saved.recall === true;
      if ([1, 2, 99].includes(saved.depth)) state.depth = saved.depth;
      state.freqOnly = saved.freqOnly === true;
    } catch (error) { /* localStorage が使えなくても表示はできる */ }
  }

  function savePrefs() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ recall: state.recall, depth: state.depth, freqOnly: state.freqOnly }));
    } catch (error) { /* 同上 */ }
  }

  // 1行の本文から、カードID・演習D案の章・ページへのリンク・印を抜き出す。
  function parseText(raw) {
    const node = { cards: [], drills: [], pages: [], marks: [] };
    let text = raw
      .replace(/\{([a-z0-9-]+)\}/g, (_, id) => { if (!node.cards.includes(id)) node.cards.push(id); return ""; })
      .replace(/@([a-z0-9-]+)\/([a-z0-9-]+)/g, (_, dir, id) => { node.drills.push({ dir, id }); return ""; })
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => { if (!/^[a-z]+:/i.test(url)) node.pages.push({ label, url }); return ""; })
      .replace(/\[(重要|ひっかけ)\]/g, (_, mark) => { node.marks.push(mark); return ""; });
    text = text.replace(/\s{2,}/g, " ").trim();
    const at = text.indexOf("::");
    node.label = (at < 0 ? text : text.slice(0, at)).trim();
    node.answer = at < 0 ? "" : text.slice(at + 2).trim();
    return node;
  }

  function parse(md) {
    const branches = [];
    let branch = null;
    let stack = [];
    md.replace(/<!--[\s\S]*?-->/g, "").split(/\r?\n/).forEach((raw) => {
      if (!raw.trim()) return;
      let m;
      if ((m = raw.match(/^#\s+(.+)$/))) {
        const [name, sub] = m[1].split("|").map((s) => s.trim());
        branch = { key: "b" + branches.length, name, sub: sub || "", note: "", children: [] };
        branches.push(branch);
        stack = [];
      } else if ((m = raw.match(/^>\s?(.*)$/)) && branch) {
        branch.note = (branch.note ? branch.note + " " : "") + m[1].trim();
      } else if ((m = raw.match(/^( *)-\s+(.+)$/)) && branch) {
        const depth = Math.floor(m[1].length / 2);
        const node = Object.assign(parseText(m[2]), { depth, children: [] });
        stack.length = Math.min(stack.length, depth);
        const parent = stack.length ? stack[stack.length - 1] : branch;
        node.key = (parent.key || branch.key) + "." + parent.children.length;
        parent.children.push(node);
        stack.push(node);
      }
    });
    return branches;
  }

  function subtreeCards(node, out = []) {
    (node.cards || []).forEach((id) => { if (!out.includes(id)) out.push(id); });
    node.children.forEach((child) => subtreeCards(child, out));
    return out;
  }

  function freqOf(id) {
    const info = state.cardInfo && state.cardInfo.get(id);
    return info ? info.frequency : "";
  }

  function isFrequent(id) { return FREQUENT.includes(freqOf(id)); }

  function isDone(id) {
    return Boolean(state.progress && state.progress[id] && state.progress[id].graduated);
  }

  function cardsUrl(ids) {
    return "../?cardIds=" + ids.slice(0, 200).map(encodeURIComponent).join(",") + "&from=map#study";
  }

  function normalize(text) {
    return String(text || "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 検索と「頻出の枝だけ」で、残す項目を決める。子が残れば親も残す。
  function visibleKeys() {
    const q = normalize(state.query);
    const keep = new Set();
    const hits = new Set();
    // 頻出のカードを結んだ項目は、その下の補足も残す。
    function walk(node, ancestorHit, ancestorFreq) {
      const selfHit = q && normalize(node.label + node.answer).includes(q);
      if (selfHit) hits.add(node.key);
      const selfFreq = node.cards.some(isFrequent);
      let show = false;
      node.children.forEach((child) => { if (walk(child, ancestorHit || selfHit, ancestorFreq || selfFreq)) show = true; });
      const passQuery = !q || selfHit || ancestorHit;
      const passFreq = !state.freqOnly || ancestorFreq || subtreeCards(node).some(isFrequent);
      if (passQuery && passFreq) show = true;
      if (show) keep.add(node.key);
      return show;
    }
    state.branches.forEach((branch) => branch.children.forEach((node) => walk(node, false, false)));
    return { keep, hits };
  }

  function isOpen(node) {
    if (state.open.has(node.key)) return state.open.get(node.key);
    if (state.query) return true;
    return node.depth < state.depth;
  }

  function renderCardsChip(ids) {
    const a = el("a", "chip cards");
    a.href = cardsUrl(ids);
    const done = ids.filter(isDone).length;
    a.textContent = state.progress ? "カード " + done + "/" + ids.length + " 習得" : "カード " + ids.length + "枚";
    if (state.progress && done === ids.length) a.classList.add("is-done");
    if (state.cardInfo) {
      a.title = ids.map((id) => {
        const info = state.cardInfo.get(id);
        return info ? "・" + info.subtopic + "（" + info.frequency + "）" : "・" + id;
      }).join("\n");
    }
    return a;
  }

  function renderNode(node, view) {
    const li = el("li", "depth-" + node.depth);
    const row = el("div", "row");
    const hasKids = node.children.some((child) => view.keep.has(child.key));
    const open = hasKids && isOpen(node);
    const twisty = el("button", "twisty" + (hasKids ? "" : " leaf"), hasKids ? (open ? "▾" : "▸") : "");
    twisty.type = "button";
    if (hasKids) {
      twisty.setAttribute("aria-expanded", String(open));
      twisty.setAttribute("aria-label", open ? "閉じる" : "開く");
      twisty.addEventListener("click", () => {
        state.open.set(node.key, !open);
        render();
      });
    } else {
      twisty.tabIndex = -1;
      twisty.setAttribute("aria-hidden", "true");
    }
    row.append(twisty, el("span", "label", node.label));
    node.marks.forEach((mark) => row.append(el("span", "mark " + MARKS[mark], mark)));
    if (state.cardInfo) {
      const best = FREQUENT.find((label) => node.cards.some((id) => freqOf(id) === label));
      if (best) row.append(el("span", "mark freq", best));
    }
    if (node.answer) {
      const ans = el("button", "ans", node.answer);
      ans.type = "button";
      ans.addEventListener("click", () => { if (state.recall) ans.classList.toggle("shown"); });
      row.append(ans);
    }
    if (node.cards.length) row.append(renderCardsChip(node.cards));
    node.drills.forEach((drill) => {
      const a = el("a", "chip drill");
      a.href = "../" + drill.dir + "/#mode=read&chapter=" + encodeURIComponent(drill.id);
      const label = state.chapters.get(drill.dir + "/" + drill.id);
      a.textContent = "演習D" + (label ? "：" + label : "") + " →";
      row.append(a);
    });
    node.pages.forEach((page) => {
      const a = el("a", "chip page", page.label + " →");
      a.href = page.url;
      row.append(a);
    });
    li.append(row);
    if (view.hits.has(node.key)) li.classList.add("hit");
    if (hasKids) {
      if (!open) li.classList.add("collapsed");
      const ul = el("ul");
      node.children.filter((child) => view.keep.has(child.key)).forEach((child) => ul.append(renderNode(child, view)));
      li.append(ul);
    }
    return li;
  }

  function renderTrunk() {
    const trunk = $("trunk");
    trunk.replaceChildren(...state.branches.map((branch, index) => {
      const ids = subtreeCards(branch);
      const a = el("a");
      a.href = "#" + branch.key;
      a.append(el("span", "step", String(index + 1).padStart(2, "0")), el("strong", "", branch.name), el("span", "sub", branch.sub));
      const meta = state.progress
        ? "カード " + ids.filter(isDone).length + "/" + ids.length + " 習得"
        : "カード " + ids.length + "枚";
      a.append(el("span", "meta", meta));
      return a;
    }));
  }

  function render() {
    const view = visibleKeys();
    const content = $("content");
    const sections = state.branches.map((branch) => {
      const roots = branch.children.filter((node) => view.keep.has(node.key));
      if (!roots.length) return null;
      const section = el("section", "map-branch");
      section.id = branch.key;
      const head = el("div", "map-branch-head");
      const h2 = el("h2", "", branch.name);
      if (branch.sub) h2.append(el("small", "", branch.sub));
      const actions = el("div", "map-branch-actions");
      const ids = subtreeCards(branch);
      if (ids.length) {
        const todo = state.progress ? ids.filter((id) => !isDone(id)) : ids;
        if (todo.length) {
          const a = el("a", "", (state.progress ? "この枝の未習得カードを解く（" : "この枝のカードを解く（") + todo.length + "枚）→");
          a.href = cardsUrl(todo);
          actions.append(a);
        } else {
          actions.append(el("span", "", "この枝のカードはすべて習得済み"));
        }
      }
      head.append(h2, actions);
      section.append(head);
      if (branch.note) section.append(el("p", "note", branch.note));
      const ul = el("ul", "tree");
      roots.forEach((node) => ul.append(renderNode(node, view)));
      section.append(ul);
      return section;
    }).filter(Boolean);
    content.replaceChildren(...(sections.length ? sections : [el("p", "status", "当てはまる項目がありません。")]));
  }

  function syncControls() {
    document.body.classList.toggle("recall", state.recall);
    $("recall-toggle").setAttribute("aria-pressed", String(state.recall));
    $("reveal-all").hidden = !state.recall;
    $("freq-only").setAttribute("aria-pressed", String(state.freqOnly));
    $("freq-only").disabled = !state.cardInfo;
    document.querySelectorAll("#depth-buttons button").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.depth) === state.depth));
    });
  }

  function bind() {
    $("recall-toggle").addEventListener("click", () => {
      state.recall = !state.recall;
      document.querySelectorAll(".ans.shown").forEach((node) => node.classList.remove("shown"));
      savePrefs();
      syncControls();
    });
    $("reveal-all").addEventListener("click", () => {
      document.querySelectorAll(".ans").forEach((node) => node.classList.add("shown"));
    });
    document.querySelectorAll("#depth-buttons button").forEach((button) => {
      button.addEventListener("click", () => {
        state.depth = Number(button.dataset.depth);
        state.open.clear();
        savePrefs();
        syncControls();
        render();
      });
    });
    $("freq-only").addEventListener("click", () => {
      state.freqOnly = !state.freqOnly;
      state.open.clear();
      savePrefs();
      syncControls();
      render();
    });
    let timer = 0;
    $("search").addEventListener("input", (event) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.query = event.target.value;
        state.open.clear();
        render();
      }, 120);
    });
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }

  // 頻出度・習得状況・章の名前は、読めなくても項目だけで使える。
  async function loadExtras() {
    const dirs = [...new Set(state.branches.flatMap((b) => {
      const out = [];
      (function walk(n) { (n.drills || []).forEach((d) => out.push(d.dir)); n.children.forEach(walk); })(b);
      return out;
    }))];
    const [cards, progress, ...drills] = await Promise.allSettled([fetchJson(CARDS), fetchJson(PROGRESS), ...dirs.map((dir) => fetchJson("../" + dir + "/chapters.json"))]);
    if (cards.status === "fulfilled" && Array.isArray(cards.value.explanationCards)) {
      state.cardInfo = new Map(cards.value.explanationCards.map((card) => [card.id, {
        subtopic: card.subtopic || card.id,
        frequency: (card.frequency && card.frequency.label) || ""
      }]));
    }
    if (progress.status === "fulfilled" && progress.value && typeof progress.value.byCard === "object") {
      state.progress = progress.value.byCard;
    }
    drills.forEach((result, index) => {
      if (result.status !== "fulfilled") return;
      (result.value.chapters || []).forEach((chapter) => {
        state.chapters.set(dirs[index] + "/" + chapter.id, String(chapter.title || chapter.id).split("――")[0]);
      });
    });
    const note = $("progress-note");
    const missing = [];
    if (!state.cardInfo) missing.push("頻出度");
    if (!state.progress) missing.push("習得状況");
    if (missing.length) {
      note.textContent = missing.join("と") + "を読み込めませんでした。項目とリンクだけ表示しています。";
      note.classList.add("error");
    } else {
      note.textContent = "「最頻出」「頻出」は結んだカードの過去20年の出題数（6問以上）。「習得」は学習画面と同じ判定（リセット後に正解が不正解より3回多い、または「絶対覚えた」）。カードの印にマウスを乗せると論点名が出ます。";
    }
    syncControls();
    renderTrunk();
    render();
  }

  async function init() {
    loadPrefs();
    bind();
    syncControls();
    try {
      const response = await fetch(SOURCE, { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      state.branches = parse(await response.text());
    } catch (error) {
      $("content").replaceChildren(el("p", "status error", "マップを読み込めませんでした。" + error.message));
      return;
    }
    renderTrunk();
    render();
    loadExtras();
  }

  init();
})();
