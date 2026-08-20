#!/usr/bin/env python3
"""純中文 20 秒視窗的 CER 計分。

這些視窗刻意挑成沒有英文,所以**不能用術語召回**——指標是 token 級 CER。
簡繁折疊與 exp5 主計分一致(OpenCC S2T),否則 SM 出簡體會被無謂懲罰。
"""
import difflib
import json
import re
from pathlib import Path

from opencc import OpenCC

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "corpus" / "zh_windows"
RAW = ROOT / "results" / "raw_zh"
S2T = OpenCC("s2t")
TOKEN = re.compile(r"[0-9A-Za-z]+|[^\s0-9A-Za-z]")
ARMS = [("Zg", "Gemini 3.5 批次"), ("Zsm", "Speechmatics 批次"),
        ("Zgma", "Gemma 4 12B"), ("Zgma_e4b", "Gemma 4 E4B"),
        ("Zgma_e2b", "Gemma 4 E2B"),
        ("Zgma_e4b_greedy", "Gemma 4 E4B(貪婪)"),
        ("Zgma_e2b_greedy", "Gemma 4 E2B(貪婪)"),
        ("Zgma_greedy", "Gemma 4 12B(貪婪)"),
        ("Zbrz", "Breeze ASR 25"), ("Zsc", "ElevenLabs Scribe v2")]

# handoff-v10 §4b(看到分數之前寫死):Scribe 會輸出 audio event 標記(如 [笑声])。
# 那是註記不是轉寫,沒有其他 arm 會產生它,不移除等於平白讓它的 CER 變差。
AUDIO_EVENT = re.compile(r"[\[［][^\]］]{0,12}[\]］]")


def toks(t: str) -> list[str]:
    t = AUDIO_EVENT.sub("", t or "")
    return [x.lower() for x in TOKEN.findall(S2T.convert(t))]


def cer(ref: str, hyp: str) -> float:
    r, h = toks(ref), toks(hyp)
    if not r:
        return float("nan")
    ops = difflib.SequenceMatcher(None, r, h, autojunk=False).get_opcodes()
    dist = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != "equal")
    return dist / len(r)


def main() -> None:
    man = json.loads((WIN / "manifest.json").read_text())["windows"]
    rows, out = [], {}
    for arm, label in ARMS:
        d = RAW / arm
        if not d.exists():
            continue
        vals, lens = [], []
        for stem, info in man.items():
            f = d / f"{stem}.json"
            if not f.exists():
                continue
            hyp = json.loads(f.read_text())["transcript"]
            c = cer(info["reference"], hyp)
            vals.append(c)
            lens.append(len(toks(hyp)) / max(len(toks(info["reference"])), 1))
            rows.append({"arm": arm, "window": stem, "cer": round(c, 4),
                         "len_ratio": round(lens[-1], 3)})
        if vals:
            out[arm] = {"label": label, "n": len(vals),
                        "cer_mean": round(sum(vals) / len(vals), 4),
                        "cer_max": round(max(vals), 4),
                        "len_ratio_mean": round(sum(lens) / len(lens), 3)}
    (ROOT / "results" / "zh_windows_scores.json").write_text(
        json.dumps({"per_window": rows, "summary": out}, ensure_ascii=False, indent=1))

    print(f"純中文 20 秒視窗({len(man)} 個,單發不切塊,CER 越低越好)\n")
    print(f"{'arm':<22}{'n':>4}{'CER 平均':>10}{'CER 最差':>10}{'長度比':>9}")
    for arm, s in sorted(out.items(), key=lambda kv: kv[1]["cer_mean"]):
        print(f"{s['label']:<22}{s['n']:>4}{s['cer_mean']:>10.3f}"
              f"{s['cer_max']:>10.3f}{s['len_ratio_mean']:>9.2f}")
    print("\n→ results/zh_windows_scores.json")


if __name__ == "__main__":
    main()
