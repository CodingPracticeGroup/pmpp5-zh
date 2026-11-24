#!/usr/bin/env python3
"""合并中文译稿(pandoc 产物)与英文提取块 → Hugo content/book/*.md (zh + .en.md)。

锚点规则: 中文 H2 ↔ 英文 TOC 节 {#sec-N-M}; 中文"本章参考文献" {#sec-refs};
中文特有子标题 {#sec-x-K}(英文侧无对应标题时不输出标题, Sync Scroll 自动平滑)。
"""
import re, json, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCSDIR = os.path.join(ROOT, "docs", "chapters")
OUT = os.path.join(ROOT, "content", "book")
STATIC_FIGS = os.path.join(ROOT, "static", "figs")
BASE = "/pmpp5-zh"

import importlib.util
spec = importlib.util.spec_from_file_location("bd", os.path.join(ROOT, "tools", "build_docs.py"))
bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
CHAPTERS = bd.CHAPTERS

TOC = json.load(open("/tmp/pmpp_toc.json"))
ENAMES = json.load(open("/tmp/pmpp_en_chapter_names.json"))

def chap_num(name):
    appmap = {"appendix-a-numerical-considerations": "A",
              "appendix-b-deep-learning-basics": "B",
              "appendix-c-cuda-memories-address-spaces-and-pointers": "C"}
    return appmap.get(name) or re.sub(r"^0+", "", name.split("-")[0])

def sec_anchor(num):
    return "sec-" + num.replace(".", "-")

def en_sections(cnum):
    d = json.load(open(f"/tmp/en_{cnum}.json"))
    return d

def md_escape_para(t):
    # 行首字符防误解析
    t = re.sub(r"^([#\-\*>+]|(\d+)([.)]))\s", lambda m: "\\" + m.group(0)[:-1] + " ", t, count=1)
    t = t.replace("*", "\\*")
    return t

def is_img_line(l):
    """独立图片行: ![alt](path) 整行。"""
    if not l.startswith("!["): return False
    r = l.rstrip()
    if not r.endswith(")"): return False
    i = r.find("](")
    return i > 1

def split_blocks(md):
    """返回 [(kind, payload)]; kind ∈ h1..h4, block, para, fence, html, img, quote, raw"""
    lines = md.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            j = i
            while j + 1 < len(lines) and not lines[j + 1].startswith("```"): j += 1
            end = min(j + 2, len(lines))  # 含闭合行
            blocks.append(("fence", "\n".join(lines[i:end]))); i = end; continue
        if l.startswith("<"):
            # 任意 HTML 块(表格/figure 等): 连续以 < 开头的行(允许行间空行)
            j = i
            while j < len(lines):
                t = lines[j].strip()
                if not t:
                    # 空行: 若后续仍是 < 行则继续
                    k = j
                    while k < len(lines) and not lines[k].strip(): k += 1
                    if k < len(lines) and lines[k].startswith("<"):
                        j = k; continue
                    break
                if not lines[j].startswith("<"): break
                j += 1
            blocks.append(("html", "\n".join(lines[i:j]))); i = j; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", l)
        if m:
            blocks.append((f"h{len(m.group(1))}", m.group(2))); i += 1; continue
        if l.startswith(">"):
            j = i
            while j + 1 < len(lines) and lines[j + 1].startswith(">"): j += 1
            blocks.append(("quote", "\n".join(lines[i:j + 1]))); i = j + 1; continue
        if is_img_line(l):
            blocks.append(("img", l)); i += 1; continue
        if not l.strip():
            i += 1; continue
        # 段落/列表块: 到空行
        j = i
        buf = []
        while j < len(lines) and lines[j].strip() and not lines[j].startswith("#") \
              and not lines[j].startswith("```") and not lines[j].startswith("<") \
              and not lines[j].startswith(">") and not is_img_line(lines[j]):
            buf.append(lines[j]); j += 1
        blocks.append(("para", "\n".join(buf))); i = j if j > i else i + 1
    return blocks


def compact(t):
    return re.sub(r"[^0-9a-z]", "", t.lower())


PAGE_NOISE = re.compile(
    r"\s\d{2,4}\s{1,2}(?:\x0c\s*\d+\s{0,2})?(?:CHAPTER|APPENDIX)\s+\d+[A-Z]?\s[\S ]{0,80}?\s(?=[a-z])"
    r"|\s\d{3}\s\s+\d{3}\s+(?:CHAPTER|APPENDIX)\s+\d+\s+[\s\S]{0,60}?\s(?=[a-z])")

def depage(t):
    """去掉段内夹入的页码+页眉(两栏/跨页断词场景)。"""
    prev = None
    while prev != t:
        prev = t
        t = PAGE_NOISE.sub(" ", t)
        t = re.sub(r"\s\d{3}\s\s+(?=\S)", " ", t)  # 残留孤立三页码
    return t

def clean_en_paras(paras, titles_compact, sec_no):
    """去噪: 小节标题页行(编号+标题前缀)/CONTENTS/OUTLINE; 参考文献条目分流。"""
    body, refs, drop = [], [], []
    for p in paras:
        t = depage(p.strip())
        if not t: continue
        if re.match(r"^(CONTENTS|OUTLINE)\b", t): continue
        m2 = re.search(r"\s+CONTENTS\s+[\dA-Z]", t)
        if m2:
            t = t[:m2.start()].strip()
            if len(t) < 40: continue
        if re.search(r"https://doi\.org|B978-0-\d+-\d+-\d-\.", t) and len(t) < 300: continue
        m = re.match(r"^((?:\d+(?:\.\d+)*|Appendix\s+[A-C]|Chapter\s+\d+)\s+)([^.]{0,80})", t)
        if m:
            cand = compact(m.group(2)[:40])
            hit = any(cand and (cand.startswith(tc[:12]) or tc.startswith(cand[:12])) for tc in titles_compact)
            if hit:
                rest = t[m.end():].strip()
                if len(rest) < 60: continue
                t = rest
        if re.match(r"^\[\d+\]", t):
            refs.append(t); continue
        body.append(t)
    return body, refs



FIGMAP = {}
def load_figmap():
    global FIGMAP
    if not FIGMAP:
        try:
            raw = json.load(open("/tmp/pmpp_figmap.json"))
            FIGMAP = {k: v.get("png") for k, v in raw.items() if v.get("png")}
        except FileNotFoundError:
            pass
    return FIGMAP

def fig_png(fid):
    """figure id '10-7'/'b-7' → 原图文件名。"""
    key = fid.split("-", 1)
    k = key[0].upper() + "." + key[1]
    return load_figmap().get(k)

def swap_fig_block(m):
    fid = m.group(1)
    png = fig_png(fid)
    if not png:
        return m.group(0)
    capm = re.search(r"<figcaption>(.*?)</figcaption>", m.group(0), re.S)
    cap = capm.group(1) if capm else ""
    cap = re.sub(r"[（(]?依据原书图\s*[0-9A-Za-z.\-]+\s*重绘[)）]?[。]?\s*", "", cap).strip()
    num = fid.replace("-", ".")
    img = f'<img src="{BASE}/figs/orig/{png}" alt="图 {num}" loading="lazy" style="max-width:100%;height:auto;" />'
    if not cap:
        cap = f"图 {num}（原书插图）"
    return f'<figure id="fig:{fid}">\n{img}\n<figcaption>{cap}</figcaption>\n</figure>'

def swap_en_figs(md):
    """英文侧: FIGURE 题注行上方插入原图。"""
    def rep(m):
        fid = m.group(1).replace(".", "-").lower()
        png = fig_png(fid)
        if not png:
            return m.group(0)
        return (f'<img src="{BASE}/figs/orig/{png}" alt="Figure {m.group(1)}" '
                f'loading="lazy" style="max-width:100%;height:auto;" />\n\n'
                f'**FIGURE {m.group(1)}.** ')
    return re.sub(r"^(?:\*\*)?FIGURE ([0-9]+\.[0-9]+[a-z]?|[A-C]\.[0-9]+[a-z]?)(?:\.\*\*)?\s*", rep, md, flags=re.M)


def build_chapter(name, title_zh, idx):
    cnum = chap_num(name)
    src = open(os.path.join(DOCSDIR, f"{name}.md"), encoding="utf-8").read()
    blocks = split_blocks(src)
    endata = en_sections(cnum)
    # 英文 TOC 序列(仅二级, 按序)
    en_secs_toc = [s for s in endata["sections"] if s["level"] == 2]
    titles_c = [compact(x["title"]) for x in en_secs_toc] + [compact(ENAMES.get(cnum,""))]
    # 清洗各节段落
    for es in endata["sections"]:
        es["paras"], es["_refs"] = clean_en_paras(es["paras"], titles_c, es["num"])
    # 中文 H2 → 锚点(按顺序对齐; "本章参考文献"特判)
    h2_map = {}   # 中文 H2 文本 → (anchor, en_sec|None)
    refs_anchor = "sec-refs"
    en_iter = iter(en_secs_toc)
    xk = 0
    h3_map = {}   # 中文 H3 文本 → anchor(仅站内锚点)
    zh_out = []
    en_out = []
    # 英文头部
    en_title = ENAMES.get(cnum, title_zh)
    chap_word = "附录" if cnum in "ABC" and not cnum.isdigit() else ""
    if cnum.isdigit():
        en_h1 = f"# Chapter {cnum} {en_title} {{#c{cnum}}}"
        zh_h1_anchor = "{#c" + cnum + "}"
    else:
        en_h1 = f"# Appendix {cnum} {en_title} {{#c{cnum}}}"
        zh_h1_anchor = "{#c" + cnum + "}"
    weight = int(cnum) if cnum.isdigit() else 30 + "ABC".index(cnum)
    fm_zh = ["---", f'title: "{title_zh}"', f"weight: {weight}",
             f'description: "{title_zh} — 《大规模并行处理器编程(第5版)》学习译稿"', "---"]
    fm_en = ["---", f'title: "Chapter {cnum} {en_title}"' if cnum.isdigit() else f'title: "Appendix {cnum} {en_title}"',
             f"weight: {weight}",
             f'description: "Programming Massively Parallel Processors 5e — English original"', "---"]
    en_out.append(en_h1)
    for p in endata["preface"]:
        en_out.append("")
        en_out.append(md_escape_para(p))
    # 遍历中文块
    used_en = set()
    en_by_anchor = {}
    pending_en = None  # 当前英文 section dict
    cur_zh_h2 = None
    for kind, payload in blocks:
        if kind == "h1":
            zh_out.append(f"# {payload} {zh_h1_anchor}")
            continue
        if kind == "h2":
            if "本章参考文献" in payload:
                h2_map[payload] = (refs_anchor, None)
                zh_out.append(f"## {payload} {{#{refs_anchor}}}")
                continue
            es = next(en_iter, None)
            if es is not None:
                a = sec_anchor(es["num"])
                h2_map[payload] = (a, es)
                en_by_anchor[a] = es
                zh_out.append(f"## {payload} {{#{a}}}")
            else:
                xk += 1
                a = f"sec-x-{xk}"
                h2_map[payload] = (a, None)
                zh_out.append(f"## {payload} {{#{a}}}")
            continue
        if kind in ("h3", "h4", "h5", "h6"):
            xk += 1
            a = f"sec-x-{xk}"
            zh_out.append(f"{kind.upper()} {payload} {{#{a}}}")
            continue
        if kind == "img":
            payload = re.sub(r"\]\(\.\./assets/figs/", f"]({BASE}/figs/", payload)
            zh_out.append(payload)
            continue
        zh_out.append(payload)
    # 中文 refs 标题已写入; 英文按锚点顺序生成
    en_final = list(en_out)
    for kind, payload in blocks:
        if kind == "h1":
            continue
        if kind == "h2":
            a, es = h2_map[payload]
            if a == refs_anchor:
                en_final.append("")
                en_final.append(f"## References {{#{refs_anchor}}}")
                got = [p for es in endata["sections"] for p in es.get("_refs", [])]
                if not got:
                    got = ref_paras(cnum)
                for p in got:
                    en_final.append("")
                    en_final.append(md_escape_para(p))
                continue
            if es is not None:
                en_final.append("")
                en_final.append(f"## {md_heading(es['title'])} {{#{a}}}")
                for p in es["paras"]:
                    en_final.append("")
                    en_final.append(md_escape_para(p))
            else:
                en_final.append("")
                en_final.append(f"## {md_heading(payload)} {{#{a}}}")
            continue
    zh_md = "\n".join(fm_zh) + "\n" + "\n\n".join(x.strip("\n") for x in zh_out if x.strip()) + "\n"
    # pandoc 把图片包在 <p> 里导致 markdown 不解析: 解包并改写为站点绝对路径
    zh_md = re.sub(r"<p>!\[([^\]]*)\]\((?:\.\./)+assets/figs/([^)]+)\)</p>",
                   lambda m: f"![{m.group(1)}]({BASE}/figs/{m.group(2)})", zh_md)
    # 个别 figure 块的图片引用在 pandoc 转换中丢失(源 tikz 存在且已渲染): 定点补回
    FIX_FIGS = {  # figure id → 渲染好的 png 文件名
        "fig:18-15": "18-graph-traversal-8.png",
        "fig:23-15": "23-multi-gpu-programming-6.png",
        "fig:23-20": "23-multi-gpu-programming-7.png",
    }
    for fid, png in FIX_FIGS.items():
        tag = f'<figure id="{fid}">'
        if tag in zh_md and f"{BASE}/figs/{png}" not in zh_md:
            zh_md = zh_md.replace(tag, tag + f"\n<img src=\"{BASE}/figs/{png}\" alt=\"本章图示\" loading=\"lazy\" />", 1)
        elif tag in zh_md:
            pass
    # HTML figure 块内 markdown 不被 goldmark 解析 → 一律输出 <img>
    zh_md = re.sub(r"!\[([^\]]*)\]\(" + re.escape(BASE) + r"/figs/([^)]+)\)",
                   lambda m: f'<img src="{BASE}/figs/{m.group(2)}" alt="{m.group(1)}" loading="lazy" />', zh_md)
    en_md = "\n".join(fm_en) + "\n" + "\n\n".join(x.strip("\n") for x in en_final if x.strip()) + "\n"
    # 全书插图统一用原书 PDF 截图
    zh_md = re.sub(r'<figure id="fig:([0-9A-Za-z]+-[0-9A-Za-z]+)">.*?</figure>', swap_fig_block, zh_md, flags=re.S)
    en_md = swap_en_figs(en_md)
    slug = f"{weight:02d}-ch{cnum.lower()}"
    open(os.path.join(OUT, f"{slug}.md"), "w", encoding="utf-8").write(zh_md)
    open(os.path.join(OUT, f"{slug}.en.md"), "w", encoding="utf-8").write(en_md)
    return slug, len(en_final)

def md_heading(t):
    return re.sub(r"\s*\{#[^}]*\}\s*", "", t).strip()

_ref_cache = {}
def ref_paras(cnum):
    """提取本章英文 References 段: 位于最后一个节之后到下一章前的 [n] 开头段。"""
    if cnum in _ref_cache: return _ref_cache[cnum]
    raw = open("/tmp/pmpp_en.txt", encoding="utf-8").read().replace("\u00ad\n", "").replace("\u00ad", "")
    lines = raw.split("\n")
    endata = json.load(open(f"/tmp/en_{cnum}.json"))
    lines_pos = [s["line"] for s in endata["sections"] if s.get("line")]
    if not lines_pos:
        _ref_cache[cnum] = []; return []
    # 用 en json 未存行号? sections 有 line 字段(写入了)
    start = max(lines_pos)
    out = []
    started = False
    for i in range(start, min(len(lines), start + 4000)):
        t = lines[i].strip()
        if re.match(r"^References\s*(\.|$)", t):
            started = True; continue
        if not started:
            continue
        if re.match(r"^\[\d+\]", t):
            out.append(t)
        elif out and t and not re.match(r"^\s*$", t) and len(t) > 30 and not re.match(r"^(Copyright|Programming Massively|https://doi|\d{1,4}$|CHAPTER|APPENDIX|CONTENTS)", t):
            out[-1] = out[-1] + " " + t
        elif re.match(r"^(CHAPTER|APPENDIX)\b", t) or t == "CONTENTS":
            break
    _ref_cache[cnum] = out
    return out

if __name__ == "__main__":
    os.makedirs(STATIC_FIGS, exist_ok=True)
    for f in os.listdir(os.path.join(ROOT, "docs", "assets", "figs")):
        shutil.copy(os.path.join(ROOT, "docs", "assets", "figs", f), os.path.join(STATIC_FIGS, f))
    # 预扫描: 各章第一段签名(用于跨章误归属去重) — 简单起见在生成后做文件级清洗
    slugs = []
    for i, (name, title) in enumerate(CHAPTERS):
        try:
            slug, n = build_chapter(name, title, i)
            slugs.append(slug)
            print("OK", slug)
        except Exception as e:
            print("FAIL", name, e)
    print("total:", len(slugs))
