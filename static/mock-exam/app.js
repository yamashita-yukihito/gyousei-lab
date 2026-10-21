'use strict';

// 本試験形式の通し演習。1年度分を時間を測って解き、本番と同じ数え方で採点する。
// 途中経過はこのブラウザのlocalStorageだけに持つ。回答は終了時にまとめて
// 過去問の回答履歴（POST /api/attempts、mode=mock_exam）へ送る。学習カードの記録には書かない。

const APP_VERSION = 'mock-20261021-3';
const API = '../api';
const STORE_KEY = 'gyouseiMock:v1';
const STORE_VERSION = 1;
const CLIENT_HEADER = 'web-v1';
const QUESTION_COUNT = 60;
const HISTORY_LIMIT = 20;
// 収録は平成28〜令和7年度。新しい年度を足したらここも広げる。
const YEARS = [2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016];
// 試験研究センターの問題PDFは平成30年度以降だけが同じ形のURLで公開されている。
const OFFICIAL_PDF_FROM = 2018;
// docs/STUDY_SCHEDULE.md の通し演習。日付を過ぎたら次の回を勧める。
const PLANNED = [
  { date: '2026-10-25', year: 2021, label: '通し演習1（10/25）' },
  { date: '2026-11-01', year: 2020, label: '通し演習2（11/1）' },
];
const SUBJECTS = [
  ['administrative-law', '行政法'],
  ['civil-law', '民法'],
  ['constitutional-law', '憲法'],
  ['commercial-law', '商法・会社法'],
  ['legal-foundations', '基礎法学'],
  ['general-knowledge', '基礎知識'],
];
const SUBJECT_LABEL = new Map(SUBJECTS);
// 合格基準（行政書士試験）。法令等は244点中122点、基礎知識は56点中24点、合計は300点中180点。
const PASS = { legal: 122, legalMax: 244, general: 24, generalMax: 56, total: 180, totalMax: 300 };
const WRITTEN_STEPS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20];
// 間違えた理由。次の1週間で何を直すかに直結する分け方にする（知識を足す／読み方を直す／時間配分を直す）。
const REASONS = [
  ['unknown', '知らなかった'],
  ['misremember', '覚え違い'],
  ['two', '2択まで絞って外した'],
  ['misread', '問題文・肢の読み違い'],
  ['time', '時間不足・焦り'],
];
const REASON_LABEL = new Map(REASONS);

const state = {
  store: loadStore(),
  storageOk: true,
  year: null,
  questions: [],
  slots: [],
  cache: new Map(),
  timerId: null,
  viewStartedAt: null,
  cardIndex: null,
  drillIndex: null,
  reviewMode: false,
};

// ---- 保存 -------------------------------------------------------------

function blankStore() {
  return { version: STORE_VERSION, active: null, history: [] };
}

function loadStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!parsed || parsed.version !== STORE_VERSION || !Array.isArray(parsed.history)) return blankStore();
    return parsed;
  } catch (_err) {
    return blankStore();
  }
}

function saveStore() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state.store));
    state.storageOk = true;
  } catch (_err) {
    state.storageOk = false;
  }
  $('storage-warning').hidden = state.storageOk;
}

// ---- 問題の取得と並べ方 -------------------------------------------------

async function fetchJson(url, options) {
  const response = await fetch(url, Object.assign({ cache: 'no-store' }, options || {}));
  let body = null;
  try { body = await response.json(); } catch (_err) { body = null; }
  if (!response.ok) {
    const error = new Error((body && body.error) || ('HTTP ' + response.status));
    error.status = response.status;
    throw error;
  }
  return body;
}

async function loadYear(year) {
  if (state.cache.has(year)) return state.cache.get(year);
  const body = await fetchJson(API + '/questions?year=' + year + '&limit=200');
  const items = (body && body.items) || [];
  state.cache.set(year, items);
  return items;
}

function buildSlots(questions) {
  const byNumber = new Map(questions.map((q) => [q.exam.number, q]));
  const slots = [];
  for (let number = 1; number <= QUESTION_COUNT; number += 1) {
    const question = byNumber.get(number) || null;
    slots.push({ number, question, section: sectionOf(number), subjectId: question ? question.subjectId : missingSubject(number) });
  }
  return slots;
}

function sectionOf(number) {
  if (number <= 40) return { id: 'legal-choice', label: '法令等・5肢択一', group: 'legal' };
  if (number <= 43) return { id: 'legal-blank', label: '法令等・多肢選択', group: 'legal' };
  if (number <= 46) return { id: 'legal-written', label: '法令等・記述', group: 'legal' };
  return { id: 'general', label: '基礎知識・5肢択一', group: 'general' };
}

// 未収録の問題。基礎知識の後ろ3問（問58〜60）は文章理解。
function missingSubject(number) {
  return number >= 47 ? 'general-knowledge' : '';
}

function maxPoints(slot) {
  if (slot.number >= 44 && slot.number <= 46) return 20;
  if (slot.number >= 41 && slot.number <= 43) return 8;
  return 4;
}

function formatOf(slot) {
  if (slot.question) return slot.question.format;
  if (slot.number >= 44 && slot.number <= 46) return 'written';
  if (slot.number >= 41 && slot.number <= 43) return 'multiple_blank';
  return 'regular';
}

function yearLabel(year) {
  if (year === 2019) return '令和元年度';
  return year >= 2019 ? '令和' + (year - 2018) + '年度' : '平成' + (year - 1988) + '年度';
}

function officialPdfUrl(year) {
  if (year < OFFICIAL_PDF_FROM) return null;
  const code = year >= 2019 ? 'r' + (year - 2018) : 'h' + (year - 1988);
  return 'https://gyosei-shiken.or.jp/pdf/' + code + '_mondai.pdf';
}

// ---- 開始前 -----------------------------------------------------------

function recommendedPlan(now) {
  const today = localDate(now || new Date());
  return PLANNED.find((plan) => plan.date >= today) || null;
}

function localDate(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
}

function renderSetupYears() {
  const select = $('setup-year');
  const plan = recommendedPlan();
  select.replaceChildren(...YEARS.map((year) => {
    const option = make('option', { value: String(year) }, yearLabel(year) + (plan && plan.year === year ? '（' + plan.label + '）' : ''));
    return option;
  }));
  select.value = String(plan ? plan.year : YEARS[0]);
  $('setup-recommend').textContent = plan
    ? '予定表では、' + plan.label + 'に' + yearLabel(plan.year) + 'を解きます。令和4〜7年度は実際に受けた年度なので、初見の力を測るには向きません。令和2・3年度も、肢別過去問集で同じ肢を解いていれば点が少し高めに出ます。'
    : '予定表の通し演習（10/25・11/1）は終わっています。';
}

async function refreshSetup() {
  const year = Number($('setup-year').value);
  const start = $('setup-start');
  start.disabled = true;
  start.textContent = '読込中';
  $('setup-summary').replaceChildren();
  $('setup-missing').hidden = true;
  try {
    const questions = await loadYear(year);
    if (Number($('setup-year').value) !== year) return;
    const slots = buildSlots(questions);
    const missing = slots.filter((slot) => !slot.question);
    const counts = { regular: 0, multiple_blank: 0, written: 0 };
    questions.forEach((q) => { counts[q.format] = (counts[q.format] || 0) + 1; });
    const rows = [
      ['収録', questions.length + '問 / 60問'],
      ['内訳', '5肢択一' + counts.regular + '・多肢選択' + counts.multiple_blank + '・記述' + counts.written],
      ['改題', questions.filter((q) => q.amended).length + '問（法改正に合わせて直した版）'],
    ];
    $('setup-summary').replaceChildren(...rows.flatMap(([term, value]) => [make('dt', {}, term), make('dd', {}, value)]));
    if (missing.length) {
      const pdf = officialPdfUrl(year);
      const note = $('setup-missing');
      note.replaceChildren(
        document.createTextNode('問' + missing.map((slot) => slot.number).join('・') + 'はラボに入っていません（問58〜60は文章理解）。'
          + (pdf ? '試験研究センターの問題PDFで解き、採点のときに正解数を入れてください。' : '紙の問題で解き、採点のときに正解数を入れてください。'))
      );
      if (pdf) {
        note.appendChild(document.createTextNode(' '));
        note.appendChild(make('a', { href: pdf, target: '_blank', rel: 'noopener noreferrer' }, '問題PDFを開く'));
      }
      note.hidden = false;
    }
    start.disabled = questions.length === 0;
    start.textContent = questions.length ? yearLabel(year) + 'を始める' : 'この年度は未収録です';
  } catch (error) {
    showError('問題を読み込めませんでした（' + error.message + '）。再読み込みしてください。');
    start.textContent = '読み込めませんでした';
  }
}

function renderHistory() {
  const history = state.store.history || [];
  $('history-wrap').hidden = history.length === 0;
  $('history-body').replaceChildren(...history.slice().reverse().map((item) => {
    const row = make('tr');
    [
      dateLabel(item.finishedAt),
      yearLabel(item.year),
      item.total + '点',
      item.withoutWritten + '点',
      item.legal + '点' + (item.legal >= PASS.legal ? '' : '（不足）'),
      item.general + '点' + (item.general >= PASS.general ? '' : '（足切り）'),
      Math.round(item.elapsedMs / 60000) + '分',
    ].forEach((value) => row.appendChild(make('td', {}, value)));
    return row;
  }));
}

function startExam() {
  const year = Number($('setup-year').value);
  if (!YEARS.includes(year)) return;
  const minutes = Number($('setup-minutes').value) || 180;
  const id = 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  state.store.active = {
    id,
    year,
    minutes,
    startedAt: new Date().toISOString(),
    elapsedMs: 0,
    runningSince: Date.now(),
    current: 0,
    answers: {},
    flags: [],
    timeByQuestion: {},
    phase: 'exam',
    writtenScores: {},
    missingCorrect: {},
    attempts: null,
    posted: {},
    reasons: {},
  };
  saveStore();
  openActive();
}

// ---- 解答中 -----------------------------------------------------------

async function openActive() {
  const active = state.store.active;
  hideAll();
  try {
    state.questions = await loadYear(active.year);
  } catch (error) {
    // 解答は消さずに残す。通信が戻れば再読み込みで続きから開ける。
    showError('問題を読み込めませんでした（' + error.message + '）。再読み込みしてください。解答はこのブラウザに残っています。');
    const abandon = make('button', { type: 'button', className: 'ghost small' }, 'この通し演習をやめて最初の画面へ');
    abandon.addEventListener('click', abandonExam);
    $('load-error').append(' ', abandon);
    return;
  }
  state.year = active.year;
  state.slots = buildSlots(state.questions);
  if (active.phase === 'result') {
    renderResult();
    return;
  }
  if (active.phase === 'grading') {
    renderGrading();
    return;
  }
  $('exam').hidden = false;
  document.querySelector('.exam-bar').hidden = false;
  state.reviewMode = false;
  renderMap();
  renderQuestion();
  startTimer();
}

function hideAll() {
  ['setup', 'exam', 'grading', 'result'].forEach((id) => { $(id).hidden = true; });
  stopTimer();
}

function elapsedNow(active) {
  return active.elapsedMs + (active.runningSince ? Date.now() - active.runningSince : 0);
}

function startTimer() {
  stopTimer();
  tick();
  state.timerId = window.setInterval(tick, 1000);
}

function stopTimer() {
  if (state.timerId) window.clearInterval(state.timerId);
  state.timerId = null;
}

function tick() {
  const active = state.store.active;
  if (!active) return;
  const limit = active.minutes * 60000;
  const elapsed = elapsedNow(active);
  const over = elapsed > limit;
  $('timer-label').textContent = over ? '超過' : '残り時間';
  $('timer').textContent = clock(over ? elapsed - limit : limit - elapsed);
  $('timer').classList.toggle('over', over);
  $('timer').classList.toggle('low', !over && limit - elapsed < 15 * 60000);
  $('overtime-banner').hidden = !over;
  const paused = !active.runningSince;
  $('paused-banner').hidden = !paused;
  $('pause').textContent = paused ? '再開する' : '一時停止';
}

function clock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const pad = (n) => String(n).padStart(2, '0');
  return Math.floor(total / 3600) + ':' + pad(Math.floor(total / 60) % 60) + ':' + pad(total % 60);
}

function togglePause() {
  const active = state.store.active;
  if (!active) return;
  noteTimeOnQuestion();
  if (active.runningSince) {
    active.elapsedMs += Date.now() - active.runningSince;
    active.runningSince = null;
  } else {
    active.runningSince = Date.now();
    state.viewStartedAt = Date.now();
  }
  saveStore();
  tick();
}

// 問題ごとに画面を開いていた時間を足す。科目ごとの時間配分に使う。
function noteTimeOnQuestion() {
  const active = state.store.active;
  if (!active || state.reviewMode || active.phase !== 'exam' || !state.viewStartedAt || !active.runningSince) return;
  const slot = state.slots[active.current];
  const spent = Date.now() - state.viewStartedAt;
  if (slot && spent > 0) active.timeByQuestion[slot.number] = (active.timeByQuestion[slot.number] || 0) + spent;
  state.viewStartedAt = Date.now();
}

function renderMap() {
  const active = state.store.active;
  const groups = [
    ['法令等・5肢択一', 1, 40],
    ['多肢選択', 41, 43],
    ['記述', 44, 46],
    ['基礎知識', 47, 60],
  ];
  const wrap = $('question-map');
  wrap.replaceChildren(...groups.map(([label, from, to]) => {
    const group = make('div', { className: 'map-group' });
    group.appendChild(make('p', { className: 'map-label' }, label));
    const grid = make('div', { className: 'map-grid' });
    for (let number = from; number <= to; number += 1) {
      const slot = state.slots[number - 1];
      const button = make('button', { type: 'button', className: 'map-button', dataset: { index: String(number - 1) } }, String(number));
      const status = slotStatus(slot);
      button.classList.toggle('answered', status === 'answered');
      button.classList.toggle('partial', status === 'partial');
      button.classList.toggle('missing', !slot.question);
      button.classList.toggle('flagged', active.flags.includes(number));
      button.classList.toggle('current', active.current === number - 1);
      if (state.reviewMode && slot.question) {
        const outcome = slotOutcome(slot);
        button.classList.toggle('wrong', outcome.points < outcome.max);
      }
      button.setAttribute('aria-label', '問' + number + (slot.question ? '' : '（未収録）') + (status === 'answered' ? '・解答済み' : ''));
      button.addEventListener('click', () => goTo(number - 1));
      grid.appendChild(button);
    }
    group.appendChild(grid);
    return group;
  }));
  const answered = state.slots.filter((slot) => slotStatus(slot) === 'answered').length;
  $('answered-count').textContent = answered + ' / ' + state.slots.filter((slot) => slot.question).length;
}

function slotStatus(slot) {
  if (!slot.question) return 'missing';
  const answer = state.store.active.answers[slot.question.id];
  if (!answer) return 'empty';
  if (slot.question.format === 'multiple_blank') {
    const filled = Object.values(answer.value || {}).filter((value) => value).length;
    if (!filled) return 'empty';
    return filled === (slot.question.content.blanks || []).length ? 'answered' : 'partial';
  }
  if (slot.question.format === 'written') return String(answer.value || '').trim() ? 'answered' : 'empty';
  return answer.value ? 'answered' : 'empty';
}

function goTo(index) {
  const active = state.store.active;
  noteTimeOnQuestion();
  active.current = clamp(index, 0, state.slots.length - 1);
  saveStore();
  renderMap();
  renderQuestion();
  $('question').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderQuestion() {
  const active = state.store.active;
  const slot = state.slots[active.current];
  const question = slot.question;
  state.viewStartedAt = Date.now();
  $('question-section').textContent = slot.section.label + (slot.subjectId ? '・' + (SUBJECT_LABEL.get(slot.subjectId) || slot.subjectId) : '');
  $('question-title').textContent = '問' + slot.number + (question && question.amended ? '（法改正に合わせた改題版）' : '');
  $('flag').checked = active.flags.includes(slot.number);
  $('flag').disabled = state.reviewMode;
  $('prev').disabled = active.current === 0;
  $('next').textContent = active.current === state.slots.length - 1 ? (state.reviewMode ? '結果へ戻る' : '解答を終える') : '次の問題';
  $('review-answer').hidden = true;

  if (!question) {
    const pdf = officialPdfUrl(active.year);
    $('question-instruction').hidden = true;
    $('question-note').hidden = true;
    $('question-text').textContent = 'この問題はラボに入っていません。' + (slot.number >= 58 ? '文章理解の問題です。' : '')
      + (pdf ? '試験研究センターの問題PDFで解いてください。' : '紙の問題で解いてください。') + '採点のときに正解数を入れます。';
    const area = $('answer-area');
    area.replaceChildren();
    if (pdf) area.appendChild(make('p', {}, '')).appendChild(make('a', { href: pdf, target: '_blank', rel: 'noopener noreferrer' }, yearLabel(active.year) + 'の問題PDFを開く'));
    return;
  }

  const content = question.content || {};
  const full = content.question || '';
  const instruction = content.instruction || '';
  const showInstruction = Boolean(instruction && !full.includes(instruction));
  $('question-instruction').hidden = !showInstruction;
  $('question-instruction').textContent = showInstruction ? instruction : '';
  $('question-text').textContent = question.format === 'multiple_blank' ? '' : full;
  $('question-text').hidden = question.format === 'multiple_blank';
  const note = question.format !== 'multiple_blank' && content.sourceNote;
  $('question-note').hidden = !note;
  $('question-note').textContent = note || '';

  const answer = active.answers[question.id];
  if (question.format === 'multiple_blank') renderBlank(question, answer);
  else if (question.format === 'written') renderWritten(question, answer);
  else renderChoices(question, answer);
  if (state.reviewMode) renderReviewAnswer(slot);
}

function renderChoices(question, answer) {
  const content = question.content || {};
  const choices = content.choices || [];
  const selected = answer ? Number(answer.value) : null;
  const correct = Number(answerValue(question.answer));
  const columns = Array.isArray(content.choiceColumns) ? content.choiceColumns : [];
  const hasTable = columns.length && choices.some((choice) => choice.cells && choice.cells.length);
  const area = $('answer-area');
  const name = 'choice-' + question.id;
  const decorate = (container, value) => {
    if (!state.reviewMode) return;
    container.classList.toggle('correct-choice', value === correct);
    container.classList.toggle('selected-wrong', value === selected && value !== correct);
  };
  if (hasTable) {
    const wrap = make('div', { className: 'choice-table-wrap', role: 'radiogroup', 'aria-labelledby': 'question-title' });
    const table = make('table', { className: 'choice-table' });
    const headRow = make('tr');
    headRow.appendChild(make('th', { scope: 'col' }, '選択'));
    columns.forEach((column) => headRow.appendChild(make('th', { scope: 'col' }, String(column))));
    const head = make('thead');
    head.appendChild(headRow);
    const body = make('tbody');
    choices.forEach((choice) => {
      const value = Number(choice.label);
      const row = make('tr');
      const cell = make('td');
      const label = make('label', { className: 'table-radio' });
      const input = make('input', { type: 'radio', name, value: String(value), checked: selected === value, disabled: state.reviewMode });
      input.addEventListener('change', () => setAnswer(question, value));
      label.append(input, make('span', {}, String(choice.label)));
      cell.appendChild(label);
      row.appendChild(cell);
      const byColumn = new Map((choice.cells || []).map((item) => [String(item.column), item.text]));
      columns.forEach((column) => row.appendChild(make('td', {}, byColumn.get(String(column)) || '')));
      row.addEventListener('click', (event) => {
        if (state.reviewMode || event.target.tagName === 'INPUT') return;
        input.checked = true;
        setAnswer(question, value);
      });
      decorate(row, value);
      body.appendChild(row);
    });
    table.append(head, body);
    wrap.appendChild(table);
    area.replaceChildren(wrap);
    return;
  }
  const list = make('div', { className: 'choice-list', role: 'radiogroup', 'aria-labelledby': 'question-title' });
  choices.forEach((choice) => {
    const value = Number(choice.label);
    const label = make('label', { className: 'choice-option' });
    const input = make('input', { type: 'radio', name, value: String(value), checked: selected === value, disabled: state.reviewMode });
    input.addEventListener('change', () => setAnswer(question, value));
    label.append(input, make('span', { className: 'choice-label' }, String(choice.label)), make('span', { className: 'choice-text' }, choice.text));
    decorate(label, value);
    list.appendChild(label);
  });
  area.replaceChildren(list);
}

function renderBlank(question, answer) {
  const content = question.content || {};
  const values = (answer && answer.value) || {};
  const correct = answerValue(question.answer) || {};
  const area = $('answer-area');
  const passage = make('p', { className: 'blank-passage' }, content.passage || '');
  const note = content.sourceNote ? make('p', { className: 'source-note' }, content.sourceNote) : null;
  const selects = make('div', { className: 'blank-selects' });
  (content.blanks || []).forEach((blank) => {
    const label = make('label');
    label.appendChild(make('span', {}, '空欄［' + blank + '］'));
    const select = make('select', { disabled: state.reviewMode });
    select.appendChild(make('option', { value: '' }, '選ぶ'));
    (content.wordBank || []).forEach((word) => select.appendChild(make('option', { value: String(word.number) }, word.number + '. ' + word.text)));
    select.value = values[blank] ? String(values[blank]) : '';
    select.addEventListener('change', () => {
      const next = Object.assign({}, (state.store.active.answers[question.id] || {}).value || {});
      if (select.value) next[blank] = Number(select.value);
      else delete next[blank];
      setAnswer(question, next);
    });
    if (state.reviewMode) label.classList.add(Number(values[blank]) === Number(correct[blank]) ? 'blank-correct' : 'blank-wrong');
    label.appendChild(select);
    selects.appendChild(label);
  });
  const bank = make('div', { className: 'word-bank' });
  (content.wordBank || []).forEach((word) => bank.appendChild(make('span', {}, word.number + '. ' + word.text)));
  area.replaceChildren(...[passage, note, selects, bank].filter(Boolean));
}

function renderWritten(question, answer) {
  const content = question.content || {};
  const limit = content.characterLimit || 40;
  const area = $('answer-area');
  const nodes = [];
  if (content.referenceText) {
    const ref = make('aside', { className: 'reference-box' });
    ref.append(make('strong', {}, '参照条文'), make('p', {}, content.referenceText));
    nodes.push(ref);
  }
  const editor = make('label', { className: 'answer-editor' });
  const textarea = make('textarea', { rows: 4, placeholder: '40字程度で書きます', readOnly: state.reviewMode });
  textarea.value = (answer && answer.value) || '';
  const counter = make('small', {});
  const updateCount = () => {
    const count = Array.from(textarea.value).length;
    counter.textContent = count + '字 / ' + limit + '字程度';
    counter.classList.toggle('over', count > limit + 5);
  };
  textarea.addEventListener('input', () => {
    updateCount();
    setAnswer(question, textarea.value, { quiet: true });
  });
  updateCount();
  editor.append(make('span', {}, '自分の答案'), textarea, counter);
  nodes.push(editor);
  area.replaceChildren(...nodes);
}

function setAnswer(question, value, options) {
  const active = state.store.active;
  if (!active || active.phase !== 'exam' || state.reviewMode) return;
  const overtime = elapsedNow(active) > active.minutes * 60000;
  active.answers[question.id] = { value, answeredAt: new Date().toISOString(), overtime };
  saveStore();
  if (options && options.quiet) {
    // 記述は1文字ごとに一覧を描き直さない。解答済みの印だけ直す。
    const slot = state.slots[active.current];
    const button = document.querySelector('.map-button[data-index="' + active.current + '"]');
    if (button) button.classList.toggle('answered', slotStatus(slot) === 'answered');
    $('answered-count').textContent = state.slots.filter((item) => slotStatus(item) === 'answered').length + ' / ' + state.slots.filter((item) => item.question).length;
    return;
  }
  renderMap();
}

function toggleFlag() {
  const active = state.store.active;
  const number = state.slots[active.current].number;
  active.flags = $('flag').checked ? [...new Set([...active.flags, number])] : active.flags.filter((item) => item !== number);
  saveStore();
  renderMap();
}

function next() {
  const active = state.store.active;
  if (active.current === state.slots.length - 1) {
    if (state.reviewMode) backToResult();
    else finishExam();
    return;
  }
  goTo(active.current + 1);
}

function finishExam() {
  const active = state.store.active;
  const empty = state.slots.filter((slot) => slot.question && slotStatus(slot) !== 'answered');
  const message = empty.length
    ? 'まだ解答していない問題が' + empty.length + '問あります（問' + empty.slice(0, 8).map((slot) => slot.number).join('・') + (empty.length > 8 ? '…' : '') + '）。解答を終えて採点に進みますか？'
    : '解答を終えて採点に進みますか？終えたあとは解答を変えられません。';
  if (!window.confirm(message)) return;
  noteTimeOnQuestion();
  if (active.runningSince) {
    active.elapsedMs += Date.now() - active.runningSince;
    active.runningSince = null;
  }
  active.finishedAt = new Date().toISOString();
  active.phase = 'grading';
  saveStore();
  hideAll();
  renderGrading();
}

// ---- 記述の自己採点 ----------------------------------------------------

function renderGrading() {
  const active = state.store.active;
  $('grading').hidden = false;
  const written = state.slots.filter((slot) => slot.question && slot.question.format === 'written');
  $('written-grades').replaceChildren(...written.map((slot) => {
    const question = slot.question;
    const answer = String((active.answers[question.id] || {}).value || '').trim();
    const box = make('article', { className: 'grade-item' });
    box.appendChild(make('h3', {}, '問' + slot.number + '・' + (SUBJECT_LABEL.get(slot.subjectId) || '')));
    box.appendChild(make('p', { className: 'grade-question' }, (question.content || {}).question || ''));
    const mine = make('div', { className: 'model-answer mine' });
    mine.append(make('span', {}, '自分の答案'), make('p', {}, answer || '（未記入）'));
    const model = make('div', { className: 'model-answer' });
    model.append(make('span', {}, officialWrittenLabel(question)), make('p', {}, modelAnswerText(question)));
    box.append(mine, model);
    const field = make('label', { className: 'field inline' });
    const select = make('select', { disabled: !answer });
    select.appendChild(make('option', { value: '' }, answer ? '点数を選ぶ' : '未記入なので0点'));
    WRITTEN_STEPS.forEach((points) => select.appendChild(make('option', { value: String(points) }, points + '点')));
    const saved = active.writtenScores[question.id];
    select.value = answer ? (saved === undefined ? '' : String(saved)) : '';
    if (!answer) active.writtenScores[question.id] = 0;
    select.addEventListener('change', () => {
      if (select.value === '') delete active.writtenScores[question.id];
      else active.writtenScores[question.id] = Number(select.value);
      saveStore();
    });
    field.append(make('span', {}, '自己採点（20点満点）'), select);
    box.appendChild(field);
    return box;
  }));

  const missing = state.slots.filter((slot) => !slot.question);
  const wrap = $('missing-inputs');
  wrap.replaceChildren();
  if (missing.length) {
    const groups = groupMissing(missing);
    const box = make('article', { className: 'grade-item' });
    box.appendChild(make('h3', {}, 'ラボに入っていない問題'));
    const pdf = officialPdfUrl(active.year);
    const lead = make('p', { className: 'hint' }, '紙や問題PDFで解いた分の正解数を入れます。入れなければ0問として数えます。');
    if (pdf) {
      lead.appendChild(document.createTextNode(' '));
      lead.appendChild(make('a', { href: pdf, target: '_blank', rel: 'noopener noreferrer' }, '問題PDFを開く'));
    }
    box.appendChild(lead);
    groups.forEach((group) => {
      const field = make('label', { className: 'field inline' });
      const select = make('select');
      for (let count = 0; count <= group.slots.length; count += 1) select.appendChild(make('option', { value: String(count) }, count + '問正解'));
      select.value = String(active.missingCorrect[group.key] || 0);
      select.addEventListener('change', () => {
        active.missingCorrect[group.key] = Number(select.value);
        saveStore();
      });
      field.append(make('span', {}, group.label + '（問' + group.slots.map((slot) => slot.number).join('・') + '）'), select);
      box.appendChild(field);
    });
    wrap.appendChild(box);
  }
  saveStore();
}

function groupMissing(missing) {
  const general = missing.filter((slot) => slot.number >= 47);
  const legal = missing.filter((slot) => slot.number < 41);
  const groups = [];
  if (legal.length) groups.push({ key: 'legal', label: '法令等の択一', slots: legal });
  if (general.length) groups.push({ key: 'general', label: general.every((slot) => slot.number >= 58) ? '基礎知識・文章理解' : '基礎知識', slots: general });
  return groups;
}

function officialWrittenLabel(question) {
  const check = (question.officialAnswerChecks || [])[0] || {};
  return check.officialAnswer ? '試験実施機関の公式答案' : '取得元に掲載された答案例';
}

function modelAnswerText(question) {
  const check = (question.officialAnswerChecks || [])[0] || {};
  const value = check.officialAnswer || (question.content || {}).modelAnswer || answerValue(question.answer);
  return String(value || '（答案例なし）');
}

async function completeGrading() {
  const active = state.store.active;
  const written = state.slots.filter((slot) => slot.question && slot.question.format === 'written');
  const ungraded = written.filter((slot) => active.writtenScores[slot.question.id] === undefined);
  if (ungraded.length) {
    window.alert('問' + ungraded.map((slot) => slot.number).join('・') + 'の自己採点を選んでください。');
    return;
  }
  active.attempts = buildAttempts();
  active.phase = 'result';
  const result = computeResult();
  state.store.history = [...(state.store.history || []).filter((item) => item.id !== active.id), {
    id: active.id,
    year: active.year,
    finishedAt: active.finishedAt,
    elapsedMs: active.elapsedMs,
    total: result.total,
    withoutWritten: result.withoutWritten,
    legal: result.legal,
    general: result.general,
  }].slice(-HISTORY_LIMIT);
  saveStore();
  hideAll();
  renderResult();
  await postAttempts();
}

// ---- 回答履歴への保存 ---------------------------------------------------

// 送る中身は終了時に固定する。やり直しで同じeventIdを同じ内容のまま送れば、
// サーバーは二重に数えない（同じeventIdで中身が違うと409になる）。
function buildAttempts() {
  const active = state.store.active;
  const attempts = [];
  state.slots.forEach((slot) => {
    const question = slot.question;
    if (!question) return;
    const answer = active.answers[question.id];
    if (!answer) return;
    const base = {
      eventId: 'mock-' + active.id + '-q' + slot.number,
      sessionId: 'mock-' + active.id,
      questionId: question.id,
      format: question.format,
      mode: 'mock_exam',
      answeredAt: answer.answeredAt,
      responseMs: Math.min(86400000, Math.round(active.timeByQuestion[slot.number] || 0)) || null,
      questionPosition: slot.number,
      appVersion: APP_VERSION,
    };
    if (question.format === 'written') {
      const text = String(answer.value || '');
      if (!text.trim()) return;
      // 記述の自己採点は点数で持つので、「書けた／もう一度」の正誤には入れない。
      attempts.push(Object.assign(base, { answerText: text, isCorrect: null }));
      return;
    }
    if (question.format === 'multiple_blank') {
      const blanks = (question.content || {}).blanks || [];
      const value = answer.value || {};
      // 過去問タブと同じく、4つ全部を選んだものだけを回答として残す。
      if (!blanks.length || blanks.some((blank) => !value[blank])) return;
      attempts.push(Object.assign(base, { selectedAnswer: value, isCorrect: answersEqual(value, answerValue(question.answer)) }));
      return;
    }
    if (!answer.value) return;
    attempts.push(Object.assign(base, { selectedAnswer: Number(answer.value), isCorrect: Number(answer.value) === Number(answerValue(question.answer)) }));
  });
  return attempts;
}

async function postAttempts() {
  const active = state.store.active;
  const attempts = active.attempts || [];
  const pending = attempts.filter((attempt) => !active.posted[attempt.eventId]);
  const status = $('save-status');
  $('retry-save').hidden = true;
  if (!attempts.length) {
    status.textContent = '回答履歴に保存する解答はありませんでした。';
    return;
  }
  if (!pending.length) {
    status.className = 'save-status';
    status.textContent = attempts.length + '問の解答を過去問の回答履歴に保存済みです。';
    return;
  }
  status.className = 'save-status';
  status.textContent = '回答履歴に保存しています…';
  let failed = 0;
  for (const attempt of pending) {
    try {
      await fetchJson(API + '/attempts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Gyousei-Client': CLIENT_HEADER },
        body: JSON.stringify(attempt),
      });
      active.posted[attempt.eventId] = true;
    } catch (error) {
      failed += 1;
      console.error(error);
    }
  }
  saveStore();
  if (failed) {
    status.className = 'save-status error';
    status.textContent = (attempts.length - failed) + '問を保存し、' + failed + '問は保存できませんでした。採点結果はこの画面に残っています。';
    $('retry-save').hidden = false;
  } else {
    status.textContent = attempts.length + '問の解答を過去問の回答履歴に保存しました。';
  }
}

// ---- 採点 -------------------------------------------------------------

function slotOutcome(slot) {
  const active = state.store.active;
  const max = maxPoints(slot);
  const question = slot.question;
  if (!question) return { points: 0, max, answered: false, overtime: false };
  const answer = active.answers[question.id];
  const overtime = Boolean(answer && answer.overtime);
  if (question.format === 'written') {
    const points = Number(active.writtenScores[question.id] || 0);
    return { points, max, answered: Boolean(answer && String(answer.value || '').trim()), overtime };
  }
  if (question.format === 'multiple_blank') {
    const correct = answerValue(question.answer) || {};
    const value = (answer && answer.value) || {};
    const hits = Object.keys(correct).filter((blank) => Number(value[blank]) === Number(correct[blank])).length;
    return { points: hits * 2, max, answered: Object.keys(value).length > 0, overtime, hits };
  }
  const selected = answer ? Number(answer.value) : null;
  const correct = selected !== null && selected === Number(answerValue(question.answer));
  return { points: correct ? 4 : 0, max, answered: selected !== null, overtime };
}

function computeResult() {
  const active = state.store.active;
  const bySubject = new Map(SUBJECTS.map(([id]) => [id, { choice: 0, blank: 0, written: 0, choiceMax: 0, blankMax: 0, writtenMax: 0, timeMs: 0, count: 0 }]));
  bySubject.set('', { choice: 0, blank: 0, written: 0, choiceMax: 0, blankMax: 0, writtenMax: 0, timeMs: 0, count: 0 });
  let legal = 0;
  let general = 0;
  let written = 0;
  let overtimePoints = 0;
  let unansweredInTime = 0;
  let overtimeAnswers = 0;
  state.slots.forEach((slot) => {
    const row = bySubject.get(slot.subjectId || '') || bySubject.get('');
    const outcome = slotOutcome(slot);
    const format = formatOf(slot);
    const key = format === 'written' ? 'written' : format === 'multiple_blank' ? 'blank' : 'choice';
    row[key + 'Max'] += outcome.max;
    row[key] += outcome.points;
    row.timeMs += active.timeByQuestion[slot.number] || 0;
    row.count += 1;
    if (slot.section.group === 'legal') legal += outcome.points;
    else general += outcome.points;
    if (format === 'written') written += outcome.points;
    if (slot.question) {
      if (outcome.overtime) {
        overtimeAnswers += 1;
        overtimePoints += outcome.points;
        unansweredInTime += 1;
      } else if (!outcome.answered) {
        unansweredInTime += 1;
      }
    }
  });
  // ラボに無い問題は、入れてもらった正解数で数える。
  const missing = groupMissing(state.slots.filter((slot) => !slot.question));
  missing.forEach((group) => {
    const count = Math.min(group.slots.length, Number(active.missingCorrect[group.key] || 0));
    const points = count * 4;
    if (group.key === 'legal') legal += points;
    else general += points;
    // 未収録の問題は科目が分からないもの（法令等）と基礎知識に分けて足す。
    const row = bySubject.get(group.key === 'general' ? 'general-knowledge' : '');
    row.choice += points;
  });
  const total = legal + general;
  const civilChoice = state.slots
    .filter((slot) => slot.subjectId === 'civil-law' && slot.question && slot.question.format === 'regular')
    .filter((slot) => slotOutcome(slot).points > 0).length;
  return {
    bySubject,
    legal,
    general,
    total,
    written,
    withoutWritten: total - written,
    overtimePoints,
    overtimeAnswers,
    unansweredInTime,
    civilChoice,
    inTimeTotal: total - overtimePoints,
  };
}

function renderResult() {
  const active = state.store.active;
  const result = computeResult();
  $('result').hidden = false;
  $('result-title').textContent = yearLabel(active.year) + 'の採点結果';

  const verdicts = [
    { label: '合計', value: result.total, max: PASS.totalMax, line: PASS.total, note: '180点以上' },
    { label: '法令等', value: result.legal, max: PASS.legalMax, line: PASS.legal, note: '122点以上（5割）' },
    { label: '基礎知識', value: result.general, max: PASS.generalMax, line: PASS.general, note: '24点以上（足切り）' },
    { label: '記述を除く', value: result.withoutWritten, max: PASS.totalMax - 60, line: null, note: '記述60点を除いた点' },
  ];
  $('verdicts').replaceChildren(...verdicts.map((item) => {
    const box = make('div', { className: 'verdict' + (item.line === null ? '' : item.value >= item.line ? ' pass' : ' fail') });
    box.append(
      make('span', {}, item.label),
      make('strong', {}, item.value + '点'),
      make('small', {}, '/ ' + item.max + '点・' + item.note + (item.line === null ? '' : item.value >= item.line ? ' ✓' : '（あと' + (item.line - item.value) + '点）'))
    );
    return box;
  }));

  const rows = [];
  let sumChoice = 0;
  let sumBlank = 0;
  let sumWritten = 0;
  SUBJECTS.concat([['', '未収録（法令等）']]).forEach(([id, label]) => {
    const row = result.bySubject.get(id);
    if (!row || (!row.count && !row.choice)) return;
    if (id === '' && !row.choiceMax && !row.choice) return;
    sumChoice += row.choice;
    sumBlank += row.blank;
    sumWritten += row.written;
    const tr = make('tr');
    tr.appendChild(make('th', { scope: 'row' }, label));
    tr.appendChild(make('td', {}, cell(row.choice, row.choiceMax)));
    tr.appendChild(make('td', {}, cell(row.blank, row.blankMax)));
    tr.appendChild(make('td', {}, cell(row.written, row.writtenMax)));
    tr.appendChild(make('td', { className: 'strong' }, (row.choice + row.blank + row.written) + ' / ' + (row.choiceMax + row.blankMax + row.writtenMax)));
    rows.push(tr);
  });
  const totalRow = make('tr', { className: 'total' });
  totalRow.append(make('th', { scope: 'row' }, '合計'), make('td', {}, String(sumChoice)), make('td', {}, String(sumBlank)), make('td', {}, String(sumWritten)), make('td', { className: 'strong' }, result.total + ' / 300'));
  rows.push(totalRow);
  $('score-body').replaceChildren(...rows);

  const minutes = Math.round(active.elapsedMs / 60000);
  const timeParts = ['かかった時間は' + minutes + '分（制限' + active.minutes + '分）。'];
  if (result.overtimeAnswers) timeParts.push('時間外に選んだ解答が' + result.overtimeAnswers + '問あり、時間内だけで数えると' + result.inTimeTotal + '点です。');
  if (result.unansweredInTime) timeParts.push('時間内に解けなかった問題は' + result.unansweredInTime + '問です。');
  timeParts.push('科目ごとに使った時間：' + SUBJECTS.map(([id, label]) => {
    const row = result.bySubject.get(id);
    return label + Math.round((row ? row.timeMs : 0) / 60000) + '分';
  }).join('・') + '。');
  $('time-summary').textContent = timeParts.join('');

  renderAdvice(result);
  $('summary-text').textContent = summaryText(result);
  renderWrongList();
  if (active.attempts && active.attempts.some((attempt) => !active.posted[attempt.eventId])) {
    $('save-status').className = 'save-status error';
    $('save-status').textContent = 'まだ回答履歴に保存していない解答があります。';
    $('retry-save').hidden = false;
  } else if (active.attempts) {
    $('save-status').className = 'save-status';
    $('save-status').textContent = active.attempts.length + '問の解答を過去問の回答履歴に保存済みです。';
  }
}

function cell(points, max) {
  return max ? points + ' / ' + max : '—';
}

// docs/STUDY_SCHEDULE.md §6「結果を見て直すルール」に当てはまるものを出す。
function renderAdvice(result) {
  const advice = [];
  if (result.general < 28) advice.push('基礎知識が28点を下回りました。予定表では、11/3の基礎知識を10/29へ前倒しし、④に文章理解を増やします。');
  if (result.civilChoice <= 3) advice.push('民法の択一が' + result.civilChoice + '問でした。予定表では、民法の日の④を民法演習Dの「答えから読む」にします。');
  if (result.unansweredInTime || result.overtimeAnswers) advice.push('時間内に解き終わりませんでした。次は解く順番を先に決めます（例：基礎知識→行政法→民法→その他）。');
  if (result.withoutWritten <= 130) advice.push('記述を除いた合計が130点以下でした。予定表では、11/2の商法を半日に減らし、行政法の2周目へ回します。');
  const list = make('ul', { className: 'advice' });
  advice.forEach((text) => list.appendChild(make('li', {}, text)));
  const wrap = make('div', { className: 'advice-wrap' });
  wrap.appendChild(make('h3', { className: 'sub-title' }, '予定表のルールに当てはまるもの'));
  wrap.appendChild(advice.length ? list : make('p', { className: 'hint' }, '当てはまるものはありません。予定どおり進めます。'));
  const old = document.querySelector('.advice-wrap');
  if (old) old.remove();
  $('verdicts').after(wrap);
}

function summaryText(result) {
  const active = state.store.active;
  const lines = [
    '通し演習：' + yearLabel(active.year) + '（' + dateLabel(active.finishedAt) + '実施・' + Math.round(active.elapsedMs / 60000) + '分）',
    '合計 ' + result.total + '点 / 法令等 ' + result.legal + '点 / 基礎知識 ' + result.general + '点 / 記述を除く ' + result.withoutWritten + '点 / 記述 ' + result.written + '点',
  ];
  SUBJECTS.forEach(([id, label]) => {
    const row = result.bySubject.get(id);
    if (!row) return;
    lines.push(label + '：' + (row.choice + row.blank + row.written) + '/' + (row.choiceMax + row.blankMax + row.writtenMax) + '点（' + Math.round(row.timeMs / 60000) + '分）');
  });
  lines.push('時間内に解けなかった問題：' + result.unansweredInTime + '問');
  const wrong = state.slots.filter((slot) => slot.question && slotOutcome(slot).points < slotOutcome(slot).max && slot.question.format !== 'written');
  lines.push('間違い・未解答：' + wrong.map((slot) => '問' + slot.number).join(' '));
  const reasons = state.store.active.reasons || {};
  const counts = reasonCounts();
  if (Array.from(counts.values()).some(Boolean)) {
    lines.push('間違えた理由：' + REASONS.filter(([value]) => counts.get(value)).map(([value, text]) => text + counts.get(value) + '問').join('・'));
    REASONS.forEach(([value, text]) => {
      const numbers = Object.keys(reasons).filter((number) => reasons[number] === value).map(Number).sort((a, b) => a - b);
      if (numbers.length) lines.push('  ' + text + '：' + numbers.map((number) => '問' + number).join(' '));
    });
  }
  return lines.join('\n');
}

// ---- 間違えた問題と、戻る先 ---------------------------------------------

function renderWrongList() {
  const wrong = state.slots.filter((slot) => {
    if (!slot.question) return false;
    const outcome = slotOutcome(slot);
    if (slot.question.format === 'written') return outcome.points <= 10;
    return outcome.points < outcome.max;
  });
  const list = $('wrong-list');
  renderReasonSummary();
  $('wrong-actions').hidden = true;
  if (!wrong.length) {
    list.replaceChildren(make('p', { className: 'hint' }, '収録されている問題はすべて正解でした。'));
    return;
  }
  list.replaceChildren(...wrong.map((slot) => {
    const question = slot.question;
    const outcome = slotOutcome(slot);
    const item = make('article', { className: 'wrong-item', dataset: { questionId: question.id } });
    const head = make('div', { className: 'wrong-head' });
    const open = make('button', { type: 'button', className: 'ghost small' }, '問' + slot.number + 'を見直す');
    open.addEventListener('click', () => openReview(slot.number - 1));
    const labels = (question.labels || []).filter((label) => label !== SUBJECT_LABEL.get(slot.subjectId)).slice(0, 2).join('・');
    head.append(open, make('span', { className: 'wrong-meta' }, (SUBJECT_LABEL.get(slot.subjectId) || '') + (labels ? '｜' + labels : '')));
    const tags = make('span', { className: 'wrong-tags' });
    if (!outcome.answered) tags.appendChild(make('span', { className: 'tag' }, '未解答'));
    if (outcome.overtime) tags.appendChild(make('span', { className: 'tag warn' }, '時間外'));
    if (question.format === 'multiple_blank') tags.appendChild(make('span', { className: 'tag' }, outcome.points + '/8点'));
    if (question.format === 'written') tags.appendChild(make('span', { className: 'tag' }, outcome.points + '/20点'));
    head.appendChild(tags);
    item.appendChild(head);
    item.appendChild(make('p', { className: 'wrong-answer' }, answerLine(slot)));
    item.appendChild(reasonSelect(slot));
    const links = make('div', { className: 'wrong-links' });
    item.appendChild(links);
    return item;
  }));
  loadLinks().then(() => fillLinks()).catch((error) => {
    console.error(error);
    $('cards-status').textContent = '学習カードとの対応を読み込めませんでした。';
  });
}

function reasonSelect(slot) {
  const active = state.store.active;
  if (!active.reasons) active.reasons = {};
  const label = make('label', { className: 'reason-field' });
  const select = make('select');
  select.appendChild(make('option', { value: '' }, '間違えた理由を選ぶ'));
  REASONS.forEach(([value, text]) => select.appendChild(make('option', { value }, text)));
  select.value = active.reasons[slot.number] || '';
  select.addEventListener('change', () => {
    if (select.value) active.reasons[slot.number] = select.value;
    else delete active.reasons[slot.number];
    saveStore();
    renderReasonSummary();
    $('summary-text').textContent = summaryText(computeResult());
    updateHistoryReasons();
  });
  label.append(make('span', {}, '理由'), select);
  return label;
}

function reasonCounts() {
  const counts = new Map(REASONS.map(([value]) => [value, 0]));
  Object.values(state.store.active.reasons || {}).forEach((value) => {
    if (counts.has(value)) counts.set(value, counts.get(value) + 1);
  });
  return counts;
}

function renderReasonSummary() {
  const counts = reasonCounts();
  const total = Array.from(counts.values()).reduce((a, b) => a + b, 0);
  const box = $('reason-summary');
  if (!total) {
    box.replaceChildren();
    return;
  }
  box.replaceChildren(
    make('strong', {}, '間違えた理由の内訳'),
    ...REASONS.filter(([value]) => counts.get(value)).map(([value, text]) => make('span', { className: 'tag' }, text + ' ' + counts.get(value) + '問'))
  );
}

// 履歴の行にも理由の内訳を残す。2回目と比べて、読み違い・時間不足が減ったかを見るため。
function updateHistoryReasons() {
  const active = state.store.active;
  const row = (state.store.history || []).find((item) => item.id === active.id);
  if (!row) return;
  row.reasons = Object.fromEntries(Array.from(reasonCounts()).filter(([, count]) => count));
  saveStore();
}

function answerLine(slot) {
  const question = slot.question;
  const answer = state.store.active.answers[question.id];
  if (question.format === 'written') return '答案：' + (answer && String(answer.value || '').trim() ? answer.value : '（未記入）');
  if (question.format === 'multiple_blank') {
    const correct = answerValue(question.answer) || {};
    const value = (answer && answer.value) || {};
    return Object.keys(correct).map((blank) => blank + '：' + (value[blank] || '—') + (Number(value[blank]) === Number(correct[blank]) ? '' : '→' + correct[blank])).join('　');
  }
  return 'あなた：' + (answer && answer.value ? answer.value : '未解答') + '　正解：' + answerValue(question.answer);
}

async function loadLinks() {
  if (state.cardIndex && state.drillIndex) return;
  $('cards-status').textContent = '学習カードとの対応を読み込んでいます…';
  const [cards, drill] = await Promise.all([
    state.cardIndex ? null : fetchJson(API + '/cards?limit=1000'),
    state.drillIndex ? null : fetchJson('../minpo-d/chapters.json').catch(() => null),
  ]);
  if (cards) state.cardIndex = buildCardIndex(cards);
  if (!state.drillIndex) state.drillIndex = buildDrillIndex(drill);
  $('cards-status').textContent = '';
}

// カードの⑤（relatedQuestionEvidence の questionId）と出典（sourceRefs の年度・問番号）から、
// 問題 → カードの対応を作る。⑤は肢単位なので、同じ問題の別の肢のカードも拾う。
function buildCardIndex(payload) {
  const cards = (payload && payload.explanationCards) || [];
  const evidence = new Map(((payload && payload.relatedQuestionEvidence) || []).map((item) => [item.choiceId, item]));
  const byKey = new Map();
  const add = (key, card) => {
    if (!byKey.has(key)) byKey.set(key, new Map());
    byKey.get(key).set(card.id, card);
  };
  cards.forEach((card) => {
    (card.relatedPastQuestions || []).forEach((ref) => {
      const item = evidence.get(ref.choiceId);
      if (item && item.examYear && item.questionNumber) add(item.examYear + ':' + item.questionNumber, card);
    });
    (card.sourceRefs || []).forEach((ref) => {
      const year = eraToYear(ref.eraYear);
      if (year && ref.questionNumber) add(year + ':' + ref.questionNumber, card);
    });
  });
  return byKey;
}

function buildDrillIndex(data) {
  const byKey = new Map();
  ((data && data.chapters) || []).forEach((chapter, index) => {
    (chapter.questions || []).forEach((question) => {
      (question.examRefs || []).forEach((ref) => {
        const match = /^(平成|令和)(\d+|元)年問(\d+)/.exec(ref);
        if (!match) return;
        const year = eraToYear(match[1] + match[2] + '年');
        const key = year + ':' + Number(match[3]);
        if (!byKey.has(key)) byKey.set(key, new Map());
        byKey.get(key).set(chapter.id, { id: chapter.id, number: index + 1, title: chapter.title.split('――')[0] });
      });
    });
  });
  return byKey;
}

function eraToYear(text) {
  const match = /^(平成|令和)(\d+|元)年/.exec(String(text || ''));
  if (!match) return null;
  const n = match[2] === '元' ? 1 : Number(match[2]);
  return match[1] === '令和' ? 2018 + n : 1988 + n;
}

function fillLinks() {
  const picked = [];
  document.querySelectorAll('.wrong-item').forEach((item) => {
    const question = state.questions.find((q) => q.id === item.dataset.questionId);
    if (!question) return;
    const key = question.exam.year + ':' + question.exam.number;
    const links = item.querySelector('.wrong-links');
    links.replaceChildren();
    const cards = Array.from(((state.cardIndex && state.cardIndex.get(key)) || new Map()).values());
    cards.slice(0, 6).forEach((card) => {
      links.appendChild(make('a', { className: 'link-chip card', href: '../?cardId=' + encodeURIComponent(card.id) + '#study' }, 'カード：' + card.topic + '／' + card.subtopic));
    });
    const chapters = Array.from(((state.drillIndex && state.drillIndex.get(key)) || new Map()).values());
    chapters.forEach((chapter) => {
      links.appendChild(make('a', { className: 'link-chip drill', href: '../minpo-d/#mode=read&chapter=' + encodeURIComponent(chapter.id) }, '民法演習D 第' + chapter.number + '章：' + chapter.title));
    });
    if (!cards.length && !chapters.length) links.appendChild(make('span', { className: 'hint' }, 'この問題を出典にしたカードはありません。'));
    cards.forEach((card) => { if (!picked.includes(card.id)) picked.push(card.id); });
  });
  renderPickedAction(picked);
}

// 間違えた問題のカードを、学習画面の「指定したカード」ビューでまとめて解く。
function renderPickedAction(ids) {
  const box = $('wrong-actions');
  if (!ids.length) {
    box.hidden = true;
    return;
  }
  const link = make('a', { className: 'primary link-button', href: '../?cardIds=' + ids.map(encodeURIComponent).join(',') + '&from=mock#study' }, '間違えた問題のカード（' + ids.length + '枚）をまとめて解く');
  box.replaceChildren(link, make('span', { className: 'hint' }, '学習画面で、この枚数だけを順に出します。回答はふつうどおり記録されます。'));
  box.hidden = false;
}

// ---- 見直し（結果から1問ずつ開く） ---------------------------------------

function openReview(index) {
  const active = state.store.active;
  state.reviewMode = true;
  active.current = index;
  $('result').hidden = true;
  $('exam').hidden = false;
  document.querySelector('.exam-bar').hidden = true;
  $('paused-banner').hidden = true;
  $('overtime-banner').hidden = true;
  renderMap();
  renderQuestion();
  $('question').scrollIntoView({ block: 'start' });
}

function renderReviewAnswer(slot) {
  const box = $('review-answer');
  const outcome = slotOutcome(slot);
  const question = slot.question;
  const lines = [make('strong', {}, outcome.points === outcome.max ? '正解' : outcome.answered ? 'ちがいました' : '未解答')];
  if (question.format === 'written') {
    lines.push(make('p', {}, officialWrittenLabel(question) + '：' + modelAnswerText(question)));
    lines.push(make('p', {}, '自己採点：' + outcome.points + '点'));
  } else {
    lines.push(make('p', {}, answerLine(slot)));
  }
  if (outcome.overtime) lines.push(make('p', {}, '時間外に選んだ解答です。'));
  const back = make('button', { type: 'button', className: 'ghost small' }, '採点結果へ戻る');
  back.addEventListener('click', backToResult);
  lines.push(back);
  box.replaceChildren(...lines);
  box.className = 'review-answer' + (outcome.points === outcome.max ? ' ok' : ' ng');
  box.hidden = false;
}

function backToResult() {
  state.reviewMode = false;
  document.querySelector('.exam-bar').hidden = false;
  $('exam').hidden = true;
  $('result').hidden = false;
  window.scrollTo({ top: 0 });
}

function newExam() {
  const active = state.store.active;
  if (active && active.attempts && active.attempts.some((attempt) => !active.posted[attempt.eventId])) {
    if (!window.confirm('まだ回答履歴に保存していない解答があります。保存せずに最初の画面へ戻りますか？（採点結果は「これまでの通し演習」に残ります）')) return;
  }
  state.store.active = null;
  state.reviewMode = false;
  saveStore();
  showSetup();
}

function abandonExam() {
  if (!window.confirm('この通し演習をやめて、解答を消します。回答履歴には何も保存しません。よろしいですか？')) return;
  state.store.active = null;
  saveStore();
  showSetup();
}

function showSetup() {
  hideAll();
  $('load-error').hidden = true;
  $('setup').hidden = false;
  renderSetupYears();
  renderHistory();
  refreshSetup();
}

async function copySummary() {
  const text = $('summary-text').textContent;
  try {
    await navigator.clipboard.writeText(text);
    $('copy-summary').textContent = 'コピーしました';
  } catch (_err) {
    $('copy-summary').closest('.panel').querySelector('.summary-text').open = true;
    $('copy-summary').textContent = '下の文を選んでコピーしてください';
  }
}

// ---- 小物 -------------------------------------------------------------

function $(id) { return document.getElementById(id); }

function make(tag, attributes, text) {
  const node = document.createElement(tag);
  Object.entries(attributes || {}).forEach(([key, value]) => {
    if (key === 'className') node.className = value;
    else if (key === 'dataset') Object.entries(value).forEach(([dataKey, dataValue]) => { node.dataset[dataKey] = dataValue; });
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  });
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }

function answerValue(answer) {
  if (!answer) return null;
  return answer.values !== undefined ? answer.values : answer.value;
}

function answersEqual(left, right) {
  if (left && typeof left === 'object' && right && typeof right === 'object') {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length && leftKeys.every((key, index) => key === rightKeys[index] && Number(left[key]) === Number(right[key]));
  }
  return Number(left) === Number(right);
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : (date.getMonth() + 1) + '/' + date.getDate();
}

function showError(message) {
  $('load-error').textContent = message;
  $('load-error').hidden = false;
}

function bind() {
  $('setup-year').addEventListener('change', refreshSetup);
  $('setup-start').addEventListener('click', startExam);
  $('pause').addEventListener('click', togglePause);
  $('resume').addEventListener('click', togglePause);
  $('finish').addEventListener('click', finishExam);
  $('flag').addEventListener('change', toggleFlag);
  $('prev').addEventListener('click', () => goTo(state.store.active.current - 1));
  $('next').addEventListener('click', next);
  $('grading-back').addEventListener('click', () => {
    const active = state.store.active;
    active.phase = 'exam';
    delete active.finishedAt;
    // 戻ったら時間を再開する。採点画面にいた時間は数えない。
    active.runningSince = Date.now();
    saveStore();
    openActive();
  });
  $('grading-done').addEventListener('click', completeGrading);
  $('retry-save').addEventListener('click', postAttempts);
  $('copy-summary').addEventListener('click', copySummary);
  $('new-exam').addEventListener('click', newExam);
  document.addEventListener('keydown', (event) => {
    const active = state.store.active;
    if (!active || $('exam').hidden || event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = event.target.tagName;
    // 文字を打つ欄と選択欄では何もしない。ラジオボタン上の矢印キーは選択肢の移動に使われるので奪わない。
    if (tag === 'TEXTAREA' || tag === 'SELECT' || (tag === 'INPUT' && !['radio', 'checkbox'].includes(event.target.type))) return;
    if (tag === 'INPUT' && event.key.startsWith('Arrow')) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); next(); }
    else if (event.key === 'ArrowLeft' && active.current > 0) { event.preventDefault(); goTo(active.current - 1); }
    else if (/^[1-5]$/.test(event.key) && !state.reviewMode) {
      const slot = state.slots[active.current];
      if (!slot.question || slot.question.format !== 'regular') return;
      const input = document.querySelector('#answer-area input[value="' + event.key + '"]');
      if (input) { input.checked = true; setAnswer(slot.question, Number(event.key)); }
    }
  });
  // 画面を離れるあいだも時計は進む（本番と同じ）。問題ごとの時間だけはここで締める。
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { noteTimeOnQuestion(); saveStore(); }
    else state.viewStartedAt = Date.now();
  });
  const abandon = make('button', { type: 'button', className: 'ghost danger small' }, 'やめる');
  abandon.addEventListener('click', abandonExam);
  document.querySelector('.bar-actions').prepend(abandon);
}

function init() {
  bind();
  saveStore();
  if (state.store.active) openActive();
  else showSetup();
}

init();
