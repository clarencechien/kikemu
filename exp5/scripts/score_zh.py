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
        ("Zbrz", "Breeze ASR 25"), ("Zsc", "ElevenLabs Scribe v2"),
        # handoff-v16 補測(2026-10-15):全部 language=zh、不開詞表;Z…GDtrans 直連,其餘經 OpenRouter
        ("ZORscribe_zh", "Scribe v2(經 OR)"), ("ZGDtrans_zh", "Gemini 3.5 Transcribe(直連)"),
        ("ZORmai2_zh", "MAI-Transcribe-2"), ("ZORmuse_zh", "Muse Voice 1.0"),
        ("ZORgptT_zh", "GPT-Transcribe"), ("ZORgrok_zh", "Grok STT(版本未定)"),
        ("ZORasm35_zh", "AssemblyAI U-3.5 Pro"), ("ZORasm36_zh", "AssemblyAI U-3.6 Pro"),
        ("ZORqwen17_zh", "Qwen3-ASR 1.7B"), ("ZORqwen06_zh", "Qwen3-ASR 0.6B"),
        ("ZORwhisperT_zh", "Whisper large-v3 turbo"), ("ZORnemo_zh", "Nemotron 3.5 ASR 0.6B")]

# handoff-v10 §4b(看到分數之前寫死):Scribe 會輸出 audio event 標記(如 [笑声])。
# 那是註記不是轉寫,沒有其他 arm 會產生它,不移除等於平白讓它的 CER 變差。
AUDIO_EVENT = re.compile(r"[\[［][^\]］]{0,12}[\]］]")


# 侷限 16r:參考沒有標點,而 tokenizer 把每個標點算一個 token → 會輸出標點的引擎平白被罰。
# 主數字維持原樣(`cer`);`cer_nopunct` 兩邊都只留中日韓字與英數,是報告 §3E 那張敏感度表的算法。
WORDISH = re.compile(r"[0-9a-z]+|[\u3400-\u9fff\uf900-\ufaff]")


def toks(t: str, nopunct: bool = False) -> list[str]:
    t = AUDIO_EVENT.sub("", t or "")
    out = [x.lower() for x in TOKEN.findall(S2T.convert(t))]
    return [x for x in out if WORDISH.fullmatch(x)] if nopunct else out


def cer(ref: str, hyp: str, nopunct: bool = False) -> float:
    r, h = toks(ref, nopunct), toks(hyp, nopunct)
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
        vals, vals_np, lens = [], [], []
        for stem, info in man.items():
            f = d / f"{stem}.json"
            if not f.exists():
                continue
            hyp = json.loads(f.read_text())["transcript"]
            c = cer(info["reference"], hyp)
            vals.append(c)
            vals_np.append(cer(info["reference"], hyp, nopunct=True))
            lens.append(len(toks(hyp)) / max(len(toks(info["reference"])), 1))
            rows.append({"arm": arm, "window": stem, "cer": round(c, 4), "cer_nopunct": round(vals_np[-1], 4),
                         "len_ratio": round(lens[-1], 3)})
        if vals:
            out[arm] = {"label": label, "n": len(vals),
                        "cer_mean": round(sum(vals) / len(vals), 4),
                        "cer_max": round(max(vals), 4),
                        "cer_nopunct_mean": round(sum(vals_np) / len(vals_np), 4),
                        "len_ratio_mean": round(sum(lens) / len(lens), 3)}
    (ROOT / "results" / "zh_windows_scores.json").write_text(
        json.dumps({"per_window": rows, "summary": out}, ensure_ascii=False, indent=1))

    print(f"純中文 20 秒視窗({len(man)} 個,單發不切塊,CER 越低越好)\n")
    print(f"{'arm':<22}{'n':>4}{'CER 平均':>10}{'CER 最差':>10}{'去標點':>9}{'長度比':>9}")
    for arm, s in sorted(out.items(), key=lambda kv: kv[1]["cer_mean"]):
        print(f"{s['label']:<22}{s['n']:>4}{s['cer_mean']:>10.3f}"
              f"{s['cer_max']:>10.3f}{s['cer_nopunct_mean']:>9.3f}{s['len_ratio_mean']:>9.2f}")
    print("\n→ results/zh_windows_scores.json")


if __name__ == "__main__":
    main()
