#!/usr/bin/env python3
"""handoff-v9 §4b L:做出「只差標點」的轉寫變體,驗證侷限 16p。

§3F.4b 觀察到同一個譯者吃 Breeze(無標點)的轉寫會留下 `data`,
吃 E4B(有標點)的會譯成「數據」。但那兩份轉寫**在標點以外還差著召回與用詞**。
這支把變因單獨拆出來:**只動標點,一個字都不改。**

兩個方向都是**純機械操作,不經過任何模型**——用 LLM 加標點會連帶改寫用詞,
那就又多一個混淆變因,整格就白做了:

    Xbrz_punct        Breeze 的 131 個 segment 是它**自己的輸出**(附時間戳),
                      依 segment 間隔插標點:> 0.5s → 「。」,否則 → 「,」
    Xgma_e4b_nopunct  regex 刪掉 E4B 轉寫的標點

**驗證方式(不是靠肉眼)**:兩個變體都會斷言「把標點刪掉之後,
與來源轉寫逐字元相同」——這是「內容沒被動到」的機器證明。

輸出寫成正常的 arm 目錄,後續由 `run_chain_translate.py` 接第二跳。

用法:
    python3 exp5/scripts/make_form_variants.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results" / "raw"
CELLS = [(s, c) for s in ("T1", "T2", "T3") for c in ("M0", "M3")]

# 中英文標點與空白。刪掉這些之後兩個變體要與來源逐字元相同。
PUNCT = re.compile(r"[、。，,．.!?！？・…‥「」『』（）()〈〉《》【】〔〕—ー–\-~〜"
                   r"：；:;\"'“”‘’`\s]+")
GAP_SENTENCE = 0.5      # segment 間隔超過這個秒數就當句號
LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9''\-]*")   # 一段拉丁字母(含縮寫的 ' 與 -)
SPACES = re.compile(r"[ \t\u3000]+")


def strip_all(t: str) -> str:
    """刪掉標點與空白——兩個變體的『內容沒被動到』就用這個當機器證明。"""
    return PUNCT.sub("", t or "")


def add_space_around_latin(t: str) -> str:
    """在每一段拉丁字母前後補半形空格(handoff-v9 §4c M)。只加空白,不改字。"""
    out = LATIN_RUN.sub(lambda m: f" {m.group(0)} ", t or "")
    return SPACES.sub(" ", out).strip()


def remove_spaces(t: str) -> str:
    """把所有空白刪掉(handoff-v9 §4c M)。只刪空白,不改字。"""
    return SPACES.sub("", t or "")


def strip_punct(t: str) -> str:
    return PUNCT.sub("", t or "")


def build_breeze_punct(stem: str) -> dict:
    """用 Breeze 自己的 segment 邊界插標點。不新增、不刪除任何字。"""
    src = json.loads((RAW / "Xbrz_auto" / f"{stem}.json").read_text())
    segs = src["segments"]
    parts, prev_end = [], None
    for s in segs:
        txt = (s["text"] or "").strip()
        if not txt:
            continue
        if parts:
            gap = (s["start"] or 0) - (prev_end or 0)
            parts.append("。" if gap > GAP_SENTENCE else "，")
        parts.append(txt)
        prev_end = s["end"]
    out = "".join(parts) + "。"
    # 機器證明:刪掉標點後與來源逐字元相同
    assert strip_punct(out) == strip_punct(src["transcript"]), \
        f"{stem}:插標點時內容被動到了"
    return {"transcript": out, "n_segments": len(segs),
            "source_arm": "Xbrz_auto", "n_punct_added": out.count("。") + out.count("，")}


def build_e4b_space(stem: str) -> dict:
    """E4B 轉寫 + 拉丁詞兩側補空格。內容不變(只多空白)。"""
    src = json.loads((RAW / "Xgma_e4b" / f"{stem}.json").read_text())
    out = add_space_around_latin(src["transcript"])
    assert strip_all(out) == strip_all(src["transcript"]), f"{stem}:加空格時內容被動到了"
    return {"transcript": out, "source_arm": "Xgma_e4b",
            "n_latin_runs": len(LATIN_RUN.findall(src["transcript"]))}


def build_breeze_nospace(stem: str) -> dict:
    """Breeze 轉寫 − 全部空白。內容不變(只少空白)。"""
    src = json.loads((RAW / "Xbrz_auto" / f"{stem}.json").read_text())
    out = remove_spaces(src["transcript"])
    assert strip_all(out) == strip_all(src["transcript"]), f"{stem}:刪空格時內容被動到了"
    return {"transcript": out, "source_arm": "Xbrz_auto",
            "n_spaces_removed": len(SPACES.findall(src["transcript"]))}


def build_e4b_nopunct(stem: str) -> dict:
    """把 E4B 轉寫的標點刪掉。不新增、不刪除任何字。"""
    src = json.loads((RAW / "Xgma_e4b" / f"{stem}.json").read_text())
    out = strip_punct(src["transcript"])
    assert out == strip_punct(src["transcript"])
    return {"transcript": out, "source_arm": "Xgma_e4b",
            "n_punct_removed": len(src["transcript"]) - len(out)}


def main() -> None:
    specs = [("Xbrz_punct", build_breeze_punct,
              "L:Breeze 轉寫 + 依自身 segment 邊界機械插入標點(內容未動)"),
             ("Xgma_e4b_nopunct", build_e4b_nopunct,
              "L:E4B 轉寫 − 標點(regex 刪除,內容未動)"),
             ("Xgma_e4b_sp", build_e4b_space,
              "M:E4B 轉寫 + 拉丁詞兩側補空格(只加空白)"),
             ("Xbrz_nosp", build_breeze_nospace,
              "M:Breeze 轉寫 − 全部空白(只刪空白)")]
    for arm, fn, note in specs:
        dst = RAW / arm
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "_meta.json").write_text(json.dumps(
            {"arm": arm, "derived": True, "note": note,
             "method": "純機械字串操作,不經過任何模型",
             "gap_sentence_sec": GAP_SENTENCE,
             "invariant": "strip_punct(輸出) == strip_punct(來源轉寫)"},
            ensure_ascii=False, indent=1))
        tot = 0
        for seg, cond in CELLS:
            stem = f"{seg}__{cond}"
            r = fn(stem)
            (dst / f"{stem}.json").write_text(json.dumps(
                {"arm": arm, "file": f"{stem}.wav", **r},
                ensure_ascii=False, indent=1))
            tot += 1
            print(f"  [{arm}] {stem}  {len(r['transcript'])}字 "
                  f"| {r['transcript'][:56]}")
        print(f"{arm}: {tot} 檔 → {dst}\n")


if __name__ == "__main__":
    main()
