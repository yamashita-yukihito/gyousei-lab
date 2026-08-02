'use strict';

/* 民法・固定配役ドリル（B案）
 *
 * 設計方針:
 * - 1場面の全命題を1ページに縦に並べる。スクロールで全部見える。
 * - URLハッシュで画面を切り替える。ブラウザの戻るボタンが自然に動く。
 *   - (なし) or #  → 場面一覧
 *   - #scene-001   → その場面の全命題
 * - 記録はlocalStorage（minpoB:v1）にだけ置く。SQLiteには書かない。
 */

const STORE_KEY = 'minpoB:v1';

const state = {
  data: null,
  records: loadRecords(),
};

/* ----------------------------------------------------------------- 記録 */

function loadRecords() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function saveRecords() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state.records));
  } catch (_) { /* 保存できなくても学習は続けられる */ }
}

function getSceneRecord(sceneId) {
  if (!state.records[sceneId]) state.records[sceneId] = {};
  return state.records[sceneId];
}

function clearRecords() {
  if (!confirm('本当に記録を削除しますか？')) return;
  state.records = {};
  saveRecords();
  route();
}

/* ----------------------------------------------------------------- 初期化 */

async function init() {
  try {
    const res = await fetch('problems.json');
    if (!res.ok) throw new Error('Failed to load');
    state.data = await res.json();
  } catch (err) {
    document.getElementById('main').innerHTML =
      '<p style="color:red;padding:2rem">問題データの読み込みに失敗しました。</p>';
    return;
  }

  window.addEventListener('hashchange', route);
  route();
}

/* ----------------------------------------------------------- ルーティング */

function route() {
  const hash = location.hash.replace('#', '');
  const listView = document.getElementById('scene-list-view');
  const sceneView = document.getElementById('scene-view');

  if (hash && state.data.scenes.find(s => s.id === hash)) {
    listView.classList.add('hidden');
    sceneView.classList.remove('hidden');
    renderScene(hash);
  } else {
    sceneView.classList.add('hidden');
    listView.classList.remove('hidden');
    renderSceneList();
    renderCast();
  }
  window.scrollTo(0, 0);
}

/* ----------------------------------------------------------- 場面一覧 */

function renderCast() {
  const el = document.getElementById('cast-content');
  if (!state.data.cast || !state.data.cast.length) {
    document.getElementById('cast-section').classList.add('hidden');
    return;
  }
  el.innerHTML = state.data.cast.map(c => `
    <div class="cast-card">
      <span class="cast-color-dot" style="background:${esc(c.color || '#ccc')}"></span>
      <span class="cast-emoji">${esc(c.emoji || '')}</span>
      <div class="cast-info">
        <span class="cast-name">${esc(c.name)}</span>
        <span class="cast-role">${esc(c.role || '')}</span>
      </div>
    </div>
  `).join('');
}

function renderSceneList() {
  const container = document.getElementById('scene-cards');
  container.innerHTML = '';

  state.data.scenes.forEach(scene => {
    const rec = getSceneRecord(scene.id);
    const total = scene.statements.length;
    const answered = Object.keys(rec).length;
    const correct = Object.values(rec).filter(r => r.correct).length;
    const done = answered >= total;

    let progress = `${total} 命題`;
    if (done) progress = `✅ ${correct} / ${total} 正解`;
    else if (answered > 0) progress = `${answered} / ${total} 回答済み（${correct} 正解）`;

    const card = document.createElement('a');
    card.href = '#' + scene.id;
    card.className = 'scene-card' + (done ? ' scene-card-complete' : '');
    card.innerHTML = `
      <div class="scene-card-topic">${esc(scene.topic)}</div>
      <div class="scene-card-subtopic">${esc(scene.subtopic)}</div>
      <div class="scene-card-progress">${progress}</div>
    `;
    container.appendChild(card);
  });
}

/* ----------------------------------------------------------- 場面表示 */

function renderScene(sceneId) {
  const scene = state.data.scenes.find(s => s.id === sceneId);
  if (!scene) return;

  const view = document.getElementById('scene-view');

  // ヘッダー（場面の画像＋事例文）
  let imgHtml = '';
  if (scene.image && scene.image.src) {
    imgHtml = `
      <figure class="scene-figure">
        <img src="${esc(scene.image.src)}" alt="${esc(scene.image.alt || '')}"
             onerror="this.style.display='none'">
        ${scene.image.caption ? `<figcaption>${esc(scene.image.caption)}</figcaption>` : ''}
      </figure>`;
  }

  const statementsHtml = scene.statements.map((stmt, idx) =>
    renderStatementCard(scene, stmt, idx)
  ).join('');

  view.innerHTML = `
    <div class="scene-nav">
      <a href="#" class="back-btn">← 場面一覧に戻る</a>
      <span class="scene-badge">${esc(scene.topic)}</span>
    </div>

    ${imgHtml}

    <div class="scene-description">
      <h2>${esc(scene.subtopic)}</h2>
      <div class="scene-text">${castHighlight(scene.scene)}</div>
    </div>

    <div class="statements-list">
      ${statementsHtml}
    </div>

    <div class="scene-bottom-nav">
      <a href="#" class="back-btn">← 場面一覧に戻る</a>
    </div>
  `;

  // ○×ボタンのイベント
  scene.statements.forEach((stmt, idx) => {
    const card = document.getElementById('stmt-' + stmt.id);
    if (!card) return;

    const rec = getSceneRecord(sceneId);
    if (rec[stmt.id]) {
      // すでに回答済み → 解説を表示した状態にする
      revealAnswer(card, stmt, rec[stmt.id].correct, rec[stmt.id].userAnswer);
      return;
    }

    const btnO = card.querySelector('.btn-o');
    const btnX = card.querySelector('.btn-x');
    btnO.addEventListener('click', () => answer(sceneId, stmt, card, true));
    btnX.addEventListener('click', () => answer(sceneId, stmt, card, false));
  });
}

function renderStatementCard(scene, stmt, idx) {
  const rec = getSceneRecord(scene.id);
  const answered = !!rec[stmt.id];

  return `
    <div class="stmt-card ${answered ? 'stmt-answered' : ''}" id="stmt-${esc(stmt.id)}">
      <div class="stmt-number">命題 ${idx + 1}</div>
      <div class="stmt-text">${esc(stmt.text)}</div>
      <div class="stmt-buttons">
        <button class="btn-o" ${answered ? 'disabled' : ''}>○</button>
        <button class="btn-x" ${answered ? 'disabled' : ''}>×</button>
      </div>
      <div class="stmt-explanation hidden"></div>
    </div>
  `;
}

function answer(sceneId, stmt, card, userAnswer) {
  const isCorrect = userAnswer === stmt.answer;

  const rec = getSceneRecord(sceneId);
  rec[stmt.id] = { correct: isCorrect, userAnswer, answeredAt: Date.now() };
  saveRecords();

  revealAnswer(card, stmt, isCorrect, userAnswer);
}

function revealAnswer(card, stmt, isCorrect, userAnswer) {
  card.classList.add('stmt-answered');
  card.querySelector('.btn-o').disabled = true;
  card.querySelector('.btn-x').disabled = true;

  // ボタンに正解マークを付ける
  const btnO = card.querySelector('.btn-o');
  const btnX = card.querySelector('.btn-x');
  if (stmt.answer === true) btnO.classList.add('btn-correct');
  else btnX.classList.add('btn-correct');
  if (userAnswer === true && !isCorrect) btnO.classList.add('btn-wrong');
  if (userAnswer === false && !isCorrect) btnX.classList.add('btn-wrong');

  const expEl = card.querySelector('.stmt-explanation');

  let stepsHtml = '';
  if (stmt.explanation && stmt.explanation.steps) {
    stepsHtml = '<div class="timeline">' +
      stmt.explanation.steps.map(s =>
        `<div class="timeline-step">${castHighlight(s)}</div>`
      ).join('') + '</div>';
  }

  let pointHtml = '';
  if (stmt.explanation && stmt.explanation.point) {
    pointHtml = `<div class="point-box">📌 ${esc(stmt.explanation.point)}</div>`;
  }

  let articleHtml = '';
  if (stmt.explanation && stmt.explanation.keyArticle) {
    articleHtml = `<div class="key-article">${esc(stmt.explanation.keyArticle)}</div>`;
  }

  expEl.innerHTML = `
    <div class="result-badge ${isCorrect ? 'correct' : 'incorrect'}">
      ${isCorrect ? '✓ 正解' : '✗ 不正解'}
    </div>
    <div class="answer-line">正答: <strong>${stmt.answer ? '○' : '×'}</strong></div>
    ${stepsHtml}
    ${articleHtml}
    ${pointHtml}
  `;
  expEl.classList.remove('hidden');
}

/* ----------------------------------------------------------- ユーティリティ */

function castHighlight(text) {
  let out = esc(text);
  if (state.data.cast) {
    state.data.cast.forEach(c => {
      const re = new RegExp(esc(c.name), 'g');
      out = out.replace(re,
        `<span style="color:${esc(c.color||'#333')};font-weight:bold">${esc(c.name)}</span>`);
    });
  }
  return out.replace(/\n/g, '<br>');
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ----------------------------------------------------------- イベント */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cast-toggle').addEventListener('click', () => {
    document.getElementById('cast-content').classList.toggle('hidden');
  });
  document.getElementById('clear-data-btn').addEventListener('click', clearRecords);
  init();
});
