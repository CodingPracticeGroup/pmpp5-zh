#!/usr/bin/env python3
"""将 chapters/*.tex 中文译稿转换为 MkDocs 站点 Markdown。

流程: 预处理(引用编号/交叉引用/tikz 占位/自定义宏) -> pandoc gfm -> 后处理(公式定界符等)。
tikzpicture 单独用 xelatex standalone 渲染为 PNG 到 docs/assets/figs/。
"""
import os, re, sys, subprocess, hashlib, json, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_DIR = os.path.join(ROOT, "chapters")
DOCS = os.path.join(ROOT, "docs")
FIGS_OUT = os.path.join(DOCS, "assets", "figs")

CHAPTERS = [
    ("01-introduction", "第1章 引言"),
    ("02-heterogeneous-data-parallel-computing", "第2章 异构并行计算与 CUDA"),
    ("03-multidimensional-grids-and-data", "第3章 多维网格与数据"),
    ("04-compute-architecture-and-scheduling", "第4章 计算架构与调度"),
    ("05-memory-architecture-and-data-locality", "第5章 内存架构与数据局部性"),
    ("06-performance-considerations", "第6章 性能考量"),
    ("07-convolution", "第7章 卷积"),
    ("08-stencil-computation", "第8章 模板计算"),
    ("09-histogram", "第9章 直方图"),
    ("10-reduction", "第10章 归约"),
    ("11-scan", "第11章 扫描"),
    ("12-filter", "第12章 过滤器"),
    ("13-merge", "第13章 合并"),
    ("14-sorting", "第14章 排序"),
    ("15-advanced-matrix-multiplication", "第15章 高级矩阵乘法"),
    ("16-dynamic-programming-wavefront", "第16章 动态规划与波纹模式"),
    ("17-sparse-matrix-computation", "第17章 稀疏矩阵计算"),
    ("18-graph-traversal", "第18章 图遍历"),
    ("19-convolutional-neural-networks", "第19章 卷积神经网络"),
    ("20-large-language-models", "第20章 大语言模型"),
    ("21-electrostatic-potential-map", "第21章 静电势图"),
    ("22-algorithm-selection-problem-decomposition-and-problem-formulation", "第22章 算法选择、问题分解与问题表述"),
    ("23-multi-gpu-programming", "第23章 多 GPU 编程"),
    ("24-conclusion-and-future-outlook", "第24章 总结与未来展望"),
    ("appendix-a-numerical-considerations", "附录A 数值计算考量"),
    ("appendix-b-deep-learning-basics", "附录B 深度学习基础"),
    ("appendix-c-cuda-memories-address-spaces-and-pointers", "附录C CUDA 内存、地址空间与指针"),
]

def is_appendix(name):
    return name.startswith("appendix")

# ---------- tikz 渲染 ----------
TIKZ_TEMPLATE = r"""
\documentclass[border=4pt,tikz]{standalone}
\usepackage{ctex}
\usepackage{amsmath,amssymb}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,shapes.geometric,calc,fit,backgrounds,decorations.pathreplacing}
\newcommand{\code}[1]{\texttt{\detokenize{#1}}}
\begin{document}
%s
\end{document}
"""

def render_tikz(tex_body, out_png):
    """standalone 编译单个 tikzpicture -> pdf -> png。返回 True/False。"""
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    h = hashlib.md5(tex_body.encode()).hexdigest()[:12]
    work = os.path.join("/tmp", "tikz_" + h)
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "t.tex")
    with open(src, "w", encoding="utf-8") as f:
        f.write(TIKZ_TEMPLATE % tex_body)
    r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"],
                       cwd=work, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(os.path.join(work, "t.pdf")):
        return False
    r = subprocess.run(["pdftoppm", "-png", "-r", "180", "-singlefile", "t.pdf", "t"],
                       cwd=work, capture_output=True)
    png = os.path.join(work, "t.png")
    if not os.path.exists(png):
        return False
    shutil.copy(png, out_png)
    return True

# ---------- LaTeX 预处理 ----------
CJK = r"[\u4e00-\u9fff\u3400-\u4dbf，。、；：（）“”‘’《》【】—…·\-0-9A-Za-z]"
CJK_END = r"[\u4e00-\u9fff\u3400-\u4dbf，。、；：（）“”‘’《》【】—…·]"
def merge_wraps(tex):
    """保守合并中文硬换行: 仅当上一行以 CJK/中文标点结尾且下一行以 CJK 开头时合并。
    lstlisting/verbatim 环境内不处理。"""
    out = []
    in_lst = False
    prev_keep = None  # 上一行(可继续被追加)
    def is_special(l):
        s = l.strip()
        return s.startswith(r"\begin{lstlisting}") or s.startswith(r"\begin{verbatim}") or s.startswith(r"\begin{lstlisting}")
    def is_lst_end(l):
        s = l.strip()
        return s.startswith(r"\end{lstlisting}") or s.startswith(r"\end{verbatim}")
    pending = None
    for l in tex.split("\n"):
        if pending is None:
            if is_special(l) or in_lst:
                out.append(l)
                if is_lst_end(l): in_lst = False
                elif is_special(l): in_lst = True
                continue
            pending = l
            continue
        # pending 存在: 判断是否合并
        if (not in_lst) and pending and re.search(CJK_END + r"$", pending) and re.match(r"^\s*" + CJK_END, l):
            pending = re.sub(r"\s+$", "", pending) + l.lstrip()
            continue
        out.append(pending)
        pending = None
        # 重新处理当前行
        if is_special(l) or in_lst:
            out.append(l)
            if is_lst_end(l): in_lst = False
            elif is_special(l): in_lst = True
        else:
            pending = l
    if pending is not None:
        out.append(pending)
    return "\n".join(out)

def preprocess(name, tex):
    tex = merge_wraps(tex)
    chap_num = name[:2]
    is_app = is_appendix(name)
    app_letter = chap_num.replace("ap", "").strip("-") if is_app else None

    # 1. tikzpicture -> 图片占位(稍后渲染 PNG)
    fig_idx = 0
    figs_to_render = []
    def tikz_replace(m):
        nonlocal fig_idx
        fig_idx += 1
        png_name = f"{name}-{fig_idx}.png"
        figs_to_render.append((png_name, m.group(0)))
        return f"\n\n![本章图示 {fig_idx}](../assets/figs/{png_name})\n"
    tex = re.sub(r"(\\begin\{tikzpicture\}.*?\\end\{tikzpicture\})", tikz_replace, tex, flags=re.S)

    # 2. 参考文献: 提取 bibitem -> 编号映射
    bibkeys = re.findall(r"\\bibitem\{([^}]+)\}", tex)
    bibmap = {k: i + 1 for i, k in enumerate(bibkeys)}
    def cite_repl(m):
        keys = [k.strip() for k in m.group(1).split(",")]
        nums = [str(bibmap.get(k, "?")) for k in keys]
        return "[" + ",".join(nums) + "]"
    tex = re.sub(r"\\cite[tp]?\{([^}]+)\}", cite_repl, tex)

    # 3. 交叉引用: 解析 label 与计数器顺序
    labels = {}
    sec = 0; ssec = 0; s3 = 0; fig = 0; tab = 0; eqn = 0; thm = 0
    lines = tex.split("\n")
    out_lines = []
    i = 0
    while i < len(lines):
        l = lines[i]
        s = l.strip()
        m = re.match(r"\\section\*?\{([^}]*)\}", s)
        if m:
            sec += 1; ssec = 0; s3 = 0
        elif re.match(r"\\subsection\*?\{", s):
            ssec += 1; s3 = 0
        elif re.match(r"\\subsubsection\*?\{", s):
            s3 += 1
        elif s.startswith(r"\begin{figure}") or s.startswith(r"\begin{figure*}"):
            fig += 1
        elif s.startswith(r"\begin{table}") or s.startswith(r"\begin{table*}"):
            tab += 1
        elif s.startswith(r"\begin{equation}"):
            eqn += 1
        # 记录该行之后出现的 \label{}
        for lab in re.findall(r"\\label\{([^}]+)\}", s):
            if lab.startswith("fig:"): labels[lab] = ("fig", f"{chap_num}.{fig}" if not is_app else f"{app_letter}.{fig}")
            elif lab.startswith("tab:"): labels[lab] = ("tab", f"{chap_num}.{tab}" if not is_app else f"{app_letter}.{tab}")
            elif lab.startswith("sec:") or lab.startswith("subsec:"):
                labels[lab] = ("sec", f"{chap_num}.{sec}" if not is_app else f"{app_letter}.{sec}")
            else: labels[lab] = ("x", lab)
        i += 1
    def ref_repl(m):
        full, cmd, lab = m.group(0), m.group(1), m.group(2)
        kind, t = labels.get(lab, ("x", lab))
        # 若调用前已有人工"图/表"前缀, 只输出编号, 避免"图 图 10.1"
        prefix = full[:full.rindex("\\")]
        pre_tail = prefix[-3:] if prefix else ""
        pref = ""
        if kind == "fig" and not re.search(r"图\s*$", prefix): pref = "图 "
        elif kind == "tab" and not re.search(r"表\s*$", prefix): pref = "表 "
        elif kind == "sec": pref = ""
        return pref + t
    tex = re.sub(r"\\(auto|eq|pag|c)?ref\{([^}]+)\}", lambda m: ref_repl(m), tex)

    # 4. chapterbibliography 环境 -> 普通 enumerate
    tex = re.sub(r"\\begin\{chapterbibliography\}\{[^}]*\}",
                 lambda m: "\n\\section*{本章参考文献}\n\\begin{enumerate}", tex)
    tex = tex.replace(r"\end{chapterbibliography}", "\\end{enumerate}")

    # 5. 自定义宏: \code{} -> \texttt{}
    tex = re.sub(r"\\code\{", "\\\\texttt{", tex)
    # 占位图命令(如有)
    tex = re.sub(r"\\placeholderfigure\{([^}]*)\}\{([^}]*)\}",
                 lambda m: f"\n\n> [图占位] {m.group(1)}：{m.group(2)}\n", tex)
    return tex, figs_to_render

# ---------- pandoc ----------
def pandoc(tex, srcname):
    p = subprocess.run(["pandoc", "-f", "latex", "-t", "gfm",
                        "--wrap=none", "--strip-comments", str(srcname)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[:500])
    return p.stdout

# ---------- 后处理 ----------
def post(name, md):
    # pandoc gfm 将数学输出为 $`...`$ / $$`...`$$ → 转回 \(\) 与 \[\] 供 arithmatex generic
    md = re.sub(r"\$\$(.+?)\$\$", lambda m: r"\[" + m.group(1).strip() + r"\]", md, flags=re.S)
    md = re.sub(r"\$`(.+?)`\$", lambda m: r"\(" + m.group(1) + r"\)", md, flags=re.S)
    return md

def build_one(name, title, render_figs=True):
    tex = open(os.path.join(CH_DIR, name + ".tex"), encoding="utf-8").read()
    tex_pp, figs = preprocess(name, tex)
    tmp = f"/tmp/{name}.pp.tex"
    open(tmp, "w", encoding="utf-8").write(tex_pp)
    md = pandoc(tex_pp, tmp)
    md = post(name, md)
    # 首行确保是 H1
    lines = md.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("#"):
            lines[i] = "# " + re.sub(r"^#+\s*", "", l)
            break
    md = "\n".join(lines)
    out_dir = os.path.join(DOCS, "chapters")
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, f"{name}.md"), "w", encoding="utf-8").write(md)
    # 渲染 tikz
    fig_ok = fig_fail = 0
    if render_figs:
        for png_name, tikz_src in figs:
            out = os.path.join(FIGS_OUT, png_name)
            if render_tikz(tikz_src, out): fig_ok += 1
            else:
                fig_fail += 1
                print(f"  ! tikz 渲染失败 {png_name}", file=sys.stderr)
    return fig_ok, fig_fail

if __name__ == "__main__":
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    total_ok = total_fail = 0
    for name, title in CHAPTERS:
        if only and name not in only:
            continue
        try:
            ok, fail = build_one(name, title)
            total_ok += ok; total_fail += fail
            print(f"OK {name} (figs ok={ok} fail={fail})")
        except Exception as e:
            print(f"FAIL {name}: {e}", file=sys.stderr)
    print(f"total figs ok={total_ok} fail={total_fail}")
