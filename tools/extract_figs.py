#!/usr/bin/env python3
"""从原书 PDF 自动截取所有 FIGURE 原始图。

定位: 页内 "FIGURE N.M" 题注文本块 → 其上方紧邻的矢量图/位图区域聚类 → clip 高清渲染。
输出: static/figs/orig/fig-<章>-<编号>.png + /tmp/pmpp_figmap.json(编号→文件/页码)
"""
import pymupdf, re, json, os, sys

PDF = "/home/pig/3-github_CodingPracticeGroup/my-cuda-for-llm/pdf/_OceanofPDF.com_Programming_Massively_Parallel_Processors_5E_-_Wen-mei_W_Hwu.pdf"
OUTDIR = "static/figs/orig"
HEADER_Y = 118          # 页眉带下边界(pt), 此线以上的绘图视为页眉
GAP = 55                # 图形聚类允许的最大纵向间隙(pt)
FIG_RE = re.compile(r"^FIGURE\s+(\d{1,2}|[A-C])[.\-](\d{1,2}[a-z]?)\s*$", re.M)

def chapter_slug(c):
    return c.lower()

def collect_figures(doc):
    figs = {}
    for pno in range(len(doc)):
        pg = doc[pno]
        blocks = pg.get_text("blocks")
        caps = []
        for b in blocks:
            t = b[4].strip()
            m = re.fullmatch(r"FIGURE\s+((?:\d{1,2}|[A-C])[.\-]\d{1,2}[a-z]?)", t)
            if m:
                caps.append((m.group(1), pymupdf.Rect(b[:4])))
        if not caps:
            continue
        # 候选绘图矩形
        draws = []
        for d in pg.get_drawings():
            r = pymupdf.Rect(d["rect"])
            if r.width < 4 or r.height < 3:
                continue
            if r.y1 < HEADER_Y:
                continue
            if r.width > pg.rect.width - 20 and r.height > 3 and r.y1 < HEADER_Y + 12:
                continue
            draws.append(r)
        for im in pg.get_images(full=True):
            for r in pg.get_image_rects(im[0]):
                if r.height > 5 and r.y1 >= HEADER_Y:
                    draws.append(pymupdf.Rect(r))
        caps.sort(key=lambda c: c[1].y0)
        for name, crect in caps:
            anchor = crect.y0
            area = None
            changed = True
            while changed:
                changed = False
                band = pymupdf.Rect(0, anchor - GAP, pg.rect.width, anchor + 2)
                for r in draws:
                    if r.y0 >= crect.y0:        # 题注行内/下方的图元(边框线)
                        continue
                    if area is not None and area.contains(r):
                        continue
                    if r.intersects(band) or (area is not None and r.intersects(area)):
                        area = r if area is None else area | r
                        anchor = area.y0
                        changed = True
            if area is None:
                figs.setdefault(name, {"page": pno + 1, "png": None, "bbox": None})
                continue
            area = pymupdf.Rect(max(area.x0 - 4, 0), max(area.y0 - 6, 0),
                                min(area.x1 + 4, pg.rect.width), min(area.y1 + 4, crect.y0 - 1))
            figs.setdefault(name, {"page": pno + 1, "bbox": list(area), "png": None})
    return figs

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    doc = pymupdf.open(PDF)
    figs = collect_figures(doc)
    ok = miss = 0
    zoom = 3.0
    for name, info in sorted(figs.items()):
        m = re.fullmatch(r"([0-9A-C]+)[.\-](\d{1,2}[a-z]?)", name)
        ch, num = m.group(1), m.group(2)
        png = f"fig-{ch.lower()}-{num.lower()}.png"
        if info["bbox"] is None:
            miss += 1
            print(f"NObbox {name} p{info['page']}")
            continue
        pg = doc[info["page"] - 1]
        clip = pymupdf.Rect(*info["bbox"])
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        path = os.path.join(OUTDIR, png)
        pix.save(path)
        info["png"] = png
        ok += 1
    json.dump(figs, open("/tmp/pmpp_figmap.json", "w"))
    print(f"captions={len(figs)} ok={ok} no_bbox={miss}")
    # 章分布
    from collections import Counter
    print(Counter(k.split('.')[0] for k in figs))

if __name__ == "__main__":
    main()
