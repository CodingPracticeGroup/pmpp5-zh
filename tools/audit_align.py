#!/usr/bin/env python3
"""中英段落对齐 + 可疑点初筛。

输入: content/book/NN-chN.md(中) 与 .en.md(英)
输出: /tmp/audit_<chap>.json 逐节段落对 + 旗标:
  num  英文段数字/单位在对应中文段缺失
  ident 英文段代码标识符缺失
  align_gap 连续无法对齐(可能漏译/增译)
"""
import re, sys, json, glob, os, difflib

NUM = re.compile(r"\d+(?:[\.,]\d+)*(?:\s?(?:%|GB|MB|KB|TB|GHz|MHz|TFLOPS|GFLOPS|MFLOPS|KB/s|GB/s|TB/s))?(?![\d\.])")
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9\.\-]{2,}(?:\([0-9]*\))?")
STOP = set("""although during hence note obviously whereas although while nonetheless moreover furthermore additionally thus consequently therefore however instead also despite throughout underneath above below since until unless because whenever whatever whichever whoever win wins winner winners advance advances champion emerge emerging entering etc fig figs figure figures table tables outline contents preface acknowledgments introduction summary exercises references chapter chapters appendix appendices page pages code codes form forms general specific sequential parallel set sets single multiple number numbers value values result results operation operations operations' example examples program programs of in on to and or for with by is are be as that this these those it its from at we you they he she not but if then else when while how what which who whom whose will would can could should may might must have has had do does did there here such also more most other some any all each every both few less many much so than too very just about into over under up down out off again further once fig figure figures table tables section sections chapter chapters example examples code listing program write your us our i""".split())
CJK = re.compile(r"[\u4e00-\u9fff]")

def norm_num(t):
    return t.replace(",", "").strip()

def en_anchors(t):
    nums = set(norm_num(x) for x in NUM.findall(t))
    idents = set(x.rstrip("()0123456789") for x in IDENT.findall(t) if x.lower() not in STOP and not x.isdigit() and len(x) >= 3)
    return nums, idents

def zh_has(nums, idents, ztext):
    zn = set(norm_num(x) for x in NUM.findall(ztext))
    znorm = ztext.replace(" ", "").lower()
    missing_n = {x for x in nums if x not in zn and not any(x in y or y in x for y in zn)}
    missing_i = {x for x in idents if x.lower() not in znorm and x.lower().replace("_","") not in znorm}
    return missing_n, missing_i

def parse_pages(path):
    """解析 md → [(level,title,anchor,[para...])]"""
    txt = open(path, encoding="utf-8").read()
    if txt.startswith("---"):
        txt = txt.split("---", 2)[2]
    secs, cur = [], None
    in_fence = False
    buf = []
    for l in txt.split("\n"):
        if l.startswith("```"): in_fence = not in_fence
        m = re.match(r"^(#{1,4})\s+(.*?)\s*(?:\{#[^}]+\})?\s*$", l) if not in_fence else None
        if m:
            if cur: cur["paras"] = [p for p in "\n".join(buf).split("\n\n") if p.strip()]; buf = []
            cur = {"level": len(m.group(1)), "title": re.sub(r"\{#[^}]+\}", "", m.group(2)).strip(), "paras": []}
            secs.append(cur); buf = []
            continue
        if cur is not None:
            buf.append(l)
    if cur: cur["paras"] = [p for p in "\n".join(buf).split("\n\n") if p.strip()]
    return secs

def para_text(p):
    # 去掉代码块/表格/图标签, 保留自然语言
    p = re.sub(r"```.*?```", " ", p, flags=re.S)
    p = re.sub(r"<[^>]+>", " ", p)
    p = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", p)
    return p.strip()

def align_pair(zh_paras, en_paras):
    """单调对齐: 标准三方向 DP, 返回 [(zi,ei)]"""
    zn = [para_text(p) for p in zh_paras]
    en = [para_text(p) for p in en_paras]
    n, m = len(zn), len(en)
    def sim(ei, zi):
        e, z = en[ei], zn[zi]
        if len(e) < 25 or len(z) < 10: return 0.0
        nums, idents = en_anchors(e)
        anchors = nums | idents
        score = 0.0
        if anchors:
            zn2 = set(norm_num(x) for x in NUM.findall(z))
            znlow = z.replace(" ", "").lower()
            hit = sum(1 for a in anchors if a in zn2 or a.lower() in znlow)
            score = hit / max(3, len(anchors))
        pos_prior = 1.0 - abs(ei / max(1, m) - zi / max(1, n))
        return (0.7 * min(1.0, score * 2) + 0.3 * pos_prior) if score > 0 else 0.15 * pos_prior
    NEG = -2.0
    # dp[i][j] = 前 i 个中文段与前 j 个英文段的最优得分
    dp = [[-1e18]*(m+1) for _ in range(n+1)]
    bk = [[None]*(m+1) for _ in range(n+1)]
    dp[0][0] = 0.0
    for i in range(n+1):
        for j in range(m+1):
            if dp[i][j] < -1e17: continue
            v = dp[i][j]
            if i < n and v + NEG > dp[i+1][j]:   # 中文段无对应(增译/中侧独有)
                dp[i+1][j] = v + NEG; bk[i+1][j] = (i, j, "z")
            if j < m and v + NEG > dp[i][j+1]:   # 英文段无对应(疑似漏译)
                dp[i][j+1] = v + NEG; bk[i][j+1] = (i, j, "e")
            if i < n and j < m:
                s = sim(j, i)
                if dp[i][j] + s > dp[i+1][j+1]:
                    dp[i+1][j+1] = dp[i][j] + s; bk[i+1][j+1] = (i, j, "m")
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        b = bk[i][j]
        if b is None: break
        pi, pj, op = b
        if op == "m": pairs.append((pi, pj))
        i, j = pi, pj
    pairs.reverse()
    return pairs

def audit(zhfile):
    enfile = zhfile.replace(".md", ".en.md")
    slug = os.path.basename(zhfile)[:-3]
    zsecs = [s for s in parse_pages(zhfile) if s["level"] == 2 and "参考文献" not in s["title"]]
    esecs = [s for s in parse_pages(enfile) if s["level"] == 2 and s["title"].strip().lower() != "references"]
    result = {"slug": slug, "sections": [], "stats": {}}
    tn = ti = tg = 0
    for zs in zsecs:
        # 英文节按标题配对(两侧 H2 顺序一致, 用序号)
        # 用锚点匹配更稳: 直接按标题文本互找
        idx = zsecs.index(zs)
        es = esecs[idx] if idx < len(esecs) else None
        if es is None: continue
        zp = [p for p in zs["paras"] if len(para_text(p)) > 30]
        ep = [p for p in es["paras"] if len(para_text(p)) > 30]
        pairs = align_pair(zp, ep)
        sec_issues = []
        paired_en = set(j for _, j in pairs)
        for i, j in pairs:
            e = para_text(ep[j]); z = para_text(zp[i])
            if re.match(r"^(FIGURE|TABLE|CONTENTS|OUTLINE|References)\b", e) or "doi.org" in e: continue
            nums, idents = en_anchors(e)
            mn, mi = zh_has(nums, idents, z)
            mn = {x for x in mn if not re.fullmatch(r"\d{1,2}", x) and x not in ("0", "1", "2")}
            if mn or mi:
                sec_issues.append({"kind": "num" if mn else "ident", "en": e[:160], "missing": sorted(mn | mi)[:8]})
        # 未对齐英文段(中文无对应 → 可能漏译)
        for j, e in enumerate(ep):
            if j not in paired_en and len(para_text(e)) > 80:
                sec_issues.append({"kind": "unaligned_en", "en": para_text(e)[:160]})
        result["sections"].append({"zh_title": zs["title"], "en_title": es["title"],
                                   "pairs": len(pairs), "zh_paras": len(zp), "en_paras": len(ep),
                                   "issues": sec_issues})
        tn += len([x for x in sec_issues if x["kind"] == "num"])
        ti += len([x for x in sec_issues if x["kind"] == "ident"])
        tg += len([x for x in sec_issues if x["kind"] == "unaligned_en"])
    result["stats"] = {"num_flags": tn, "ident_flags": ti, "unaligned": tg}
    ch = re.sub(r"^0*", "", slug.split("-")[1].replace("ch", "")) or "A"
    json.dump(result, open(f"/tmp/audit_{ch}.json", "w"), ensure_ascii=False, indent=1)
    print(f"{slug}: pairs={sum(s['pairs'] for s in result['sections'])} zh={sum(s['zh_paras'] for s in result['sections'])} en={sum(s['en_paras'] for s in result['sections'])} num={tn} ident={ti} unaligned={tg}")

def match_section_by_title(ztitle, esecs):
    zt = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", ztitle.lower())
    best, bs = None, 0
    for es in esecs:
        et = re.sub(r"[^0-9a-z]", "", es["title"].lower())
        r = difflib.SequenceMatcher(None, zt[:20], et[:20]).ratio()
        # 章节序对齐兜底: 用标题里的编号(中文节可能无编号) — 位置序号更可靠, 交给调用顺序
        if r > bs: bs, best = r, es
    return best if bs > 0.3 else None

if __name__ == "__main__":
    for f in sorted(glob.glob("content/book/*.md")):
        if f.endswith(".en.md") or "_index" in f: continue
        audit(f)
