'use strict';

const STORE_KEY = 'minpoC:v1';
const NS = 'http://www.w3.org/2000/svg';
const MODE_NOTES = {
  read: '1〜2周目向け。答えと解説を最初から表示し、正答率には含めません。「理解した・次へ」で読了を記録します。',
  solve: '3周目以降向け。素直／ひっかけと前問との差分を見たうえで、○×を選びます。',
  exam: '仕上げ向け。人物をA・B・Cへ戻し、素直／ひっかけと差分表示を隠します。',
};

const state = {
  data: null,
  mode: 'read',
  chapterId: null,
  index: 0,
  store: loadStore(),
};

function blankStore() {
  return { version: 1, mode: 'read', chapters: {} };
}

function loadStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    return parsed && parsed.version === 1 ? parsed : blankStore();
  } catch (err) {
    return blankStore();
  }
}

function saveStore() {
  try {
    state.store.mode = state.mode;
    localStorage.setItem(STORE_KEY, JSON.stringify(state.store));
  } catch (err) {
    // 保存できなくても学習は続けられる。
  }
}

function chapterState(chapterId) {
  if (!state.store.chapters[chapterId]) {
    state.store.chapters[chapterId] = {
      positions: { read: 0, solve: 0, exam: 0 },
      read: { rounds: 0, seen: [], completed: false, lastRound: null },
      solve: { rounds: 0, answers: {}, stats: {}, completed: false, lastRound: null },
      exam: { rounds: 0, answers: {}, stats: {}, completed: false, lastRound: null },
    };
  }
  return state.store.chapters[chapterId];
}

function modeState(chapterId, mode = state.mode) {
  const cs = chapterState(chapterId);
  return mode === 'read' ? cs.read : cs[mode];
}

function chapterById(id) {
  return state.data.chapters.find((c) => c.id === id) || null;
}

function currentChapter() {
  return chapterById(state.chapterId);
}

function currentQuestion() {
  const chapter = currentChapter();
  return chapter ? chapter.questions[state.index] : null;
}

function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function htmlText(id, value) {
  document.getElementById(id).textContent = value || '';
}

function aliasText(text, chapter) {
  if (state.mode !== 'exam' || !text) return text || '';
  let output = String(text);
  Object.entries(chapter.examAliases || {})
    .sort((a, b) => b[0].length - a[0].length)
    .forEach(([name, alias]) => {
      output = output.split(name).join(alias);
    });
  return output;
}

function castName(cast, chapter) {
  if (state.mode === 'exam') return chapter.examAliases[cast.name] || cast.name;
  return cast.name;
}

function setMode(mode, options = {}) {
  if (!MODE_NOTES[mode]) return;
  if (state.chapterId) {
    chapterState(state.chapterId).positions[state.mode] = state.index;
  }
  state.mode = mode;
  document.body.classList.toggle('exam-mode', mode === 'exam');
  document.querySelectorAll('.mode-button').forEach((button) => {
    button.setAttribute('aria-checked', String(button.dataset.mode === mode));
  });
  htmlText('mode-note', MODE_NOTES[mode]);
  saveStore();

  if (state.chapterId && !options.skipRender) {
    const cs = chapterState(state.chapterId);
    state.index = Math.max(0, Number(cs.positions[mode] || 0));
    if (modeState(state.chapterId).completed) showSummary();
    else showStudy();
  } else if (!state.chapterId) {
    renderHome();
  }
  updateHash();
}

function totalStats(chapterId, mode) {
  const ms = modeState(chapterId, mode);
  if (mode === 'read') return { attempts: 0, correct: 0 };
  return Object.values(ms.stats || {}).reduce((acc, row) => {
    acc.attempts += Number(row.attempts || 0);
    acc.correct += Number(row.correct || 0);
    return acc;
  }, { attempts: 0, correct: 0 });
}

function renderHome() {
  document.getElementById('home-view').hidden = false;
  document.getElementById('study-view').hidden = true;
  document.getElementById('summary-view').hidden = true;
  state.chapterId = null;
  state.index = 0;

  const grid = document.getElementById('chapter-grid');
  grid.textContent = '';
  state.data.chapters.forEach((chapter) => {
    const cs = chapterState(chapter.id);
    const solve = totalStats(chapter.id, 'solve');
    const exam = totalStats(chapter.id, 'exam');
    const article = document.createElement('article');
    article.className = 'chapter-card';

    const body = document.createElement('div');
    const no = document.createElement('p');
    no.className = 'chapter-no';
    no.textContent = `第${chapter.number}章　${chapter.topic}　全${chapter.questions.length}問`;
    const title = document.createElement('h3');
    title.textContent = chapter.title;
    const lead = document.createElement('p');
    lead.className = 'lead';
    lead.textContent = chapter.lead;
    const meta = document.createElement('div');
    meta.className = 'chapter-meta';
    meta.append(
      chip(`読む ${cs.read.rounds}周`, cs.read.rounds > 0),
      chip(solve.attempts ? `解く ${solve.correct}/${solve.attempts}正解` : '解く 未着手', solve.attempts > 0),
      chip(exam.attempts ? `本番 ${exam.correct}/${exam.attempts}正解` : '本番 未着手', exam.attempts > 0),
    );
    body.append(no, title, lead, meta);

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'primary chapter-open';
    open.textContent = `${state.mode === 'read' ? '読む' : state.mode === 'solve' ? '解く' : '本番で解く'}`;
    open.addEventListener('click', () => openChapter(chapter.id));
    article.append(body, open);
    grid.appendChild(article);
  });
  updateHash();
}

function chip(text, done) {
  const span = document.createElement('span');
  span.className = `meta-chip${done ? ' done' : ''}`;
  span.textContent = text;
  return span;
}

function openChapter(chapterId, requestedIndex) {
  const chapter = chapterById(chapterId);
  if (!chapter) return;
  state.chapterId = chapterId;
  const cs = chapterState(chapterId);
  state.index = Number.isInteger(requestedIndex)
    ? Math.min(Math.max(requestedIndex, 0), chapter.questions.length - 1)
    : Math.min(Math.max(Number(cs.positions[state.mode] || 0), 0), chapter.questions.length - 1);
  if (modeState(chapterId).completed) showSummary();
  else showStudy();
}

function showStudy() {
  const chapter = currentChapter();
  if (!chapter) return renderHome();
  document.getElementById('home-view').hidden = true;
  document.getElementById('study-view').hidden = false;
  document.getElementById('summary-view').hidden = true;
  renderChapterHeader(chapter);
  renderQuestion();
  updateHash();
}

function renderChapterHeader(chapter) {
  htmlText('chapter-number', `第${chapter.number}章　${chapter.topic}`);
  htmlText('chapter-title', aliasText(chapter.title, chapter));
  htmlText('chapter-lead', aliasText(chapter.lead, chapter));
  htmlText('chapter-goal', aliasText(chapter.goal, chapter));
  htmlText('common-case', aliasText(chapter.commonCase, chapter));

  const castStrip = document.getElementById('cast-strip');
  castStrip.textContent = '';
  chapter.cast.forEach((cast) => {
    const div = document.createElement('div');
    div.className = 'cast-chip';
    div.style.borderLeftColor = cast.color;
    const strong = document.createElement('strong');
    strong.textContent = `${cast.emoji} ${castName(cast, chapter)}`;
    const role = document.createElement('span');
    role.textContent = aliasText(cast.role, chapter);
    div.append(strong, role);
    castStrip.appendChild(div);
  });
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function edgeKey(edge) {
  return `${edge.from}>${edge.to}`;
}

function patchedDiagram(chapter, question) {
  const diagram = clone(chapter.diagram);
  const patch = question.diagramPatch || {};
  const hiddenNodes = new Set(patch.hiddenNodes || []);
  const hiddenEdges = new Set(patch.hiddenEdges || []);
  const labels = patch.nodeLabels || {};
  const edgeLabels = patch.edgeLabels || {};

  diagram.nodes = diagram.nodes
    .filter((node) => !hiddenNodes.has(node.id))
    .map((node) => ({ ...node, label: labels[node.id] || node.label, changed: Object.hasOwn(labels, node.id) }));
  (patch.extraNodes || []).forEach((node) => diagram.nodes.push({ ...node, changed: true }));

  diagram.edges = diagram.edges
    .filter((edge) => !hiddenEdges.has(edgeKey(edge)) && !hiddenNodes.has(edge.from) && !hiddenNodes.has(edge.to))
    .map((edge) => {
      const key = edgeKey(edge);
      return { ...edge, label: edgeLabels[key] || edge.label, changed: Object.hasOwn(edgeLabels, key) };
    });
  (patch.extraEdges || []).forEach((edge) => diagram.edges.push({ ...edge, changed: true }));
  return diagram;
}

function measure(text) {
  let width = 0;
  for (const ch of String(text)) width += /[\x20-\x7e]/.test(ch) ? 0.55 : 1;
  return width;
}

function wrap(text, limit = 15) {
  const lines = [];
  let current = '';
  let width = 0;
  for (const ch of String(text || '')) {
    const w = /[\x20-\x7e]/.test(ch) ? 0.55 : 1;
    if (current && width + w > limit) {
      lines.push(current);
      current = '';
      width = 0;
    }
    current += ch;
    width += w;
  }
  if (current) lines.push(current);
  return lines;
}

function clip(box, target) {
  const dx = target.x - box.cx;
  const dy = target.y - box.cy;
  if (!dx && !dy) return { x: box.cx, y: box.cy };
  const sx = dx ? (box.w / 2 + 4) / Math.abs(dx) : Infinity;
  const sy = dy ? (box.h / 2 + 4) / Math.abs(dy) : Infinity;
  const scale = Math.min(sx, sy);
  return { x: box.cx + dx * scale, y: box.cy + dy * scale };
}

function renderDiagram(chapter, question) {
  const diagram = patchedDiagram(chapter, question);
  const castMap = new Map(chapter.cast.map((c) => [c.id, c]));
  const nodeW = 190;
  const col = 235;
  const row = 140;
  const pad = 28;
  const boxes = new Map();

  diagram.nodes.forEach((node) => {
    const lines = wrap(aliasText(node.label, chapter), 15);
    const h = 50 + lines.length * 16;
    boxes.set(node.id, {
      node, lines, w: nodeW, h,
      cx: pad + Number(node.x) * col + nodeW / 2,
      cy: pad + Number(node.y) * row + h / 2,
    });
  });

  let maxX = 0;
  let maxY = 0;
  boxes.forEach((b) => {
    maxX = Math.max(maxX, b.cx + b.w / 2 + pad);
    maxY = Math.max(maxY, b.cy + b.h / 2 + pad);
  });
  const svg = el('svg', { role: 'img', 'aria-label': '今回の登場人物と物の関係図', viewBox: `0 0 ${Math.ceil(maxX)} ${Math.ceil(maxY)}` });
  const defs = el('defs');
  [['arrow','#4a5c55'],['arrow-green','#1c7157'],['arrow-amber','#8a6412']].forEach(([id, color]) => {
    const marker = el('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
    marker.appendChild(el('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: color }));
    defs.appendChild(marker);
  });
  svg.appendChild(defs);

  const styleMap = {
    solid: { stroke: '#4a5c55', width: 2, dash: '', marker: 'arrow' },
    bold: { stroke: '#1c7157', width: 2.8, dash: '', marker: 'arrow-green' },
    dashed: { stroke: '#8a6412', width: 2, dash: '7 5', marker: 'arrow-amber' },
    thin: { stroke: '#9aa8a2', width: 1.4, dash: '4 4', marker: '' },
  };

  diagram.edges.forEach((edge, index) => {
    const a = boxes.get(edge.from);
    const b = boxes.get(edge.to);
    if (!a || !b) return;
    const start = clip(a, { x: b.cx, y: b.cy });
    const end = clip(b, { x: a.cx, y: a.cy });
    const style = styleMap[edge.style] || styleMap.solid;
    const stroke = edge.changed ? '#8a6412' : style.stroke;
    const path = el('path', {
      d: `M ${start.x} ${start.y} L ${end.x} ${end.y}`,
      fill: 'none', stroke, 'stroke-width': edge.changed ? 2.8 : style.width,
      'stroke-linecap': 'round',
    });
    if (style.dash) path.setAttribute('stroke-dasharray', style.dash);
    if (style.marker) path.setAttribute('marker-end', `url(#${edge.changed ? 'arrow-amber' : style.marker})`);
    svg.appendChild(path);

    if (edge.label) {
      const lines = wrap(aliasText(edge.label, chapter), 11);
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2 + ((index % 3) - 1) * 20;
      const width = Math.max(...lines.map((line) => measure(line) * 11 + 14), 48);
      const height = lines.length * 14 + 8;
      svg.appendChild(el('rect', { x: midX - width / 2, y: midY - height / 2, width, height, rx: 5, fill: '#fffefa', stroke: edge.changed ? '#d9bd74' : '#dfe7e2' }));
      lines.forEach((line, i) => {
        svg.appendChild(el('text', { x: midX, y: midY - height / 2 + 15 + i * 14, 'text-anchor': 'middle', 'font-size': '11', 'font-weight': '700', fill: edge.changed ? '#8a6412' : '#4a5c55' }, line));
      });
    }
  });

  boxes.forEach((box) => {
    const node = box.node;
    const cast = node.kind === 'person' ? castMap.get(node.cast) : null;
    const color = cast ? cast.color : '#64716c';
    const stroke = node.changed ? '#8a6412' : color;
    svg.appendChild(el('rect', {
      x: box.cx - box.w / 2, y: box.cy - box.h / 2, width: box.w, height: box.h,
      rx: node.kind === 'person' ? 11 : 6, fill: node.kind === 'person' ? '#fff' : '#f2f6f3',
      stroke, 'stroke-width': node.changed ? 3 : node.kind === 'person' ? 2 : 1.4,
      'stroke-dasharray': node.kind === 'thing' ? '5 4' : '',
    }));
    const title = cast ? `${cast.emoji} ${castName(cast, chapter)}` : aliasText(node.label, chapter);
    svg.appendChild(el('text', { x: box.cx, y: box.cy - box.h / 2 + 22, 'text-anchor': 'middle', 'font-size': '14', 'font-weight': '800', fill: stroke }, title));
    if (node.kind === 'person') {
      box.lines.forEach((line, i) => {
        svg.appendChild(el('text', { x: box.cx, y: box.cy - box.h / 2 + 43 + i * 16, 'text-anchor': 'middle', 'font-size': '11.5', fill: '#64716c' }, line));
      });
    }
    if (node.changed) {
      svg.appendChild(el('text', { x: box.cx + box.w / 2 - 7, y: box.cy - box.h / 2 + 13, 'text-anchor': 'end', 'font-size': '10', 'font-weight': '900', fill: '#8a6412' }, '今回変更'));
    }
  });
  return svg;
}

function renderQuestion() {
  const chapter = currentChapter();
  const question = currentQuestion();
  if (!chapter || !question) return;
  const cs = chapterState(chapter.id);
  cs.positions[state.mode] = state.index;
  saveStore();

  const ms = modeState(chapter.id);
  const total = chapter.questions.length;
  htmlText('chapter-progress', `${state.mode === 'read' ? '読む' : state.mode === 'solve' ? '解く' : '本番変換'}　${state.index + 1} / ${total}`);
  htmlText('q-topic', chapter.topic);
  htmlText('q-index', `${state.index + 1} / ${total}　${question.id}`);
  const kind = document.getElementById('q-kind');
  kind.className = `badge kind ${question.type}`;
  kind.textContent = question.type === 'trap' ? 'ひっかけ' : '素直';

  htmlText('delta-title', question.delta.label);
  htmlText('delta-before', aliasText(question.delta.before, chapter));
  htmlText('delta-after', aliasText(question.delta.after, chapter));
  htmlText('delta-unchanged', aliasText(question.delta.unchanged, chapter));
  htmlText('question-text', aliasText(question.question, chapter));
  htmlText('common-case', aliasText(chapter.commonCase, chapter));

  const diagramBox = document.getElementById('case-diagram');
  diagramBox.textContent = '';
  diagramBox.appendChild(renderDiagram(chapter, question));

  const rec = state.mode === 'read' ? null : (ms.answers[question.id] || null);
  const answerRow = document.getElementById('answer-row');
  const readNext = document.getElementById('read-next');
  answerRow.hidden = state.mode === 'read';
  readNext.hidden = state.mode !== 'read';
  document.querySelectorAll('.ans').forEach((button) => {
    const picked = rec && String(rec.picked) === button.dataset.value;
    button.classList.toggle('picked', Boolean(picked));
    button.disabled = Boolean(rec);
  });

  const shouldShow = state.mode === 'read' || Boolean(rec);
  renderResult(chapter, question, rec, shouldShow);
  document.getElementById('retry-question').hidden = state.mode === 'read' || !rec;

  document.getElementById('prev-question').disabled = state.index === 0;
  const next = document.getElementById('next-question');
  next.hidden = state.mode === 'read';
  next.textContent = state.index === total - 1 ? '章のまとめへ →' : '次の問題 →';
  next.disabled = state.mode !== 'read' && !rec;
  readNext.textContent = state.index === total - 1 ? '理解した・章のまとめへ' : '理解した・次へ';
}

function renderResult(chapter, question, rec, show) {
  const result = document.getElementById('result');
  result.hidden = !show;
  if (!show) return;

  const verdict = document.getElementById('verdict');
  if (state.mode === 'read') {
    verdict.className = 'verdict read';
    verdict.textContent = `答えは ${question.answer ? '○' : '×'}`;
  } else {
    verdict.className = `verdict ${rec.correct ? 'ok' : 'ng'}`;
    verdict.textContent = rec.correct
      ? `正解　答えは ${question.answer ? '○' : '×'}`
      : `まちがい　答えは ${question.answer ? '○' : '×'}`;
  }

  const fields = {
    'result-summary': question.summary,
    'logic-fact': question.logic.fact,
    'logic-rule': question.logic.rule,
    'logic-conclusion': question.logic.conclusion,
    connection: question.connection,
    'changed-fact': question.changedFact,
    protect: question.protect,
    'reverse-problem': question.reverseProblem,
    'legal-rule': question.legalRule,
    intuition: question.intuition,
    trap: question.trap,
    minimum: question.minimum,
    'next-preview': question.nextPreview,
  };
  Object.entries(fields).forEach(([id, value]) => htmlText(id, aliasText(value, chapter)));

  const basis = document.getElementById('legal-basis');
  basis.textContent = '';
  (question.legalBasis || []).forEach((item) => {
    const link = document.createElement('a');
    link.href = item.url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = item.label;
    basis.appendChild(link);
  });
}

function answerCurrent(picked) {
  if (state.mode === 'read') return;
  const chapter = currentChapter();
  const question = currentQuestion();
  const ms = modeState(chapter.id);
  if (ms.answers[question.id]) return;
  const correct = picked === question.answer;
  ms.answers[question.id] = { picked, correct, at: new Date().toISOString() };
  if (!ms.stats[question.id]) ms.stats[question.id] = { attempts: 0, correct: 0 };
  ms.stats[question.id].attempts += 1;
  if (correct) ms.stats[question.id].correct += 1;
  saveStore();
  renderQuestion();
  document.getElementById('result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function markReadAndAdvance() {
  const chapter = currentChapter();
  const ms = modeState(chapter.id, 'read');
  const question = currentQuestion();
  if (!ms.seen.includes(question.id)) ms.seen.push(question.id);
  saveStore();
  advance();
}

function advance() {
  const chapter = currentChapter();
  if (state.index >= chapter.questions.length - 1) {
    completeRound();
    return;
  }
  state.index += 1;
  chapterState(chapter.id).positions[state.mode] = state.index;
  saveStore();
  renderQuestion();
  document.getElementById('question-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  updateHash();
}

function completeRound() {
  const chapter = currentChapter();
  const ms = modeState(chapter.id);
  if (!ms.completed) {
    ms.completed = true;
    ms.rounds += 1;
    if (state.mode === 'read') {
      const qids = chapter.questions.map((q) => q.id);
      qids.forEach((id) => { if (!ms.seen.includes(id)) ms.seen.push(id); });
      ms.lastRound = { seen: ms.seen.length, total: qids.length, at: new Date().toISOString() };
    } else {
      const answers = Object.values(ms.answers);
      ms.lastRound = {
        correct: answers.filter((a) => a.correct).length,
        total: chapter.questions.length,
        at: new Date().toISOString(),
      };
    }
    saveStore();
  }
  showSummary();
}

function showSummary() {
  const chapter = currentChapter();
  if (!chapter) return renderHome();
  document.getElementById('home-view').hidden = true;
  document.getElementById('study-view').hidden = true;
  document.getElementById('summary-view').hidden = false;
  const ms = modeState(chapter.id);
  htmlText('summary-title', `${aliasText(chapter.title, chapter)}　完了`);
  let result;
  if (state.mode === 'read') {
    result = `読むモードを${ms.rounds}周完了しました。今回は${ms.lastRound ? ms.lastRound.seen : chapter.questions.length}問を読みました。正答率には含めていません。`;
  } else {
    const last = ms.lastRound || { correct: 0, total: chapter.questions.length };
    const rate = last.total ? Math.round(last.correct / last.total * 100) : 0;
    result = `${state.mode === 'solve' ? '解く' : '本番変換'}モード第${ms.rounds}周：${last.correct} / ${last.total}正解（${rate}%）`;
  }
  htmlText('summary-result', result);

  const table = chapter.summaryTable;
  htmlText('summary-caption', table.caption);
  const thead = document.getElementById('summary-thead');
  const tbody = document.getElementById('summary-tbody');
  thead.textContent = '';
  tbody.textContent = '';
  const trh = document.createElement('tr');
  table.headers.forEach((header) => {
    const th = document.createElement('th');
    th.textContent = aliasText(header, chapter);
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  table.rows.forEach((row) => {
    const tr = document.createElement('tr');
    row.forEach((value) => {
      const td = document.createElement('td');
      td.textContent = aliasText(value, chapter);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  htmlText('summary-note', aliasText(table.note, chapter));
  document.getElementById('switch-solve').hidden = state.mode !== 'read';
  updateHash(true);
}

function repeatChapter() {
  const chapter = currentChapter();
  const ms = modeState(chapter.id);
  ms.completed = false;
  ms.lastRound = null;
  if (state.mode === 'read') ms.seen = [];
  else ms.answers = {};
  state.index = 0;
  chapterState(chapter.id).positions[state.mode] = 0;
  saveStore();
  showStudy();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function retryCurrent() {
  if (state.mode === 'read') return;
  const chapter = currentChapter();
  const question = currentQuestion();
  delete modeState(chapter.id).answers[question.id];
  saveStore();
  renderQuestion();
  document.getElementById('question-text').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function previous() {
  if (state.index <= 0) return;
  state.index -= 1;
  chapterState(state.chapterId).positions[state.mode] = state.index;
  saveStore();
  renderQuestion();
  document.getElementById('question-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  updateHash();
}

function updateHash(summary = false) {
  const hash = state.chapterId
    ? `chapter=${encodeURIComponent(state.chapterId)}&q=${state.index + 1}&mode=${state.mode}${summary ? '&summary=1' : ''}`
    : `mode=${state.mode}`;
  history.replaceState(null, '', `#${hash}`);
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

function resetAll() {
  if (!window.confirm('民法・連続事例ドリルC案の読了・回答記録をすべて消します。通常の学習カード履歴には影響しません。')) return;
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
  document.querySelectorAll('.ans').forEach((button) => {
    button.addEventListener('click', () => answerCurrent(button.dataset.value === 'true'));
  });
  document.getElementById('read-next').addEventListener('click', markReadAndAdvance);
  document.getElementById('next-question').addEventListener('click', () => {
    if (state.mode === 'read') markReadAndAdvance();
    else advance();
  });
  document.getElementById('prev-question').addEventListener('click', previous);
  document.getElementById('retry-question').addEventListener('click', retryCurrent);
  document.getElementById('back-home').addEventListener('click', renderHome);
  document.getElementById('summary-home').addEventListener('click', renderHome);
  document.getElementById('repeat-chapter').addEventListener('click', repeatChapter);
  document.getElementById('switch-solve').addEventListener('click', () => {
    setMode('solve', { skipRender: true });
    const ms = modeState(state.chapterId, 'solve');
    if (ms.completed) {
      ms.completed = false;
      ms.answers = {};
      ms.lastRound = null;
    }
    state.index = 0;
    chapterState(state.chapterId).positions.solve = 0;
    saveStore();
    showStudy();
  });
  document.getElementById('reset-all').addEventListener('click', resetAll);
}

async function init() {
  bind();
  try {
    const response = await fetch('cases.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    htmlText('as-of', `法令基準日 ${state.data.asOf}`);
    const initial = parseHash();
    state.mode = MODE_NOTES[initial.mode] ? initial.mode : (MODE_NOTES[state.store.mode] ? state.store.mode : 'read');
    setMode(state.mode, { skipRender: true });
    if (initial.chapter && chapterById(initial.chapter)) {
      state.chapterId = initial.chapter;
      state.index = Math.min(initial.index, chapterById(initial.chapter).questions.length - 1);
      chapterState(state.chapterId).positions[state.mode] = state.index;
      if (initial.summary && modeState(state.chapterId).completed) showSummary();
      else showStudy();
    } else {
      renderHome();
    }
  } catch (err) {
    const grid = document.getElementById('chapter-grid');
    grid.textContent = `問題データを読み込めませんでした。${err.message}`;
  }
}

init();
