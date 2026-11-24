#!/usr/bin/env python3
"""修正 PDF 文本层的版面塌陷: 分式/上标/下标错位成句中孤行数字或插入词中。

每处修正依据: 对应书页 200dpi 渲染图像直读核对(2026-11-23), 见会话记录。
规则格式: (file, 正则, 替换)。全部 MISS 会显式报告。可重复运行(已修复则无匹配)。
"""
import json, re, sys, glob, os

RULES = [
    # ---- ch10 §10.4: 分式 31/32 的分子成孤行, 句子错位 (印刷页 230-231) ----
    ("/tmp/en_10.json", r"active, of the execution resources\. wasting 32 If",
     r"active, wasting \(\frac{31}{32}\) of the execution resources. If"),
    ("/tmp/en_10.json", r"would have only one of the execution resource\. active thread, exhibiting control divergence and wasting 32 During",
     r"would have only one active thread, exhibiting control divergence and wasting \(\frac{31}{32}\) of the execution resource. During"),
    ("/tmp/en_10.json", r"only Warp 0 would be active, exhibiting control divergence of the execution resource\. and wasting 32 In general",
     r"only Warp 0 would be active, exhibiting control divergence and wasting \(\frac{31}{32}\) of the execution resource. In general"),
    # ---- ch5 §5.6: TILE_WIDTH^2 上标 2 错位 (印刷页 116) ----
    ("/tmp/en_5.json", r"every block has TILE_WIDTH2 threads, and uses TILE_WIDTH2 \*4 B of shared memory for Mds and TILE_WIDTH \*4 B of shared memory for Nds\. Thus, the thread block uses an average of \(TILE_WIDTH2 \*4 B \+ TILE_WIDTH2 \*4 B\)/\(TILE_WIDTH2 threads\) = 8 B/thread",
     r"every block has \(\texttt{TILE\\_WIDTH}^2\\) threads, and uses \(\texttt{TILE\\_WIDTH}^2\\times 4\\) B of shared memory for \\(M_{ds}\\) and \(\texttt{TILE\\_WIDTH}^2\\times 4\\) B of shared memory for \\(N_{ds}\\). Thus, the thread block uses an average of \\((\\texttt{TILE\\_WIDTH}^2\\times 4\\ \\text{B}+\\texttt{TILE\\_WIDTH}^2\\times 4\\ \\text{B})/(\\texttt{TILE\\_WIDTH}^2\\ \\text{threads})=8\\) B/thread"),
    ("/tmp/en_5.json", r"the size of Mds and Nds is set to be TILE_WIDTH2 elements",
     r"the size of \(M_{ds}\) and \(N_{ds}\) is set to be \(\\texttt{TILE\\_WIDTH}^2\\) elements"),
    # ---- ch11 §11.8: 内联公式 (3.35e12)/8 等错位 (印刷页 272) ----
    ("/tmp/en_11.json", r"the arithmetic \(N −1\) intensity of the scan operation is 4·N \+4·N ≈ 8 OP/B",
     r"the arithmetic intensity of the scan operation is \\(\\frac{(N-1)}{4\\cdot N+4\\cdot N}\\approx\\frac{1}{8}\\) OP/B"),
    ("/tmp/en_11.json", r"thereby requiring a computation to perform 66\.9 = 20\.0 FLOP/B to be compute-bound\. Clearly, the arithmetic intensity of at least 3\.35 the scan operation",
     r"thereby requiring a computation to perform at least \\(\\frac{66.9}{3.35}=20.0\\) FLOP/B to be compute-bound. Clearly, the arithmetic intensity of the scan operation"),
    ("/tmp/en_11.json", r"the peak performance of a scan op12 eration is 3\.35×10 = 419 × 109 elements/s",
     r"the peak performance of a scan operation is \\(\\frac{3.35\\times10^{12}}{8}=419\\times10^{9}\\) elements/s"),
    ("/tmp/en_11.json", r"Achieving this 419 × 109 elements/s",
     r"Achieving this \\(419\\times10^{9}\\) elements/s"),
    # ---- ch11 §11.9: N/S 分式与 16B/12B 公式 (印刷页 273-274) ----
    ("/tmp/en_11.json", r"loads NS elements", r"loads \\(\\frac{N}{S}\\) elements"),
    ("/tmp/en_11.json", r"stores NS elements", r"stores \\(\\frac{N}{S}\\) elements"),
    ("/tmp/en_11.json", r"accesses a total of N \+ N \+ NS \+ NS \+ N \+ N = \(4 \+ S2 \) · N elements, or \(16 \+ S8 \) · N bytes\. For a very large S where S8 is negligible, the three-kernel approach accesses ≈ 16 · N bytes",
     r"accesses a total of \\(N+N+\\frac{N}{S}+\\frac{N}{S}+N+N=\\left(4+\\frac{2}{S}\\right)\\cdot N\\) elements, or \\(\\left(16+\\frac{8}{S}\\right)\\cdot N\\) bytes. For a very large \\(S\\) where \\(\\frac{8}{S}\\) is negligible, the three-kernel approach accesses \\(\\approx 16\\cdot N\\) bytes"),
    ("/tmp/en_11.json", r"the peak performance of this approach is 3\.35×10 = 209 × 109 elements/s\. This peak performance is half the peak performance of an ideal scan implementation which was found in Section 11\.8 to be 419×109 elements/s",
     r"the peak performance of this approach is \\(\\frac{3.35\\times10^{12}}{16}=209\\times10^{9}\\) elements/s. This peak performance is half the peak performance of an ideal scan implementation which was found in Section 11.8 to be \\(419\\times10^{9}\\) elements/s"),
    ("/tmp/en_11.json", r"accesses a total of N \+ NS \+ NS \+ NS \+ N \+ N = \(3 \+ S3 \) · N elements, or \(12 \+ 12 S \) · N bytes\. For a very large S where S is negligible, the three-kernel approach with the reduce-scan-scan decomposition accesses ≈ 12 · N bytes",
     r"accesses a total of \\(N+\\frac{N}{S}+\\frac{N}{S}+\\frac{N}{S}+N+N=\\left(3+\\frac{3}{S}\\right)\\cdot N\\) elements, or \\(\\left(12+\\frac{12}{S}\\right)\\cdot N\\) bytes. For a very large \\(S\\) where \\(\\frac{12}{S}\\) is negligible, the three-kernel approach with the reduce-scan-scan decomposition accesses \\(\\approx 12\\cdot N\\) bytes"),
    ("/tmp/en_11.json", r"the peak performance of this approach is 3\.35×10 = 279 × 109 el12 ements/s\. This peak performance is two-thirds the 419 × 109 elements/s",
     r"the peak performance of this approach is \\(\\frac{3.35\\times10^{12}}{12}=279\\times10^{9}\\) elements/s. This peak performance is two-thirds the \\(419\\times10^{9}\\) elements/s"),
    # ---- 附录 A §A.3: 2^29 / 2^3 / 10^80 / 10^308 上标错位 (印刷页 590) ----
    ("/tmp/en_A.json", r"the largest rounding error for representing a number is reduced to 2129 of that",
     r"the largest rounding error for representing a number is reduced to \\(\\frac{1}{2^{29}}\\) of that"),
    ("/tmp/en_A.json", r"by 22 = 256 times", r"by \\(2^3=256\\) times"),
    ("/tmp/en_A.json", r"estimated at 1080 , whereas the double-precision format can represent up to 10308 !",
     r"estimated at \\(10^{80}\\), whereas the double-precision format can represent up to \\(10^{308}\\)!"),
    # ---- 附录 B §B.2.1: E=(y-t)^2/2 分式、∂y/∂w1 下标错位 (印刷页 608-609) ----
    ("/tmp/en_B.json", r"the error function value using E = \(y−t\) would be 2\. If",
     r"the error function value using \\(E=\\frac{(y-t)^2}{2}\\) would be 2. If"),
    ("/tmp/en_B.json", r"to illustrate the back2 propagation operation\. Assume error function E = \(y−t\) and that the back2 propagation is triggered",
     r"to illustrate the back-propagation operation. Assume error function \\(E=\\frac{(y-t)^2}{2}\\) and that the back-propagation is triggered"),
    ("/tmp/en_B.json", r"^∂y However, we face", r"However, we face"),
    ("/tmp/en_B.json", r"challenge with ∂w \. Note that the sign function is not a dif1 ferentiable",
     r"challenge with \\(\\frac{\\partial y}{\\partial w_1}\\). Note that the sign function is not a differentiable"),
    # ---- 跨列断词丢失空格 ----
    ("/tmp/en_11.json", r"shared memoryaccess", r"shared memory access"),
    ("/tmp/en_B.json", r"inference resultdiffers", r"inference result differs"),
    # ---- 词内空格断裂(kerning 异常, 全部人工确认) ----
    ("/tmp/en_11.json", r"\bna[\s\u00a0]ve\b", r"naive"),
    ("/tmp/en_11.json", r"\bop[\s\u00a0]eration\b", r"operation"),
    ("/tmp/en_11.json", r"\bel[\s\u00a0]ements\b", r"elements"),
    ("/tmp/en_16.json", r"\bze ros\b", r"zeros"),
    ("/tmp/en_16.json", r"\bW orksquare\b", r"Worksquare"),
]

def main():
    hit = miss = 0
    for f, pat, rep in RULES:
        d = json.load(open(f))
        changed = 0
        for sec in d.get("sections", []):
            new = []
            for p in sec.get("paras", []):
                p2 = re.sub(pat, rep, p)
                if p2 != p:
                    changed += 1
                new.append(p2)
            sec["paras"] = new
        if changed:
            json.dump(d, open(f, "w"), ensure_ascii=False)
            hit += 1
            print(f"HIT  {os.path.basename(f)} x{changed}  {pat[:60]}")
        else:
            miss += 1
            print(f"MISS {os.path.basename(f)}      {pat[:70]}")
    print(f"\nmatched={hit} missed={miss}")

if __name__ == "__main__":
    main()
