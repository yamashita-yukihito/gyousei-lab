"""static/gyosei-d/index.html を手本に、他科目のD案ページを作る。使い方: python3 make_page.py kenpo-d kenpoD:v1 憲法 CONSTITUTION"""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
d, store, name, kicker = sys.argv[1:5]
src = (APP / "static/gyosei-d/index.html").read_text(encoding="utf-8")
reps = [
    ('data-store-key="gyoseiD:v1" data-drill-name="行政法ドリルD案"', f'data-store-key="{store}" data-drill-name="{name}ドリルD案"'),
    ("<title>行政法・法律家の常識ドリル D案</title>", f"<title>{name}・法律家の常識ドリル D案</title>"),
    ("GYOUSEI LAB / ADMINISTRATIVE LAW D", f"GYOUSEI LAB / {kicker} D"),
    ("<h1>行政法・法律家の常識ドリル <span>D案</span></h1>", f"<h1>{name}・法律家の常識ドリル <span>D案</span></h1>"),
    ("<code>gyoseiD:v1</code>", f"<code>{store}</code>"),
]
for a, b in reps:
    assert src.count(a) == 1, a
    src = src.replace(a, b)
out = APP / "static" / d / "index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(src, encoding="utf-8")
print("作成", out)
