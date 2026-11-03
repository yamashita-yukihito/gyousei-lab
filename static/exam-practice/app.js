// 本試験形式（5肢択一）の練習を、学習カードの画面のように解くページ（2026-11-03〜04に利用者が依頼）。
// 正本は sets.json（ねらい・知識が足りないときの絞り方・各肢の理由・肢に結び付いたカード）。
// 問題文と正解は ../api/questions から読む。問題文を公開リポジトリ（sets.json）に入れないため。
// 記録はこの端末の localStorage（examPractice:v1）だけに持つ。サーバーの回答履歴（answer_attempts・card_attempts）へは書かない。
// 「解く前に答えを表示する」は既定でオン（1周目は答えを知った状態で解く。カードの同名の設定と同じ考え方）。
// 文字列はすべて textContent か TextMarks（文字の色付け）で入れ、HTMLとして解釈しない。
(function () {
  "use strict";

  const STORE_KEY = "examPractice:v1";
  const SHOW_KEY = "examPractice:showAnswer";
  const COMBO = /^[ア-オ](?:・[ア-オ])+$/;
  const FALSE_PHRASE = /(誤っている|妥当でない|適切でない|合致しない|適当でない|正しくない)(もの|記述)/g;
  const TRUE_PHRASE = /(正しい|妥当な|適切な|合致する|適当な)(もの|記述)/g;
  const SUBJECT_NAMES = {
    "administrative-law": "行政法",
    "civil-law": "民法",
    "constitutional-law": "憲法",
    "commercial-law": "商法・会社法",
    "general-knowledge": "基礎知識",
    "legal-foundations": "基礎法学"
  };
  const RULE_TABLES = {
    sense: { text: "考え方", field: "使い方" },
    ok: { text: "言い切ってよい", field: "解説" },
    ng: { text: "言い切るとだめ", field: "解説" }
  };
  // 形だけで選ぶとき。数字は絞り込みテクニック（technique/ の「肢の長さと位置」）と同じ実測値。
  // 平成18〜令和7年度の「正しい／誤っているもの」を1つ選ぶ問題で、その位置の肢が正解だった割合（手がかりがなければ20%）。
  const SHAPE_RULES = {
    "constitutional-law": "long",
    "civil-law": "long",
    "commercial-law": "long",
    "administrative-law": "none",
    "general-knowledge": "first"
  };

  const $ = (id) => document.getElementById(id);
  const state = {
    data: null,
    set: null,
    index: 0,
    questions: new Map(),
    cards: new Map(),
    cardsLoaded: null,
    hints: { cards: {}, kinds: {} },
    rules: new Map(),
    words: [],
    marks: {},
    justAnswered: {},
    resultMode: false,
    loading: false
  };

  function make(tag, props, text) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((key) => {
        if (key === "dataset") Object.assign(node.dataset, props.dataset);
        else if (key in node) node[key] = props[key];
        else node.setAttribute(key, props[key]);
      });
    }
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // 色付けのスクリプトが読めなかったときは、素の文字で出す（学習は止めない）。
  function marked(node, text, options) {
    const value = String(text || "");
    if (window.TextMarks) window.TextMarks.append(node, value, options || {});
    else node.append(document.createTextNode(value.replace(/\*\*|__|==|!!|%%|@@/g, "")));
    return node;
  }

  function plain(text) {
    return String(text || "").replace(/\*\*|__|==|!!|%%|@@/g, "").replace(/\s+/g, "");
  }

  // ---- 記録（この端末だけ） ----
  function loadStore() {
    try {
      const raw = window.localStorage.getItem(STORE_KEY);
      const value = raw ? JSON.parse(raw) : null;
      if (value && typeof value === "object") {
        value.answers = value.answers || {};
        value.pos = value.pos || {};
        return value;
      }
    } catch (error) {
      console.warn("記録を読めませんでした", error);
    }
    return { answers: {}, pos: {} };
  }
  let store = loadStore();
  function saveStore() {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(store));
    } catch (error) {
      console.warn("記録を保存できませんでした", error);
    }
  }
  function showAnswerSetting() {
    try {
      return window.localStorage.getItem(SHOW_KEY) !== "0";
    } catch (error) {
      return true;
    }
  }

  // ---- 読み込み ----
  function fetchJson(url) {
    return fetch(url, { cache: "no-cache", headers: { Accept: "application/json" } }).then((response) => {
      if (!response.ok) throw new Error(url + " HTTP " + response.status);
      return response.json();
    });
  }
  function fetchText(url) {
    return fetch(url, { cache: "no-cache" }).then((response) => {
      if (!response.ok) throw new Error(url + " HTTP " + response.status);
      return response.text();
    });
  }

  // 法律家の常識（simple-rules/content.md）を「概念 → 説明」の表にする。学習画面（static/app.js）の読み方と同じ。
  function parseSimpleRules(text) {
    const out = new Map();
    const field = /^\s*(?:-\s+)?(概念|解説|使い方)\s*::\s*(.*)$/;
    let table = "";
    let item = null;
    text.replace(/<!--[\s\S]*?-->/g, "").split(/\r?\n/).forEach((line) => {
      const top = line.match(/^#\s+(sense|ok|ng)\s*$/);
      if (top) {
        table = top[1];
        item = null;
        return;
      }
      const found = line.match(field);
      if (!found || !table) return;
      if (found[1] === "概念") {
        item = { table };
        out.set(found[2].trim(), item);
      }
      if (item) item[found[1]] = found[2].trim();
    });
    return out;
  }

  function loadHintResources() {
    const soft = (promise, label) => promise.catch((error) => {
      console.warn(label + "を読めませんでした", error);
      return null;
    });
    return Promise.all([
      soft(fetchText("../simple-rules/content.md"), "法律家の常識"),
      soft(fetchJson("../assets/card-hints.json"), "カードの常識・テクニック"),
      soft(fetchJson("../technique/word-signals.json"), "語の傾向")
    ]).then(([rules, hints, signals]) => {
      if (rules) state.rules = parseSimpleRules(rules);
      if (hints) state.hints = { cards: hints.cards || {}, kinds: hints.kinds || {} };
      if (signals) {
        state.words = (signals.rules || []).filter((rule) => rule.stable && rule.pattern).map((rule) => Object.assign({}, rule, {
          regex: new RegExp(rule.pattern),
          excludeRegex: rule.exclude ? new RegExp(rule.exclude) : null
        }));
      }
    });
  }

  function loadCards() {
    if (!state.cardsLoaded) {
      state.cardsLoaded = fetchJson("../api/cards").then((data) => {
        (data.explanationCards || []).forEach((card) => state.cards.set(card.id, card));
      }).catch((error) => {
        console.warn("カードを読めませんでした", error);
        state.cardsLoaded = null;
      });
    }
    return state.cardsLoaded;
  }

  function loadQuestions(set) {
    const missing = set.questions.filter((item) => !state.questions.has(item.questionId));
    // 1問読めなくても、ほかの問題は解けるようにする。全部読めなければ失敗として扱う。
    return Promise.all(missing.map((item) =>
      fetchJson("../api/questions?questionId=" + encodeURIComponent(item.questionId)).then((data) => {
        const question = (data.items || [])[0];
        if (question && question.id === item.questionId) state.questions.set(item.questionId, question);
      }).catch((error) => console.warn(item.exam + " を読めませんでした", error))
    )).then(() => {
      if (!set.questions.some((item) => state.questions.has(item.questionId))) throw new Error("問題を1問も読めませんでした");
    });
  }

  // ---- 問題の形 ----
  // 組合せの問題は、問題文の中の「ア．…イ．…」を肢に分ける。最後の肢の後ろの（注）は肢から外す。
  function splitStatements(text) {
    const marks = [...String(text || "").matchAll(/(^|\s)([ア-オ])[．.]/g)];
    if (marks.length < 3) return null;
    const first = marks[0];
    const head = text.slice(0, first.index + first[1].length).trim();
    const items = marks.map((match, i) => {
      const start = match.index + match[0].length;
      const end = i + 1 < marks.length ? marks[i + 1].index : text.length;
      return { label: match[2], text: text.slice(start, end).trim() };
    });
    let note = "";
    const last = items[items.length - 1];
    const cut = last.text.search(/\n+\s*（注）/);
    if (cut >= 0) {
      note = last.text.slice(cut).trim();
      last.text = last.text.slice(0, cut).trim();
    }
    return { head, items, note };
  }

  function buildModel(question, entry) {
    const content = question.content || {};
    const choices = (content.choices || []).map((choice) => ({ label: String(choice.label), text: String(choice.text || "").trim() }));
    const answer = String(question.answer && question.answer.value);
    const asksFalse = Boolean(entry.asksFalse);
    const side = !asksFalse; // 問われている側の正誤（「正しいもの」なら○、「誤っているもの」なら×）
    const text = String(content.question || content.instruction || "");
    const isCombo = (question.task && question.task.kind) === "combination" && choices.length && choices.every((choice) => COMBO.test(choice.text));
    const model = { question, entry, choices, answer, asksFalse, side, combo: false, stem: text, stemNote: "", items: choices, truths: {}, truthOk: true };
    if (isCombo) {
      const split = splitStatements(text);
      if (split) {
        model.combo = true;
        model.stem = split.head;
        model.stemNote = split.note;
        model.items = split.items;
        model.options = choices.map((choice) => ({ label: choice.label, text: choice.text, set: new Set(choice.text.split("・")) }));
        const picked = (model.options.find((option) => option.label === answer) || { set: new Set() }).set;
        model.items.forEach((item) => { model.truths[item.label] = picked.has(item.label) ? side : !side; });
      }
    }
    if (!model.combo) {
      choices.forEach((choice) => { model.truths[choice.label] = choice.label === answer ? side : !side; });
    }
    // sets.json に書いた各肢の理由の○×と、正解から導いた○×が合わなければ、○×を出さない（どちらかが古い）。
    Object.keys(entry.notes || {}).forEach((label) => {
      const mark = String(entry.notes[label]).charAt(0);
      if ((mark === "○") !== model.truths[label]) model.truthOk = false;
    });
    if (!model.truthOk) console.warn(entry.exam + "：理由の○×と正解が合いません。sets.json を作り直してください。");
    return model;
  }

  // 自分で付けた○×から、残る選択肢を出す。
  function remainingOptions(model) {
    const marks = state.marks[model.entry.questionId] || {};
    const known = Object.keys(marks).filter((label) => marks[label]);
    if (model.combo) {
      return model.options.filter((option) => known.every((label) => option.set.has(label) === ((marks[label] === "o") === model.side)));
    }
    const asked = model.side ? "o" : "x";
    const hits = known.filter((label) => marks[label] === asked);
    if (hits.length) return model.choices.filter((choice) => hits.includes(choice.label));
    return model.choices.filter((choice) => !known.includes(choice.label));
  }

  // 組合せの絞り方を計算する。知っている肢の正誤と合う選択肢だけを残す。
  function comboAnalysis(model, cardLabels) {
    const asked = (label) => model.truths[label] === model.side;
    const left = (known) => model.options.filter((option) => known.every((label) => option.set.has(label) === asked(label))).length;
    const labels = model.items.map((item) => item.label);
    const one = labels.filter((label) => asked(label)).map((label) => ({ label, left: left([label]) }));
    const pairs = [];
    let pairCount = 0;
    labels.forEach((a, i) => labels.slice(i + 1).forEach((b) => {
      pairCount += 1;
      if (left([a, b]) === 1) pairs.push(a + "と" + b);
    }));
    return { one, pairs, pairCount, cards: cardLabels.length ? left(cardLabels) : null };
  }

  // ---- 画面：セットの一覧 ----
  function setStats(set) {
    let solved = 0;
    let hiddenTried = 0;
    let hiddenOk = 0;
    set.questions.forEach((item) => {
      const record = store.answers[item.questionId];
      if (!record) return;
      solved += 1;
      if (record.hiddenTries) {
        hiddenTried += 1;
        if (record.lastHiddenOk) hiddenOk += 1;
      }
    });
    return { solved, hiddenTried, hiddenOk };
  }

  function quizLink(set) {
    return "../?questionIds=" + set.questions.map((item) => encodeURIComponent(item.questionId)).join(",") + "&from=practice#quiz";
  }

  function renderSetList() {
    const root = $("set-list");
    root.replaceChildren();
    state.data.sets.forEach((set) => {
      const card = make("article", { className: "set-card", id: "set-" + set.id });
      card.append(make("h3", {}, set.name));
      card.append(make("p", { className: "set-note" }, set.note));
      const stats = setStats(set);
      const line = make("p", { className: "set-stats" });
      line.append(make("strong", {}, set.questions.length + "問"), " · 解いた " + stats.solved + "/" + set.questions.length);
      if (stats.hiddenTried) line.append(" · 答えを隠して " + stats.hiddenOk + "/" + stats.hiddenTried + "問正解");
      card.append(line);
      const actions = make("div", { className: "set-actions" });
      const pos = Math.min(store.pos[set.id] || 0, set.questions.length - 1);
      const start = make("button", { type: "button", className: "primary" }, pos ? "続きから解く（" + (pos + 1) + "問目）" : "解く");
      start.addEventListener("click", () => openSet(set.id, pos));
      actions.append(start);
      if (pos) {
        const again = make("button", { type: "button" }, "最初から");
        again.addEventListener("click", () => openSet(set.id, 0));
        actions.append(again);
      }
      actions.append(make("a", { className: "ghost", href: quizLink(set) }, "本番と同じ画面で解く（答えを隠す）"));
      card.append(actions);
      const details = make("details", { className: "set-questions" });
      details.append(make("summary", {}, "問題の一覧"));
      const list = make("ol");
      set.questions.forEach((item) => {
        const row = make("li");
        row.append(make("a", { href: item.url, target: "_blank", rel: "noopener" }, item.exam), " ");
        row.append(make("span", { className: "tag" }, item.field), " ");
        row.append(make("span", { className: "tag fmt" + (item.asksFalse ? " false" : "") }, item.format));
        list.append(row);
      });
      details.append(list);
      card.append(details);
      root.append(card);
    });
  }

  // ---- 画面：解く ----
  function openSet(setId, index) {
    const set = state.data.sets.find((item) => item.id === setId);
    if (!set) return;
    state.set = set;
    state.index = Math.max(0, Math.min(index || 0, set.questions.length - 1));
    state.resultMode = false;
    $("set-list-wrap").hidden = true;
    $("solver").hidden = false;
    $("solver-title").textContent = set.name;
    $("question-area").replaceChildren(make("p", { className: "loading" }, "問題を読み込んでいます…"));
    window.scrollTo({ top: $("practice-section").offsetTop - 8 });
    // 問題が届いたらすぐ出し、カード（約3MB）は届き次第カード名と覚え方を足す。カードを待って問題を出し遅らせない。
    state.loading = true;
    loadCards().then(() => { if (state.set === set && !state.loading) render(); });
    loadQuestions(set).then(() => {
      if (state.set !== set) return;
      state.loading = false;
      render();
    }).catch((error) => {
      console.warn(error);
      state.loading = false;
      if (state.set !== set) return;
      const box = make("div", { className: "load-error" });
      box.append(make("p", {}, "問題を読み込めませんでした。ラボのAPI（Macの過去問ラボ）につながっているか確かめて、再読み込みしてください。"));
      box.append(make("p", {}, "問題だけなら、問題の一覧の年度・問番号から合格道場で開けます。"));
      $("question-area").replaceChildren(box);
    });
  }

  function closeSet() {
    state.set = null;
    $("solver").hidden = true;
    $("set-list-wrap").hidden = false;
    renderSetList();
  }

  function render() {
    if (!state.set || state.loading) return;
    if (state.resultMode) {
      renderResult();
      return;
    }
    const entry = state.set.questions[state.index];
    store.pos[state.set.id] = state.index;
    saveStore();
    renderProgress();
    const question = state.questions.get(entry.questionId);
    const area = $("question-area");
    if (!question) {
      area.replaceChildren(make("p", { className: "load-error" }, entry.exam + " を読み込めませんでした。"));
      return;
    }
    area.replaceChildren(renderQuestion(buildModel(question, entry)));
  }

  function renderProgress() {
    const bar = $("solver-progress");
    bar.replaceChildren();
    state.set.questions.forEach((item, i) => {
      const record = store.answers[item.questionId];
      const dot = make("button", {
        type: "button",
        className: "dot" + (i === state.index ? " current" : "") + (record ? (record.lastOk ? " ok" : " ng") : ""),
        title: (i + 1) + "問目 " + item.exam + (record ? (record.lastOk ? "（前回 正解）" : "（前回 不正解）") : "")
      }, String(i + 1));
      dot.addEventListener("click", () => {
        state.index = i;
        state.resultMode = false;
        render();
      });
      bar.append(dot);
    });
    $("solver-position").textContent = (state.index + 1) + " / " + state.set.questions.length;
  }

  function truthBadge(value) {
    return make("span", { className: "truth " + (value ? "is-true" : "is-false"), title: "この肢の正誤（答え）" }, value ? "○ 正しい" : "× 誤り");
  }

  function cardLink(id, text) {
    return make("a", { className: "card-link", href: "../?cardId=" + encodeURIComponent(id) + "#study", target: "_blank", rel: "noopener" }, text);
  }

  // 肢の文の中で、誤りとして壊された箇所（カードのテクニックの「箇所」）に印を付ける。
  function renderItemText(node, text, spot) {
    const at = spot ? text.indexOf(spot) : -1;
    if (at < 0) return marked(node, text, { warn: false });
    marked(node, text.slice(0, at), { warn: false });
    node.append(make("span", { className: "broken-spot", title: "誤りとして壊された箇所" }, spot));
    marked(node, text.slice(at + spot.length), { warn: false });
    return node;
  }

  function matchWord(text, subjectId, offIds) {
    const subject = SUBJECT_NAMES[subjectId];
    const rank = (rule) => (rule.subject === "全科目" ? 1 : 0);
    return state.words
      .filter((rule) => (rule.subject === "全科目" || rule.subject === subject) && !offIds.includes(rule.id))
      .sort((a, b) => rank(a) - rank(b))
      .map((rule) => {
        if (rule.excludeRegex && rule.excludeRegex.test(text)) return null;
        const found = text.match(rule.regex);
        return found ? { rule, word: found[0] } : null;
      })
      .find(Boolean) || null;
  }

  function wordLabel(rule, word) {
    const named = (rule.label.match(/「[^」]+」/g) || []).find((part) => part.includes(word));
    if (named) return named;
    return word.length <= 12 ? "「" + word + "」" : rule.label;
  }

  function renderItem(model, item, visible) {
    const entry = model.entry;
    const truth = model.truths[item.label];
    const mainIds = (entry.links && entry.links[item.label]) || [];
    const nearIds = (entry.near && entry.near[item.label]) || [];
    const li = make("li", { className: "item" + (visible && model.truthOk ? (truth ? " is-true" : " is-false") : "") });
    const head = make("div", { className: "item-head" });
    head.append(make("span", { className: "item-label" }, item.label));
    if (visible && model.truthOk) head.append(truthBadge(truth));
    if (mainIds.length) head.append(make("span", { className: "card-flag", title: "この肢を扱う学習カードがあります" }, "★カード"));
    const marks = make("span", { className: "my-marks", role: "group", "aria-label": item.label + " に自分の判定を付ける" });
    marks.append(make("span", { className: "my-label" }, "自分の判定"));
    const mine = (state.marks[entry.questionId] || {})[item.label] || "";
    [["o", "○"], ["x", "×"]].forEach(([value, text]) => {
      const button = make("button", { type: "button", className: "my-mark" + (mine === value ? " on" : ""), title: "自分の判定：" + text }, text);
      button.setAttribute("aria-pressed", mine === value ? "true" : "false");
      button.addEventListener("click", () => {
        const marksForQuestion = state.marks[entry.questionId] || (state.marks[entry.questionId] = {});
        marksForQuestion[item.label] = mine === value ? "" : value;
        render();
      });
      marks.append(button);
    });
    head.append(marks);
    li.append(head);

    // 誤りの肢で、結び付いたカードの「壊された箇所」が肢の文にそのまま出ていれば、赤で囲む。
    let spot = "";
    let breakInfo = null;
    const textKey = plain(item.text);
    if (visible && model.truthOk && !truth) {
      mainIds.some((id) => {
        const hint = state.hints.cards[id];
        const card = state.cards.get(id);
        if (!hint || !hint.break || !card || card.correct) return false;
        const sameA = plain(card.variants && card.variants.a) === textKey;
        if (hint.break.spot && item.text.includes(hint.break.spot)) spot = hint.break.spot;
        if (spot || sameA) breakInfo = hint.break;
        return Boolean(breakInfo);
      });
    }
    li.append(renderItemText(make("p", { className: "item-text" }), item.text, spot));
    if (!visible) return li;

    const detail = make("div", { className: "item-detail" });
    const note = entry.notes && entry.notes[item.label];
    if (note) detail.append(marked(make("p", { className: "item-note" }), String(note).replace(/^[○×]\s*/, "")));
    mainIds.forEach((id) => {
      const card = state.cards.get(id);
      const row = make("div", { className: "item-card" });
      row.append(make("span", { className: "item-card-tag" }, "カード"), cardLink(id, card ? card.subtopic : id));
      if (card && card.memoryPoint) {
        const memory = make("p", { className: "item-memory" });
        memory.append(make("b", {}, "覚え方"), " ");
        marked(memory, card.memoryPoint);
        row.append(memory);
      }
      detail.append(row);
      // カードのAがこの肢と同じ文なら、そのカードに書いた常識（「この問題では」付き）がそのまま使える。
      const hint = state.hints.cards[id];
      if (card && hint && plain(card.variants && card.variants.a) === textKey && card.correct === truth) {
        (hint.rules || []).forEach((rule) => {
          const info = state.rules.get(rule.name);
          if (!info) return;
          const line = make("p", { className: "item-hint" });
          line.append(make("a", { className: "hint-tag rule", href: "../simple-rules/#" + info.table, target: "_blank", rel: "noopener" }, "常識：" + RULE_TABLES[info.table].text), " ");
          line.append(make("b", {}, info["概念"] || rule.name), " ");
          marked(line, rule.apply || "");
          detail.append(line);
        });
      }
    });
    if (!mainIds.length && nearIds.length) {
      const row = make("div", { className: "item-card near" });
      row.append(make("span", { className: "item-card-tag" }, "近い論点"));
      nearIds.forEach((id, i) => {
        const card = state.cards.get(id);
        if (i) row.append("、");
        row.append(cardLink(id, card ? card.subtopic : id));
      });
      detail.append(row);
    }
    if (breakInfo) {
      const kind = state.hints.kinds[breakInfo.kind];
      if (kind) {
        const line = make("p", { className: "item-hint" });
        line.append(make("a", { className: "hint-tag break", href: "../technique/#how", target: "_blank", rel: "noopener" }, "誤りの作り方"), " ");
        line.append(make("b", {}, kind.label), " ");
        marked(line, (breakInfo.where || "") + (spot ? "（赤で囲んだ部分）" : ""));
        detail.append(line);
      }
    }
    const offIds = [];
    mainIds.forEach((id) => {
      const card = state.cards.get(id);
      const hint = state.hints.cards[id];
      if (card && hint && hint.wordsOff && plain(card.variants && card.variants.a) === textKey) offIds.push(...hint.wordsOff);
    });
    const word = matchWord(item.text, model.question.subjectId, offIds);
    if (word && model.truthOk) {
      const toTrue = word.rule.direction === "true";
      const fits = toTrue === truth;
      const line = make("p", { className: "item-hint word" });
      line.append(make("a", { className: "hint-tag word", href: "../technique/#word", target: "_blank", rel: "noopener" }, "語"), " ");
      line.append(make("b", {}, wordLabel(word.rule, word.word) + "は" + (toTrue ? "正しい" : "誤り") + "寄り"), " ");
      line.append(document.createTextNode(word.rule.subject + "の過去問で" + word.rule.count + "肢中" + word.rule.hit + "%（同じ問題の平均" + word.rule.base + "%）。"
        + (fits ? "この肢は傾向どおりです。" : "この肢は傾向と逆なので、語は決め手にしません。")));
      detail.append(line);
    }
    if (detail.children.length) li.append(detail);
    return li;
  }

  function askBanner(model) {
    const entry = model.entry;
    const kind = model.combo ? "の組合せ" : "を1つ";
    const banner = make("div", { className: "ask " + (model.asksFalse ? "ask-false" : "ask-true") });
    banner.append(make("strong", {}, model.asksFalse ? "誤っている（妥当でない）もの" + kind + "選ぶ" : "正しい（妥当な）もの" + kind + "選ぶ"));
    banner.append(make("span", {}, model.asksFalse
      ? "答えは「×の肢」です。○と分かった肢は消します。"
      : "答えは「○の肢」です。×と分かった肢は消します。"));
    if (/事例/.test(entry.format)) banner.append(make("span", {}, "事例の問題です。人物の関係を先に整理します。"));
    return banner;
  }

  // 設問文の「誤っているもの」「妥当なもの」に印を付ける（最後に出てくるものが問いの向き）。
  function renderStem(node, text) {
    const found = [];
    for (const match of text.matchAll(FALSE_PHRASE)) found.push({ match, asksFalse: true });
    for (const match of text.matchAll(TRUE_PHRASE)) found.push({ match, asksFalse: false });
    found.sort((a, b) => a.match.index - b.match.index);
    const hit = found[found.length - 1];
    if (!hit) return marked(node, text, { mark: false, warn: false });
    const { match } = hit;
    marked(node, text.slice(0, match.index), { mark: false, warn: false });
    node.append(make("span", { className: "ask-word " + (hit.asksFalse ? "false" : "true") }, match[0]));
    marked(node, text.slice(match.index + match[0].length), { mark: false, warn: false });
    return node;
  }

  function renderQuestion(model) {
    const entry = model.entry;
    const qid = entry.questionId;
    const record = store.answers[qid];
    const answeredNow = state.justAnswered[qid];
    const showFirst = $("show-answer").checked;
    const visible = showFirst || Boolean(answeredNow);
    const card = make("article", { className: "q-card" });

    const meta = make("div", { className: "q-meta" });
    meta.append(make("a", { className: "q-exam", href: entry.url, target: "_blank", rel: "noopener" }, entry.exam));
    meta.append(make("span", { className: "tag" }, entry.field));
    meta.append(make("span", { className: "tag fmt" + (entry.asksFalse ? " false" : "") }, entry.format));
    if (entry.amended) meta.append(make("span", { className: "tag amended", title: "取得元が法改正に合わせて文言を直した版です" }, "法改正に合わせた改題版"));
    if (record) meta.append(make("span", { className: "tag past" + (record.lastOk ? " ok" : " ng") }, "前回 " + (record.lastOk ? "正解" : "不正解") + "（" + record.tries + "回目まで）"));
    card.append(meta);
    card.append(marked(make("p", { className: "q-aim" }, null), "ねらい：" + entry.aim));
    card.append(askBanner(model));

    const stem = make("div", { className: "q-stem" });
    String(model.stem).split(/\n{2,}/).forEach((part) => {
      if (part.trim()) stem.append(renderStem(make("p"), part.trim()));
    });
    card.append(stem);

    if (visible && model.truthOk) {
      const route = make("section", { className: "route-box" });
      const title = make("h3");
      title.append(make("span", { className: "answer-chip" }, "答え " + model.answer), " 知識が足りないときの絞り方");
      route.append(title);
      route.append(marked(make("p"), entry.route));
      card.append(route);
    }

    const list = make("ol", { className: "items" + (model.combo ? " statements" : "") });
    model.items.forEach((item) => list.append(renderItem(model, item, visible)));
    card.append(list);
    if (model.stemNote) card.append(make("p", { className: "stem-note" }, model.stemNote));

    const left = remainingOptions(model);
    const leftLabels = new Set(left.map((option) => option.label));
    const anyMark = Object.values(state.marks[qid] || {}).some(Boolean);
    if (model.combo) {
      const options = make("ol", { className: "options" });
      model.options.forEach((option) => {
        const row = make("li", {
          className: "option" + (anyMark && !leftLabels.has(option.label) ? " out" : "") + (visible && option.label === model.answer ? " is-answer" : "")
        });
        row.append(make("span", { className: "option-label" }, option.label), make("span", { className: "option-text" }, option.text));
        options.append(row);
      });
      card.append(options);
    }
    const narrow = make("p", { className: "narrow" });
    if (anyMark) {
      narrow.append(make("b", {}, "自分の○×で残る選択肢："));
      narrow.append(left.length ? left.map((option) => option.label).join("・") + (left.length === 1 ? "（決まり）" : "（" + left.length + "つ）") : "なし（○×のどこかが食い違っています）");
    } else {
      narrow.append("分かる肢だけに ○ × を付けると、残る選択肢をここに出します（分からない肢は空けておきます）。");
    }
    card.append(narrow);

    const buttons = make("div", { className: "answer-buttons", role: "group", "aria-label": "答えを選ぶ" });
    model.choices.forEach((choice) => {
      const button = make("button", {
        type: "button",
        className: "answer" + (visible && !answeredNow && choice.label === model.answer ? " answer-hint" : "")
          + (anyMark && !leftLabels.has(choice.label) ? " out" : "")
          + (answeredNow && answeredNow.choice === choice.label ? (answeredNow.ok ? " chosen-ok" : " chosen-ng") : "")
      }, choice.label);
      button.addEventListener("click", () => answer(model, choice.label));
      buttons.append(button);
    });
    card.append(buttons);
    if (answeredNow) {
      card.append(make("p", { className: "verdict " + (answeredNow.ok ? "ok" : "ng") },
        answeredNow.ok ? "正解です（" + model.answer + "）。" : "不正解です。あなたの答え " + answeredNow.choice + " ／ 正解 " + model.answer));
    } else if (!showFirst) {
      card.append(make("p", { className: "verdict-hint" }, "答えを選ぶと、正解・各肢の○×・絞り方が出ます。"));
    }

    if (visible && model.truthOk) card.append(renderTips(model));
    if (visible) card.append(renderCards(entry));

    const nav = make("div", { className: "q-nav" });
    const prev = make("button", { type: "button", disabled: state.index === 0 }, "← 前の問題");
    prev.addEventListener("click", () => move(-1));
    const last = state.index === state.set.questions.length - 1;
    const next = make("button", { type: "button", className: "primary" }, last ? "結果を見る" : "次の問題 →");
    next.addEventListener("click", () => (last ? showResult() : move(1)));
    nav.append(prev, next);
    card.append(nav);
    return card;
  }

  // 使える常識・テクニック。問いの向き、組合せの計算、絞り方に出てくる常識と誤りの作り方、形で選ぶときの実測。
  function renderTips(model) {
    const entry = model.entry;
    const box = make("section", { className: "tips" });
    box.append(make("h3", {}, "この問題で使える常識・テクニック"));
    const list = make("ul");
    const row = (tagText, tagClass, href, title, body) => {
      const li = make("li", { className: "tip" });
      li.append(href
        ? make("a", { className: "hint-tag " + tagClass, href, target: "_blank", rel: "noopener" }, tagText)
        : make("span", { className: "hint-tag " + tagClass }, tagText));
      const main = make("div", { className: "tip-body" });
      main.append(make("p", { className: "tip-title" }, title));
      if (body) main.append(body instanceof Node ? body : marked(make("p", { className: "tip-text" }), body));
      li.append(main);
      list.append(li);
    };

    if (model.combo) {
      const cardLabels = model.items.map((item) => item.label).filter((label) => (entry.links || {})[label]);
      const info = comboAnalysis(model, cardLabels);
      const body = make("div");
      if (info.one.length) {
        const mark = model.side ? "○" : "×";
        body.append(marked(make("p", { className: "tip-text" }), "問われている側（" + mark + "）の肢は" + info.one.map((row) => row.label).join("・") + "です。"
          + info.one.map((row) => row.label + "が" + mark + "と分かれば選択肢は" + row.left + "つ").join("、") + "に減ります。"));
      }
      body.append(marked(make("p", { className: "tip-text" }), "次の2肢が分かれば、答えが1つに決まります：" + (info.pairs.join("、") || "なし") + "（2肢の組" + info.pairCount + "通りのうち" + info.pairs.length + "通り）。"));
      if (info.cards !== null) {
        body.append(marked(make("p", { className: "tip-text" }), "★カードのある肢（" + cardLabels.join("・") + "）だけが分かったとき、残る選択肢は" + info.cards + "つ" + (info.cards === 1 ? "で、それだけで決まります。" : "です。")));
      }
      row("テクニック", "calc", "#guide", "組合せは、全部の肢を判定しなくても決まる", body);
    }

    const seen = new Set();
    quoted(entry.route, "常識").forEach((name) => {
      const info = state.rules.get(name);
      if (!info || seen.has(name)) return;
      seen.add(name);
      row("常識：" + RULE_TABLES[info.table].text, "rule", "../simple-rules/#" + info.table, name, info[RULE_TABLES[info.table].field] || "");
    });
    const kinds = Object.values(state.hints.kinds || {});
    quoted(entry.route, "誤りの作り方").forEach((name) => {
      const kind = kinds.find((item) => item.label === name);
      if (!kind || seen.has(name)) return;
      seen.add(name);
      row("誤りの作り方", "break", "../technique/#how", "「" + kind.label + "」で作られた×の肢", "見る場所：" + kind.look);
    });

    const shape = !model.combo && shapeTip(model);
    if (shape) row("最後の手段", "shape", "../technique/#length", "知識で決まらないときは、形で選ぶ", shape);

    box.append(list);
    if (!list.children.length) return make("span");
    box.append(make("p", { className: "tips-note" }, "常識とテクニックは、知識で切れなかった肢に使います。カードで覚えた知識の○×を、形や語で覆さないでください。"));
    return box;
  }

  function quoted(text, head) {
    const names = [];
    let start = 0;
    const source = String(text || "");
    for (;;) {
      const i = source.indexOf(head + "「", start);
      if (i < 0) return names;
      let k = i + head.length + 1;
      let depth = 1;
      while (k < source.length && depth) {
        if (source[k] === "「") depth += 1;
        else if (source[k] === "」") depth -= 1;
        k += 1;
      }
      names.push(source.slice(i + head.length + 1, k - 1));
      start = k;
    }
  }

  function shapeTip(model) {
    const rule = SHAPE_RULES[model.question.subjectId];
    if (!rule) return "";
    const sizes = model.choices.map((choice) => ({ label: choice.label, size: plain(choice.text).length }));
    const longest = sizes.reduce((a, b) => (b.size > a.size ? b : a));
    const isCommerceTrue = model.question.subjectId === "commercial-law" && !model.asksFalse;
    if (rule === "none") return "行政法では、肢の位置と長さは効きません（正解だった割合は1番20%・最も短い肢18%・最も長い肢21%）。語の傾向（各肢の「語」）と常識で選びます。";
    if (rule === "first") return "基礎知識では、最も短い肢を外しません（正解だった割合25%）。1番は避けてよい（15%）。この問題の正解は" + model.answer + "番" + (model.answer === "1" ? "で、1番を避けると外れます。" : "です。");
    if (isCommerceTrue) return "商法・会社法の「正しいもの」の問題では、最も長い肢を選ぶ手は使えません。";
    return "憲法・民法・商法では、1番（正解だった割合10%）と最も短い肢（13%）を外し、最も長い肢（26%）を選びます。この問題で最も長い肢は" + longest.label + "で、" + (longest.label === model.answer ? "正解でした。" : "外れでした。") + "当たる確率は少し上がるだけなので、本当に分からない問題だけに使います。";
  }

  function renderCards(entry) {
    const box = make("section", { className: "q-cards" });
    if (!entry.cards || !entry.cards.length) return box;
    box.append(make("h3", {}, "この問題のカード"));
    const list = make("ul");
    entry.cards.forEach((item) => {
      const li = make("li");
      li.append(cardLink(item.id, item.label));
      list.append(li);
    });
    box.append(list);
    box.append(make("a", {
      className: "ghost",
      href: "../?cardIds=" + entry.cards.map((item) => encodeURIComponent(item.id)).join(",") + "&from=practice#study",
      target: "_blank",
      rel: "noopener"
    }, "このカードをまとめて解く（" + entry.cards.length + "枚）"));
    return box;
  }

  function answer(model, choice) {
    const qid = model.entry.questionId;
    if (state.justAnswered[qid]) return;
    const ok = choice === model.answer;
    const hidden = !$("show-answer").checked;
    state.justAnswered[qid] = { choice, ok };
    const record = store.answers[qid] || { tries: 0, hiddenTries: 0 };
    record.tries += 1;
    record.lastChoice = choice;
    record.lastOk = ok;
    record.at = new Date().toISOString();
    if (hidden) {
      record.hiddenTries += 1;
      record.lastHiddenOk = ok;
    }
    store.answers[qid] = record;
    saveStore();
    render();
  }

  function move(step) {
    const next = state.index + step;
    if (next < 0 || next >= state.set.questions.length) return;
    state.index = next;
    render();
    $("solver").scrollIntoView({ block: "start" });
  }

  function showResult() {
    state.resultMode = true;
    render();
    $("solver").scrollIntoView({ block: "start" });
  }

  function renderResult() {
    const set = state.set;
    renderProgress();
    $("solver-position").textContent = "結果";
    const area = $("question-area");
    const box = make("article", { className: "q-card result" });
    box.append(make("h3", {}, set.name + "の結果"));
    const stats = setStats(set);
    const wrong = set.questions.filter((item) => {
      const record = store.answers[item.questionId];
      return record && !record.lastOk;
    });
    const summary = make("p", { className: "result-line" });
    summary.append(make("strong", {}, "解いた " + stats.solved + "/" + set.questions.length + "問"), "・前回の答えで正解 " + (stats.solved - wrong.length) + "問");
    if (stats.hiddenTried) summary.append("・答えを隠して解いた " + stats.hiddenTried + "問のうち " + stats.hiddenOk + "問正解");
    box.append(summary);
    if (wrong.length) {
      box.append(make("h4", {}, "前回まちがえた問題"));
      const list = make("ul");
      wrong.forEach((item) => {
        const li = make("li");
        const button = make("button", { type: "button", className: "link-button" }, item.exam + "（" + item.field + "）");
        button.addEventListener("click", () => {
          state.index = set.questions.indexOf(item);
          state.resultMode = false;
          render();
        });
        li.append(button, " あなたの答え " + store.answers[item.questionId].lastChoice);
        list.append(li);
      });
      box.append(list);
      const ids = [];
      wrong.forEach((item) => (item.cards || []).forEach((cardItem) => { if (!ids.includes(cardItem.id)) ids.push(cardItem.id); }));
      if (ids.length) {
        box.append(make("a", {
          className: "primary-link",
          href: "../?cardIds=" + ids.map(encodeURIComponent).join(",") + "&from=practice#study",
          target: "_blank",
          rel: "noopener"
        }, "まちがえた問題のカードをまとめて解く（" + ids.length + "枚）"));
      }
    } else if (stats.solved) {
      box.append(make("p", {}, "前回の答えは全問正解でした。"));
    }
    const next = make("p", { className: "result-next" });
    next.append("次は「解く前に答えを表示する」を外して、答えを隠して解きます。仕上げは ", make("a", { href: quizLink(set) }, "本番と同じ画面（過去問原文タブ）"), " で解きます。");
    box.append(next);
    const actions = make("div", { className: "q-nav" });
    const again = make("button", { type: "button" }, "もう一度このセットを解く");
    again.addEventListener("click", () => {
      state.justAnswered = {};
      state.marks = {};
      openSet(set.id, 0);
    });
    const back = make("button", { type: "button", className: "primary" }, "セットの一覧へ");
    back.addEventListener("click", closeSet);
    actions.append(again, back);
    box.append(actions);
    area.replaceChildren(box);
  }

  // ---- タブ ----
  const TABS = ["practice", "guide", "why"];
  function showTab(name) {
    const tab = TABS.includes(name) ? name : "practice";
    TABS.forEach((key) => {
      $(key + "-section").hidden = key !== tab;
      const button = $(key + "-tab");
      button.setAttribute("aria-selected", key === tab ? "true" : "false");
      button.classList.toggle("active", key === tab);
    });
  }
  function tabFromHash() {
    const hash = location.hash.replace("#", "");
    if (hash === "sets" || hash === "conclusion") return "practice";
    if (["how", "full-year"].includes(hash)) return "why";
    return hash;
  }

  function boot() {
    TABS.forEach((key) => {
      $(key + "-tab").addEventListener("click", () => {
        showTab(key);
        try {
          history.replaceState(null, "", "#" + key);
        } catch (error) {
          /* 履歴を書き換えられなくても表示は変わる */
        }
      });
    });
    window.addEventListener("hashchange", () => showTab(tabFromHash()));
    showTab(tabFromHash());
    const toggle = $("show-answer");
    toggle.checked = showAnswerSetting();
    toggle.addEventListener("change", () => {
      try {
        window.localStorage.setItem(SHOW_KEY, toggle.checked ? "1" : "0");
      } catch (error) {
        /* 保存できなくてもこの画面では効く */
      }
      render();
    });
    $("back-to-sets").addEventListener("click", closeSet);
    document.addEventListener("keydown", (event) => {
      if (!state.set || $("solver").hidden || state.resultMode) return;
      if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "ArrowRight") move(1);
      else if (event.key === "ArrowLeft") move(-1);
      else if (/^[1-5]$/.test(event.key)) {
        const entry = state.set.questions[state.index];
        const question = state.questions.get(entry.questionId);
        if (question) answer(buildModel(question, entry), event.key);
      }
    });
    if (window.TextMarks && $("legend-slot")) $("legend-slot").append(window.TextMarks.legend());
    const hints = loadHintResources();
    fetchJson("sets.json").then((data) => {
      state.data = data;
      renderSetList();
      // ?set=minpo&q=3 で、そのセットの3問目を直接開く（ブックマーク用）。値は一覧にあるIDと数字だけを通す。
      const params = new URLSearchParams(location.search);
      // &answer=hide で答えを隠して開く（2周目のブックマーク用）。この端末の設定としても残す。
      if (params.get("answer") === "hide" || params.get("answer") === "show") {
        toggle.checked = params.get("answer") === "show";
        try {
          window.localStorage.setItem(SHOW_KEY, toggle.checked ? "1" : "0");
        } catch (error) {
          /* 保存できなくてもこの画面では効く */
        }
      }
      const requested = data.sets.find((set) => set.id === params.get("set"));
      const number = Number.parseInt(params.get("q") || "1", 10);
      if (requested) {
        showTab("practice");
        openSet(requested.id, Number.isFinite(number) ? number - 1 : 0);
      }
      hints.then(() => { if (state.set) render(); });
    }).catch((error) => {
      console.warn(error);
      $("set-list").replaceChildren(make("p", { className: "load-error" }, "練習セットを読み込めませんでした。再読み込みしてください。"));
    });
  }

  boot();
})();
