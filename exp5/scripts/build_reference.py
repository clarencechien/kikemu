#!/usr/bin/env python3
"""exp5 正解建置步驟 2:兩路 Gemini 合併 + 分歧落檔待裁決(沿用 exp2 協定)。

與 exp2 相同的部分(判準已凍結,不改):
  - base = gm35 與 gm36 的 token 級一致處
  - 分歧預設取 gm35,人工裁決寫進 reference/overrides.json 覆蓋
  - SM 不投票,只當第三意見

exp5 新增的部分(handoff-v6 §4 的省力設計):
  - review.md 把分歧**分成兩區**:含拉丁字母的(必看)與純中文的(可略)。
    核心指標是英文術語召回,純中文的用字差異不影響它,人力該花在前者。
  - 每則分歧附上 SM 在同一段的讀法當第三意見,裁決時不用回頭翻檔案。
"""
import difflib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VER = ROOT / "corpus" / "verify"
REF = ROOT / "corpus" / "reference"

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*|\d+(?:\.\d+)?|[㐀-鿿]")
HAS_LATIN = re.compile(r"[A-Za-z]")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def join(toks: list[str]) -> str:
    out = []
    for t in toks:
        if out and t[0].isascii() and out[-1][-1].isascii():
            out.append(" ")
        out.append(t)
    return "".join(out)


def sm_align(sm_toks, a_toks):
    """gm35 → SM 的 token 位置對照表。用整篇 SequenceMatcher 對齊,
       不要用『前 N 個 token 完全相同』去定位——兩個引擎的中文用字幾乎不會
       整段一樣,嚴格比對的結果是永遠找不到(第一版就是這樣全空的)。"""
    m = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_toks, sm_toks,
                                                      autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                m[i1 + k] = j1 + k
    return m


def sm_context(sm_toks, amap, i1, i2, pad=3):
    """SM 在同一段的讀法(用對齊表把 gm35 的位置換算到 SM 的位置)"""
    lo = next((amap[i] for i in range(i1 - 1, max(-1, i1 - 12), -1) if i in amap), None)
    hi = next((amap[i] for i in range(i2, min(len(amap) + 99, i2 + 12)) if i in amap), None)
    if lo is None and hi is None:
        return ""
    lo = lo if lo is not None else max(0, hi - 6)
    hi = hi if hi is not None else min(len(sm_toks), lo + 6)
    return join(sm_toks[max(0, lo - pad + 1): hi + pad])


def main():
    REF.mkdir(parents=True, exist_ok=True)
    overrides = json.loads((REF / "overrides.json").read_text()) if (REF / "overrides.json").exists() else {}
    segs = [p["seg"] for p in json.loads((ROOT / "corpus" / "picks.json").read_text())]

    latin_sec, cjk_sec = [], []
    stats = []
    for seg in segs:
        a = tokens((VER / f"{seg}.gm35.txt").read_text())
        b = tokens((VER / f"{seg}.gm36.txt").read_text())
        sm = tokens((VER / f"{seg}.sm.txt").read_text())
        amap = sm_align(sm, a)
        merged, diffs, n_latin = [], 0, 0
        latin_sec.append(f"\n## {seg}\n")
        cjk_sec.append(f"\n## {seg}\n")
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                merged.extend(a[i1:i2])
                continue
            diffs += 1
            ka, kb = join(a[i1:i2]), join(b[j1:j2])
            key = f"{seg}:{diffs}"
            ruling = overrides.get(key)
            merged.extend(tokens(ruling) if ruling is not None else a[i1:i2])
            chosen = f"OVERRIDE={ruling!r}" if ruling is not None else "gm35(預設)"
            line = (f"- [{key}] gm35='{ka}' | gm36='{kb}'"
                    f" | sm≈'{sm_context(sm, amap, i1, i2)}' -> {chosen}\n")
            if HAS_LATIN.search(ka) or HAS_LATIN.search(kb):
                n_latin += 1
                latin_sec.append(line)
            else:
                cjk_sec.append(line)
        (REF / f"{seg}.txt").write_text(join(merged), encoding="utf-8")
        stats.append((seg, len(merged), diffs, n_latin))
        print(f"{seg}: {len(merged)} tokens、分歧 {diffs} 處,其中含拉丁 {n_latin} 處")

    head = ["# exp5 正解裁決表\n\n",
            "裁決寫進 `reference/overrides.json`(key = 分歧編號,value = 正確文字),再重跑本腳本。\n",
            "未裁決者一律取 gm35。SM 只是第三意見,沒有投票權。\n\n",
            "| 段 | token | 分歧 | 含拉丁(**要看的**) |\n|---|---|---|---|\n"]
    head += [f"| {s} | {t} | {d} | **{l}** |\n" for s, t, d, l in stats]
    head += [f"\n合計 {sum(x[2] for x in stats)} 處分歧,其中 **{sum(x[3] for x in stats)} 處含拉丁字母**。\n"]
    (REF / "review.md").write_text(
        "".join(head) + "\n---\n\n# A. 含拉丁字母的分歧(必須人工裁決)\n"
        + "".join(latin_sec) + "\n---\n\n# B. 純中文分歧(可略過)\n" + "".join(cjk_sec),
        encoding="utf-8")


if __name__ == "__main__":
    main()
