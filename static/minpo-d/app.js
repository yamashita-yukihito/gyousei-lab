'use strict';

const STORE_KEY = 'minpoD:v1';
const STORE_VERSION = 1;
const MODES = {
  read: '最初の2周向けです。答え・解説・答え入り図を隠さず、章の8問を上から一気に読みます。',
  solve: '3周目以降向けです。素直／ひっかけと前問との差分を見て、1問ずつ○×します。',
  exam: '仕上げ向けです。人物をA・B・Cへ置き換え、章のヒント・論点名・素直／ひっかけ・差分を隠します。基準事例と今回の事実だけで判断します。',
};
const SVG_NS = 'http://www.w3.org/2000/svg';

const state = {
  data: null,
  cast: new Map(),
  store: loadStore(),
  mode: 'read',
  chapterId: null,
  index: 0,
  summary: null,
  diagramSerial: 0,
};

function blankStore() {
  return { version: STORE_VERSION, mode: 'read', chapters: {} };
}

function loadStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!parsed || parsed.version !== STORE_VERSION || !parsed.chapters) return blankStore();
    return parsed;
  } catch (_err) {
    return blankStore();
  }
}

function saveStore() {
  state.store.mode = state.mode;
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state.store));
  } catch (_err) {
    const warning = document.getElementById('storage-warning');
    if (warning) warning.hidden = false;
  }
}

function blankCurrent() {
  return { index: 0, answers: {} };
}

function chapterRecord(chapterId) {
  if (!state.store.chapters[chapterId]) {
    state.store.chapters[chapterId] = {
      readRounds: 0,
      current: { solve: blankCurrent(), exam: blankCurrent() },
      sessions: { solve: [], exam: [] },
    };
  }
  const row = state.store.chapters[chapterId];
  if (!row.current) row.current = { solve: blankCurrent(), exam: blankCurrent() };
  if (!row.current.solve) row.current.solve = blankCurrent();
  if (!row.current.exam) row.current.exam = blankCurrent();
  if (!row.sessions) row.sessions = { solve: [], exam: [] };
  if (!row.sessions.solve) row.sessions.solve = [];
  if (!row.sessions.exam) row.sessions.exam = [];
  row.readRounds = Number(row.readRounds || 0);
  return row;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== undefined) node.textContent = text;
  return node;
}

function chapterById(id) {
  return state.data.chapters.find((chapter) => chapter.id === id);
}

function currentChapter() {
  return chapterById(state.chapterId);
}

function castAliases(chapter) {
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  return new Map((chapter.castIds || []).map((id, index) => [id, letters[index]]));
}

function displayName(castId, chapter) {
  const cast = state.cast.get(castId);
  if (!cast) return castId;
  if (state.mode !== 'exam') return cast.name;
  return castAliases(chapter).get(castId) || cast.name;
}

function aliasText(value, chapter) {
  let result = String(value || '');
  if (state.mode !== 'exam') return result;
  const replacements = (chapter.castIds || [])
    .map((id) => ({ name: state.cast.get(id)?.name, alias: castAliases(chapter).get(id) }))
    .filter((row) => row.name && row.alias)
    .sort((a, b) => b.name.length - a.name.length);
  replacements.forEach(({ name, alias }) => {
    result = result.split(name).join(alias);
  });
  return result;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function appendRichText(target, value, chapter) {
  target.textContent = '';
  const names = (chapter.castIds || []).map((id) => ({
    id,
    token: displayName(id, chapter),
    color: state.mode === 'exam' ? '#1d2925' : (state.cast.get(id)?.color || '#1d2925'),
  })).filter((row) => row.token);
  const text = aliasText(value, chapter);
  if (!names.length) {
    target.textContent = text;
    return;
  }
  const tokenMap = new Map(names.map((row) => [row.token, row]));
  const pattern = new RegExp(`(${names.map((row) => escapeRegExp(row.token)).sort((a, b) => b.length - a.length).join('|')})`, 'g');
  text.split(pattern).forEach((part) => {
    const hit = tokenMap.get(part);
    if (!hit) {
      target.appendChild(document.createTextNode(part));
      return;
    }
    const span = el('span', 'name', part);
    span.style.color = hit.color;
    target.appendChild(span);
  });
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function setMode(mode, options = {}) {
  if (!MODES[mode]) return;
  const changed = state.mode !== mode;
  state.mode = mode;
  state.summary = null;
  if (changed && state.chapterId) {
    const chapter = chapterById(state.chapterId);
    if (mode === 'read') state.index = 0;
    else if (chapter) {
      const savedIndex = Number(chapterRecord(chapter.id).current[mode].index || 0);
      state.index = Math.min(Math.max(savedIndex, 0), chapter.questions.length - 1);
    }
  }
  document.body.classList.toggle('exam-mode', mode === 'exam');
  document.querySelectorAll('.mode-button').forEach((button) => {
    button.setAttribute('aria-checked', String(button.dataset.mode === mode));
  });
  setText('mode-note', MODES[mode]);
  saveStore();
  if (options.skipRender) return;
  if (state.chapterId) renderStudy();
  else renderHome();
}

function chip(text, done = false) {
  return el('span', `meta-chip${done ? ' done' : ''}`, text);
}

function sessionSummary(chapterId, mode) {
  const sessions = chapterRecord(chapterId).sessions[mode];
  if (!sessions.length) return '未着手';
  const latest = sessions[sessions.length - 1];
  return `${sessions.length}周・直近 ${latest.correct}/${latest.total}`;
}

function renderHome() {
  state.chapterId = null;
  state.index = 0;
  state.summary = null;
  document.getElementById('home-view').hidden = false;
  document.getElementById('study-view').hidden = true;
  document.getElementById('summary-view').hidden = true;
  const grid = document.getElementById('chapter-grid');
  grid.textContent = '';

  state.data.chapters.forEach((chapter) => {
    const rec = chapterRecord(chapter.id);
    const card = el('article', 'chapter-card');
    const body = el('div');
    body.append(
      el('p', 'chapter-no', `第${chapter.number}章　${chapter.topic}　全${chapter.questions.length}問`),
      el('h3', '', chapter.title),
      el('p', 'lead', chapter.lead),
    );
    const meta = el('div', 'chapter-meta');
    meta.append(
      chip(`読む ${rec.readRounds}周`, rec.readRounds > 0),
      chip(`解く ${sessionSummary(chapter.id, 'solve')}`, rec.sessions.solve.length > 0),
      chip(`本番 ${sessionSummary(chapter.id, 'exam')}`, rec.sessions.exam.length > 0),
    );
    if (rec.readRounds >= 2 && !rec.sessions.solve.length) meta.append(chip('3周目：解く推奨', true));
    body.appendChild(meta);

    const open = el('button', 'primary chapter-open');
    open.type = 'button';
    open.textContent = state.mode === 'read' ? '答えから読む' : state.mode === 'solve' ? '普通に解く' : '本番で解く';
    open.addEventListener('click', () => openChapter(chapter.id));
    card.append(body, open);
    grid.appendChild(card);
  });
  updateHash();
}

function openChapter(chapterId, requestedIndex) {
  const chapter = chapterById(chapterId);
  if (!chapter) return;
  state.chapterId = chapterId;
  const rec = chapterRecord(chapterId);
  if (state.mode === 'read') state.index = 0;
  else {
    const current = rec.current[state.mode];
    state.index = Number.isInteger(requestedIndex)
      ? Math.min(Math.max(requestedIndex, 0), chapter.questions.length - 1)
      : Math.min(Math.max(Number(current.index || 0), 0), chapter.questions.length - 1);
  }
  renderStudy();
}

function renderChapterHeader(chapter) {
  const exam = state.mode === 'exam';
  setText('chapter-number', exam ? `第${chapter.number}章　本番変換` : `第${chapter.number}章　${chapter.topic}`);
  setText('chapter-title', exam ? '共通事例を読んで○×を判断する' : aliasText(chapter.title, chapter));
  setText('chapter-lead', aliasText(chapter.lead, chapter));
  setText('chapter-goal', aliasText(chapter.goal, chapter));
  appendRichText(document.getElementById('base-scenario'), chapter.baseScenario, chapter);
  const strip = document.getElementById('cast-strip');
  strip.textContent = '';
  (chapter.castIds || []).forEach((castId) => {
    const cast = state.cast.get(castId);
    if (!cast) return;
    const item = el('div', 'cast-chip');
    item.style.borderLeftColor = cast.color;
    item.append(el('strong', '', `${cast.emoji} ${displayName(castId, chapter)}`), el('span', '', aliasText(cast.role, chapter)));
    strip.appendChild(item);
  });
}

function renderStudy() {
  const chapter = currentChapter();
  if (!chapter) return renderHome();
  state.summary = null;
  document.getElementById('home-view').hidden = true;
  document.getElementById('study-view').hidden = false;
  document.getElementById('summary-view').hidden = true;
  renderChapterHeader(chapter);

  const readList = document.getElementById('read-list');
  const single = document.getElementById('single-card');
  const finish = document.getElementById('read-finish');
  readList.textContent = '';
  single.textContent = '';
  if (state.mode === 'read') {
    setText('chapter-progress', `答えから読む・第${chapterRecord(chapter.id).readRounds + 1}周　全${chapter.questions.length}問`);
    chapter.questions.forEach((question, index) => readList.appendChild(renderQuestionCard(chapter, question, index, { reading: true })));
    finish.hidden = false;
  } else {
    const question = chapter.questions[state.index];
    const answer = chapterRecord(chapter.id).current[state.mode].answers[question.id];
    setText('chapter-progress', `${state.mode === 'solve' ? '普通に解く' : '本番変換'}　${state.index + 1} / ${chapter.questions.length}`);
    single.appendChild(renderQuestionCard(chapter, question, state.index, { answer }));
    finish.hidden = true;
  }
  updateHash();
}

function renderDelta(question, chapter) {
  const box = el('section', 'delta');
  const reset = question.delta.transition === 'reset';
  box.appendChild(el('h3', '', reset ? '小系列を切り替える（基準事例へ戻る）' : '前問から変わった1点'));
  const grid = el('div', 'delta-grid');
  const before = el('div');
  before.append(el('small', '', '前'), el('p', '', aliasText(question.delta.before, chapter)));
  const now = el('div');
  now.append(el('small', '', '今回'), el('p', '', aliasText(question.delta.now, chapter)));
  grid.append(before, el('div', 'arrow', '→'), now);
  box.append(grid, el('p', 'unchanged', aliasText(question.delta.same, chapter)));
  return box;
}

function renderQuestionCard(chapter, question, index, options = {}) {
  const reading = Boolean(options.reading);
  const answered = options.answer;
  const card = el('article', 'question-card');
  card.id = `question-${question.id}`;

  const head = el('div', 'question-head');
  const badges = el('div', 'badges');
  if (state.mode !== 'exam') {
    badges.appendChild(el('span', 'badge topic', question.topic));
    badges.appendChild(el('span', `badge kind ${question.kind}`, question.kind === 'trap' ? 'ひっかけ' : '素直'));
  } else {
    badges.appendChild(el('span', 'badge topic', '本番変換'));
  }
  head.append(badges, el('span', 'q-no', `${index + 1} / ${chapter.questions.length}`));
  card.appendChild(head);

  const scenario = el('p', 'current-scenario');
  appendRichText(scenario, question.currentScenario, chapter);
  card.appendChild(scenario);
  if (state.mode !== 'exam') card.appendChild(renderDelta(question, chapter));

  const statement = el('p', 'statement');
  appendRichText(statement, question.statement, chapter);
  card.appendChild(statement);
  card.appendChild(renderDiagramFigure(chapter, question, false));

  if (!reading) {
    const row = el('div', 'answer-row');
    [
      { value: true, label: '○ 正しい', className: 'o' },
      { value: false, label: '× 誤り', className: 'x' },
    ].forEach((choice) => {
      const button = el('button', `answer-button ${choice.className}`, choice.label);
      button.type = 'button';
      if (answered) {
        button.disabled = true;
        if (answered.selected === choice.value) button.classList.add('picked');
      }
      button.addEventListener('click', () => answerQuestion(question.id, choice.value));
      row.appendChild(button);
    });
    card.appendChild(row);
  }

  if (reading || answered) card.appendChild(renderExplanation(chapter, question, reading, answered));
  if (!reading) card.appendChild(renderNavigation(chapter, question, Boolean(answered)));
  return card;
}

function renderNavigation(chapter, question, answered) {
  const nav = el('div', 'nav-row');
  const current = chapterRecord(chapter.id).current[state.mode];
  const firstUnanswered = chapter.questions.findIndex((item) => !current.answers[item.id]);
  const atLast = state.index === chapter.questions.length - 1;
  const complete = firstUnanswered === -1;
  const previous = el('button', 'ghost', '← 前の問題');
  previous.type = 'button';
  previous.disabled = state.index === 0;
  previous.addEventListener('click', () => moveQuestion(-1));
  const next = el('button', 'primary', atLast ? (complete ? '章の結果を見る' : '未回答の問題へ →') : '次の問題 →');
  next.type = 'button';
  next.disabled = !answered;
  next.addEventListener('click', () => {
    if (atLast && complete) finishRound();
    else if (atLast) moveToQuestion(firstUnanswered);
    else moveQuestion(1);
  });
  nav.append(previous, next);
  return nav;
}

function answerQuestion(questionId, selected) {
  const chapter = currentChapter();
  if (!chapter || state.mode === 'read') return;
  const question = chapter.questions.find((item) => item.id === questionId);
  if (!question) return;
  const current = chapterRecord(chapter.id).current[state.mode];
  if (current.answers[questionId]) return;
  current.answers[questionId] = {
    selected,
    correct: selected === question.answer,
    at: new Date().toISOString(),
  };
  current.index = state.index;
  saveStore();
  renderStudy();
  document.getElementById(`question-${questionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function moveQuestion(delta) {
  const chapter = currentChapter();
  if (!chapter) return;
  moveToQuestion(state.index + delta);
}

function moveToQuestion(index) {
  const chapter = currentChapter();
  if (!chapter || state.mode === 'read') return;
  state.index = Math.min(Math.max(index, 0), chapter.questions.length - 1);
  chapterRecord(chapter.id).current[state.mode].index = state.index;
  saveStore();
  renderStudy();
  document.getElementById('study-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function answerMark(answer) {
  return answer ? '○' : '×';
}

function renderExplanation(chapter, question, reading, answered) {
  const ex = question.explanation;
  const box = el('section', 'explanation');
  let verdictText;
  let verdictClass;
  if (reading) {
    verdictText = `答え ${answerMark(question.answer)}`;
    verdictClass = 'read';
  } else if (answered.correct) {
    verdictText = `正解　答えは ${answerMark(question.answer)}`;
    verdictClass = 'correct';
  } else {
    verdictText = `不正解　答えは ${answerMark(question.answer)}`;
    verdictClass = 'wrong';
  }
  box.append(el('p', `verdict ${verdictClass}`, verdictText), el('p', 'answer-summary', aliasText(ex.summary, chapter)));

  const flow = el('div', 'logic-flow');
  [
    ['決め手の事実', ex.fact],
    ['正確なルール', ex.rule],
    ['この事例の結論', ex.conclusion],
  ].forEach(([label, value], index) => {
    const article = el('article');
    article.append(el('small', '', label), el('strong', '', aliasText(value, chapter)));
    flow.appendChild(article);
    if (index < 2) flow.appendChild(el('span', 'logic-arrow', '→'));
  });
  box.appendChild(flow);

  const reasons = el('div', 'reason-grid');
  const protectedSide = el('article');
  protectedSide.append(el('h3', '', '誰を守る？'), el('p', '', aliasText(`${ex.protectWho}。${ex.why}`, chapter)));
  const opposite = el('article');
  opposite.append(el('h3', '', '反対の結論だと何が困る？'), el('p', '', aliasText(ex.ifOpposite, chapter)));
  reasons.append(protectedSide, opposite);
  box.appendChild(reasons);

  const sense = el('section', 'sense-box');
  sense.append(el('h3', '', '法律家の常識へ補正'), el('p', '', aliasText(ex.legalSense, chapter)));
  box.appendChild(sense);
  box.appendChild(renderDiagramFigure(chapter, question, true));

  const deep = el('details', 'deep-details');
  deep.open = reading;
  deep.appendChild(el('summary', '', '負ける側に残る救済・根拠を確認'));
  const deepGrid = el('div', 'deep-grid');
  const remedy = el('article');
  remedy.append(
    el('h3', '', 'この結論で終わりにしない'),
    el('p', '', aliasText(ex.remedy || chapter.remedyGuide || '別の契約上・手続上の救済がないかを確認します。', chapter)),
  );
  deepGrid.appendChild(remedy);
  const links = el('div', 'links');
  (question.legalBasis || []).forEach((basis) => {
    const link = el('a', '', basis.label);
    link.href = basis.url;
    link.target = '_blank';
    link.rel = 'noreferrer';
    links.appendChild(link);
  });
  if (question.cardId) {
    const cardLink = el('a', '', '対応する学習カードを開く');
    cardLink.href = `../?cardId=${encodeURIComponent(question.cardId)}#study`;
    links.appendChild(cardLink);
  }
  deepGrid.appendChild(links);
  deep.appendChild(deepGrid);
  box.appendChild(deep);
  box.append(el('p', 'minimum', aliasText(ex.minimum, chapter)), el('p', 'next-preview', aliasText(ex.next, chapter)));
  return box;
}

function cloneNodes(chapter, question) {
  const diagram = question.diagram || {};
  const hidden = new Set(diagram.hiddenNodes || []);
  const labels = diagram.nodeLabels || {};
  const nodes = chapter.diagram.nodes
    .filter((node) => !hidden.has(node.id))
    .map((node) => ({ ...node, label: labels[node.id] || node.label }));
  (diagram.extraNodes || []).forEach((node) => nodes.push({ ...node }));
  return nodes;
}

function wrapText(value, limit = 15) {
  const lines = [];
  let current = '';
  let width = 0;
  for (const character of String(value || '')) {
    const charWidth = /[\x20-\x7e]/.test(character) ? 0.55 : 1;
    if (current && width + charWidth > limit) {
      lines.push(current);
      current = '';
      width = 0;
    }
    current += character;
    width += charWidth;
  }
  if (current) lines.push(current);
  return lines;
}

function clipPoint(box, target) {
  const dx = target.cx - box.cx;
  const dy = target.cy - box.cy;
  if (!dx && !dy) return { x: box.cx, y: box.cy };
  const sx = dx ? (box.width / 2 + 3) / Math.abs(dx) : Infinity;
  const sy = dy ? (box.height / 2 + 3) / Math.abs(dy) : Infinity;
  const scale = Math.min(sx, sy);
  return { x: box.cx + dx * scale, y: box.cy + dy * scale };
}

function renderDiagramFigure(chapter, question, answerDiagram) {
  const figure = el('figure', 'diagram');
  figure.appendChild(el('figcaption', '', answerDiagram ? '答えを入れた関係図（回答後）' : '今回の事実だけを描いた関係図'));
  const box = el('div', 'diagram-box');
  box.appendChild(renderDiagram(chapter, question, answerDiagram));
  figure.append(box, el('p', 'diagram-hint', '図は横へスクロールできます'));
  return figure;
}

function renderDiagram(chapter, question, answerDiagram) {
  state.diagramSerial += 1;
  const unique = `d-${chapter.id}-${question.id}-${state.diagramSerial}`.replace(/[^A-Za-z0-9_-]/g, '');
  const nodes = cloneNodes(chapter, question);
  const castAliasesMap = castAliases(chapter);
  const nodeWidth = 182;
  const column = 215;
  const row = 132;
  const pad = 26;
  const boxes = new Map();

  nodes.forEach((node) => {
    const cast = node.castId ? state.cast.get(node.castId) : null;
    let label = aliasText(node.label, chapter);
    const title = cast
      ? (state.mode === 'exam' ? castAliasesMap.get(node.castId) : `${cast.emoji} ${cast.name}`)
      : label;
    if (!cast) label = '';
    const lines = wrapText(label, 15);
    const height = cast ? 50 + lines.length * 15 : 48;
    boxes.set(node.id, {
      node,
      cast,
      title,
      lines,
      width: nodeWidth,
      height,
      cx: pad + Number(node.x) * column + nodeWidth / 2,
      cy: pad + Number(node.y) * row + height / 2,
    });
  });

  let maxX = 680;
  let maxY = 260;
  boxes.forEach((item) => {
    maxX = Math.max(maxX, item.cx + item.width / 2 + pad);
    maxY = Math.max(maxY, item.cy + item.height / 2 + pad);
  });
  const svg = svgEl('svg', { role: 'img', 'aria-label': answerDiagram ? '答えを入れた人物関係図' : '今回の事実関係図', viewBox: `0 0 ${Math.ceil(maxX)} ${Math.ceil(maxY)}` });
  const defs = svgEl('defs');
  const colors = { normal: '#52625b', yes: '#176a52', no: '#ad4138', amber: '#8a6310' };
  Object.entries(colors).forEach(([name, color]) => {
    const marker = svgEl('marker', { id: `${unique}-${name}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' });
    marker.appendChild(svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: color }));
    defs.appendChild(marker);
  });
  svg.appendChild(defs);

  const factEdges = question.diagram?.edges || [];
  const edges = answerDiagram ? [...factEdges, ...(question.answerEdges || [])] : factEdges;
  const styleMap = {
    solid: { color: colors.normal, width: 2, dash: '', marker: 'normal' },
    bold: { color: colors.yes, width: 2.7, dash: '', marker: 'yes' },
    dashed: { color: colors.amber, width: 2, dash: '7 5', marker: 'amber' },
    thin: { color: '#9aa8a2', width: 1.4, dash: '4 4', marker: 'normal' },
    'answer-yes': { color: colors.yes, width: 3.2, dash: '', marker: 'yes' },
    'answer-no': { color: colors.no, width: 3.2, dash: '', marker: 'no' },
  };

  edges.forEach((edge, index) => {
    const from = boxes.get(edge.from);
    const to = boxes.get(edge.to);
    if (!from || !to) return;
    const style = styleMap[edge.style] || styleMap.solid;
    const start = clipPoint(from, to);
    const end = clipPoint(to, from);
    const path = svgEl('path', {
      d: `M ${start.x} ${start.y} L ${end.x} ${end.y}`,
      fill: 'none',
      stroke: style.color,
      'stroke-width': style.width,
      'stroke-linecap': 'round',
      'marker-end': `url(#${unique}-${style.marker})`,
    });
    if (style.dash) path.setAttribute('stroke-dasharray', style.dash);
    svg.appendChild(path);

    if (edge.label) {
      const lines = wrapText(aliasText(edge.label, chapter), 12);
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2 + ((index % 3) - 1) * 18;
      const width = Math.max(58, ...lines.map((line) => line.length * 12 + 14));
      const height = lines.length * 14 + 9;
      svg.appendChild(svgEl('rect', { x: midX - width / 2, y: midY - height / 2, width, height, rx: 5, fill: '#fffef9', stroke: style.color, 'stroke-width': .8 }));
      lines.forEach((line, lineIndex) => {
        svg.appendChild(svgEl('text', { x: midX, y: midY - height / 2 + 15 + lineIndex * 14, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: style.color }, line));
      });
    }
  });

  boxes.forEach((item) => {
    const color = state.mode === 'exam' ? '#68746f' : (item.cast?.color || '#68746f');
    svg.appendChild(svgEl('rect', {
      x: item.cx - item.width / 2,
      y: item.cy - item.height / 2,
      width: item.width,
      height: item.height,
      rx: item.cast ? 10 : 6,
      fill: item.cast ? '#fff' : '#f1f5f2',
      stroke: color,
      'stroke-width': item.cast ? 2 : 1.4,
      'stroke-dasharray': item.cast ? '' : '5 4',
    }));
    svg.appendChild(svgEl('text', { x: item.cx, y: item.cy - item.height / 2 + 20, 'text-anchor': 'middle', 'font-size': item.cast ? 14 : 13, 'font-weight': 800, fill: color }, item.title));
    item.lines.forEach((line, lineIndex) => {
      svg.appendChild(svgEl('text', { x: item.cx, y: item.cy - item.height / 2 + 40 + lineIndex * 15, 'text-anchor': 'middle', 'font-size': 11.5, fill: '#68746f' }, line));
    });
  });
  return svg;
}

function finishRound() {
  const chapter = currentChapter();
  if (!chapter || state.mode === 'read') return;
  const rec = chapterRecord(chapter.id);
  const current = rec.current[state.mode];
  const answers = chapter.questions.map((question) => current.answers[question.id]).filter(Boolean);
  if (answers.length !== chapter.questions.length) {
    const firstUnanswered = chapter.questions.findIndex((question) => !current.answers[question.id]);
    if (firstUnanswered >= 0) moveToQuestion(firstUnanswered);
    return;
  }
  const session = {
    at: new Date().toISOString(),
    total: chapter.questions.length,
    correct: answers.filter((answer) => answer.correct).length,
  };
  rec.sessions[state.mode].push(session);
  rec.current[state.mode] = blankCurrent();
  saveStore();
  showSummary({ mode: state.mode, ...session });
}

function completeReadRound() {
  const chapter = currentChapter();
  if (!chapter) return;
  const rec = chapterRecord(chapter.id);
  rec.readRounds += 1;
  saveStore();
  showSummary({ mode: 'read', round: rec.readRounds });
}

function showSummary(summary) {
  const chapter = currentChapter();
  if (!chapter) return;
  state.summary = summary;
  document.getElementById('home-view').hidden = true;
  document.getElementById('study-view').hidden = true;
  document.getElementById('summary-view').hidden = false;
  setText('summary-title', `第${chapter.number}章　${chapter.title}`);
  if (summary.mode === 'read') {
    setText('summary-result', `答えから読む：第${summary.round}周を記録しました。${summary.round >= 2 ? '次は「普通に解く」へ進めます。' : 'もう1周、答えから読むのがおすすめです。'}`);
  } else {
    const modeName = summary.mode === 'solve' ? '普通に解く' : '本番変換';
    setText('summary-result', `${modeName}：${summary.correct} / ${summary.total}正解（${Math.round(summary.correct / summary.total * 100)}%）`);
  }
  renderSummaryTable(chapter);
  document.getElementById('switch-solve').hidden = summary.mode !== 'read';
  updateHash(true);
}

function renderSummaryTable(chapter) {
  const table = chapter.summaryTable;
  setText('summary-caption', table.caption);
  setText('summary-note', table.note);
  const thead = document.getElementById('summary-thead');
  const tbody = document.getElementById('summary-tbody');
  thead.textContent = '';
  tbody.textContent = '';
  const headRow = el('tr');
  table.headers.forEach((header) => headRow.appendChild(el('th', '', aliasText(header, chapter))));
  thead.appendChild(headRow);
  table.rows.forEach((row) => {
    const tr = el('tr');
    row.forEach((value) => tr.appendChild(el('td', '', aliasText(value, chapter))));
    tbody.appendChild(tr);
  });
}

function repeatChapter() {
  state.index = 0;
  state.summary = null;
  if (state.mode !== 'read') chapterRecord(state.chapterId).current[state.mode] = blankCurrent();
  saveStore();
  renderStudy();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function updateHash(summary = false) {
  const params = new URLSearchParams();
  params.set('mode', state.mode);
  if (state.chapterId) {
    params.set('chapter', state.chapterId);
    if (state.mode !== 'read') params.set('q', String(state.index + 1));
    if (summary) params.set('summary', '1');
  }
  history.replaceState(null, '', `#${params.toString()}`);
}

function parseHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  return {
    mode: params.get('mode'),
    chapter: params.get('chapter'),
    index: Math.max(0, Number(params.get('q') || 1) - 1),
    summary: params.get('summary') === '1',
  };
}

function savedSummary(chapterId, mode) {
  const rec = chapterRecord(chapterId);
  if (mode === 'read') return rec.readRounds ? { mode: 'read', round: rec.readRounds } : null;
  const sessions = rec.sessions[mode] || [];
  const latest = sessions[sessions.length - 1];
  return latest ? { mode, ...latest } : null;
}

function resetAll() {
  if (!window.confirm('民法ドリルD案の読了・回答記録をすべて消します。通常の学習カード履歴と本番SQLiteには影響しません。')) return;
  state.store = blankStore();
  state.mode = 'read';
  saveStore();
  setMode('read', { skipRender: true });
  renderHome();
}

function bind() {
  document.querySelectorAll('.mode-button').forEach((button) => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
  });
  document.querySelectorAll('[data-action="home"]').forEach((button) => button.addEventListener('click', renderHome));
  document.getElementById('reset-all').addEventListener('click', resetAll);
  document.getElementById('complete-read').addEventListener('click', completeReadRound);
  document.getElementById('repeat-chapter').addEventListener('click', repeatChapter);
  document.getElementById('switch-solve').addEventListener('click', () => {
    setMode('solve', { skipRender: true });
    state.index = 0;
    chapterRecord(state.chapterId).current.solve = blankCurrent();
    saveStore();
    renderStudy();
  });
}

async function init() {
  bind();
  try {
    const response = await fetch('chapters.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    state.cast = new Map(state.data.cast.map((cast) => [cast.id, cast]));
    setText('as-of', `法令基準日 ${state.data.asOf}`);
    const initial = parseHash();
    const initialMode = MODES[initial.mode] ? initial.mode : (MODES[state.store.mode] ? state.store.mode : 'read');
    setMode(initialMode, { skipRender: true });
    if (initial.chapter && chapterById(initial.chapter)) {
      if (initial.summary) {
        state.chapterId = initial.chapter;
        state.index = initial.index;
        const summary = savedSummary(initial.chapter, initialMode);
        if (summary) showSummary(summary);
        else openChapter(initial.chapter, initial.index);
      } else openChapter(initial.chapter, initial.index);
    } else renderHome();
  } catch (error) {
    const grid = document.getElementById('chapter-grid');
    grid.textContent = `問題データを読み込めませんでした。${error.message}`;
  }
}

init();
