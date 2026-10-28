import re, sys
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(s): super().__init__(); s.svgs=[]; s.cur=None
    def handle_starttag(s, tag, a):
        a=dict(a)
        if tag=='svg': s.cur=[]; s.svgs.append(s.cur)
        if tag=='rect' and s.cur is not None and a.get('class') in ('node-box','edge-label-box'):
            s.cur.append((a['class'], float(a['x']), float(a['y']), float(a['width']), float(a['height'])))
for f in sys.argv[1:]:
    p=P(); p.feed(open(f).read()); bad=0
    for i,svg in enumerate(p.svgs):
        for j,a in enumerate(svg):
            for b in svg[j+1:]:
                if a[0]=='node-box' and b[0]=='node-box': kind='node'
                else: kind='label'
                ov = a[1] < b[1]+b[3]-1 and b[1] < a[1]+a[3]-1 and a[2] < b[2]+b[4]-1 and b[2] < a[2]+a[4]-1
                if ov: bad+=1; print(f, 'svg', i, kind, a, b)
    print(f.split('/')[-1], 'svg', len(p.svgs), 'overlaps', bad)
