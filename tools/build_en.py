#!/usr/bin/env python3
"""从原书 PDF 文本层提取英文正文, 按中文标题树切段, 生成英文对照块。

算法: 1) 全文全局成段(处理软连字符/页码/running header/跨页缝合/图题)
     2) 按 TOC 节标题行号切段归属
输出: /tmp/en_<chap>.json {preface: [...], sections: [{num,title,level,paras}]}
"""
import re, json, sys, difflib

EN_TXT = "/tmp/pmpp_en.txt"
TOC_JSON = "/tmp/pmpp_toc.json"

def norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def compact(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

def find_section_line(lines, num, title, start):
    tn = norm_title(title)
    pat = re.compile(r"^\s*" + re.escape(num) + r"\s+\S")
    chap = re.match(r"^(\d+|[A-C])\.", num).group(1)
    sibling = re.compile(r"^\s*" + re.escape(chap) + r"\.\d+\s+\S")
    contents = re.compile(r"^\s*(?:CONTENTS|CONTENTS|Contents)\s*$")
    for i in range(start, len(lines)):
        l = lines[i]
        if re.search(r"(\.\s){3,}", l):
            continue
        if pat.match(l):
            cand = norm_title(re.sub(r"^\s*" + re.escape(num) + r"\s+", "", l))
            hit = len(cand) >= 3 and (
                compact(cand)[:20] == compact(tn)[:20] or
                compact(tn).startswith(compact(cand)) or
                compact(cand).startswith(compact(tn)) or
                difflib.SequenceMatcher(None, cand, tn).ratio() > 0.75)
            if not hit:
                continue
            # 跳过章级目录行: 紧邻上一行(无空行隔开)为同章节标题短行或 CONTENTS
            if i > 0 and lines[i - 1].strip():
                j = i - 1
                if contents.match(lines[j]) or (sibling.match(lines[j]) and len(lines[j].strip()) < 60):
                    continue
            return i
    return None

def main():
    raw = open(EN_TXT, encoding="utf-8").read().replace("\u00ad\n", "").replace("\u00ad", "")
    lines = raw.split("\n")
    toc = json.load(open(TOC_JSON))

    # 正文起点: 第一条无点线 "1.1 标题"
    BODY_START = 0
    for i, l in enumerate(lines):
        if re.match(r"^\s*1\.1\s+\S", l) and not re.search(r"(\.\s){3,}", l):
            BODY_START = i
            break
    if not BODY_START:
        print("无法定位正文起点"); return

    # 章内节
    def chap_of(name):
        appmap = {"appendix-a-numerical-considerations": "A",
                  "appendix-b-deep-learning-basics": "B",
                  "appendix-c-cuda-memories-address-spaces-and-pointers": "C"}
        if name in appmap: return appmap[name]
        return re.sub(r"^0+", "", name.split("-")[0])
    cn = sys.argv[1]
    chap = chap_of(cn)
    secs = []
    for num, title in toc:
        m = re.match(r"^(" + re.escape(chap) + r")\.(\d+)(?:\.(\d+))?$", num)
        if m:
            secs.append({"num": num, "title": title,
                         "level": 2 if m.group(3) is None else 3})
    if not secs:
        print("no toc for", chap); return

    # 先定位本章第一节, 再从它向前找章题页特征(章题页版式: CHAPTER/标题/章数/CONTENTS)
    pos = find_section_line(lines, secs[0]["num"], secs[0]["title"], BODY_START)
    if pos is None:
        print("定位失败", chap); return
    ch_start = pos
    num_alone = re.compile(r"^\s*" + re.escape(chap) + r"\s*$")
    def is_feat(l):
        t = l.strip()
        if t == "CONTENTS": return True
        m = re.match(r"^(?:CHAPTER|APPENDIX)\s*(\d+|[A-C])?\s*$", t)
        if m and (m.group(1) is None or m.group(1) == chap): return True
        m2 = re.match(r"^(?:CHAPTER|APPENDIX)\s+" + re.escape(chap) + r"\b", t)
        return bool(m2 or num_alone.match(l))
    found = None
    for i in range(max(BODY_START, pos - 150), pos):
        if is_feat(lines[i]):
            found = i
            break
    if found is not None:
        ch_start = found
        j = found - 1
        while j > BODY_START and lines[j].strip() and len(lines[j].strip()) < 50 \
              and not re.search(r"[.?!]$", lines[j].strip()):
            ch_start = j; j -= 1
    heads = set()
    for s in secs:
        heads.add(norm_title(s["title"]))
        heads.add(norm_title(s["num"] + " " + s["title"]))
    # 下一章起点
    end_key = None
    if chap.isdigit():
        n = int(chap) + 1
        if n <= 24: end_key = f"{n}.1"
    elif chap in ("A", "B"):
        end_key = chr(ord(chap) + 1) + ".1"
    ch_end = len(lines)
    if end_key:
        cands = [t for n, t in toc if n == end_key]
        if cands:
            p = find_section_line(lines, end_key, cands[0], pos)
            if p: ch_end = p
    # 各节行号(顺序搜索; 失败则全局找未使用的最近候选——PDF 页序错乱回退)
    used = set()
    cur = ch_start
    for s in secs:
        p = find_section_line(lines, s["num"], s["title"], cur)
        if p is None or p in used:
            # 回退: 全局搜索, 排除已用与紧邻标题, 取距 cur 最近的
            cands = []
            q = cur
            while True:
                q2 = find_section_line(lines, s["num"], s["title"], q)
                if q2 is None or q2 in used or (cands and q2 == cands[-1]): break
                cands.append(q2)
                q = q2 + 1
                if len(cands) > 30: break
            if cands:
                p = min(cands, key=lambda c: abs(c - cur))
        s["line"] = p
        if p is not None:
            used.add(p)
            cur = max(cur, p + 1)

    # ---------- 全局成段 ----------
    # 原始段落块: 空行分隔
    blocks = []  # (start_line, [text lines])
    cur, cur_start = [], None
    for i, l in enumerate(lines):
        t = l.strip()
        if not t:
            if cur: blocks.append((cur_start, cur)); cur, cur_start = [], None
            continue
        if not cur: cur_start = i
        cur.append(t)
    if cur: blocks.append((cur_start, cur))

    # 过滤块
    def is_noise(txt):
        if re.fullmatch(r"[\dIVXivx\s]{1,8}", txt): return True
        if txt.startswith("Programming Massively Parallel Processors.") or txt.startswith("Copyright ©") or txt.startswith("https://doi.org") or txt.startswith("Copyright (c)"):
            return True
        if norm_title(txt) in heads: return True
        if re.match(r"^(chapter|appendix)\s+[a-z0-9]", norm_title(txt)): return True
        if re.match(r"^part\s+[ivx]+\b", norm_title(txt)): return True
        return False

    paras = []  # (start_line, text)
    for start_l, ls in blocks:
        joined = " ".join(ls)
        if is_noise(joined):
            # 章题行保留行号用于 preface 起点, 不输出
            continue
        paras.append((start_l, joined))

    # 跨页缝合
    TERM = re.compile(r"[.!?:;\u201d\u2019\u2019\]\"’)\]]\s*$")
    merged = []
    for ln, p in paras:
        if merged and not TERM.search(merged[-1][1]) and len(merged[-1][1]) > 40 \
           and ln - merged[-1][0] <= 6:
            merged[-1] = (merged[-1][0], merged[-1][1] + " " + p)
        else:
            merged.append((ln, p))

    # FIGURE/TABLE 题注: 独立 "FIGURE 17.4" 行 + 下一段说明合并
    out = []
    fig_pat = re.compile(r"^(FIGURE|TABLE)\s+([0-9]+[A-Za-z]?\.[0-9]+[A-Za-z]?)\s*$")
    for ln, p in merged:
        m = fig_pat.match(p.strip())
        if m:
            out.append((ln, f"**{m.group(1)} {m.group(2)}.**"))
            continue
        # 若上一段是纯 FIGURE 标记且本段短且无完整句号结尾多层 → 并为题注(题注一般1-3句)
        if out and re.fullmatch(r"\*\*(FIGURE|TABLE) [0-9A-Za-z.]+\.\*\*", out[-1][1]):
            out[-1] = (out[-1][0], out[-1][1] + " " + p)
            continue
        out.append((ln, p))
    paras = out

    # ---------- 切段归属 ----------
    positioned = [s for s in secs if s["line"] is not None]
    result = {"preface": [], "sections": []}
    by_line = sorted(positioned, key=lambda s: s["line"])
    first_line = by_line[0]["line"] if by_line else pos
    result["preface"] = [p for ln, p in paras if ch_start <= ln < first_line]
    for idx, s in enumerate(by_line):
        a = s["line"]
        b = by_line[idx + 1]["line"] if idx + 1 < len(by_line) else ch_end
        s["paras"] = [p for ln, p in paras if a <= ln < b]
    result["sections"] = positioned  # 按 TOC 顺序输出
    json.dump(result, open(f"/tmp/en_{chap}.json", "w"), ensure_ascii=False)
    tot = sum(len(s["paras"]) for s in positioned)
    print(f"OK {chap}: preface={len(result['preface'])} sections={len(positioned)}/{len(secs)} paras={tot}")
    for s in positioned[:2]:
        print(f"  {s['num']} paras={len(s['paras'])} first={(s['paras'][0][:70] if s['paras'] else '(空)')}")

if __name__ == "__main__":
    main()
