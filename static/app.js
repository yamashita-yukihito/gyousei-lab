(function () {
  "use strict";

  const APP_VERSION = "20260727-2";
  const API = "api";
  const PAGE_SIZE = 250;
  const MASTERY_SCORE = 3;
  const SESSION_KEY = "gyousei_production_session_v1";
  const PENDING_ATTEMPTS_KEY = "gyousei_production_pending_attempts_v1";
  const CARD_PENDING_PREFIX = "gyousei_production_card_pending_v1:";
  const CARD_FAILED_PREFIX = "gyousei_production_card_failed_v1:";

  const state = {
    overview: {},
    dataInventory: {},
    questions: [],
    officialChecks: [],
    studyDeckDefinitions: [],
    activeStudyDeck: null,
    studyDecks: [],
    subjects: [],
    cards: [],
    relatedEvidence: [],
    claudeReviews: [],
    claudeRuns: [],
    similarityPairs: [],
    decisions: new Map(),
    progress: emptyProgress(),
    questionById: new Map(),
    checkById: new Map(),
    evidenceById: new Map(),
    studyById: new Map(),
    studyFiltered: [],
    studyCurrent: null,
    studyIndex: 0,
    studyAnswered: false,
    studyShownAt: null,
    studyStartedAt: null,
    cardServerProgress: {},
    cardProgress: {},
    cardPendingAttempts: [],
    cardStorageMode: "loading",
    cardSaveChain: Promise.resolve(),
    cardApiAvailable: false,
    weaknessAnalysis: { available: false, summary: {}, targets: [] },
    weaknessTargets: new Map(),
    quizPool: [],
    quizIndex: 0,
    quizAnswered: false,
    writtenAllItems: [],
    writtenItems: [],
    writtenIndex: 0,
    writtenDrafts: new Map(),
    cardIndex: 0,
    claudeFiltered: [],
    claudeIndex: 0,
    similarityQueue: [],
    similarityIndex: 0,
    sessionId: loadSessionId(),
    pendingAttempts: loadPendingAttempts(),
    shownAtByQuestion: new Map(),
    startedAtByQuestion: new Map(),
    saving: false,
    loadedTabs: new Set(["study", "cards", "diagrams", "about"]),
    tabLoadPromises: new Map(),
    similarityLoaded: false
  };

  const $ = (id) => document.getElementById(id);

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    bindTabs();
    bindStudyEvents();
    bindQuizEvents();
    bindWrittenEvents();
    bindCardEvents();
    bindClaudeEvents();
    bindSimilarityEvents();
    window.addEventListener("online", () => {
      syncPendingAttempts();
      scheduleCardPendingSync();
    });

    try {
      const cardRequest = fetchAllCardPages()
        .then((payload) => ({ payload, error: null }))
        .catch((error) => ({ payload: {}, error }));
      const weaknessRequest = fetchJson(API + "/learning-analysis")
        .then((payload) => ({ payload, error: null }))
        .catch((error) => ({ payload: { available: false }, error }));
      const [overview, cardResult, weaknessResult] = await Promise.all([
        fetchJson(API + "/overview"),
        cardRequest,
        weaknessRequest
      ]);

      ingestInitialData(overview, cardResult.payload, weaknessResult.payload);
      if (weaknessResult.error) {
        console.warn("弱点分析だけ読み込めませんでした", weaknessResult.error);
      }
      await syncPendingAttempts();
      renderSummary();
      try {
        await setupStudy(cardResult.error);
      } catch (cardSetupError) {
        $("study-loading").hidden = true;
        $("study-error").hidden = false;
        $("study-card").hidden = true;
        $("study-storage-status").textContent = "学習カードAPIを利用できません";
        $("study-storage-status").className = "study-storage error";
        console.warn("学習カード画面だけ初期化できませんでした", cardSetupError);
      }
      try { setupCards(); } catch (cardListError) { console.warn("解説カード一覧だけ初期化できませんでした", cardListError); }
      renderAbout();
      $("global-loading").hidden = true;
      const requestedTab = location.hash.slice(1);
      if (
        requestedTab
        && requestedTab !== "study"
        && document.querySelector('[data-tab="' + CSS.escape(requestedTab) + '"]')
      ) {
        activateTab(requestedTab);
      }
    } catch (error) {
      $("global-loading").hidden = true;
      $("global-error").hidden = false;
      $("global-error").textContent = "本番データを読み込めませんでした。少し待って再読み込みしてください。";
      console.error(error);
    }
  }

  function ingestInitialData(overview, cardPayload, weaknessPayload) {
    state.overview = overview || {};
    state.dataInventory = state.overview.dataInventory || {};
    state.cards = pickArray(cardPayload, ["explanationCards", "cards", "items"]);
    state.studyDeckDefinitions = pickArray(cardPayload, ["studyDecks", "decks"]);
    state.subjects = pickArray(cardPayload, ["subjects", "subjectCatalog"]);
    state.activeStudyDeck = cardPayload.studyDeck || state.studyDeckDefinitions.find((deck) => deck.isDefault || deck.default) || state.studyDeckDefinitions[0] || null;
    state.studyDecks = state.cards.slice();
    state.relatedEvidence = pickArray(cardPayload, ["relatedQuestionEvidence", "evidence"]);
    state.progress = normalizeProgress(
      overview.progress || overview.answerProgress || overview.statistics || overview
    );
    state.evidenceById = new Map(state.relatedEvidence.map((item) => [item.choiceId, item]));
    state.studyById = new Map(state.studyDecks.map((item) => [studyCardId(item), item]));
    ingestWeaknessAnalysis(weaknessPayload);
  }

  function mergeQuestionPayloads(payloads) {
    const questionsById = new Map(state.questions.map((question) => [question.id, question]));
    const checksById = new Map(
      state.officialChecks.map((check) => [check.id || check.questionId, check])
    );
    payloads.forEach((payload) => {
      pickArray(payload, ["questions", "items"]).forEach((question) => {
        questionsById.set(question.id, question);
        (question.officialAnswerChecks || []).forEach((check) => {
          checksById.set(check.id || check.questionId, check);
        });
      });
      pickArray(payload, ["officialAnswerChecks", "answerChecks", "checks"]).forEach((check) => {
        checksById.set(check.id || check.questionId, check);
      });
    });
    state.questions = Array.from(questionsById.values());
    state.officialChecks = Array.from(checksById.values());
    state.questionById = questionsById;
    state.checkById = new Map(
      state.officialChecks.map((check) => [check.questionId, check])
    );
  }

  function pickArray(payload, keys) {
    if (Array.isArray(payload)) return payload;
    for (const key of keys) {
      if (payload && Array.isArray(payload[key])) return payload[key];
    }
    return [];
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) {
      const error = new Error((payload && payload.error) || ("HTTP " + response.status));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload || {};
  }

  function pagedUrl(path, offset) {
    const separator = path.includes("?") ? "&" : "?";
    return path + separator + "limit=" + PAGE_SIZE + "&offset=" + offset;
  }

  async function fetchAllItems(path, keys) {
    const items = [];
    const payloads = [];
    let firstPayload = null;
    let offset = 0;
    let revision = null;
    while (true) {
      const payload = await fetchJson(pagedUrl(path, offset));
      payloads.push(payload);
      if (!firstPayload) firstPayload = payload;
      const pageRevision = payload.bundle && payload.bundle.revision;
      if (revision && pageRevision && revision !== pageRevision) {
        throw new Error("読込中に教材が更新されました。もう一度開いてください。");
      }
      revision = revision || pageRevision || null;
      const pageItems = pickArray(payload, keys);
      items.push(...pageItems);
      const page = payload.page || {};
      if (!page.hasMore) break;
      if (!pageItems.length) throw new Error("ページングされたデータを最後まで取得できません");
      offset += pageItems.length;
    }
    return { payload: firstPayload || {}, payloads, items };
  }

  async function fetchAllCardPages() {
    const result = await fetchAllItems(API + "/cards", ["explanationCards", "cards", "items"]);
    const cards = result.items;
    const evidenceById = new Map();
    result.payloads.forEach((payload) => {
      pickArray(payload, ["relatedQuestionEvidence", "evidence"]).forEach((item) => {
        evidenceById.set(item.choiceId, item);
      });
    });
    return Object.assign({}, result.payload, {
      explanationCards: cards,
      relatedQuestionEvidence: Array.from(evidenceById.values())
    });
  }

  function bindTabs() {
    document.querySelectorAll("[data-tab]").forEach((button) => {
      button.addEventListener("click", () => activateTab(button.dataset.tab));
    });
  }

  function activateTab(name) {
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const active = button.dataset.tab === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    history.replaceState(null, "", "#" + name);
    ensureTabLoaded(name).catch((error) => {
      showLazyStatus(name, "error", "この画面のデータを読み込めませんでした。再読み込みしてください。");
      console.error(error);
    });
    window.scrollTo({ top: document.querySelector(".app-tabs").offsetTop - 4, behavior: motionBehavior() });
  }

  function ensureTabLoaded(name) {
    if (state.loadedTabs.has(name)) return Promise.resolve();
    if (state.tabLoadPromises.has(name)) return state.tabLoadPromises.get(name);
    const loader = {
      quiz: loadQuizTab,
      written: loadWrittenTab,
      claude: loadClaudeTab,
      similarity: loadSimilarityTab
    }[name];
    if (!loader) return Promise.resolve();
    showLazyStatus(name, "loading", "この画面のデータを読み込んでいます…");
    const promise = loader()
      .then(() => {
        state.loadedTabs.add(name);
        showLazyStatus(name, "ready", "");
      })
      .finally(() => state.tabLoadPromises.delete(name));
    state.tabLoadPromises.set(name, promise);
    return promise;
  }

  function showLazyStatus(name, status, message) {
    const node = $(name + "-loading");
    if (!node) return;
    node.hidden = status === "ready";
    node.className = "message-card" + (status === "error" ? " error" : "");
    node.textContent = message;
  }

  async function loadQuizTab() {
    const [regular, multipleBlank] = await Promise.all([
      fetchAllItems(API + "/questions?format=regular", ["questions", "items"]),
      fetchAllItems(API + "/questions?format=multiple_blank", ["questions", "items"])
    ]);
    mergeQuestionPayloads([regular.payload, multipleBlank.payload, {
      items: [...regular.items, ...multipleBlank.items]
    }]);
    setupQuiz();
  }

  async function loadWrittenTab() {
    const written = await fetchAllItems(
      API + "/questions?format=written",
      ["questions", "items"]
    );
    mergeQuestionPayloads([written.payload, { items: written.items }]);
    setupWritten();
  }

  async function loadClaudeTab() {
    const result = await fetchAllItems(
      API + "/claude-reviews",
      ["claudeReviews", "reviews", "items"]
    );
    state.claudeReviews = result.items;
    state.claudeRuns = pickArray(result.payload, ["claudeRuns", "runs"]);
    const questionIds = uniqueSorted(
      state.claudeReviews
        .map((review) => /^(.*):choice:[^:]+$/.exec(review.candidateId || ""))
        .filter(Boolean)
        .map((match) => match[1])
    );
    const contextPayloads = await Promise.all(
      questionIds.map((questionId) =>
        fetchJson(API + "/questions?questionId=" + encodeURIComponent(questionId) + "&limit=1")
          .catch(() => ({}))
      )
    );
    mergeQuestionPayloads(contextPayloads);
    setupClaude();
  }

  async function loadSimilarityTab() {
    const result = await fetchAllItems(
      API + "/similarities",
      ["similarityPairs", "pairs", "items"]
    );
    state.similarityPairs = result.items;
    const latest = result.payload.latestDecisions || result.payload.decisions || result.payload.latest || {};
    state.decisions = normalizeDecisionMap(latest);
    state.similarityPairs.forEach((pair) => {
      if (pair.latestDecision && pair.decisionState !== "stale") {
        state.decisions.set(pair.id, normalizeDecision(pair.latestDecision));
      }
    });
    state.similarityLoaded = true;
    setupSimilarity();
  }

  function renderSummary() {
    const catalog = state.overview.catalog || {};
    const summary = state.overview.summary || {};
    const formatCounts = summary.questionFormatCounts || {};
    const questionCount = safeCount(catalog.questions);
    const writtenCount = safeCount(formatCounts.written);
    const reviewCount = safeCount(catalog.claudeReviews);
    const similarityCount = safeCount(catalog.similarityPairs);
    $("summary-questions").textContent = questionCount + "問";
    $("summary-subjects").textContent = state.subjects.map((subject) => subject.label).join("・") || "科目情報なし";
    $("summary-written").textContent = writtenCount + "問";
    $("summary-claude").textContent = reviewCount + "肢";
    $("summary-claude-note").textContent = "AI一次確認 / 655肢中";
    $("summary-similarity").textContent = similarityCount + "組";
    const coverage = state.dataInventory && state.dataInventory.coverage;
    $("source-data-status").textContent = state.dataInventory.available && coverage
      ? "全分野20年分 " + countLabel(coverage.storedQuestionUnits) + "問を保存・構造化済み"
      : "全分野の保存件数を確認できません";
    const cardCount = state.studyDecks.length;
    $("study-tab-count").textContent = cardCount ? cardCount + "問" : "利用不可";
    $("editorial-tab-count").textContent = cardCount ? cardCount + "論点" : "利用不可";
    $("study-card-status").textContent = cardCount
      ? "A・B二案・C・解説・常識力まで編集済みの学習カードは現在" + cardCount + "論点です。"
      : "学習カードAPIは現在利用できません。";
    $("editorial-card-count").textContent = cardCount ? "現在" + cardCount + "論点あります。" : "現在利用できません。";
    $("pipeline-card-count").textContent = cardCount ? "現在" + cardCount + "論点" : "現在利用不可";
    if ($("production-status")) {
      $("production-status").textContent = "本番掲載は現在" + questionCount + "問です。";
    }
    if ($("quiz-tab-count")) {
      $("quiz-tab-count").textContent =
        safeCount(formatCounts.regular) + safeCount(formatCounts.multiple_blank) + "問";
    }
    if ($("written-tab-count")) $("written-tab-count").textContent = writtenCount + "問";
    if ($("claude-tab-count")) $("claude-tab-count").textContent = reviewCount + "件";
    if ($("similarity-tab-count")) $("similarity-tab-count").textContent = similarityCount + "組";
    updateSimilaritySummary();
  }

  // ---- Frequent-topic OX study cards -----------------------------------

  function ingestWeaknessAnalysis(payload) {
    const analysis = payload && typeof payload === "object" ? payload : {};
    const targets = Array.isArray(analysis.targets)
      ? analysis.targets.filter((target) =>
        target && typeof target.cardId === "string" &&
        ["weak", "watch"].includes(target.status)
      )
      : [];
    state.weaknessAnalysis = {
      available: analysis.available === true,
      analysis: analysis.analysis || {},
      freshness: analysis.freshness || {},
      summary: analysis.summary || {},
      targets
    };
    state.weaknessTargets = new Map(targets.map((target) => [target.cardId, target]));
  }

  async function loadWeaknessAnalysis() {
    try {
      ingestWeaknessAnalysis(await fetchJson(API + "/learning-analysis"));
      renderStudyViewSummary();
      if (state.cardApiAvailable) {
        renderStudyScopeSummary();
        if (state.studyCurrent) renderStudyBadges(state.studyCurrent);
      }
      return true;
    } catch (error) {
      console.warn("弱点分析を更新できませんでした", error);
      return false;
    }
  }

  function bindStudyEvents() {
    $("study-subject").addEventListener("change", () => {
      refreshStudyTopicOptions();
      refreshStudyPool();
    });
    $("study-topic").addEventListener("change", refreshStudyPool);
    $("study-view").addEventListener("change", () => {
      updateStudyViewControls();
      refreshStudyPool();
    });
    $("study-scope").addEventListener("change", refreshStudyPool);
    $("study-order").addEventListener("change", () => { state.studyIndex = 0; });
    $("study-next").addEventListener("click", showNextStudyCard);
    $("study-show-all").addEventListener("click", () => {
      $("study-view").value = "standard";
      $("study-scope").value = "all";
      updateStudyViewControls();
      refreshStudyPool();
    });
    $("study-show-all-topics").addEventListener("click", () => {
      $("study-subject").value = "all";
      refreshStudyTopicOptions();
      $("study-topic").value = "all";
      refreshStudyPool();
    });
    document.querySelectorAll("[data-study-answer]").forEach((button) => {
      button.addEventListener("click", () => answerStudyCard(button));
    });
  }

  async function setupStudy(cardError) {
    $("study-loading").hidden = true;
    if (cardError || !state.studyDecks.length) {
      $("study-error").hidden = false;
      $("study-storage-status").textContent = "学習カードAPIを利用できません";
      $("study-storage-status").className = "study-storage error";
      if (cardError) console.warn("学習カードだけ読み込めませんでした", cardError);
      return;
    }

    state.cardApiAvailable = true;
    const subjectCatalog = state.subjects.length
      ? state.subjects
      : uniqueSorted(state.studyDecks.map((item) => item.subjectId)).map((id) => ({ id, label: id }));
    subjectCatalog.forEach((subject) => {
      if (!subject || !subject.id) return;
      $("study-subject").appendChild(make("option", { value: subject.id }, subject.label || subject.id));
    });
    refreshStudyTopicOptions();
    state.cardPendingAttempts = loadCardPendingAttempts();
    await loadCardProgress();
    rebuildCardProgress();
    updateStudyViewControls();
    refreshStudyPool();
    scheduleCardPendingSync();
  }

  function isWeaknessStudyView() {
    return $("study-view").value === "weakness";
  }

  function updateStudyViewControls() {
    const weakness = isWeaknessStudyView();
    $("study-scope").disabled = weakness;
    $("study-mode-note").textContent = weakness
      ? "苦手・要観察では、現行版カードの回答から要観察・苦手だけを表示します。2回連続で正解して「回復中」になった問題は、このビューから外れます。"
      : "おまかせでは、正解が不正解より3回多くなった問題を「習得済み」として出題から外します。全問題モードならいつでも復習できます。";
    renderStudyViewSummary();
  }

  function renderStudyViewSummary() {
    const node = $("study-view-summary");
    if (!state.weaknessAnalysis.available) {
      node.textContent = "現在は通常ビューのみ利用できます";
      return;
    }
    const summary = state.weaknessAnalysis.summary || {};
    node.textContent = "復習対象 " + safeCount(summary.targetCount) + "問・最新回答まで反映";
  }

  function refreshStudyTopicOptions() {
    const subject = $("study-subject").value;
    const topics = uniqueSorted(
      state.studyDecks
        .filter((item) => subject === "all" || item.subjectId === subject)
        .map((item) => item.topic)
    );
    $("study-topic").replaceChildren(make("option", { value: "all" }, "すべて"));
    populateSelect($("study-topic"), topics, identity);
  }

  async function loadCardProgress() {
    try {
      const snapshot = await fetchJson(API + "/card-progress");
      state.cardServerProgress = normalizeCardProgress(snapshot);
      state.cardStorageMode = "server";
      setCardStorageStatus("サーバーに保存", "saved");
    } catch (error) {
      state.cardServerProgress = {};
      state.cardStorageMode = "pending";
      setCardStorageStatus("履歴は一時保存・あとで再送", "pending");
      console.warn("学習カードの履歴だけ読み込めませんでした", error);
    }
  }

  function getStudyTopicCards() {
    const subject = $("study-subject").value;
    const topic = $("study-topic").value;
    return state.studyDecks.filter((item) =>
      (subject === "all" || item.subjectId === subject) &&
      (topic === "all" || item.topic === topic)
    );
  }

  function getFilteredStudyCards() {
    const cards = getStudyTopicCards();
    if (isWeaknessStudyView()) {
      return cards
        .filter((item) => state.weaknessTargets.has(studyCardId(item)))
        .sort((left, right) => {
          const leftTarget = state.weaknessTargets.get(studyCardId(left));
          const rightTarget = state.weaknessTargets.get(studyCardId(right));
          return safeCount(rightTarget && rightTarget.priority) - safeCount(leftTarget && leftTarget.priority);
        });
    }
    return $("study-scope").value === "all" ? cards : cards.filter((item) => !isStudyMastered(item));
  }

  function refreshStudyPool() {
    state.studyFiltered = getFilteredStudyCards();
    state.studyIndex = 0;
    state.studyCurrent = state.studyFiltered[0] || null;
    renderStudyScopeSummary();
    renderStudyCard();
  }

  function renderStudyScopeSummary() {
    const cards = getStudyTopicCards();
    if (isWeaknessStudyView()) {
      const targets = cards
        .map((item) => state.weaknessTargets.get(studyCardId(item)))
        .filter(Boolean);
      const weak = targets.filter((target) => target.status === "weak").length;
      const watch = targets.filter((target) => target.status === "watch").length;
      $("study-scope-summary").textContent =
        "復習対象 " + targets.length + "問 / 苦手 " + weak + "・要観察 " + watch;
      return;
    }
    const mastered = cards.filter(isStudyMastered).length;
    const target = $("study-scope").value === "all" ? cards.length : cards.length - mastered;
    $("study-scope-summary").textContent = $("study-scope").value === "all"
      ? "全" + cards.length + "問を出題 / 習得済み " + mastered + "問"
      : "出題対象 " + target + "問 / 習得済み " + mastered + "問 / 全" + cards.length + "問";
  }

  function renderStudyEmpty() {
    const cards = getStudyTopicCards();
    const weakness = isWeaknessStudyView();
    const review = $("study-scope").value === "review";
    const topicSelected = $("study-topic").value !== "all" || $("study-subject").value !== "all";
    const allMastered = cards.length && cards.every(isStudyMastered);
    $("study-empty").hidden = false;
    $("study-show-all").hidden = (!review && !weakness) || !cards.length;
    $("study-show-all").textContent = weakness ? "通常ビューで全問題を見る" : "全問題モードで復習する";
    $("study-show-all-topics").hidden = !topicSelected;
    if (weakness && !state.weaknessAnalysis.available) {
      $("study-empty-title").textContent = "弱点分析を読み込めませんでした";
      $("study-empty-message").textContent = "通常ビューはそのまま利用できます。少し待って再読み込みしてください。";
    } else if (weakness && topicSelected) {
      $("study-empty-title").textContent = "この分野に復習対象はありません";
      $("study-empty-message").textContent = "科目・分野を「すべて」に戻すか、通常ビューで学習を続けられます。";
    } else if (weakness) {
      $("study-empty-title").textContent = "現在、復習対象の苦手はありません";
      $("study-empty-message").textContent = "通常ビューで回答すると、要観察・苦手・回復中が自動更新されます。";
    } else if (review && allMastered && topicSelected) {
      $("study-empty-title").textContent = "この分野の問題はすべて習得済みです";
      $("study-empty-message").textContent = "全問題モードで復習するか、科目・分野を「すべて」に戻すと続けられます。";
    } else if (review && allMastered) {
      $("study-empty-title").textContent = "全問題を習得しています";
      $("study-empty-message").textContent = "全問題モードなら、履歴を残したままもう一度解けます。";
    } else {
      $("study-empty-title").textContent = "この条件に合う問題がありません";
      $("study-empty-message").textContent = "出題範囲または分野を変更してください。";
    }
  }

  function renderStudyCard() {
    const item = state.studyCurrent;
    $("study-card").hidden = !item;
    $("study-empty").hidden = Boolean(item);
    if (!item) {
      renderStudyEmpty();
      return;
    }

    const variants = item.variants || {};
    state.studyAnswered = false;
    state.studyShownAt = new Date().toISOString();
    state.studyStartedAt = performance.now();
    $("study-answer-panel").hidden = true;
    $("study-cross-field").hidden = true;
    $("study-save-status").textContent = "";
    $("study-save-status").className = "save-status";
    $("study-feedback").textContent = "";
    $("study-subtopic").textContent = item.subtopic || "";
    renderStudyFrequency(item);
    $("study-position").textContent = (state.studyFiltered.indexOf(item) + 1) + " / " + state.studyFiltered.length;
    $("study-variant-a").textContent = variants.a || "";
    $("study-variant-b").textContent = variants.b || "";
    $("study-variant-b-casual").textContent = variants.bCasual || variants.b || "";
    $("study-variant-b-style").textContent = variants.bCasualStyle || "やわらかくほどく";
    $("study-variant-c").textContent = variants.c || "";
    renderStudyBadges(item);
    renderStudyHistory(item);
    document.querySelectorAll("[data-study-answer]").forEach((button) => {
      button.disabled = false;
      button.classList.remove("correct-answer", "selected-wrong");
    });
  }

  function renderStudyFrequency(item) {
    const node = $("study-frequency");
    const frequency = item.frequency;
    node.hidden = !frequency;
    if (!frequency) {
      node.textContent = "";
      node.removeAttribute("title");
      return;
    }
    node.textContent = "20年の出題傾向：" + frequency.label + " · 関連出題 " + frequency.occurrences + "問（" + frequency.yearCount + "年度）";
    node.title = frequency.scope + "。" + frequency.basis + "。単に同じ分野というだけの問題は回数に含めていません。";
  }

  function renderStudyBadges(item) {
    const relatedCount = (item.relatedPastQuestions || []).length;
    const labels = [item.category, item.topic];
    const weaknessTarget = state.weaknessTargets.get(studyCardId(item));
    if (weaknessTarget) {
      labels.push({
        weak: "苦手",
        watch: "要観察",
      }[weaknessTarget.status]);
    }
    if (relatedCount) labels.push(relatedCount + "件の実際の肢");
    if (item.derivedFromWritten) labels.push("記述式から作成");
    if (isStudyMastered(item)) labels.push("習得済み");
    renderBadges($("study-badges"), labels);
  }

  function renderStudyHistory(item) {
    const progress = getCardProgress(studyCardId(item));
    $("study-history-label").textContent = "正解 " + progress.correct + " / 不正解 " + progress.incorrect;
    $("study-card-correct-count").textContent = progress.correct;
    $("study-card-incorrect-count").textContent = progress.incorrect;
  }

  function answerStudyCard(button) {
    if (state.studyAnswered || !state.studyCurrent) return;
    const item = state.studyCurrent;
    const selected = button.dataset.studyAnswer === "true";
    const isCorrect = selected === Boolean(item.correct);
    const answeredAt = new Date().toISOString();
    const attemptId = "card-attempt-" + uuid();
    const studyMode = isWeaknessStudyView() ? "weakness" : $("study-scope").value;
    const responseMs = state.studyStartedAt === null
      ? null
      : Math.max(0, Math.min(86400000, Math.round(performance.now() - state.studyStartedAt)));
    const attempt = {
      eventId: attemptId,
      attemptId,
      sessionId: state.sessionId,
      studyDeckId: state.activeStudyDeck && state.activeStudyDeck.id || undefined,
      cardId: studyCardId(item),
      answerRevision: studyAnswerRevision(item),
      selectedAnswer: selected,
      mode: studyMode,
      orderMode: $("study-order").value,
      topicFilter: $("study-topic").value,
      scopeMode: studyMode,
      shownAt: state.studyShownAt,
      answeredAt,
      responseMs,
      questionPosition: state.studyFiltered.indexOf(item) + 1,
      appVersion: APP_VERSION
    };

    state.studyAnswered = true;
    queueCardAttempt(attempt);
    rebuildCardProgress();
    renderStudyProgressDisplays();
    scheduleCardPendingSync();
    document.querySelectorAll("[data-study-answer]").forEach((choice) => {
      choice.disabled = true;
      if ((choice.dataset.studyAnswer === "true") === Boolean(item.correct)) choice.classList.add("correct-answer");
    });
    if (!isCorrect) button.classList.add("selected-wrong");
    renderStudyAnswer(item, selected, isCorrect);
  }

  function renderStudyAnswer(item, selected, isCorrect) {
    $("study-feedback").textContent = isCorrect ? "正解です！" : "今回は不正解です";
    $("study-selected-answer").textContent = studyTruthLabel(selected);
    $("study-correct-answer").textContent = studyTruthLabel(Boolean(item.correct));
    $("study-correction-text").textContent = item.correction || "";
    $("study-memory-point").textContent = item.memoryPoint || item.variants && item.variants.c || "";
    const explanations = item.explanations || {};
    const deep = explanations.deepDive || {};
    $("study-normal-explanation").textContent = explanations.normal || "";
    $("study-deep-background").textContent = deep.background || "";
    $("study-deep-trap").textContent = deep.trap || "";
    $("study-deep-example").textContent = deep.example || "";
    $("study-common-sense").textContent = explanations.commonSense || "";
    $("study-answer-summary").classList.toggle("incorrect-result", !isCorrect);
    renderStudyAccuracy();
    renderStudyBasis(item);
    renderStudyRelated(item);
    renderStudyCrossField(item);
    $("study-answer-panel").hidden = false;
    requestAnimationFrame(() => $("study-answer-summary").focus({ preventScroll: true }));
  }

  function renderStudyBasis(item) {
    const legalAsOf = item.lawAsOf || state.activeStudyDeck && state.activeStudyDeck.lawAsOf || state.overview.bundle && state.overview.bundle.legalAsOf || "2026-04-01";
    $("study-law-date").textContent = "令和8年度試験の法令基準日：" + legalAsOf;
    const nodes = (item.legalBasis || []).map((basis) => {
      const li = make("li");
      const link = make("a", { target: "_blank", rel: "noopener noreferrer" }, basis.label || "根拠資料");
      setSafeHref(link, basis.url);
      if (link.hidden) return make("li", {}, basis.label || "根拠資料");
      li.appendChild(link);
      return li;
    });
    $("study-basis-list").replaceChildren(...(nodes.length ? nodes : [make("li", {}, "根拠資料を編集中です。")]));
  }

  function renderStudyRelated(item) {
    const refs = item.relatedPastQuestions || [];
    const origin = item.derivedFromWritten;
    $("study-related-count").textContent = origin
      ? "記述式由来 1問" + (refs.length ? "＋関連肢 " + refs.length + "件" : "")
      : refs.length + "件";
    const nodes = [];
    if (origin) {
      const article = make("article", { className: "study-related-item" });
      article.appendChild(make("p", {}, origin.promptSummary || "記述式で問われた場面"));
      const meta = make("footer");
      meta.appendChild(make("span", {}, "記述式から○×化"));
      meta.appendChild(make("span", {}, origin.label || "記述式"));
      if (safeUrl(origin.officialQuestionUrl)) meta.appendChild(make("a", { href: origin.officialQuestionUrl, target: "_blank", rel: "noopener noreferrer" }, "公式問題を見る ↗"));
      article.appendChild(meta);
      nodes.push(article);
    }
    refs.forEach((ref) => {
      const evidence = state.evidenceById.get(ref.choiceId) || ref;
      const article = make("article", { className: "study-related-item" });
      article.appendChild(make("p", {}, evidence.statementText || evidence.officialOriginalText || ref.statementText || ref.text || "関連する過去問肢"));
      const meta = make("footer");
      const historical = ref.historicalTruth !== undefined ? ref.historicalTruth : evidence.historicalTruth;
      if (typeof historical === "boolean") meta.appendChild(make("span", { className: historical ? "true" : "false" }, "出題時 " + (historical ? "○" : "×")));
      const era = ref.eraYear || evidence.eraYear || (evidence.examYear ? yearLabel(evidence.examYear) : "");
      const questionNumber = ref.questionNumber || evidence.questionNumber;
      const choiceNumber = ref.choiceNumber || evidence.choiceNumber;
      meta.appendChild(make("span", {}, [era, questionNumber ? "問" + questionNumber : null, choiceNumber ? "肢" + choiceNumber : null].filter(Boolean).join(" · ")));
      meta.appendChild(make("span", {}, relationLabel(ref.relation)));
      const url = ref.sourceUrl || evidence.sourceUrl;
      if (safeUrl(url)) meta.appendChild(make("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, "出典を見る ↗"));
      article.appendChild(meta);
      nodes.push(article);
    });
    $("study-related-list").replaceChildren(...(nodes.length ? nodes : [make("p", {}, "関連する実際の肢を編集中です。")]));
  }

  function renderStudyCrossField(item) {
    const comparisons = Array.isArray(item.crossFieldComparisons) ? item.crossFieldComparisons : [];
    const section = $("study-cross-field");
    section.hidden = !comparisons.length;
    $("study-cross-field-count").textContent = comparisons.length ? comparisons.length + "件" : "";
    const nodes = comparisons.map((comparison) => {
      const article = make("article", { className: "cross-field-item" });
      const heading = make("div", { className: "cross-field-heading" });
      heading.appendChild(make("strong", {}, comparison.title));
      heading.appendChild(make("span", {}, [comparison.comparedCategory, comparison.comparedTopic].filter(Boolean).join(" · ")));
      article.appendChild(heading);
      article.appendChild(make("p", {}, comparison.explanation));
      if (comparison.memoryCue) article.appendChild(make("small", {}, "見分け方：" + comparison.memoryCue));
      if (comparison.relatedCardId && state.studyById.has(comparison.relatedCardId)) {
        const button = make("button", { type: "button", className: "cross-field-jump" }, "関連論点を開く");
        button.addEventListener("click", () => openRelatedStudyCard(comparison.relatedCardId));
        article.appendChild(button);
      }
      return article;
    });
    $("study-cross-field-list").replaceChildren(...nodes);
  }

  function openRelatedStudyCard(cardId) {
    const target = state.studyById.get(cardId);
    if (!target) return;
    $("study-subject").value = target.subjectId || "all";
    refreshStudyTopicOptions();
    $("study-topic").value = target.topic || "all";
    $("study-view").value = "standard";
    $("study-scope").value = "all";
    updateStudyViewControls();
    state.studyFiltered = getFilteredStudyCards();
    state.studyCurrent = target;
    state.studyIndex = state.studyFiltered.indexOf(target);
    renderStudyScopeSummary();
    renderStudyCard();
    scrollToPanelCard($("study-card"));
  }

  function renderStudyAccuracy() {
    const item = state.studyCurrent;
    const progress = item ? getCardProgress(studyCardId(item)) : cleanCardProgress({});
    formatStudyAccuracy($("study-question-accuracy"), $("study-question-accuracy-detail"), progress);
    const overall = aggregateCardProgress();
    formatStudyAccuracy($("study-overall-accuracy"), $("study-overall-accuracy-detail"), overall);
    const mastered = Boolean(item && isStudyMastered(item));
    $("study-mastery-status").hidden = !mastered;
    $("study-question-accuracy-card").classList.toggle("mastered", mastered);
  }

  function formatStudyAccuracy(rateNode, detailNode, progress) {
    const total = progress.correct + progress.incorrect;
    rateNode.textContent = total ? Math.round(progress.correct / total * 1000) / 10 + "%" : "—";
    detailNode.textContent = total ? "正解 " + progress.correct + "回 / 全" + total + "回答" : "まだ回答がありません";
  }

  function renderStudyProgressDisplays() {
    renderStudyScopeSummary();
    if (state.studyCurrent) {
      renderStudyHistory(state.studyCurrent);
      renderStudyBadges(state.studyCurrent);
    }
    if (state.studyAnswered) renderStudyAccuracy();
  }

  function showNextStudyCard() {
    const currentId = state.studyCurrent ? studyCardId(state.studyCurrent) : null;
    const cards = getFilteredStudyCards();
    state.studyFiltered = cards;
    renderStudyScopeSummary();
    if (!cards.length) {
      state.studyCurrent = null;
      renderStudyCard();
      scrollToPanelCard($("study-empty"));
      requestAnimationFrame(() => $("study-empty-title").focus());
      return;
    }

    const order = $("study-order").value;
    if (order === "weak") {
      const sorted = cards.slice().sort((a, b) => studyWeaknessScore(b) - studyWeaknessScore(a));
      state.studyCurrent = sorted.find((item) => studyCardId(item) !== currentId) || sorted[0];
    } else if (order === "random") {
      const alternatives = cards.filter((item) => studyCardId(item) !== currentId);
      const pool = alternatives.length ? alternatives : cards;
      state.studyCurrent = pool[Math.floor(Math.random() * pool.length)];
    } else {
      const index = cards.findIndex((item) => studyCardId(item) === currentId);
      if (index >= 0) state.studyCurrent = cards[(index + 1) % cards.length];
      else {
        const sourceIndex = state.studyDecks.findIndex((item) => studyCardId(item) === currentId);
        state.studyCurrent = cards.find((item) => state.studyDecks.indexOf(item) > sourceIndex) || cards[0];
      }
    }
    state.studyIndex = cards.indexOf(state.studyCurrent);
    renderStudyCard();
    scrollToPanelCard($("study-card"));
    requestAnimationFrame(() => $("study-question-heading").focus({ preventScroll: true }));
  }

  function studyWeaknessScore(item) {
    const analyzed = state.weaknessTargets.get(studyCardId(item));
    if (isWeaknessStudyView() && analyzed) return safeCount(analyzed.priority);
    const progress = getCardProgress(studyCardId(item));
    if (!progress.correct && !progress.incorrect) return 2;
    return progress.incorrect * 3 - progress.correct;
  }

  function normalizeCardProgress(payload) {
    let raw = payload && (payload.progress || payload.byCard || payload.cards || payload.byQuestion || payload.items);
    if (!raw && payload && typeof payload === "object" && !Array.isArray(payload)) raw = payload;
    const result = {};
    if (Array.isArray(raw)) {
      raw.forEach((entry) => {
        const id = entry && (entry.cardId || entry.questionId || entry.id);
        if (id) result[id] = cleanCardProgress(entry);
      });
    } else if (raw && typeof raw === "object") {
      Object.entries(raw).forEach(([id, entry]) => {
        if (entry && typeof entry === "object") result[id] = cleanCardProgress(entry);
      });
    }
    return result;
  }

  function hasCardProgressSnapshot(payload) {
    return Boolean(payload && (
      Array.isArray(payload) || payload.progress || payload.byCard || payload.cards ||
      payload.byQuestion || payload.items
    ));
  }

  function cleanCardProgress(entry) {
    return {
      correct: safeCount(entry && entry.correct),
      incorrect: safeCount(entry && entry.incorrect),
      lastAnsweredAt: entry && entry.lastAnsweredAt || null
    };
  }

  function getCardProgress(cardId) {
    return state.cardProgress[cardId] || cleanCardProgress({});
  }

  function rebuildCardProgress() {
    const progress = {};
    Object.entries(state.cardServerProgress).forEach(([id, entry]) => { progress[id] = cleanCardProgress(entry); });
    state.cardPendingAttempts.forEach((attempt) => applyCardAttempt(progress, attempt));
    state.cardProgress = progress;
  }

  function applyCardAttempt(progress, attempt) {
    const item = state.studyById.get(attempt.cardId);
    if (!item) return;
    const entry = progress[attempt.cardId] || cleanCardProgress({});
    if (attempt.selectedAnswer === Boolean(item.correct)) entry.correct += 1;
    else entry.incorrect += 1;
    entry.lastAnsweredAt = attempt.answeredAt || entry.lastAnsweredAt;
    progress[attempt.cardId] = entry;
  }

  function aggregateCardProgress() {
    return state.studyDecks.reduce((total, item) => {
      const progress = getCardProgress(studyCardId(item));
      total.correct += progress.correct;
      total.incorrect += progress.incorrect;
      return total;
    }, cleanCardProgress({}));
  }

  function isStudyMastered(item) {
    const progress = getCardProgress(studyCardId(item));
    return progress.correct - progress.incorrect >= MASTERY_SCORE;
  }

  function queueCardAttempt(attempt) {
    if (state.cardPendingAttempts.some((item) => item.attemptId === attempt.attemptId)) return;
    state.cardPendingAttempts.push(attempt);
    $("study-save-status").className = "save-status";
    try {
      localStorage.setItem(CARD_PENDING_PREFIX + attempt.attemptId, JSON.stringify(attempt));
      setCardStorageStatus("サーバーへ保存中…", "saving");
    } catch (_) {
      setCardStorageStatus("回答は反映済み・端末への一時保存に失敗", "error");
    }
  }

  function loadCardPendingAttempts() {
    const attempts = [];
    try {
      const keys = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key && key.startsWith(CARD_PENDING_PREFIX)) keys.push(key);
      }
      keys.forEach((key) => {
        try {
          const attempt = normalizeCardPendingAttempt(JSON.parse(localStorage.getItem(key)));
          if (attempt) attempts.push(attempt);
          else quarantineCardAttempt({ attemptId: key.slice(CARD_PENDING_PREFIX.length) }, "invalid local data");
        } catch (_) {
          quarantineCardAttempt({ attemptId: key.slice(CARD_PENDING_PREFIX.length) }, "invalid JSON");
        }
      });
    } catch (_) { /* localStorage unavailable */ }
    return attempts.sort((a, b) => String(a.answeredAt).localeCompare(String(b.answeredAt)));
  }

  function normalizeCardPendingAttempt(attempt) {
    if (!attempt || typeof attempt !== "object") return null;
    const attemptId = attempt.attemptId || attempt.eventId;
    if (typeof attemptId !== "string" || typeof attempt.cardId !== "string" ||
        typeof attempt.selectedAnswer !== "boolean" || typeof attempt.answeredAt !== "string") return null;
    return Object.assign({}, attempt, { attemptId, eventId: attempt.eventId || attemptId });
  }

  function scheduleCardPendingSync() {
    state.cardSaveChain = state.cardSaveChain
      .catch((error) => console.warn("前回の学習カード保存を再開します", error))
      .then(() => syncCardPendingAttempts());
  }

  async function syncCardPendingAttempts() {
    if (!state.cardPendingAttempts.length) {
      if (state.cardStorageMode === "server") setCardStorageStatus("サーバーに保存", "saved");
      return true;
    }
    if (!navigator.onLine) {
      setCardStorageStatus("未送信 " + state.cardPendingAttempts.length + "件・通信復帰後に再送", "pending");
      return false;
    }

    setCardStorageStatus("サーバーへ保存中…", "saving");
    let savedAny = false;
    while (state.cardPendingAttempts.length) {
      const attempt = state.cardPendingAttempts[0];
      try {
        const snapshot = await postJson(API + "/card-attempts", attempt);
        state.cardPendingAttempts.shift();
        removeCardPendingAttempt(attempt.attemptId);
        if (hasCardProgressSnapshot(snapshot)) state.cardServerProgress = normalizeCardProgress(snapshot);
        else applyCardAttempt(state.cardServerProgress, attempt);
        state.cardStorageMode = "server";
        savedAny = true;
        rebuildCardProgress();
        renderStudyProgressDisplays();
      } catch (error) {
        if (isPermanentCardAttemptError(error)) {
          state.cardPendingAttempts.shift();
          quarantineCardAttempt(attempt, "API " + error.status);
          rebuildCardProgress();
          renderStudyProgressDisplays();
          $("study-save-status").className = "save-status error";
          $("study-save-status").textContent = "この回答はサーバーに送れませんでした。再送を止め、端末内の要確認データへ移しました。";
          continue;
        }
        state.cardStorageMode = "pending";
        setCardStorageStatus("未送信 " + state.cardPendingAttempts.length + "件・次回再送", "pending");
        $("study-save-status").className = "save-status error";
        $("study-save-status").textContent = "答えは表示しました。履歴は一時保存し、通信が戻ったときに自動送信します。";
        console.warn("学習カードの回答履歴をまだ送信できません", error);
        return false;
      }
    }
    if (savedAny) await loadWeaknessAnalysis();
    setCardStorageStatus(countCardFailedAttempts() ? "サーバー保存・要確認データあり" : "サーバーに保存", countCardFailedAttempts() ? "error" : "saved");
    if (!$("study-save-status").classList.contains("error")) $("study-save-status").textContent = "回答履歴をサーバーに保存しました。";
    return true;
  }

  function isPermanentCardAttemptError(error) {
    const status = Number(error && error.status);
    return status >= 400 && status < 500 && status !== 408 && status !== 429;
  }

  function removeCardPendingAttempt(attemptId) {
    try { localStorage.removeItem(CARD_PENDING_PREFIX + attemptId); } catch (_) { /* no-op */ }
  }

  function quarantineCardAttempt(attempt, reason) {
    const attemptId = attempt && (attempt.attemptId || attempt.eventId) || "invalid-" + uuid();
    removeCardPendingAttempt(attemptId);
    try {
      localStorage.setItem(CARD_FAILED_PREFIX + attemptId, JSON.stringify({
        failedAt: new Date().toISOString(), reason, attempt
      }));
    } catch (_) { /* no-op */ }
  }

  function countCardFailedAttempts() {
    try {
      let count = 0;
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key && key.startsWith(CARD_FAILED_PREFIX)) count += 1;
      }
      return count;
    } catch (_) { return 0; }
  }

  function setCardStorageStatus(text, status) {
    $("study-storage-status").textContent = text;
    $("study-storage-status").className = "study-storage " + status;
  }

  function studyCardId(item) { return String(item && (item.cardId || item.id) || ""); }
  function studyAnswerRevision(item) { return item && (item.answerRevision || item.revision) || "1"; }
  function studyTruthLabel(value) { return value ? "○ 合っている" : "× 間違っている"; }

  // ---- Past questions ---------------------------------------------------

  function bindQuizEvents() {
    $("quiz-subject").addEventListener("change", () => {
      refreshQuizTopicOptions();
      refreshQuizPool();
    });
    ["quiz-year", "quiz-topic", "quiz-format", "quiz-scope"].forEach((id) => {
      $(id).addEventListener("change", refreshQuizPool);
    });
    $("quiz-answer-form").addEventListener("submit", handleQuizSubmit);
    $("quiz-prev").addEventListener("click", () => moveQuiz(-1));
    $("quiz-next").addEventListener("click", () => moveQuiz(1));
    $("quiz-random").addEventListener("click", randomQuiz);
  }

  function setupQuiz() {
    const quizItems = state.questions.filter((q) => q.format !== "written");
    populateQuestionSubjectSelect($("quiz-subject"), quizItems);
    $("quiz-year").replaceChildren(make("option", { value: "all" }, "すべて"));
    populateSelect($("quiz-year"), uniqueSorted(quizItems.map((q) => q.exam.year), true), yearLabel);
    refreshQuizTopicOptions();
    refreshQuizPool();
  }

  function refreshQuizTopicOptions() {
    const previous = $("quiz-topic").value;
    const subject = $("quiz-subject").value;
    const genericLabels = new Set([
      ...state.subjects.map((item) => item.label),
      "多肢選択式",
      "記述式"
    ]);
    const topics = uniqueSorted(
      state.questions
        .filter((question) => question.format !== "written")
        .filter((question) => subject === "all" || questionSubjectId(question) === subject)
        .flatMap((question) => (question.labels || []).filter((label) => !genericLabels.has(label)))
    );
    $("quiz-topic").replaceChildren(make("option", { value: "all" }, "すべて"));
    populateSelect($("quiz-topic"), topics, identity);
    $("quiz-topic").value = topics.includes(previous) ? previous : "all";
  }

  function refreshQuizPool() {
    const subject = $("quiz-subject").value;
    const year = $("quiz-year").value;
    const topic = $("quiz-topic").value;
    const format = $("quiz-format").value;
    const scope = $("quiz-scope").value;
    state.quizPool = state.questions
      .filter((q) => q.format !== "written")
      .filter((q) => subject === "all" || questionSubjectId(q) === subject)
      .filter((q) => year === "all" || String(q.exam.year) === year)
      .filter((q) => topic === "all" || (q.labels || []).includes(topic))
      .filter((q) => format === "all" || q.format === format)
      .filter((q) => scope === "all" || !isMastered(q.id))
      .sort((a, b) => b.exam.year - a.exam.year || a.exam.number - b.exam.number);
    state.quizIndex = clamp(state.quizIndex, 0, Math.max(0, state.quizPool.length - 1));
    $("quiz-pool-count").textContent = state.quizPool.length + "問";
    renderOverallAccuracy();
    renderQuiz();
  }

  function renderQuiz() {
    const question = state.quizPool[state.quizIndex];
    $("quiz-card").hidden = !question;
    $("quiz-empty").hidden = Boolean(question);
    if (!question) return;

    state.quizAnswered = false;
    state.startedAtByQuestion.set(question.id, performance.now());
    state.shownAtByQuestion.set(question.id, new Date().toISOString());
    $("quiz-result").hidden = true;
    $("quiz-save-status").textContent = "";
    $("quiz-check").disabled = false;
    $("quiz-answer-form").reset();
    $("quiz-position").textContent = (state.quizIndex + 1) + " / " + state.quizPool.length;
    $("quiz-title").textContent = question.title;
    renderBadges($("quiz-badges"), [...new Set([
      ...(question.labels || []),
      formatLabel(question.format),
      question.amended ? "改題" : null,
      isMastered(question.id) ? "習得済み" : null
    ].filter(Boolean))]);

    const content = question.content || {};
    const fullQuestion = content.question || content.passage || "";
    const instruction = content.instruction || "";
    const showInstruction = Boolean(instruction && !fullQuestion.includes(instruction));
    $("quiz-instruction").hidden = !showInstruction;
    $("quiz-instruction").textContent = showInstruction ? instruction : "";
    $("quiz-question").textContent = question.format === "multiple_blank" ? "" : fullQuestion;
    $("quiz-question").hidden = question.format === "multiple_blank";
    const globalSourceNote = question.format !== "multiple_blank" && content.sourceNote;
    $("quiz-source-note").hidden = !globalSourceNote;
    $("quiz-source-note").textContent = globalSourceNote || "";

    if (question.format === "multiple_blank") {
      renderBlankQuestion(question);
    } else {
      renderChoiceQuestion(question);
    }
    updateQuestionNav("quiz", state.quizIndex, state.quizPool.length);
  }

  function renderChoiceQuestion(question) {
    const area = $("quiz-answer-area");
    const content = question.content || {};
    const choices = content.choices || [];
    const hasTable = Array.isArray(content.choiceColumns) && content.choiceColumns.length && choices.some((c) => c.cells && c.cells.length);
    if (hasTable) {
      area.replaceChildren(buildChoiceTable(content.choiceColumns, choices));
      return;
    }
    const list = make("div", { className: "choice-list", role: "radiogroup", "aria-labelledby": "quiz-title" });
    choices.forEach((choice) => {
      const label = make("label", { className: "choice-option" });
      const input = make("input", { type: "radio", name: "quiz-option", value: String(choice.label) });
      label.append(input, make("span", { className: "choice-label" }, String(choice.label)), make("span", { className: "choice-text" }, choice.text));
      list.appendChild(label);
    });
    area.replaceChildren(list);
  }

  function buildChoiceTable(columns, choices) {
    const wrap = make("div", { className: "choice-table-wrap", role: "radiogroup", "aria-labelledby": "quiz-title" });
    const table = make("table", { className: "choice-table" });
    table.appendChild(make("caption", { className: "sr-only" }, "解答の選択肢"));
    const head = make("thead");
    const headRow = make("tr");
    headRow.appendChild(make("th", { scope: "col" }, "選択"));
    columns.forEach((column) => headRow.appendChild(make("th", { scope: "col" }, String(column))));
    head.appendChild(headRow);
    const body = make("tbody");
    choices.forEach((choice) => {
      const row = make("tr");
      const selectCell = make("td");
      const label = make("label", { className: "table-radio" });
      label.append(make("input", { type: "radio", name: "quiz-option", value: String(choice.label) }), make("span", {}, String(choice.label)));
      selectCell.appendChild(label);
      row.appendChild(selectCell);
      const byColumn = new Map((choice.cells || []).map((cell) => [String(cell.column), cell.text]));
      columns.forEach((column) => row.appendChild(make("td", {}, byColumn.get(String(column)) || "")));
      row.addEventListener("click", (event) => {
        if (event.target.tagName !== "INPUT") label.querySelector("input").checked = true;
      });
      body.appendChild(row);
    });
    table.append(head, body);
    wrap.appendChild(table);
    return wrap;
  }

  function renderBlankQuestion(question) {
    const content = question.content || {};
    const area = $("quiz-answer-area");
    const passage = make("p", { className: "blank-passage" }, content.passage || "");
    const sourceNote = content.sourceNote ? make("p", { className: "source-note blank-source-note" }, content.sourceNote) : null;
    const selects = make("div", { className: "blank-selects" });
    (content.blanks || []).forEach((blank) => {
      const label = make("label");
      label.appendChild(make("span", {}, "空欄［" + blank + "］"));
      const select = make("select", { name: "blank-" + blank, dataset: { blank: blank } });
      select.appendChild(make("option", { value: "" }, "選ぶ"));
      (content.wordBank || []).forEach((word) => select.appendChild(make("option", { value: String(word.number) }, word.number + ". " + word.text)));
      label.appendChild(select);
      selects.appendChild(label);
    });
    const bank = make("div", { className: "word-bank" });
    (content.wordBank || []).forEach((word) => bank.appendChild(make("span", {}, word.number + ". " + word.text)));
    area.replaceChildren(...[passage, sourceNote, selects, bank].filter(Boolean));
  }

  async function handleQuizSubmit(event) {
    event.preventDefault();
    if (state.quizAnswered || state.saving) return;
    const question = state.quizPool[state.quizIndex];
    const selected = readQuizAnswer(question);
    if (selected === null) {
      window.alert(question.format === "multiple_blank" ? "4つの空欄をすべて選んでください。" : "選択肢を1つ選んでください。");
      return;
    }
    const isCorrect = answersEqual(selected, answerValue(question.answer));
    state.quizAnswered = true;
    $("quiz-check").disabled = true;
    showQuizResult(question, selected, isCorrect);
    lockQuizAnswer(question, selected);
    await saveAttempt({
      questionId: question.id,
      format: question.format,
      selectedAnswer: selected,
      isCorrect: isCorrect,
      mode: $("quiz-scope").value,
      responseMs: elapsedMs(question.id),
      shownAt: state.shownAtByQuestion.get(question.id),
      questionPosition: state.quizIndex + 1
    }, $("quiz-save-status"));
    renderQuestionAccuracy(question.id);
    renderOverallAccuracy();
  }

  function readQuizAnswer(question) {
    if (question.format === "multiple_blank") {
      const result = {};
      const selects = Array.from($("quiz-answer-area").querySelectorAll("select[data-blank]"));
      if (!selects.length || selects.some((select) => !select.value)) return null;
      selects.forEach((select) => { result[select.dataset.blank] = Number(select.value); });
      return result;
    }
    const checked = document.querySelector('input[name="quiz-option"]:checked');
    return checked ? Number(checked.value) : null;
  }

  function showQuizResult(question, selected, isCorrect) {
    $("quiz-result").hidden = false;
    $("quiz-feedback").textContent = isCorrect ? "正解です" : "今回はちがいました";
    $("quiz-feedback").className = "feedback" + (isCorrect ? "" : " wrong");
    $("quiz-selected-answer").textContent = formatAnswer(selected, question);
    $("quiz-correct-answer").textContent = formatAnswer(answerValue(question.answer), question);
    $("quiz-answer-source").textContent = verificationLabel(state.checkById.get(question.id));
    renderQuestionAccuracy(question.id);
    renderOverallAccuracy();
    $("quiz-result").focus({ preventScroll: true });
  }

  function moveQuiz(delta) {
    if (!state.quizPool.length) return;
    const current = state.quizPool[state.quizIndex];
    if (delta > 0 && $("quiz-scope").value === "review" && current && isMastered(current.id)) {
      refreshQuizPool();
      scrollToPanelCard($("quiz-card"));
      return;
    }
    state.quizIndex = (state.quizIndex + delta + state.quizPool.length) % state.quizPool.length;
    renderQuiz();
    scrollToPanelCard($("quiz-card"));
  }

  function randomQuiz() {
    if ($("quiz-scope").value === "review") refreshQuizPool();
    if (state.quizPool.length < 2) return;
    let next = state.quizIndex;
    while (next === state.quizIndex) next = Math.floor(Math.random() * state.quizPool.length);
    state.quizIndex = next;
    renderQuiz();
    scrollToPanelCard($("quiz-card"));
  }

  function lockQuizAnswer(question, selected) {
    const correct = answerValue(question.answer);
    $("quiz-answer-area").querySelectorAll("input, select").forEach((control) => { control.disabled = true; });
    if (question.format === "multiple_blank") return;
    $("quiz-answer-area").querySelectorAll('input[name="quiz-option"]').forEach((input) => {
      const container = input.closest("label.choice-option") || input.closest("tr");
      if (!container) return;
      const value = Number(input.value);
      container.classList.toggle("correct-choice", value === Number(correct));
      container.classList.toggle("selected-wrong", input.checked && value !== Number(correct));
    });
  }

  // ---- Written questions ------------------------------------------------

  function bindWrittenEvents() {
    $("written-subject").addEventListener("change", () => {
      saveCurrentWrittenDraft();
      refreshWrittenOptions();
    });
    $("written-year").addEventListener("change", () => {
      saveCurrentWrittenDraft();
      state.writtenIndex = state.writtenItems.findIndex((q) => q.id === $("written-year").value);
      renderWritten();
    });
    $("written-answer").addEventListener("input", updateWrittenCount);
    $("written-reveal").addEventListener("click", revealWrittenAnswer);
    $("written-prev").addEventListener("click", () => moveWritten(-1));
    $("written-next").addEventListener("click", () => moveWritten(1));
    document.querySelectorAll("[data-written-grade]").forEach((button) => {
      button.addEventListener("click", () => gradeWritten(button.dataset.writtenGrade === "true"));
    });
  }

  function setupWritten() {
    state.writtenAllItems = state.questions
      .filter((q) => q.format === "written")
      .sort((a, b) => b.exam.year - a.exam.year || a.exam.number - b.exam.number);
    populateQuestionSubjectSelect($("written-subject"), state.writtenAllItems);
    refreshWrittenOptions();
  }

  function refreshWrittenOptions() {
    const subject = $("written-subject").value;
    const current = state.writtenItems[state.writtenIndex];
    state.writtenItems = state.writtenAllItems.filter(
      (question) => subject === "all" || questionSubjectId(question) === subject
    );
    state.writtenIndex = current
      ? Math.max(0, state.writtenItems.findIndex((question) => question.id === current.id))
      : 0;
    $("written-year").replaceChildren(...state.writtenItems.map((question) =>
      make(
        "option",
        { value: question.id },
        question.exam.era + "・問" + question.exam.number + "・" + subjectLabel(questionSubjectId(question))
      )
    ));
    renderWritten();
  }

  function renderWritten() {
    const question = state.writtenItems[state.writtenIndex];
    $("written-card").hidden = !question;
    if (!question) {
      $("written-position").textContent = "0 / 0";
      return;
    }
    const content = question.content || {};
    state.startedAtByQuestion.set(question.id, performance.now());
    state.shownAtByQuestion.set(question.id, new Date().toISOString());
    $("written-year").value = question.id;
    $("written-position").textContent = (state.writtenIndex + 1) + " / " + state.writtenItems.length;
    $("written-title").textContent = question.title + "・記述式";
    $("written-question").textContent = content.question || "";
    $("written-reference-wrap").hidden = !content.referenceText;
    $("written-reference").textContent = content.referenceText || "";
    $("written-limit").textContent = (content.characterLimit || 40) + "字程度";
    $("written-answer").value = state.writtenDrafts.get(question.id) || "";
    $("written-model").hidden = true;
    $("written-save-status").textContent = "";
    $("written-reveal").disabled = false;
    renderBadges($("written-badges"), [question.exam.era, "問" + question.exam.number, ...(question.labels || [])]);
    setSafeHref($("written-source-link"), question.source && question.source.url);
    updateWrittenCount();
    renderWrittenAccuracy(question.id);
    updateQuestionNav("written", state.writtenIndex, state.writtenItems.length);
  }

  function updateWrittenCount() {
    const count = Array.from($("written-answer").value).length;
    const question = state.writtenItems[state.writtenIndex];
    const limit = question && question.content && question.content.characterLimit || 40;
    $("written-count").textContent = count + "字";
    $("written-count").classList.toggle("over", count > limit + 10);
    if (question) state.writtenDrafts.set(question.id, $("written-answer").value);
  }

  function revealWrittenAnswer() {
    const question = state.writtenItems[state.writtenIndex];
    const check = state.checkById.get(question.id) || {};
    const provider = question.content && question.content.modelAnswer || answerValue(question.answer);
    const official = check.officialAnswer;
    $("written-model").hidden = false;
    $("written-official-wrap").hidden = !official;
    $("written-official").textContent = displayValue(official);
    $("written-provider-wrap").hidden = !provider;
    $("written-provider").textContent = formatModelAnswer(provider);
    $("written-check-note").textContent = verificationLabel(check);
    $("written-reveal").disabled = true;
    renderWrittenAccuracy(question.id);
    $("written-model").focus({ preventScroll: true });
    $("written-model").scrollIntoView({ behavior: motionBehavior(), block: "nearest" });
  }

  async function gradeWritten(isCorrect) {
    if (state.saving) return;
    const question = state.writtenItems[state.writtenIndex];
    const status = $("written-save-status");
    if (!$("written-answer").value.trim()) {
      status.className = "save-status error";
      status.textContent = "回答履歴に残すには、先に自分の答案を入力してください。";
      return;
    }
    const saved = await saveAttempt({
      questionId: question.id,
      format: "written",
      answerText: $("written-answer").value,
      isCorrect: isCorrect,
      mode: "self_grade",
      responseMs: elapsedMs(question.id),
      shownAt: state.shownAtByQuestion.get(question.id),
      questionPosition: state.writtenIndex + 1
    }, status);
    if (saved) {
      status.textContent = isCorrect ? "「だいたい書けた」としてサーバーに保存しました。" : "「もう一度やる」としてサーバーに保存しました。";
      renderWrittenAccuracy(question.id);
    }
  }

  function moveWritten(delta) {
    saveCurrentWrittenDraft();
    state.writtenIndex = (state.writtenIndex + delta + state.writtenItems.length) % state.writtenItems.length;
    renderWritten();
    scrollToPanelCard($("written-card"));
  }

  function saveCurrentWrittenDraft() {
    const current = state.writtenItems[state.writtenIndex];
    if (current) state.writtenDrafts.set(current.id, $("written-answer").value);
  }

  // ---- Editorial cards --------------------------------------------------

  function bindCardEvents() {
    $("editorial-prev").addEventListener("click", () => moveCard(-1));
    $("editorial-next").addEventListener("click", () => moveCard(1));
  }

  function setupCards() {
    const selector = $("card-selector");
    selector.replaceChildren();
    state.cards.forEach((card, index) => {
      const button = make("button", { type: "button" }, card.subtopic || ("論点" + (index + 1)));
      button.addEventListener("click", () => { state.cardIndex = index; renderCard(); });
      selector.appendChild(button);
    });
    renderCard();
  }

  function renderCard() {
    const card = state.cards[state.cardIndex];
    $("editorial-card").hidden = !card;
    if (!card) return;
    const variants = card.variants || {};
    const explanations = card.explanations || {};
    const deep = explanations.deepDive || {};
    $("editorial-position").textContent = (state.cardIndex + 1) + " / " + state.cards.length;
    $("editorial-subtopic").textContent = card.subtopic || "";
    renderBadges($("editorial-badges"), [card.category, card.topic].filter(Boolean));
    $("editorial-a").textContent = variants.a || "";
    $("editorial-b").textContent = variants.b || "";
    $("editorial-b-casual").textContent = variants.bCasual || "";
    $("editorial-b-style").textContent = "もうひとつの言い方 · " + (variants.bCasualStyle || "やわらかくほどく");
    $("editorial-c").textContent = variants.c || "";
    $("editorial-correct").textContent = card.correct ? "○ 合っている" : "× 間違っている";
    $("editorial-correction").textContent = card.correction || "";
    $("editorial-normal").textContent = explanations.normal || "";
    $("editorial-background").textContent = deep.background || "";
    $("editorial-trap").textContent = deep.trap || "";
    $("editorial-example").textContent = deep.example || "";
    $("editorial-common-sense").textContent = explanations.commonSense || "";
    renderRelatedEvidence(card);
    renderEditorialCrossField(card);
    Array.from($("card-selector").children).forEach((button, index) => button.classList.toggle("active", index === state.cardIndex));
    updateQuestionNav("editorial", state.cardIndex, state.cards.length);
  }

  function renderRelatedEvidence(card) {
    const refs = card.relatedPastQuestions || [];
    $("editorial-related-count").textContent = refs.length + "件";
    const container = $("editorial-related");
    const nodes = refs.map((ref) => {
      const evidence = state.evidenceById.get(ref.choiceId) || {};
      const item = make("article", { className: "related-item" });
      const text = ref.statementText || ref.text || ref.questionText || evidence.statementText;
      if (text) item.appendChild(make("p", {}, text));
      else item.appendChild(make("p", {}, "関連する過去問肢（本文データの関連付け待ち）"));
      const examYear = ref.examYear || evidence.examYear;
      const eraYear = ref.eraYear || evidence.eraYear;
      const questionNumber = ref.questionNumber || evidence.questionNumber;
      const choiceNumber = ref.choiceNumber || evidence.choiceNumber;
      const historicalTruth = ref.historicalTruth !== undefined ? ref.historicalTruth : evidence.historicalTruth;
      const meta = [eraYear || (examYear ? yearLabel(examYear) : null), questionNumber ? "問" + questionNumber : null, choiceNumber ? "肢" + choiceNumber : null, truthLabel(historicalTruth)].filter(Boolean).join(" · ");
      item.appendChild(make("small", {}, meta || relationLabel(ref.relation)));
      return item;
    });
    container.replaceChildren(...nodes);
  }

  function renderEditorialCrossField(card) {
    const comparisons = Array.isArray(card.crossFieldComparisons) ? card.crossFieldComparisons : [];
    $("editorial-cross-field-section").hidden = !comparisons.length;
    $("editorial-cross-field-count").textContent = comparisons.length ? comparisons.length + "件" : "";
    const nodes = comparisons.map((comparison) => {
      const item = make("article", { className: "related-item cross-field-item" });
      item.appendChild(make("strong", {}, comparison.title));
      item.appendChild(make("p", {}, comparison.explanation));
      item.appendChild(make("small", {}, [comparison.comparedCategory, comparison.comparedTopic, comparison.memoryCue ? "見分け方：" + comparison.memoryCue : null].filter(Boolean).join(" · ")));
      return item;
    });
    $("editorial-cross-field").replaceChildren(...nodes);
  }

  function moveCard(delta) {
    state.cardIndex = (state.cardIndex + delta + state.cards.length) % state.cards.length;
    renderCard();
    scrollToPanelCard($("editorial-card"));
  }

  // ---- Claude review ----------------------------------------------------

  function bindClaudeEvents() {
    $("claude-search").addEventListener("input", refreshClaudeList);
  }

  function setupClaude() {
    renderClaudeRuns();
    refreshClaudeList();
  }

  function renderClaudeRuns() {
    const nodes = state.claudeRuns.map((run) => {
      const status = run.status || "unknown";
      const card = make("article", { className: "run-card" + (status === "rate_limited" ? " rate-limited" : "") });
      card.appendChild(make("strong", {}, runStatusLabel(status) + " · " + (run.itemCount || 0) + "件"));
      const models = Array.isArray(run.models) && run.models.length ? run.models.join(" + ") : (run.model || run.modelActual || run.modelRequested || "Claude Fable");
      card.appendChild(make("span", {}, [models, dateTimeLabel(run.finishedAt || run.recordedAt)].filter(Boolean).join(" / ")));
      if (status === "rate_limited") card.appendChild(make("small", {}, "レート制限で停止。結果データには含めていません。"));
      else if (status !== "completed") card.appendChild(make("small", {}, "この実行から監査結果は採用していません。"));
      return card;
    });
    $("claude-runs").replaceChildren(...nodes);
  }

  function refreshClaudeList() {
    const query = normalizeText($("claude-search").value);
    state.claudeFiltered = state.claudeReviews.filter((review) => {
      if (!query) return true;
      const context = getCandidateContext(review.candidateId);
      return normalizeText([review.candidateId, context.title, context.statement, ...(context.labels || []), ...(review.relationNotes || [])].join(" ")).includes(query);
    });
    state.claudeIndex = clamp(state.claudeIndex, 0, Math.max(0, state.claudeFiltered.length - 1));
    const nodes = state.claudeFiltered.map((review, index) => {
      const context = getCandidateContext(review.candidateId);
      const button = make("button", { type: "button", className: "record-button" + (index === state.claudeIndex ? " active" : "") });
      button.append(make("strong", {}, context.title || review.candidateId), make("small", {}, context.statement || "肢文を参照できません"));
      button.addEventListener("click", () => { state.claudeIndex = index; renderClaudeDetail(); });
      return button;
    });
    $("claude-list").replaceChildren(...nodes);
    renderClaudeDetail();
  }

  function renderClaudeDetail() {
    const review = state.claudeFiltered[state.claudeIndex];
    $("claude-detail").hidden = !review;
    if (!review) return;
    const context = getCandidateContext(review.candidateId);
    $("claude-position").textContent = (state.claudeIndex + 1) + " / " + state.claudeFiltered.length;
    $("claude-title").textContent = context.title || review.candidateId;
    $("claude-statement").textContent = context.statement || "元の肢文を参照できません。";
    renderBadges($("claude-badges"), ["7/18時点：" + statusLabel(review.currentLawStatus), "AI一次確認", context.officialStatus].filter(Boolean));
    $("claude-historical").textContent = (truthLabel(context.historicalTruth) || "判定を取得できません") + (context.officialStatus ? "（" + context.officialStatus + "）" : "");
    $("claude-current").textContent = (truthLabel(review.currentTruth) || statusLabel(review.currentLawStatus)) + "（2026年7月18日時点・AI一次確認）";
    renderTextList($("claude-notes"), review.relationNotes || [], "確認メモはありません。");
    renderTextList($("claude-risks"), review.risks || [], "Claudeが挙げた注意点はありません。");
    const citations = (review.citationCandidates || []).map((citation) => {
      const node = make("article", { className: "citation" });
      const label = [citation.title, citation.locator].filter(Boolean).join(" · ");
      if (safeUrl(citation.url)) node.appendChild(make("a", { href: citation.url, target: "_blank", rel: "noopener noreferrer" }, label || "一次資料"));
      else node.appendChild(make("strong", {}, label || "一次資料候補"));
      if (citation.relevance) node.appendChild(make("p", {}, citation.relevance));
      return node;
    });
    $("claude-citations").replaceChildren(...citations.length ? citations : [make("p", {}, "参照候補はありません。")]);
    Array.from($("claude-list").children).forEach((button, index) => button.classList.toggle("active", index === state.claudeIndex));
  }

  function getCandidateContext(candidateId) {
    const match = /^(.*):choice:([^:]+)$/.exec(candidateId || "");
    if (!match) return {};
    const question = state.questionById.get(match[1]);
    if (!question) return {};
    const choice = ((question.content && question.content.choices) || []).find((item) => String(item.label) === String(match[2]));
    const answer = Number(answerValue(question.answer));
    const label = Number(match[2]);
    let historicalTruth = null;
    if (question.task && question.task.kind === "select_true") historicalTruth = label === answer;
    if (question.task && question.task.kind === "select_false") historicalTruth = label !== answer;
    const check = state.checkById.get(question.id);
    return {
      title: question.title + "・肢" + match[2],
      statement: choice && choice.text,
      labels: question.labels,
      historicalTruth,
      officialStatus: check && ["exact", "match-after-normalization", "mismatch"].includes(check.status)
        ? "公式照合済み"
        : "公式未照合"
    };
  }

  // ---- Similarity curation ---------------------------------------------

  function bindSimilarityEvents() {
    $("similarity-scope").addEventListener("change", refreshSimilarityQueue);
    $("similarity-show-deferred").addEventListener("click", () => {
      $("similarity-scope").value = "deferred";
      refreshSimilarityQueue();
    });
    $("similarity-related-open").addEventListener("click", () => {
      const willOpen = $("similarity-relations").hidden;
      $("similarity-relations").hidden = !willOpen;
      $("similarity-related-open").setAttribute("aria-expanded", String(willOpen));
    });
    document.querySelectorAll("[data-decision]").forEach((button) => button.addEventListener("click", () => saveSimilarity(button.dataset.decision, null)));
    document.querySelectorAll("[data-relation]").forEach((button) => button.addEventListener("click", () => saveSimilarity("related", button.dataset.relation)));
    $("similarity-prev").addEventListener("click", () => moveSimilarity(-1));
    $("similarity-next").addEventListener("click", nextUndecidedSimilarity);
  }

  function setupSimilarity() {
    state.similarityPairs.sort((a, b) => tierRank(a.tier) - tierRank(b.tier) || (b.reviewScore || b.score || 0) - (a.reviewScore || a.score || 0));
    refreshSimilarityQueue();
  }

  function refreshSimilarityQueue() {
    const scope = $("similarity-scope").value;
    state.similarityQueue = state.similarityPairs.filter((pair) => {
      const saved = state.decisions.get(pair.id);
      if (scope === "all") return true;
      if (scope === "deferred") return saved && saved.decision === "defer";
      return !saved;
    });
    state.similarityIndex = clamp(state.similarityIndex, 0, Math.max(0, state.similarityQueue.length - 1));
    renderSimilarity();
    updateSimilaritySummary();
  }

  function renderSimilarity() {
    const pair = state.similarityQueue[state.similarityIndex];
    $("similarity-card").hidden = !pair;
    $("similarity-empty").hidden = Boolean(pair);
    if (!pair) {
      const scope = $("similarity-scope").value;
      const deferred = Array.from(state.decisions.values()).filter((item) => item.decision === "defer").length;
      const unreviewed = state.similarityPairs.filter((item) => !state.decisions.has(item.id)).length;
      $("similarity-empty-title").textContent = scope === "deferred" && unreviewed
        ? "AIが候補を仕分け中です"
        : scope === "deferred" ? "保留中の候補はありません" : scope === "all" ? "類似候補がありません" : "未確認の候補はすべて仕分けました";
      $("similarity-empty-copy").textContent = scope === "deferred" && unreviewed
        ? "残り" + unreviewed + "組をAI側で確認しています。判断が必要な候補だけ、ここに表示されます。"
        : deferred ? "保留が" + deferred + "組あります。あとでまとめて見直せます。" : "表示範囲を「すべて」にすると、判定を見直せます。";
      $("similarity-show-deferred").hidden = scope === "deferred" || deferred === 0;
      return;
    }
    const decision = state.decisions.get(pair.id);
    $("similarity-position").textContent = (state.similarityIndex + 1) + " / " + state.similarityQueue.length;
    renderBadges($("similarity-badges"), [pair.tier === "strict" ? "類似度が高い" : "広めに抽出", ...(pair.commonLabels || [])]);
    $("similarity-left-meta").textContent = pastItemMeta(pair.left);
    $("similarity-right-meta").textContent = pastItemMeta(pair.right);
    $("similarity-left").textContent = pair.left.statementText || "";
    $("similarity-right").textContent = pair.right.statementText || "";
    $("similarity-left-truth").textContent = similarityTruthText(pair.left);
    $("similarity-right-truth").textContent = similarityTruthText(pair.right);
    $("similarity-reason").textContent = pair.reasonSummary || "文章の重なりなどから自動抽出されました。";
    $("similarity-score").textContent = "自動抽出スコア：" + percentage(pair.reviewScore || pair.score);
    $("similarity-current-decision").hidden = !decision;
    $("similarity-current-decision").textContent = decision
      ? "現在の判定：" + decisionLabel(decision.decision, decision.relationType)
        + "（ここで選び直すと更新されます）"
        + (decision.note ? "\n判定メモ：" + decision.note : "")
      : "";
    $("similarity-relations").hidden = true;
    $("similarity-related-open").setAttribute("aria-expanded", "false");
    $("similarity-save-status").textContent = "";
    updateQuestionNav("similarity", state.similarityIndex, state.similarityQueue.length);
  }

  async function saveSimilarity(decision, relationType) {
    if (state.saving) return;
    const pair = state.similarityQueue[state.similarityIndex];
    if (!pair) return;
    if (decision === "merge" && pair.left.inferredTruth !== pair.right.inferredTruth) {
      const proceed = window.confirm("2つは出題時の○×が異なります。本当に「同じ論点グループにまとめる」で保存しますか？");
      if (!proceed) return;
    }
    const status = $("similarity-save-status");
    setSaving(true, status, "保存しています…");
    const previous = state.decisions.get(pair.id);
    const payload = {
      decisionId: "pair-" + uuid(),
      pairId: pair.id,
      pairContentDigest: pair.pairContentDigest,
      decision,
      relationType: relationType || null,
      decidedAt: new Date().toISOString(),
      supersedes: previous && (previous.decisionId || previous.eventId || previous.event_id) || null
    };
    try {
      const response = await postJson(API + "/similarity-decisions", payload);
      const saved = response.decision || response.latestDecision || Object.assign({}, payload, { saved: true });
      state.decisions.set(pair.id, saved);
      status.className = "save-status";
      status.textContent = "保存しました：" + decisionLabel(decision, relationType);
      updateSimilaritySummary();
      await new Promise((resolve) => window.setTimeout(resolve, 450));
      refreshSimilarityQueue();
    } catch (error) {
      status.className = "save-status error";
      status.textContent = "保存できませんでした。通信を確認してもう一度押してください。";
      console.error(error);
    } finally {
      setSaving(false);
    }
  }

  function moveSimilarity(delta) {
    if (state.saving || !state.similarityQueue.length) return;
    state.similarityIndex = (state.similarityIndex + delta + state.similarityQueue.length) % state.similarityQueue.length;
    renderSimilarity();
    scrollToPanelCard($("similarity-card"));
  }

  function nextUndecidedSimilarity() { moveSimilarity(1); }

  function updateSimilaritySummary() {
    const review = state.overview.similarityReview || {};
    const catalog = state.overview.catalog || {};
    const byDecision = review.byDecision || {};
    const completed = state.similarityLoaded
      ? Array.from(state.decisions.values()).filter((item) => item.decision && item.decision !== "defer").length
      : safeCount(byDecision.merge) + safeCount(byDecision.related) + safeCount(byDecision.reject);
    const deferred = state.similarityLoaded
      ? Array.from(state.decisions.values()).filter((item) => item.decision === "defer").length
      : safeCount(byDecision.defer);
    const total = state.similarityLoaded
      ? state.similarityPairs.length
      : safeCount(review.total || catalog.similarityPairs);
    $("summary-similarity-note").textContent = completed + "組確認 / 保留" + deferred + "組";
    $("similarity-reviewed").textContent = completed + " / " + total;
    $("similarity-progress-bar").style.width = (total ? completed / total * 100 : 0) + "%";
    $("similarity-progress-track").setAttribute("aria-valuenow", String(completed));
    $("similarity-progress-track").setAttribute("aria-valuemax", String(total));
    if ($("pipeline-similarity-status")) {
      $("pipeline-similarity-status").textContent = completed + deferred === total
        ? "AI整理完了（本人確認 " + deferred + "組）"
        : "AI判定を反映中（残り " + Math.max(0, total - completed - deferred) + "組）";
    }
  }

  // ---- Progress ---------------------------------------------------------

  async function saveAttempt(attempt, statusElement) {
    setSaving(true, statusElement, "回答をサーバーに保存しています…");
    const payload = Object.assign({
      eventId: "attempt-" + uuid(),
      sessionId: state.sessionId,
      answeredAt: new Date().toISOString(),
      appVersion: APP_VERSION
    }, attempt);
    const queued = queuePendingAttempt(payload);
    try {
      const response = await postJson(API + "/attempts", payload);
      removePendingAttempt(payload.eventId);
      if (response.progress || response.statistics || response.overall || response.byQuestion) {
        state.progress = normalizeProgress(response.progress || response.statistics || response);
      } else {
        applyOptimisticProgress(attempt.questionId, attempt.isCorrect);
      }
      if (statusElement) {
        statusElement.className = statusElement.className.replace(/\berror\b/g, "").trim();
        statusElement.textContent = "回答履歴をサーバーに保存しました。";
      }
      return true;
    } catch (error) {
      if (statusElement) {
        statusElement.classList.add("error");
        statusElement.textContent = queued
          ? "答えは表示しました。履歴は一時保存し、通信が戻ったときに自動送信します。"
          : "答えは表示しましたが、回答履歴を保存できませんでした。";
      }
      console.error(error);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function postJson(url, payload) {
    return fetchJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Gyousei-Client": "web-v1" },
      body: JSON.stringify(payload)
    });
  }

  function loadPendingAttempts() {
    try {
      const parsed = JSON.parse(localStorage.getItem(PENDING_ATTEMPTS_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item.eventId === "string").slice(-100) : [];
    } catch (_) {
      return [];
    }
  }

  function persistPendingAttempts() {
    try {
      localStorage.setItem(PENDING_ATTEMPTS_KEY, JSON.stringify(state.pendingAttempts.slice(-100)));
      return true;
    } catch (_) {
      return false;
    }
  }

  function queuePendingAttempt(payload) {
    state.pendingAttempts = state.pendingAttempts.filter((item) => item.eventId !== payload.eventId);
    state.pendingAttempts.push(payload);
    return persistPendingAttempts();
  }

  function removePendingAttempt(eventId) {
    state.pendingAttempts = state.pendingAttempts.filter((item) => item.eventId !== eventId);
    persistPendingAttempts();
  }

  async function syncPendingAttempts() {
    if (!state.pendingAttempts.length || !navigator.onLine) return;
    for (const payload of [...state.pendingAttempts]) {
      try {
        const response = await postJson(API + "/attempts", payload);
        removePendingAttempt(payload.eventId);
        if (response.overall || response.byQuestion) state.progress = normalizeProgress(response);
      } catch (error) {
        console.warn("保留中の回答履歴は次回再送します", error);
        break;
      }
    }
  }

  function normalizeProgress(raw) {
    const overallRaw = raw.overall || raw.total || {};
    const byQuestionRaw = raw.byQuestion || raw.questions || {};
    const byQuestion = {};
    if (Array.isArray(byQuestionRaw)) {
      byQuestionRaw.forEach((item) => { if (item.questionId) byQuestion[item.questionId] = cleanProgressItem(item); });
    } else if (byQuestionRaw && typeof byQuestionRaw === "object") {
      Object.entries(byQuestionRaw).forEach(([id, item]) => { byQuestion[id] = cleanProgressItem(item || {}); });
    }
    return { overall: cleanProgressItem(overallRaw), byQuestion };
  }

  function cleanProgressItem(item) {
    const correct = safeCount(item.correct);
    const incorrect = safeCount(item.incorrect);
    const attempts = safeCount(item.attempts || item.total) || correct + incorrect;
    const graded = correct + incorrect;
    return { attempts, correct, incorrect, accuracy: graded ? correct / graded : null, lastAnsweredAt: item.lastAnsweredAt || null };
  }

  function emptyProgress() { return { overall: cleanProgressItem({}), byQuestion: {} }; }

  function applyOptimisticProgress(questionId, isCorrect) {
    if (typeof isCorrect !== "boolean") return;
    const item = state.progress.byQuestion[questionId] || cleanProgressItem({});
    item.attempts += 1;
    item.correct += isCorrect ? 1 : 0;
    item.incorrect += isCorrect ? 0 : 1;
    item.accuracy = item.correct / (item.correct + item.incorrect);
    state.progress.byQuestion[questionId] = item;
    const overall = state.progress.overall;
    overall.attempts += 1;
    overall.correct += isCorrect ? 1 : 0;
    overall.incorrect += isCorrect ? 0 : 1;
    overall.accuracy = overall.correct / (overall.correct + overall.incorrect);
  }

  function renderQuestionAccuracy(questionId) {
    const item = state.progress.byQuestion[questionId] || cleanProgressItem({});
    const graded = item.correct + item.incorrect;
    $("question-accuracy").textContent = graded ? percentage(item.correct / graded) : "—";
    $("question-accuracy-detail").textContent = graded ? "正解" + item.correct + "回 / 全" + graded + "回" : "まだ回答履歴がありません";
  }

  function renderOverallAccuracy() {
    const item = aggregateProgress((question) => question.format !== "written");
    const graded = item.correct + item.incorrect;
    const rate = graded ? percentage(item.correct / graded) : "—";
    $("overall-accuracy").textContent = rate;
    $("result-overall-accuracy").textContent = rate;
    $("result-overall-detail").textContent = graded ? "正解" + item.correct + "回 / 全" + graded + "回" : "まだ回答履歴がありません";
  }

  function renderWrittenAccuracy(questionId) {
    const item = state.progress.byQuestion[questionId] || cleanProgressItem({});
    const total = aggregateProgress((question) => question.format === "written");
    const graded = item.correct + item.incorrect;
    const totalGraded = total.correct + total.incorrect;
    $("written-question-accuracy").textContent = graded ? percentage(item.correct / graded) : "—";
    $("written-question-accuracy-detail").textContent = graded ? "書けた" + item.correct + "回 / 全" + graded + "回" : "自己評価はまだありません";
    $("written-overall-accuracy").textContent = totalGraded ? percentage(total.correct / totalGraded) : "—";
    $("written-overall-accuracy-detail").textContent = totalGraded ? "書けた" + total.correct + "回 / 全" + totalGraded + "回" : "自己評価はまだありません";
  }

  function aggregateProgress(predicate) {
    const result = { attempts: 0, correct: 0, incorrect: 0 };
    Object.entries(state.progress.byQuestion).forEach(([questionId, progress]) => {
      const question = state.questionById.get(questionId);
      if (!question || !predicate(question)) return;
      result.attempts += progress.attempts;
      result.correct += progress.correct;
      result.incorrect += progress.incorrect;
    });
    return result;
  }

  function isMastered(questionId) {
    const item = state.progress.byQuestion[questionId] || cleanProgressItem({});
    return item.correct - item.incorrect >= MASTERY_SCORE;
  }

  // ---- About ------------------------------------------------------------

  function renderAbout() {
    const summary = state.overview.summary || state.overview.bundleSummary || {};
    const catalog = state.overview.catalog || {};
    const status = $("data-status-list");
    const formatCounts = summary.questionFormatCounts || {};
    const questionCount = safeCount(catalog.questions);
    const reviewCount = safeCount(catalog.claudeReviews);
    const similarityCount = safeCount(catalog.similarityPairs);
    const cardCount = safeCount(catalog.explanationCards);
    const answerStatuses = summary.officialAnswerStatusCounts || {};
    const matchedAnswers =
      safeCount(answerStatuses.exact) + safeCount(answerStatuses["match-after-normalization"]);
    const rows = [
      ["本番掲載中の過去問", questionCount + "問（択一式" + safeCount(formatCounts.regular) + "・多肢選択式" + safeCount(formatCounts.multiple_blank) + "・記述式" + safeCount(formatCounts.written) + "）"],
      ["収録科目", state.subjects.map((subject) => subject.label).join("・") || "未設定"],
      ["取得検証", "抽出エラー0件・警告0件"],
      ["公式正答との照合", "一致" + matchedAnswers + "問・文言差" + safeCount(answerStatuses.mismatch) + "問・公式未照合" + (safeCount(answerStatuses.unavailable) + safeCount(answerStatuses.unsupported)) + "問"],
      ["Claude監査", reviewCount + "肢（2026年7月18日時点・AI一次確認）"],
      ["類似候補", similarityCount + "組（自動抽出。人の仕分け前を含む）"],
      ["学習用解説カード", cardCount + "論点"],
      ["生成時刻", dateTimeLabel(state.overview.generatedAt || (state.overview.bundle && state.overview.bundle.generatedAt) || summary.generatedAt) || "APIで管理"]
    ];
    const nodes = [];
    rows.forEach(([term, value]) => nodes.push(make("dt", {}, term), make("dd", {}, value)));
    status.replaceChildren(...nodes);
    if ($("pipeline-similarity-copy")) {
      $("pipeline-similarity-copy").textContent =
        similarityCount + "件をAI側で判定し、どうしても迷う候補だけを本人確認に残します。";
    }
    if ($("similarity-heading-copy")) {
      $("similarity-heading-copy").textContent =
        similarityCount + "件はAI側で先に仕分けます。この画面には、文脈だけでは判断しきれなかった少数の候補を残します。";
    }
    if ($("claude-coverage-count")) {
      $("claude-coverage-count").textContent =
        "現在の監査範囲：" + reviewCount + "肢 / 対象655肢";
    }
    renderDataInventory();
  }

  function renderDataInventory() {
    const inventory = state.dataInventory || {};
    const unavailable = $("inventory-unavailable");
    const content = $("inventory-content");
    if (!inventory.available) {
      content.hidden = true;
      unavailable.hidden = false;
      unavailable.textContent = inventory.message || "保存データの集計を表示できません。";
      $("pipeline-source-count").textContent = "保存データの集計を確認できません。";
      $("inventory-coverage-copy").textContent = "保存データと本番掲載データは別管理です。";
      return;
    }

    unavailable.hidden = true;
    content.hidden = false;
    const coverage = inventory.coverage || {};
    const scopes = Array.isArray(inventory.scopes) ? inventory.scopes : [];
    const allScope = scopes.find((scope) => scope.id === "all") || scopes[scopes.length - 1];
    const allTotals = allScope && allScope.totals || {};
    const yearCopy = coverage.firstExamYear + "〜" + coverage.lastExamYear + "年";
    $("inventory-coverage-copy").textContent =
      yearCopy + "の" + coverage.yearCount + "年分。全試験は年60問ですが、公開本文に含まれない問題などを除き、現在" +
      countLabel(coverage.storedQuestionUnits) + "問を保存しています。";
    $("pipeline-source-count").textContent =
      yearCopy + "の全分野" + countLabel(coverage.storedQuestionUnits) +
      "問を保存済み。本番画面に載せる教材は、小分けに検証してから追加します。";

    const scopeCards = scopes.map((scope) => {
      const totals = scope.totals || {};
      const article = make("article");
      article.append(
        make("span", {}, scope.label),
        make("strong", {}, countLabel(totals.questionUnits) + "問"),
        make("small", {}, "通常肢 " + countLabel(totals.regularChoiceCount) + " / ○×候補 " + countLabel(totals.safeOxChoiceCount))
      );
      return article;
    });
    $("inventory-scope-cards").replaceChildren(...scopeCards);

    const rows = (allScope && Array.isArray(allScope.subjects) ? allScope.subjects : []).map((subject) => {
      const row = make("tr");
      [
        subject.subjectLabel,
        countLabel(subject.questionUnits),
        countLabel(subject.regularChoiceCount),
        countLabel(subject.safeOxChoiceCount),
        countLabel(subject.multipleBlankQuestions) + "問 / " + countLabel(subject.blankSlotCount) + "空欄",
        countLabel(subject.writtenQuestions)
      ].forEach((value) => row.appendChild(make("td", {}, value)));
      return row;
    });
    $("inventory-subject-rows").replaceChildren(...rows);
    const totalRow = make("tr");
    [
      "全分野",
      countLabel(allTotals.questionUnits),
      countLabel(allTotals.regularChoiceCount),
      countLabel(allTotals.safeOxChoiceCount),
      countLabel(allTotals.multipleBlankQuestions) + "問 / " + countLabel(allTotals.blankSlotCount) + "空欄",
      countLabel(allTotals.writtenQuestions)
    ].forEach((value) => totalRow.appendChild(make("td", {}, value)));
    $("inventory-subject-total").replaceChildren(totalRow);

    const omissions = Array.isArray(coverage.omissions) ? coverage.omissions : [];
    const publicMissing = omissions.find((item) => item.kind === "publicTextUnavailable");
    const providerMissing = omissions.find((item) => item.kind === "providerIndexAbsent");
    const missingParts = [
      "理論上は60問×" + coverage.yearCount + "年＝" + countLabel(coverage.expectedQuestionUnits) + "問です。"
    ];
    if (publicMissing) {
      missingParts.push("問58〜60の本文が" + countLabel(publicMissing.questionUnits) + "問分、著作権上の理由で公開データにありません。");
    }
    if (providerMissing) {
      missingParts.push(providerMissing.examYear + "年問" + providerMissing.questionNumber + "が取得元一覧にないため、さらに1問少なくなっています。");
    }
    $("inventory-missing-note").textContent = missingParts.join(" ");
  }

  // ---- Helpers ----------------------------------------------------------

  function questionSubjectId(question) {
    if (question && typeof question.subjectId === "string" && question.subjectId) return question.subjectId;
    const labels = question && Array.isArray(question.labels) ? question.labels : [];
    const subject = state.subjects.find((item) => item && labels.includes(item.label));
    return subject ? subject.id : "";
  }

  function subjectLabel(subjectId) {
    const subject = state.subjects.find((item) => item && item.id === subjectId);
    return subject ? subject.label : subjectId;
  }

  function populateQuestionSubjectSelect(select, questions) {
    const subjectIds = uniqueSorted(questions.map(questionSubjectId));
    select.replaceChildren(make("option", { value: "all" }, "すべて"));
    subjectIds.forEach((subjectId) => {
      select.appendChild(make("option", { value: subjectId }, subjectLabel(subjectId)));
    });
  }

  function populateSelect(select, values, formatter) {
    values.forEach((value) => select.appendChild(make("option", { value: String(value) }, formatter(value))));
  }

  function uniqueSorted(values, numericDescending) {
    const unique = [...new Set(values.filter((value) => value !== null && value !== undefined && value !== ""))];
    return unique.sort(numericDescending ? (a, b) => Number(b) - Number(a) : (a, b) => String(a).localeCompare(String(b), "ja"));
  }

  function make(tag, attributes, text) {
    const node = document.createElement(tag);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      if (key === "className") node.className = value;
      else if (key === "dataset") Object.entries(value).forEach(([dataKey, dataValue]) => { node.dataset[dataKey] = dataValue; });
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    });
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function renderBadges(container, labels) {
    const unique = [...new Set(labels.filter(Boolean))];
    container.replaceChildren(...unique.map((label, index) => make("span", { className: "badge" + (index === 0 ? "" : index === 1 ? " blue" : " gray") }, label)));
  }

  function renderTextList(container, values, emptyText) {
    const items = values.length ? values : [emptyText];
    container.replaceChildren(...items.map((value) => make("li", {}, value)));
  }

  function updateQuestionNav(prefix, index, length) {
    const prev = $(prefix + "-prev");
    const next = $(prefix + "-next");
    if (prev) prev.disabled = length < 2;
    if (next) next.disabled = length < 2;
  }

  function setSaving(saving, statusElement, message) {
    state.saving = saving;
    if (statusElement && message) statusElement.textContent = message;
    document.querySelectorAll(".decision-area button, .self-grade button, #similarity-prev, #similarity-next, #quiz-prev, #quiz-next, #quiz-random, #written-prev, #written-next").forEach((button) => { button.disabled = saving; });
    if (!saving) {
      updateQuestionNav("quiz", state.quizIndex, state.quizPool.length);
      updateQuestionNav("written", state.writtenIndex, state.writtenItems.length);
      updateQuestionNav("similarity", state.similarityIndex, state.similarityQueue.length);
    }
  }

  function loadSessionId() {
    let value = null;
    try { value = localStorage.getItem(SESSION_KEY); } catch (_) { /* no-op */ }
    if (!value) {
      value = "session-" + uuid();
      try { localStorage.setItem(SESSION_KEY, value); } catch (_) { /* no-op */ }
    }
    return value;
  }

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + "-" + Math.random().toString(36).slice(2);
  }

  function elapsedMs(questionId) {
    const startedAt = state.startedAtByQuestion.get(questionId);
    return startedAt === undefined ? null : Math.max(0, Math.round(performance.now() - startedAt));
  }
  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function safeCount(value) { return Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : 0; }
  function identity(value) { return String(value); }
  function yearLabel(year) { return Number(year) === 2019 ? "令和元年" : Number(year) >= 2019 ? "令和" + (Number(year) - 2018) + "年" : "平成" + (Number(year) - 1988) + "年"; }
  function formatLabel(format) { return ({ regular: "択一式", multiple_blank: "多肢選択式", written: "記述式" })[format] || format; }
  function truthLabel(value) { return value === true ? "○ 正しい" : value === false ? "× 誤り" : ""; }
  function statusLabel(value) { return ({ confirmed: "現行法でも同じ", changed: "法改正等で変化", uncertain: "要確認" })[value] || value || "要確認"; }
  function relationLabel(value) { return ({ same_rule: "同じルール", wording_variant: "聞き方違い", opposite_claim: "逆の言い方", exception: "原則と例外", contrast: "比べて覚える", same_topic: "同じテーマ" })[value] || value || "関連問題"; }
  function runStatusLabel(value) { return ({ completed: "完了", rate_limited: "レート制限", claude_failed: "実行失敗", invalid_stream: "応答形式エラー", invalid_outer_json: "応答形式エラー" })[value] || value; }
  function tierRank(value) { return value === "strict" ? 0 : 1; }
  function percentage(value) { return Number.isFinite(Number(value)) ? Math.round(Number(value) * 100) + "%" : "—"; }
  function countLabel(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString("ja-JP") : "—"; }
  function normalizeText(value) { return String(value || "").normalize("NFKC").toLowerCase().replace(/\s+/g, ""); }
  function dateTimeLabel(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("ja-JP", { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date); }
  function pastItemMeta(item) { return yearLabel(item.examYear) + "・問" + item.questionNumber + "・肢" + item.choiceLabel; }

  function answerValue(answer) {
    if (!answer) return null;
    return answer.values !== undefined ? answer.values : answer.value;
  }

  function answersEqual(left, right) {
    if (left && typeof left === "object" && right && typeof right === "object") {
      const leftKeys = Object.keys(left).sort();
      const rightKeys = Object.keys(right).sort();
      return leftKeys.length === rightKeys.length && leftKeys.every((key, index) => key === rightKeys[index] && Number(left[key]) === Number(right[key]));
    }
    return Number(left) === Number(right);
  }

  function formatAnswer(value, question) {
    if (value && typeof value === "object") {
      const bank = new Map((((question || {}).content || {}).wordBank || []).map((word) => [Number(word.number), word.text]));
      return Object.entries(value).map(([blank, number]) => blank + "＝" + number + "（" + (bank.get(Number(number)) || "") + "）").join(" / ");
    }
    if (value === null || value === undefined) return "—";
    const choice = ((((question || {}).content || {}).choices) || []).find((item) => String(item.label) === String(value));
    return "選択肢 " + value + (choice && choice.text ? "\n" + choice.text : "");
  }

  function formatModelAnswer(value) {
    return displayValue(value).replace(/\s+(例[②③④])/g, "\n\n$1");
  }

  function similarityTruthText(item) {
    const check = state.checkById.get(item.questionId);
    const base = "出題時の判定：" + truthLabel(item.inferredTruth);
    if (!check || !["exact", "match-after-normalization", "mismatch"].includes(check.status)) {
      return base + "（取得元正答から推定・公式未照合）";
    }
    if (check.status === "exact" || check.status === "match-after-normalization") return base + "（公式正答と照合済み）";
    return base + "（正答の照合に注意が必要）";
  }

  function displayValue(value) {
    if (typeof value === "string" || typeof value === "number") return String(value);
    if (Array.isArray(value)) return value.map(displayValue).join("\n");
    if (value && typeof value === "object") {
      if (value.value !== undefined) return displayValue(value.value);
      if (value.values !== undefined) return displayValue(value.values);
      return Object.entries(value).map(([key, item]) => key + "：" + displayValue(item)).join("\n");
    }
    return "";
  }

  function verificationLabel(check) {
    if (!check) return "正解は取得元データによるものです（公式照合状態を取得できません）。";
    if (check.status === "exact" || check.status === "match-after-normalization") return "試験実施機関の公式正答と一致しています。";
    if (check.status === "mismatch") return check.format === "written" ? "公式答案例と取得元の答案例に文言差があります。誤答とは限らないため、両方を表示しています。" : "取得元正答と公式正答に差があるため、要確認です。";
    if (check.status === "unavailable") {
      if (check.reason === "official_reconciliation_not_run_for_subject") {
        return "この科目は公式正答との照合前のため、取得元に掲載された正答を表示しています。";
      }
      return "平成28・29年度は公式正答を取得できていないため、取得元に掲載された正答を表示しています。";
    }
    if (check.status === "unsupported") return "この問題は公式正答との照合対象外のため、取得元に掲載された正答を表示しています。";
    return "正答の照合状態：" + (check.status || "不明");
  }

  function decisionLabel(decision, relationType) {
    if (decision === "merge") return "同じ論点グループにまとめる";
    if (decision === "related") return "関連問題として残す（" + relationLabel(relationType) + "）";
    if (decision === "reject") return "関係ない";
    if (decision === "defer") return "あとで見る";
    return decision || "未確認";
  }

  function normalizeDecisionMap(raw) {
    const map = new Map();
    if (Array.isArray(raw)) raw.forEach((item) => { if (item.pairId || item.pair_id) map.set(item.pairId || item.pair_id, normalizeDecision(item)); });
    else if (raw && typeof raw === "object") Object.entries(raw).forEach(([id, item]) => map.set(id, normalizeDecision(item || {})));
    return map;
  }

  function normalizeDecision(item) {
    return Object.assign({}, item, {
      decision: item.decision,
      relationType: item.relationType || item.relation_type || null,
      decisionId: item.decisionId || item.decision_id || item.eventId || item.event_id || null,
      eventId: item.eventId || item.event_id || item.decisionId || item.decision_id || null
    });
  }

  function safeUrl(value) {
    if (!value) return false;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && (
        /\.go\.jp$/i.test(url.hostname) || /\.lg\.jp$/i.test(url.hostname) ||
        url.hostname === "elaws.e-gov.go.jp" || url.hostname === "laws.e-gov.go.jp" ||
        url.hostname === "www.courts.go.jp" || url.hostname === "www.pro.goukakudojyo.com" ||
        url.hostname === "gyosei-shiken.or.jp" || url.hostname === "www.gyosei-shiken.or.jp"
      );
    } catch (_) { return false; }
  }

  function setSafeHref(anchor, value) {
    if (safeUrl(value)) {
      anchor.href = value;
      anchor.hidden = false;
    } else {
      anchor.removeAttribute("href");
      anchor.hidden = true;
    }
  }

  function scrollToPanelCard(node) {
    if (node) node.scrollIntoView({ behavior: motionBehavior(), block: "start" });
  }

  function motionBehavior() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  }

})();
