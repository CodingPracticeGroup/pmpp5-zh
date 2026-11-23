#!/usr/bin/env python3
"""从 /tmp/pmpp_en.txt 解析原书目录 → /tmp/pmpp_toc.json [(num,title)]。处理标题断行。"""
import re, json
lines = open('/tmp/pmpp_en.txt', encoding='utf-8').read().replace('\u00ad\n', '').replace('\u00ad', '').split('\n')
started = False; raw = []; last = -1
for i, l in enumerate(lines):
    if re.match(r'^\s*Contents\s*$', l): started = True; continue
    if not started: continue
    if i - last > 40 and raw: break
    m = re.match(r'^\s{0,2}((?:Chapter\s+\d+|Appendix\s+[ABC]|(?:\d+|[A-C])\.\d+(?:\.\d+)?))\s+(.*?)\s*(?:\.\s*)*\d*\s*$', l)
    if m and re.search(r"\S", m.group(2)):
        num, title, ii = m.group(1).strip(), m.group(2).strip().rstrip('. '), i
        # 标题断行: 下一行以续行词(小写开头)开头且非新条目
        while title and re.search(r"[a-z-]$", title) and ii + 1 < len(lines):
            nx = lines[ii + 1]
            if re.match(r'^\s{0,2}(?:(?:\d+|[A-C])\.\d+|Chapter|Appendix|References|INDEX|Glossary|Contents)\b', nx): break
            if re.match(r'^\s*\.\s', nx):  # 纯点线续行
                part = re.sub(r'^\s*\.\s*', '', nx).strip()
                part = re.sub(r'\d+\s*$', '', part).rstrip('. ').strip()
                if not part: break
                title = title + " " + part; ii += 1; continue
            part = re.sub(r'\s*\.\s*.*$', '', nx).strip()
            if not part or not re.match(r'^[a-z(]', part): break
            title = title + " " + part; ii += 1
        raw.append([num, title, ii]); last = ii
        continue
    if re.match(r'^\s*(References|INDEX|Glossary)\s*(\.\s*)*$', l):
        raw.append(['refs', 'References', i]); last = i
seen = set(); ded = []
for num, title, _ in raw:
    k = (num, title)
    if k in seen: continue
    seen.add(k); ded.append((num, title))
json.dump(ded, open('/tmp/pmpp_toc.json', 'w'), ensure_ascii=False)
print("TOC:", len(ded))
bad = [t for t in ded if ' References' in t[1] or ' . .' in t[1]]
print("疑似污染:", bad[:5])
