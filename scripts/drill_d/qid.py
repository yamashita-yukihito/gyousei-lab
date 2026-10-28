"""合格道場の問題ID（:肢番号）から出題箇所と肢の本文を引く。使い方: python3 qid.py 1408:3 1528"""
import glob, json, sys
from pathlib import Path
from pq import era, limbs, truth
ROOT = Path.home()/".local/share/yuki-services/gyousei-lab/authoring/all_subjects"
want = {a.split(':')[0]: (a.split(':')[1] if ':' in a else None) for a in sys.argv[1:]}
for p in glob.glob(str(ROOT/"*/extracted/*/*.json")):
    q = json.loads(Path(p).read_text(encoding="utf-8"))
    if q["externalQuestionId"] in want:
        kind, items = limbs(q); tv = truth(q, kind, items)
        for label, text in items:
            if want[q["externalQuestionId"]] in (None, str(label)):
                print(f"{era(q['examYear'])}問{q['questionNumber']}肢{label} [{ {True:'○',False:'×'}.get(tv.get(label),'?') }] {text[:200]}")
