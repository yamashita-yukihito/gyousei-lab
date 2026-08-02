import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINPO_C = ROOT / 'static' / 'minpo-c'


def load_data():
    return json.loads((MINPO_C / 'cases.json').read_text(encoding='utf-8'))


def test_minpo_c_files_exist():
    for name in ('index.html', 'styles.css', 'app.js', 'cases.json'):
        assert (MINPO_C / name).is_file(), name


def test_case_schema_and_unique_ids():
    data = load_data()
    assert data['schemaVersion'] == 'minpo-cases@1'
    assert data['asOf'] == '2026-04-01'
    assert len(data['chapters']) == 3

    chapter_ids = [chapter['id'] for chapter in data['chapters']]
    assert len(chapter_ids) == len(set(chapter_ids))

    question_ids = []
    for chapter in data['chapters']:
        assert 8 <= len(chapter['questions']) <= 10
        assert chapter['diagram']['nodes']
        assert chapter['diagram']['edges']
        assert chapter['summaryTable']['rows']
        for question in chapter['questions']:
            question_ids.append(question['id'])
            assert question['type'] in {'straight', 'trap'}
            assert isinstance(question['answer'], bool)
            for field in (
                'delta', 'question', 'summary', 'connection', 'changedFact',
                'protect', 'reverseProblem', 'legalRule', 'intuition', 'trap',
                'minimum', 'nextPreview', 'logic', 'legalBasis',
            ):
                assert question.get(field), (question['id'], field)
            assert set(question['logic']) == {'fact', 'rule', 'conclusion'}
            assert all(item['url'].startswith('https://') for item in question['legalBasis'])

    assert len(question_ids) == 27
    assert len(question_ids) == len(set(question_ids))


def test_answer_and_label_balance():
    questions = [q for c in load_data()['chapters'] for q in c['questions']]
    true_count = sum(q['answer'] for q in questions)
    trap_count = sum(q['type'] == 'trap' for q in questions)
    assert 9 <= true_count <= 18
    assert 8 <= trap_count <= 19


def test_html_references_only_local_runtime_files():
    html = (MINPO_C / 'index.html').read_text(encoding='utf-8')
    assert 'styles.css?v=' in html
    assert 'app.js?v=' in html
    assert 'minpoC:v1' in html
    assert 'cases.json' not in html  # app.jsだけが問題データを読み込む


def test_javascript_has_separate_read_and_answer_paths():
    js = (MINPO_C / 'app.js').read_text(encoding='utf-8')
    assert "const STORE_KEY = 'minpoC:v1'" in js
    assert 'markReadAndAdvance' in js
    assert 'answerCurrent' in js
    assert "state.mode === 'exam'" in js
    assert "fetch('cases.json'" in js
