#!/usr/bin/env python3
"""Scoring: proper-noun recall, CER, Taiwan-term scan, latency, rewrite rate.

Judgment rules (frozen before any arm output was inspected):
  Recall STRICT   — the noun's reference surface appears verbatim in the
                    normalized hypothesis. Kana folded hiragana<->katakana
                    on both sides; punctuation stripped.
  Recall TOLERANT — STRICT, or the official reading (page gloss), or a
                    frozen alternate orthography (nouns.json, generated
                    before arms ran) appears. This neutralizes pure
                    orthography choices (kanji vs kana of the same name).
  CER             — Levenshtein / len(ref), both sides normalized (same
                    normalizer as reference construction).
  TW-term scan    — counts of PRC-usage terms and simplified-only chars in
                    the zh output.
  Latency (SM)    — per AddTranscript/AddPartialTranscript message:
                    t(message) - t(audio position `end_time` was pushed).
  Rewrite rate    — chars retracted across successive partials / final chars.
Statistical treatment: paired per-noun outcomes; segment-level cluster
bootstrap (10k) for deltas between arms.
"""
import bisect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_reference import normalize, cer

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
REF = ROOT / "corpus" / "reference"

ARMS_JA = {
    "A": "input_transcription",
    "C": "transcript",
    "Cplus": "transcript",
    "Cb": "transcript",       # Speechmatics batch, no dictionary
    "Cbplus": "transcript",   # Speechmatics batch + dictionary
    # exp3 preprocessing arms
    "Cbplus_pp_webrtc": "transcript",
    "Cbplus_pp_sg": "transcript",
    "Cbplus_pp_blend": "transcript",
    "A_pp_webrtc": "input_transcription",
    "A_pp_sg": "input_transcription",
    "A_pp_blend": "input_transcription",
    # exp4 directivity arms
    "Cbplus_dir_card": "transcript",
    "Cbplus_dir_conf": "transcript",
    "Cbplus_dir_lav": "transcript",
    "A_dir_conf": "input_transcription",
    "A_dir_lav": "input_transcription",
}
CONDS = ["N0", "N1", "N2", "N3", "N4"]

TW_BAD = ["视频", "視頻", "质量", "質量", "信息", "软件", "軟件", "网络", "網絡", "數據", "数据"]
SIMPLIFIED_RE = re.compile(r"[们过还这来对说时将实现发经动让门问间样体访见电买卖头华万与]")


def fold(s: str) -> str:
    """Normalize + fold hiragana to katakana for matching."""
    s = normalize(s, strip_gloss=False)
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def noun_hit(noun: dict, hyp_folded: str) -> tuple[bool, bool]:
    strict = fold(noun["surface"]) in hyp_folded
    forms = [noun["surface"], noun.get("reading", "")] + noun.get("alternates", [])
    tolerant = any(f and fold(f) in hyp_folded for f in forms)
    return strict, tolerant


def sm_latency(log: list) -> dict:
    """Latency stats from a Speechmatics runner log."""
    pushed = [(e["audio_s"], e["t"]) for e in log if e["kind"] == "audio"]
    xs = [p[0] for p in pushed]
    ts = [p[1] for p in pushed]

    def t_push(audio_time: float) -> float:
        i = bisect.bisect_left(xs, audio_time)
        return ts[min(i, len(ts) - 1)]

    fin, par = [], []
    for e in log:
        if e["kind"] == "AddTranscript" and e.get("end_time") is not None:
            fin.append(e["t"] - t_push(e["end_time"]))
        elif e["kind"] == "AddPartialTranscript" and e.get("end_time") is not None:
            par.append(e["t"] - t_push(e["end_time"]))
    out = {}
    for name, v in (("final", fin), ("partial", par)):
        if v:
            v = sorted(v)
            out[name + "_p50"] = round(v[len(v) // 2], 3)
            out[name + "_p90"] = round(v[int(len(v) * 0.9)], 3)
    return out


def sm_rewrite_rate(log: list) -> float:
    """Chars retracted across successive partials / total final chars."""
    retracted = 0
    finals = 0
    cur = ""
    for e in log:
        if e["kind"] == "AddPartialTranscript":
            new = e.get("transcript") or ""
            common = 0
            for a, b in zip(cur, new):
                if a != b:
                    break
                common += 1
            retracted += len(cur) - common
            cur = new
        elif e["kind"] == "AddTranscript":
            txt = e.get("transcript") or ""
            finals += len(txt)
            cur = ""
    return round(retracted / finals, 3) if finals else 0.0


def a_latency(log: list, audio_s: float) -> dict:
    feed_end = next((e["t"] for e in log if e["kind"] == "audioStreamEnd_sent"), None)
    first_zh = next((e["t"] for e in log if e["kind"] == "outputTranscription"), None)
    last_sub = max(
        (e["t"] for e in log if e["kind"] in ("outputTranscription", "inputTranscription", "audioOut")),
        default=None,
    )
    out = {}
    if first_zh is not None:
        out["first_zh_s"] = round(first_zh, 3)
    if feed_end is not None and last_sub is not None:
        out["tail_after_audio_end_s"] = round(last_sub - feed_end, 3)
    return out


def main():
    nouns = json.loads((ROOT / "corpus" / "nouns" / "nouns.json").read_text())
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    rows = []
    noun_outcomes = []  # per (arm, cond, seg, noun) binary — for paired stats
    for arm, ja_field in ARMS_JA.items():
        if not (RES / "raw" / arm).exists():
            continue
        for seg in picks:
            ref = (REF / f"{seg}.txt").read_text()
            for cond in CONDS:
                f = RES / "raw" / arm / f"{seg}__{cond}.json"
                if not f.exists():
                    continue
                d = json.loads(f.read_text())
                hyp_ja = d.get(ja_field, "") or ""
                hyp_folded = fold(hyp_ja)
                ns = nouns[seg]
                hits_s = hits_t = 0
                for n in ns:
                    s, t = noun_hit(n, hyp_folded)
                    hits_s += s
                    hits_t += t
                    noun_outcomes.append(
                        {"arm": arm, "cond": cond, "seg": seg,
                         "noun": n["surface"], "strict": int(s), "tolerant": int(t)}
                    )
                row = {
                    "arm": arm, "seg": seg, "cond": cond,
                    "n_nouns": len(ns),
                    "recall_strict": round(hits_s / len(ns), 4),
                    "recall_tolerant": round(hits_t / len(ns), 4),
                    "cer": round(cer(normalize(ref, strip_gloss=False), normalize(hyp_ja, strip_gloss=False)), 4),
                }
                # translation text
                if arm == "A":
                    zh = d.get("translation", "")
                else:
                    tf = RES / "raw" / f"{arm}_translate" / f"{seg}__{cond}.json"
                    zh = json.loads(tf.read_text())["translation"] if tf.exists() else ""
                # cross-check: kanji proper nouns surviving into the zh text
                zh_folded = fold(zh)
                row["recall_in_zh"] = round(
                    sum(noun_hit(n, zh_folded)[1] for n in ns) / len(ns), 4
                )
                row["tw_bad_hits"] = sum(zh.count(w) for w in TW_BAD)
                row["simplified_chars"] = len(SIMPLIFIED_RE.findall(zh))
                row["zh_len"] = len(zh)
                # latency & rewrite (batch arms have no realtime log)
                if arm in ("C", "Cplus") and d.get("log"):
                    row.update(sm_latency(d["log"]))
                    row["rewrite_rate"] = sm_rewrite_rate(d["log"])
                elif arm == "A":
                    row.update(a_latency(d["log"], d["audio_s"]))
                rows.append(row)

    (RES / "scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (RES / "noun_outcomes.json").write_text(json.dumps(noun_outcomes, ensure_ascii=False))
    print(f"scored {len(rows)} rows, {len(noun_outcomes)} noun outcomes")


if __name__ == "__main__":
    main()
