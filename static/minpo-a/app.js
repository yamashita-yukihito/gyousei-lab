'use strict';

/* 民法・固定配役ドリル（A案）
 *
 * ねらいは1つだけ。事例問題の「登場人物の設定を読み解く時間」をゼロに近づける。
 * そのために (1) 役割ごとにキャラクターを固定し (2) 関係図を問題文より先に出す。
 * 記録はこの端末のlocalStorageにだけ置く。学習アプリの回答履歴とは混ぜない。
 */

const STORE_KEY = 'minpoA:v1';
const NS = 'http://www.w3.org/2000/svg';

const state = {
  data: null,
  cast: new Map(),
  order: [],
  pos: 0,
  answers: loadAnswers(),
};

/* ------------------------------------------------------------------ 記録 */

function loadAnswers() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (err) {
    return {};
  }
}

function saveAnswers() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state.answers));
  } catch (err) {
    /* 保存できなくても学習は続けられる */
  }
}

/* ------------------------------------------------------------ SVG関係図 */

const NODE_W = 190;
const COL_STEP = 250;
const ROW_STEP = 150;
const PAD = 22;
const CHARS_PER_LINE = 14;
const LABEL_CHARS = 11;

// 全角と半角を混ぜても折り返し位置がずれないよう、幅を重み付けで数える。
function measure(text) {
  let width = 0;
  for (const ch of String(text)) width += /[\x20-\x7e]/.test(ch) ? 0.5 : 1;
  return width;
}

function wrapLabel(text, limit) {
  const lines = [];
  let line = '';
  let width = 0;
  for (const ch of String(text)) {
    const w = /[\x20-\x7e]/.test(ch) ? 0.5 : 1;
    if (width + w > limit && line) {
      lines.push(line);
      line = '';
      width = 0;
    }
    line += ch;
    width += w;
  }
  if (line) lines.push(line);
  return lines;
}

function el(name, attrs, text) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function layoutNodes(nodes) {
  const boxes = new Map();
  for (const n of nodes) {
    const lines = n.label ? wrapLabel(n.label, CHARS_PER_LINE) : [];
    const isPerson = n.kind === 'person';
    const head = isPerson ? 21 : 19;
    const h = 10 + head + lines.length * 16 + 8;
    boxes.set(n.id, {
      node: n,
      lines,
      w: NODE_W,
      h,
      cx: PAD + n.x * COL_STEP + NODE_W / 2,
      cy: PAD + n.y * ROW_STEP + h / 2,
    });
  }
  return boxes;
}

// 中心から相手方向へ伸ばした線が、自分の枠と交わる点。矢印を枠の外側で止める。
function clipToBox(box, tx, ty) {
  const dx = tx - box.cx;
  const dy = ty - box.cy;
  if (!dx && !dy) return [box.cx, box.cy];
  const sx = dx ? (box.w / 2 + 4) / Math.abs(dx) : Infinity;
  const sy = dy ? (box.h / 2 + 4) / Math.abs(dy) : Infinity;
  const s = Math.min(sx, sy);
  return [box.cx + dx * s, box.cy + dy * s];
}

function overlaps(a, b, margin) {
  return a.x < b.x + b.w - margin && b.x < a.x + a.w - margin
    && a.y < b.y + b.h - margin && b.y < a.y + a.h - margin;
}

// 線のラベルは、まん中に置くと隣の枠や別のラベルに重なることがある。
// 線に沿った位置と、線と直角方向のずらし幅を順に試し、何にもぶつからない場所に置く。
function placeLabel(at, boxW, boxH, occupied) {
  let fallback = null;
  for (const t of [0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78, 0.15, 0.85]) {
    for (const step of [0, -1, 1, -2, 2, -3, 3, -4, 4]) {
      const p = at(t);
      const len = Math.hypot(p.dx, p.dy) || 1;
      const x = p.x + (-p.dy / len) * step * 26;
      const y = p.y + (p.dx / len) * step * 26;
      const box = { x: x - boxW / 2, y: y - boxH / 2, w: boxW, h: boxH };
      if (!fallback) fallback = { x, y };
      if (!occupied.some((o) => overlaps(box, o, 3))) return { x, y };
    }
  }
  return fallback;
}

const EDGE_STYLE = {
  solid: { stroke: '#4a5c55', width: 2, dash: null, arrow: 'arrow', text: '#3c4a44' },
  bold: { stroke: '#1c7157', width: 2.6, dash: null, arrow: 'arrow-green', text: '#1c7157' },
  dashed: { stroke: '#8a6412', width: 2, dash: '7 5', arrow: 'arrow-amber', text: '#8a6412' },
  thin: { stroke: '#9aa8a2', width: 1.4, dash: '3 4', arrow: null, text: '#64716c' },
};

function marker(id, color) {
  const m = el('marker', {
    id, viewBox: '0 0 10 10', refX: '9', refY: '5',
    markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse',
  });
  m.appendChild(el('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: color }));
  return m;
}

function renderDiagram(diagram) {
  const boxes = layoutNodes(diagram.nodes);

  // 枠も線ラベルも切れないよう、置いたものすべてを囲む範囲をあとでviewBoxにする。
  const bounds = { x0: 0, y0: 0, x1: 0, y1: 0 };
  const cover = (x, y, w, h) => {
    bounds.x0 = Math.min(bounds.x0, x);
    bounds.y0 = Math.min(bounds.y0, y);
    bounds.x1 = Math.max(bounds.x1, x + w);
    bounds.y1 = Math.max(bounds.y1, y + h);
  };
  for (const b of boxes.values()) cover(b.cx - b.w / 2, b.cy - b.h / 2, b.w, b.h);

  const svg = el('svg', { role: 'img', 'aria-label': '登場人物の関係図' });

  const defs = el('defs');
  defs.appendChild(marker('arrow', '#4a5c55'));
  defs.appendChild(marker('arrow-green', '#1c7157'));
  defs.appendChild(marker('arrow-amber', '#8a6412'));
  svg.appendChild(defs);

  // 同じ2点を結ぶ線が複数あるときは、重ならないよう左右へ振り分けて曲げる。
  const groups = new Map();
  (diagram.edges || []).forEach((e) => {
    const key = [e.from, e.to].sort().join('|');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  });

  const edgeLayer = el('g');
  const labelLayer = el('g');
  // ラベルを置くときに避けるもの。人物や物の枠から始めて、置いたラベルを足していく。
  const occupied = [...boxes.values()].map((b) => ({
    x: b.cx - b.w / 2, y: b.cy - b.h / 2, w: b.w, h: b.h,
  }));

  for (const list of groups.values()) {
    list.forEach((e, i) => {
      const a = boxes.get(e.from);
      const b = boxes.get(e.to);
      if (!a || !b) return;
      const st = EDGE_STYLE[e.style] || EDGE_STYLE.solid;
      const shift = (i - (list.length - 1) / 2) * 52;

      const mx = (a.cx + b.cx) / 2;
      const my = (a.cy + b.cy) / 2;
      const len = Math.hypot(b.cx - a.cx, b.cy - a.cy) || 1;
      const nx = -(b.cy - a.cy) / len;
      const ny = (b.cx - a.cx) / len;
      const ctrl = { x: mx + nx * shift * 2, y: my + ny * shift * 2 };

      const aim = shift ? ctrl : { x: b.cx, y: b.cy };
      const back = shift ? ctrl : { x: a.cx, y: a.cy };
      const [x1, y1] = clipToBox(a, aim.x, aim.y);
      const [x2, y2] = clipToBox(b, back.x, back.y);

      const path = el('path', {
        d: shift ? `M ${x1} ${y1} Q ${ctrl.x} ${ctrl.y} ${x2} ${y2}` : `M ${x1} ${y1} L ${x2} ${y2}`,
        fill: 'none',
        stroke: st.stroke,
        'stroke-width': st.width,
        'stroke-linecap': 'round',
      });
      if (st.dash) path.setAttribute('stroke-dasharray', st.dash);
      if (st.arrow) path.setAttribute('marker-end', `url(#${st.arrow})`);
      edgeLayer.appendChild(path);

      if (!e.label) return;
      const lines = wrapLabel(e.label, LABEL_CHARS);
      const boxH = lines.length * 14 + 6;
      let boxW = 0;
      for (const s of lines) boxW = Math.max(boxW, measure(s) * 11 + 12);
      const at = (t) => (shift
        ? {
          x: (1 - t) ** 2 * x1 + 2 * (1 - t) * t * ctrl.x + t ** 2 * x2,
          y: (1 - t) ** 2 * y1 + 2 * (1 - t) * t * ctrl.y + t ** 2 * y2,
          dx: 2 * (1 - t) * (ctrl.x - x1) + 2 * t * (x2 - ctrl.x),
          dy: 2 * (1 - t) * (ctrl.y - y1) + 2 * t * (y2 - ctrl.y),
        }
        : { x: x1 + (x2 - x1) * t, y: y1 + (y2 - y1) * t, dx: x2 - x1, dy: y2 - y1 });
      const spot = placeLabel(at, boxW, boxH, occupied);
      occupied.push({ x: spot.x - boxW / 2, y: spot.y - boxH / 2, w: boxW, h: boxH });

      labelLayer.appendChild(el('rect', {
        class: 'edge-label-box',
        x: spot.x - boxW / 2, y: spot.y - boxH / 2, width: boxW, height: boxH,
        rx: 5, fill: '#fbfdfb', stroke: '#e3ebe6',
      }));
      cover(spot.x - boxW / 2, spot.y - boxH / 2, boxW, boxH);
      lines.forEach((s, k) => {
        labelLayer.appendChild(el('text', {
          x: spot.x, y: spot.y - boxH / 2 + 14 + k * 14,
          'text-anchor': 'middle', 'font-size': '11', 'font-weight': '700', fill: st.text,
        }, s));
      });
    });
  }

  svg.appendChild(edgeLayer);

  const nodeLayer = el('g');
  for (const b of boxes.values()) {
    const n = b.node;
    const isPerson = n.kind === 'person';
    const cast = isPerson ? state.cast.get(n.cast) : null;
    const color = cast ? cast.color : '#64716c';
    const g = el('g');
    g.appendChild(el('rect', {
      class: 'node-box',
      x: b.cx - b.w / 2, y: b.cy - b.h / 2, width: b.w, height: b.h,
      rx: isPerson ? 11 : 6,
      fill: isPerson ? '#ffffff' : '#f2f6f3',
      stroke: color,
      'stroke-width': isPerson ? 2 : 1.4,
      'stroke-dasharray': isPerson ? '' : '5 4',
    }));
    const top = b.cy - b.h / 2 + 10;
    const title = isPerson ? `${cast ? cast.emoji : ''} ${cast ? cast.name : n.id}` : n.label;
    g.appendChild(el('text', {
      x: b.cx, y: top + 14, 'text-anchor': 'middle',
      'font-size': isPerson ? '14' : '13', 'font-weight': '700',
      fill: isPerson ? color : '#3c4a44',
    }, title));
    if (isPerson) {
      b.lines.forEach((s, k) => {
        g.appendChild(el('text', {
          x: b.cx, y: top + 21 + 12 + k * 16, 'text-anchor': 'middle',
          'font-size': '11.5', fill: '#64716c',
        }, s));
      });
    }
    nodeLayer.appendChild(g);
  }
  svg.appendChild(nodeLayer);
  svg.appendChild(labelLayer);

  const vx = Math.round(bounds.x0 - PAD);
  const vy = Math.round(bounds.y0 - PAD);
  const vw = Math.round(bounds.x1 - bounds.x0 + PAD * 2);
  const vh = Math.round(bounds.y1 - bounds.y0 + PAD * 2);
  svg.setAttribute('viewBox', `${vx} ${vy} ${vw} ${vh}`);
  svg.setAttribute('style', `max-width:${vw}px`);
  return svg;
}

// 問題文の上に出す関係図には答えを書かない。答えを入れた図は回答後に別で出す。
// 座標は上の図をそのまま使い、枠のラベルだけ差し替える。ずれる余地を残さない。
function answerDiagramOf(problem) {
  const patch = problem.answerDiagram;
  if (!patch || !patch.edges) return null;
  const labels = patch.nodeLabels || {};
  return {
    nodes: problem.diagram.nodes.map((n) => (n.id in labels ? { ...n, label: labels[n.id] } : n)),
    edges: patch.edges,
  };
}

/* -------------------------------------------------------------- 場面画像 */

// 画像は assets/ の下だけ。問題データが別の場所を指していたら描かない。
const SCENE_SRC = /^assets\/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|svg|webp)$/;

function renderScene(slot, image) {
  const box = document.getElementById(`p-scene-${slot}`);
  box.textContent = '';
  if (!image || image.placement !== slot || !SCENE_SRC.test(image.src || '')) {
    box.hidden = true;
    return;
  }
  const img = document.createElement('img');
  img.src = image.src;
  img.alt = image.alt || '';
  img.loading = 'lazy';
  const caption = document.createElement('figcaption');
  caption.textContent = image.caption || '';
  box.append(img, caption);
  box.hidden = false;
}

/* ---------------------------------------------------------------- 配役表 */

function renderCast() {
  const list = document.getElementById('cast-list');
  list.textContent = '';
  for (const c of state.data.cast) {
    const li = document.createElement('li');
    li.className = 'cast-item';
    li.style.borderLeftColor = c.color;

    const emoji = document.createElement('span');
    emoji.className = 'cast-emoji';
    emoji.textContent = c.emoji;

    const body = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'cast-name';
    name.textContent = c.name;
    const role = document.createElement('span');
    role.className = 'cast-role';
    role.style.background = c.color;
    role.textContent = c.role;
    name.appendChild(role);

    const detail = document.createElement('p');
    detail.className = 'cast-detail';
    detail.textContent = c.detail;

    body.append(name, detail);
    li.append(emoji, body);
    list.appendChild(li);
  }
  document.getElementById('cast-note').textContent = state.data.roleSwapNote;
}

/* ---------------------------------------------------------------- 出題順 */

function pool() {
  const topic = document.getElementById('filter-topic').value;
  const status = document.getElementById('filter-status').value;
  return state.data.problems.filter((p) => {
    if (topic && p.topic !== topic) return false;
    const rec = state.answers[p.id];
    if (status === 'unanswered') return !rec;
    if (status === 'wrong') return rec && !rec.correct;
    return true;
  });
}

function rebuildOrder(keepId) {
  state.order = pool().map((p) => p.id);
  const at = keepId ? state.order.indexOf(keepId) : -1;
  state.pos = at >= 0 ? at : 0;
  render();
}

function shuffleOrder() {
  const ids = state.order.slice();
  for (let i = ids.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  state.order = ids;
  state.pos = 0;
  render();
}

/* ------------------------------------------------------------------ 描画 */

function currentProblem() {
  const id = state.order[state.pos];
  return state.data.problems.find((p) => p.id === id) || null;
}

function render() {
  const problem = currentProblem();
  const box = document.getElementById('problem');
  const empty = document.getElementById('empty');
  document.getElementById('loading').hidden = true;

  renderProgress();
  renderScore();

  if (!problem) {
    box.hidden = true;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  box.hidden = false;

  document.getElementById('p-topic').textContent = problem.topic;
  document.getElementById('p-subtopic').textContent = problem.subtopic;
  document.getElementById('p-index').textContent =
    `${state.pos + 1} / ${state.order.length}　${problem.id}`;

  const dia = document.getElementById('p-diagram');
  dia.textContent = '';
  dia.appendChild(renderDiagram(problem.diagram));

  renderScene('setup', problem.image);

  const setup = document.getElementById('p-setup');
  setup.textContent = '';
  for (const line of problem.setup.split('\n')) {
    const p = document.createElement('p');
    p.textContent = line;
    setup.appendChild(p);
  }

  document.getElementById('p-question').textContent = problem.question;

  const rec = state.answers[problem.id];
  for (const btn of document.querySelectorAll('.ans')) {
    const picked = rec && String(rec.picked) === btn.dataset.value;
    btn.classList.toggle('picked', Boolean(picked));
    btn.disabled = Boolean(rec);
  }

  const result = document.getElementById('p-result');
  renderScene('explanation', rec ? problem.image : null);

  const answerFigure = document.getElementById('p-answer-diagram');
  const answered = answerDiagramOf(problem);
  const answerBox = document.getElementById('p-answer-diagram-box');
  answerBox.textContent = '';
  if (rec && answered) {
    answerBox.appendChild(renderDiagram(answered));
    answerFigure.hidden = false;
  } else {
    answerFigure.hidden = true;
  }

  if (!rec) {
    result.hidden = true;
  } else {
    result.hidden = false;
    const verdict = document.getElementById('p-verdict');
    verdict.className = `verdict ${rec.correct ? 'ok' : 'ng'}`;
    verdict.textContent = rec.correct
      ? `正解　答えは ${problem.answer ? '○' : '×'}`
      : `まちがい　答えは ${problem.answer ? '○' : '×'}`;
    document.getElementById('p-correction').textContent = problem.correction;
    document.getElementById('p-point').textContent = `覚えどころ　${problem.point}`;
    const linked = document.getElementById('p-linked');
    if (problem.cardId) {
      linked.hidden = false;
      linked.textContent = '';
      linked.append(document.createTextNode('学習アプリの対応カード： '));
      const code = document.createElement('code');
      code.textContent = problem.cardId;
      linked.appendChild(code);
    } else {
      linked.hidden = false;
      linked.textContent = '学習アプリにまだカードが無い論点。記述式で問われやすい。';
    }
  }

  document.getElementById('prev').disabled = state.pos === 0;
  document.getElementById('next').disabled = state.pos >= state.order.length - 1;
}

function renderProgress() {
  const total = state.data.problems.length;
  const done = Object.keys(state.answers).length;
  const ok = Object.values(state.answers).filter((a) => a.correct).length;
  const rate = done ? Math.round((ok / done) * 100) : 0;
  document.getElementById('progress').textContent =
    `全${total}問中 ${done}問回答／正答 ${ok}問（${rate}%）　いま出題中の範囲 ${state.order.length}問`;
}

function renderScore() {
  const byTopic = new Map();
  for (const p of state.data.problems) {
    if (!byTopic.has(p.topic)) byTopic.set(p.topic, { total: 0, done: 0, ok: 0 });
    const row = byTopic.get(p.topic);
    row.total += 1;
    const rec = state.answers[p.id];
    if (rec) {
      row.done += 1;
      if (rec.correct) row.ok += 1;
    }
  }
  const list = document.getElementById('score-list');
  list.textContent = '';
  for (const [topic, row] of byTopic) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = topic;
    const val = document.createElement('span');
    val.className = 'val';
    val.textContent = row.done
      ? `${row.ok} / ${row.done} 正解（全${row.total}問）`
      : `未回答（全${row.total}問）`;
    li.append(name, val);
    list.appendChild(li);
  }
}

/* ------------------------------------------------------------------ 操作 */

function answer(picked) {
  const problem = currentProblem();
  if (!problem || state.answers[problem.id]) return;
  state.answers[problem.id] = {
    picked,
    correct: picked === problem.answer,
    at: new Date().toISOString(),
  };
  saveAnswers();
  render();
  document.getElementById('p-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function bind() {
  document.getElementById('cast-toggle').addEventListener('click', (ev) => {
    const body = document.getElementById('cast-body');
    const open = body.hidden;
    body.hidden = !open;
    ev.currentTarget.textContent = open ? 'たたむ' : 'ひらく';
    ev.currentTarget.setAttribute('aria-expanded', String(open));
  });

  for (const btn of document.querySelectorAll('.ans')) {
    btn.addEventListener('click', () => answer(btn.dataset.value === 'true'));
  }

  document.getElementById('prev').addEventListener('click', () => {
    if (state.pos > 0) {
      state.pos -= 1;
      render();
    }
  });
  document.getElementById('next').addEventListener('click', () => {
    if (state.pos < state.order.length - 1) {
      state.pos += 1;
      render();
    }
  });

  const keep = () => currentProblem()?.id;
  document.getElementById('filter-topic').addEventListener('change', () => rebuildOrder(keep()));
  document.getElementById('filter-status').addEventListener('change', () => rebuildOrder());
  document.getElementById('shuffle').addEventListener('click', shuffleOrder);
  document.getElementById('reset').addEventListener('click', () => {
    if (!window.confirm('この端末に保存した回答の記録を消します。よろしいですか。')) return;
    state.answers = {};
    saveAnswers();
    rebuildOrder();
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.target instanceof HTMLSelectElement) return;
    if (ev.key === 'ArrowRight') document.getElementById('next').click();
    if (ev.key === 'ArrowLeft') document.getElementById('prev').click();
    if (ev.key === 'o' || ev.key === '1') answer(true);
    if (ev.key === 'x' || ev.key === '2') answer(false);
  });
}

/* ------------------------------------------------------------------ 起動 */

async function boot() {
  const res = await fetch('problems.json', { cache: 'no-cache' });
  if (!res.ok) {
    document.getElementById('loading').textContent = '問題を読み込めませんでした。';
    return;
  }
  state.data = await res.json();
  for (const c of state.data.cast) state.cast.set(c.id, c);

  const sel = document.getElementById('filter-topic');
  for (const topic of [...new Set(state.data.problems.map((p) => p.topic))]) {
    const opt = document.createElement('option');
    opt.value = topic;
    opt.textContent = topic;
    sel.appendChild(opt);
  }

  renderCast();
  bind();
  // ?p=minpo-a-008 のように問題を直接指定できる。特定の1問を見返すとき用。
  rebuildOrder(new URLSearchParams(location.search).get('p') || undefined);
}

boot();
