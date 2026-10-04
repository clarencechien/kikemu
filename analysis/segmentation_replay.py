"""斷句規則重播:把 exp1 的 Speechmatics 即時定稿事件,照 relay.ts 的斷句邏輯重跑一遍。

為什麼要有:使用者 9/25 的試酒導覽(導覽模式)出現「純米大吟醸|です。」
「なるん|ですが。」這種被切在字中間的碎句,譯文跟著變成「。」「但是。」。
要改導覽模式的斷句,得先有數字說「改了比較少碎句、代價是字幕多等幾秒」,
而不是憑一份紀錄的印象。

輸入:results/raw/Cplus/*.json(exp1 C+ arm = SM enhanced + 詞表,1× 即時推流;
      log 裡每則 AddTranscript 都有牆鐘 t)。純計算,不呼叫任何 API。

重播的是 relay.ts 的這幾段(數值與程式碼同步,改了要一起改):
  · 句末標點 。!?!? 切句
  · MAX_PENDING_CHARS 48:殘句太長的保險
  · PENDING_STALE_MS 6000:殘句擱太久的保險(watchdog 每秒檢查一次)
  · 收尾時把殘句 flush

規則:
  old   — 2026-10-04 之前的導覽模式:48 字一到整段硬切;6 秒從「殘句開始累積」起算
  soft  — 只換長度那道:48 字切在最後一個軟斷點(、, 空白),找不到等到 96 字才硬切
  idle  — 只換時間那道:6 秒從「最後一則定稿」起算(=講者真的停住了才切)
  new   — soft + idle(提案)

指標(每句一筆):
  frag   ≤4 字的碎句(「です。」「ですが。」那種)
  open   不是收在句末標點、也不是收在軟斷點的句子(=切在字中間或詞中間)
  wait   這句第一個字定稿 → 這句送出去翻譯的牆鐘秒數(字幕灰字停留多久)

Gemini 對照模式另跑一組(results/raw/GTv,gemini-3.5-transcribe-live + 詞表,同一批 30 檔):
  gem_old     — 2026-10-04 PR #78 為止:每則 Gemini 定稿直接當一句送出(假設「定稿 = 講者停頓 = 斷點」)
  gem_shared  — 跟 SM 一樣的共用規則
  gem_new     — 共用規則 + **暫定也重設停頓計時**(提案,已上線)
  為什麼要多那一條:Gemini 的定稿間隔中位 7.6 秒(results/final_cadence.json),比 6 秒還長,
  只看定稿的話「講者還在講」也會被當成停住。暫定約每 0.5 秒一則,才是「還在講」的訊號。
  使用者 10-04 21:47 的 Gemini 紀錄(試酒影片)23 句有 14 句沒收在句末標點,這組就是為它跑的。

用法:python3 analysis/segmentation_replay.py   → results/segmentation_replay.json
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "results", "raw", "Cplus")
OUT = os.path.join(ROOT, "results", "segmentation_replay.json")

MAX_PENDING_CHARS = 48
PENDING_STALE_S = 6.0
SENT_END = re.compile(r"(?<=[。!?!?])")
END_PUNCT = re.compile(r"[。!?!?]$")
SOFT_BREAK = re.compile(r"[、,,;;:: ]")
SOFT_END = re.compile(r"[。!?!?、,,;;:: ]$")


def replay(
    events: list[tuple],
    soft: bool,
    idle: bool,
    flush_each_final: bool = False,
    partial_resets: bool = False,
) -> list[dict]:
    """events = [(牆鐘秒, 定稿文字)] 或 [(牆鐘秒, 定稿文字, 'F'|'P')](P = 暫定,文字不用),依序。
    回傳每句 {text, first, sent}"""
    out: list[dict] = []
    pending = ""
    pending_since = 0.0  # old: 殘句開始累積的時刻;idle: 最後一則定稿的時刻
    first = 0.0  # 這句第一個字定稿的時刻(只為了量 wait,不影響邏輯)

    def emit(s: str, now: float):
        nonlocal first
        s = s.strip()
        if s:
            out.append({"text": s, "first": first, "sent": now})
        first = now

    def flush(now: float):
        nonlocal pending, pending_since
        s, pending, pending_since = pending, "", 0.0
        emit(s, now)

    def on_final(text: str, now: float):
        nonlocal pending, pending_since, first
        if not pending.strip():
            first = now
        pending += text
        parts = SENT_END.split(pending)
        pending = parts.pop() if parts and not END_PUNCT.search(parts[-1]) else ""
        for s in parts:
            emit(s, now)
        if pending.strip() == "":
            first = now
        if flush_each_final and pending.strip():
            return flush(now)
        if len(pending) >= MAX_PENDING_CHARS:
            if not soft:
                flush(now)
            else:
                cut = -1
                for m in SOFT_BREAK.finditer(pending):
                    if m.start() >= 8:
                        cut = m.start()
                if cut >= 0:
                    head, pending = pending[: cut + 1], pending[cut + 1 :]
                    emit(head, now)
                elif len(pending) >= MAX_PENDING_CHARS * 2:
                    flush(now)
        if idle:
            pending_since = now if pending else 0.0
        else:
            if pending and not pending_since:
                pending_since = now
            if not pending:
                pending_since = 0.0

    t0 = events[0][0] if events else 0.0
    i = 0
    end = (events[-1][0] if events else 0.0) + 1.0
    tick = t0 + 1.0
    # 依時間交錯處理定稿與 watchdog(每秒一跳)
    while i < len(events) or tick <= end:
        if i < len(events) and events[i][0] <= tick:
            e = events[i]
            if len(e) < 3 or e[2] == "F":
                on_final(e[1], e[0])
            elif partial_resets and pending:
                pending_since = e[0]  # 暫定還在長 = 講者還在講
            i += 1
        else:
            if pending_since and tick - pending_since > PENDING_STALE_S:
                flush(tick)
            tick += 1.0
    flush(end)  # 收尾
    return out


def metrics(sents: list[dict]) -> dict:
    n = len(sents)
    frag = sum(1 for s in sents if len(s["text"]) <= 4)
    open_ = sum(1 for s in sents if not SOFT_END.search(s["text"]))
    waits = [s["sent"] - s["first"] for s in sents]
    lens = [len(s["text"]) for s in sents]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, math.ceil(p * len(xs)) - 1)] if xs else 0
    return {
        "sentences": n,
        "frag_le4": frag,
        "open_end": open_,
        "frag_rate": round(frag / n, 3) if n else 0,
        "open_rate": round(open_ / n, 3) if n else 0,
        "len_median": statistics.median(lens) if lens else 0,
        "wait_p50_s": round(q(waits, 0.5), 2),
        "wait_p90_s": round(q(waits, 0.9), 2),
        "wait_max_s": round(max(waits), 2) if waits else 0,
    }


RULES = {"old": (False, False), "soft": (True, False), "idle": (False, True), "new": (True, True)}
GEM_RULES = {
    "gem_old": dict(soft=True, idle=True, flush_each_final=True),
    "gem_shared": dict(soft=True, idle=True),
    "gem_new": dict(soft=True, idle=True, partial_resets=True),
}
# relay 對日文的 Gemini 輸出先拿掉 CJK 之間的空白(upstream.ts 的 tidy),重播也要
_tidy = lambda x: re.sub(r"(?<=[^\x00-\x7F])\s+(?=[^\x00-\x7F])", "", x)


def gemini_events(path: str) -> list[tuple]:
    d = json.load(open(path, encoding="utf-8"))
    kinds = {"inputTranscription": "F", "interimInputTranscription": "P"}
    return [(e["t"], _tidy(e["text"]), kinds[e["kind"]]) for e in d["log"] if e["kind"] in kinds and e.get("text")]


def main():
    files = sorted(f for f in glob.glob(os.path.join(RAW, "*.json")) if not f.endswith("_meta.json"))
    per_file: dict[str, dict] = {}
    pooled: dict[str, list[dict]] = {r: [] for r in RULES}
    by_cond: dict[str, dict[str, list[dict]]] = {}
    examples: dict[str, list[str]] = {r: [] for r in RULES}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        ev = [(e["t"], e["transcript"] or "") for e in d["log"] if e.get("kind") == "AddTranscript"]
        name = os.path.basename(f)[:-5]
        cond = name.split("__")[1]
        per_file[name] = {}
        for r, (soft, idle) in RULES.items():
            s = replay(ev, soft, idle)
            per_file[name][r] = metrics(s)
            pooled[r] += s
            by_cond.setdefault(cond, {}).setdefault(r, []).extend(s)
            if name.endswith("__N0"):
                examples[r] += [x["text"] for x in s if len(x["text"]) <= 4 or not SOFT_END.search(x["text"])][:3]
    res = {
        "source": "results/raw/Cplus(exp1 C+ = SM enhanced + 詞表,1× 即時推流),30 檔 = 6 段 × N0–N4",
        "relay_constants": {"MAX_PENDING_CHARS": MAX_PENDING_CHARS, "PENDING_STALE_S": PENDING_STALE_S},
        "rules": {
            "old": "2026-10-04 之前的導覽模式:48 字整段硬切;6 秒從殘句開始累積起算",
            "soft": "48 字切在最後一個軟斷點,找不到等到 96 字",
            "idle": "6 秒從最後一則定稿起算",
            "new": "soft + idle",
        },
        "pooled": {r: metrics(s) for r, s in pooled.items()},
        "by_condition": {c: {r: metrics(s) for r, s in rs.items()} for c, rs in sorted(by_cond.items())},
        "examples_N0_bad": examples,
        "per_file": per_file,
    }
    gem_pooled: dict[str, list[dict]] = {r: [] for r in GEM_RULES}
    for f in sorted(glob.glob(os.path.join(ROOT, "results", "raw", "GTv", "*__N*.json"))):
        ev = gemini_events(f)
        for r, kw in GEM_RULES.items():
            gem_pooled[r] += replay(ev, **kw)
    res["gemini"] = {
        "source": "results/raw/GTv(gemini-3.5-transcribe-live + 詞表,1× 即時),30 檔",
        "note": "wait 從該句第一則 Gemini 定稿起算,不含 Gemini 自己的定稿延遲(那個見 final_cadence.json)",
        "rules": {
            "gem_old": "每則定稿直接當一句(PR #78 為止)",
            "gem_shared": "與 SM 相同的共用規則",
            "gem_new": "共用規則 + 暫定也重設停頓計時",
        },
        "pooled": {r: metrics(s) for r, s in gem_pooled.items()},
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(f"{'rule':6} {'句數':>5} {'≤4字':>6} {'切在中間':>8} {'中位長':>6} {'wait p50':>8} {'p90':>6} {'max':>6}")
    for r, m in res["pooled"].items():
        print(
            f"{r:6} {m['sentences']:>5} {m['frag_le4']:>4}({m['frag_rate']:.0%}) {m['open_end']:>4}({m['open_rate']:.0%})"
            f" {m['len_median']:>6} {m['wait_p50_s']:>8} {m['wait_p90_s']:>6} {m['wait_max_s']:>6}"
        )
    print("\n各噪音條件(切在中間的比例 old → new):")
    for c, rs in res["by_condition"].items():
        print(f"  {c}: {rs['old']['open_rate']:.0%} → {rs['new']['open_rate']:.0%}   ≤4字 {rs['old']['frag_rate']:.0%} → {rs['new']['frag_rate']:.0%}")
    print("\nGemini(GTv):")
    for r, m in res["gemini"]["pooled"].items():
        print(
            f"  {r:10} {m['sentences']:>4} 句 收在中間 {m['open_end']:>3}({m['open_rate']:.0%}) ≤4字 {m['frag_le4']}"
            f" wait p50 {m['wait_p50_s']} p90 {m['wait_p90_s']} max {m['wait_max_s']}"
        )
    print("\nN0 壞例:")
    for r in ("old", "new"):
        print(" ", r, examples[r][:8])


if __name__ == "__main__":
    main()
