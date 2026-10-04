#!/usr/bin/env python3
"""handoff-v12 Step 2:新一代 Gemini Live 模型 × exp1 30 檔。

與 `run_gemini_live.py`(exp1 arm A,8 月)**同一套推流方式**:1× 實時、0.5 秒
PCM chunk、每則伺服器訊息落檔(wall-clock + 已推入音訊秒數)。那支保持原樣
不動(歷史 arm 要可重現),這支用 preset 涵蓋 v12 的六個 arm。

輸出欄位與 arm A 相容(`input_transcription` / `translation` / `usage` / `log`),
所以 `score.py` 照 A 的方式計分。transcribe-live 另外記 `interim` 事件。

保險絲(handoff-v12 §3):每檔結束累加牌價估計,全部 arm 合計超過 $8 就停。
transcribe-live 不回 usageMetadata → 以「音訊分鐘 × 官方 blended $0.009/min」記帳。

用法:
    python3 scripts/run_gemini_live_v12.py G38 [--workers 3] [--only stem ...]
"""
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import soundfile as sf
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompts import INTERPRETER_SYSTEM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
KEY = os.environ["gemini_key"]
URL = ("wss://generativelanguage.googleapis.com/ws/"
       "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
CHUNK_S, SR, TAIL_QUIET_S = 0.5, 16000, 12.0
PICKS = json.loads((ROOT / "corpus" / "picks.json").read_text())
VOCAB = json.loads((ROOT / "corpus" / "dict" / "speechmatics_vocab.json").read_text())
BUDGET_USD = 8.0
LEDGER = ROOT / "results" / "raw" / "_v12_ledger.json"
# 牌價:ai.google.dev/pricing(頁面 2026-10-01 更新,2026-10-04 查)
PRICE = {
    "live3x": {"audio_in": 3.00e-6, "text_in": 0.75e-6, "audio_out": 12.0e-6, "text_out": 4.50e-6},
    "translate": {"audio_in": 3.50e-6, "text_in": 3.50e-6, "audio_out": 21.0e-6, "text_out": 21.0e-6},
}
TRANSCRIBE_PER_MIN = 0.009


def arm_a_setup(model: str, think: str | None = None) -> dict:
    gc = {"responseModalities": ["AUDIO"]}
    if think:
        gc["thinkingConfig"] = {"thinkingLevel": think}
    return {"setup": {"model": f"models/{model}", "generationConfig": gc,
                      "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
                      "inputAudioTranscription": {}, "outputAudioTranscription": {}}}


def transcribe_setup(seg: str, vocab: bool) -> dict:
    itc = {"languageCodes": ["ja-JP"]}
    if vocab:
        itc["customVocabulary"] = [t["content"] for t in VOCAB[PICKS[seg]["domain"]]]
    return {"setup": {"model": "models/gemini-3.5-transcribe-live",
                      "generationConfig": {"responseModalities": ["TEXT"]},
                      "inputAudioTranscription": itc}}


def translate_setup() -> dict:
    return {"setup": {"model": "models/gemini-3.5-live-translate-preview",
                      "generationConfig": {"responseModalities": ["AUDIO"],
                                           "translationConfig": {"targetLanguageCode": "zh-Hant",
                                                                 "echoTargetLanguage": False}},
                      "inputAudioTranscription": {}, "outputAudioTranscription": {}}}


ARMS = {
    "G31": {"model": "gemini-3.1-flash-live-preview", "setup": lambda s: arm_a_setup("gemini-3.1-flash-live-preview"), "price": "live3x"},
    "G38": {"model": "gemini-3.8-live", "setup": lambda s: arm_a_setup("gemini-3.8-live"), "price": "live3x"},
    "G38T": {"model": "gemini-3.8-live-extended-thinking", "setup": lambda s: arm_a_setup("gemini-3.8-live-extended-thinking", "low"), "price": "live3x"},
    # handoff-v12 §6 偏離 2:G31 靜默中斷的對照重跑(同模型同設定,**併發 1**)
    "G31r": {"model": "gemini-3.1-flash-live-preview", "setup": lambda s: arm_a_setup("gemini-3.1-flash-live-preview"), "price": "live3x"},
    "GX": {"model": "gemini-3.5-live-translate-preview", "setup": lambda s: translate_setup(), "price": "translate"},
    "GT": {"model": "gemini-3.5-transcribe-live", "setup": lambda s: transcribe_setup(s, False), "price": None},
    "GTv": {"model": "gemini-3.5-transcribe-live", "setup": lambda s: transcribe_setup(s, True), "price": None},
}


def cost_of(arm: str, usage: dict | None, audio_s: float) -> float:
    p = ARMS[arm]["price"]
    if p is None:
        return audio_s / 60 * TRANSCRIBE_PER_MIN
    if not usage:  # 沒回 usage 就用最壞情況的時長牌價,寧可高估
        return audio_s / 60 * (0.005 + 0.018)
    c = PRICE[p]
    tot = 0.0
    for d in usage.get("promptTokensDetails", []):
        # live-translate:官方註明「依輸入與輸出的**音訊** token 計費」,而且實測它的
        # promptTokenCount 只等於 AUDIO 那一項(TEXT 明細不含在內,2026-10-04 探針)。
        # 第一版把 TEXT 明細也算進去,估出 $0.34/min,是官方 blended $0.0368/min 的 9 倍
        # ——全域保險絲差點因此把其他五個 arm 一起餓死。
        if p == "translate" and d["modality"] != "AUDIO":
            continue
        tot += d["tokenCount"] * (c["audio_in"] if d["modality"] == "AUDIO" else c["text_in"])
    for d in usage.get("responseTokensDetails", []):
        tot += d["tokenCount"] * (c["audio_out"] if d["modality"] == "AUDIO" else c["text_out"])
    tot += usage.get("thoughtsTokenCount", 0) * c["text_out"]
    return tot


def ledger_add(arm: str, stem: str, usd: float) -> float:
    led = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"entries": []}
    led["entries"].append({"arm": arm, "file": stem, "usd": round(usd, 5)})
    led["total_usd"] = round(sum(e["usd"] for e in led["entries"]), 4)
    LEDGER.write_text(json.dumps(led, indent=1))
    return led["total_usd"]


def ledger_total() -> float:
    return json.loads(LEDGER.read_text()).get("total_usd", 0.0) if LEDGER.exists() else 0.0


async def run_one(arm: str, wav: Path, out: Path):
    seg = wav.stem.split("__")[0]
    x, sr = sf.read(wav, dtype="int16")
    assert sr == SR
    pcm = x.tobytes()
    chunk = int(SR * CHUNK_S) * 2
    audio_s = len(pcm) / 2 / SR
    log, translation, input_tx = [], [], []
    usage_msgs = []
    t0 = time.monotonic()
    pushed = [0.0]

    def ev(kind, **kw):
        log.append({"t": round(time.monotonic() - t0, 3), "kind": kind,
                    "audio_s": round(pushed[0], 3), **kw})

    async with websockets.connect(f"{URL}?key={KEY}", max_size=2**24, open_timeout=30) as ws:
        await ws.send(json.dumps(ARMS[arm]["setup"](seg)))
        first = json.loads(await asyncio.wait_for(ws.recv(), 30))
        if "setupComplete" not in first:
            raise RuntimeError(f"setup failed: {str(first)[:200]}")
        ev("setupComplete")

        async def feeder():
            sent, nt = 0, time.monotonic()
            while sent < len(pcm):
                await ws.send(json.dumps({"realtimeInput": {"audio": {
                    "data": base64.b64encode(pcm[sent:sent + chunk]).decode(),
                    "mimeType": "audio/pcm;rate=16000"}}}))
                sent += chunk
                pushed[0] = min(sent, len(pcm)) / 2 / SR
                ev("audio")
                nt += CHUNK_S
                await asyncio.sleep(max(0, nt - time.monotonic()))
            await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            ev("audioStreamEnd_sent")

        feed = asyncio.create_task(feeder())
        last_sub = time.monotonic()
        hard_cap = audio_s + 120
        try:
            while True:
                if time.monotonic() - t0 > hard_cap:
                    ev("hard_cap_exit")
                    break
                if feed.done() and time.monotonic() - last_sub > TAIL_QUIET_S:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                sc = m.get("serverContent", {})
                if "usageMetadata" in m:
                    usage_msgs.append(m["usageMetadata"])
                if sc.get("interimInputTranscription", {}).get("text"):
                    ev("interimInputTranscription", text=sc["interimInputTranscription"]["text"])
                    last_sub = time.monotonic()
                if sc.get("inputTranscription", {}).get("text"):
                    t = sc["inputTranscription"]["text"]
                    input_tx.append(t)
                    ev("inputTranscription", text=t)
                    last_sub = time.monotonic()
                if sc.get("outputTranscription", {}).get("text"):
                    t = sc["outputTranscription"]["text"]
                    translation.append(t)
                    ev("outputTranscription", text=t)
                    last_sub = time.monotonic()
                for p in sc.get("modelTurn", {}).get("parts", []):
                    if p.get("text"):
                        translation.append(p["text"])
                        ev("modelText", text=p["text"])
                        last_sub = time.monotonic()
                    elif p.get("inlineData"):
                        last_sub = time.monotonic()
                for k in ("turnComplete", "generationComplete", "interrupted"):
                    if sc.get(k):
                        ev(k)
                if m.get("goAway"):
                    ev("goAway")
                    break
        finally:
            if not feed.done():
                feed.cancel()

    # usageMetadata 是逐 turn 回報;合計所有 turn 才是整場用量
    usage = None
    if usage_msgs:
        usage = {"promptTokensDetails": [], "responseTokensDetails": [], "thoughtsTokenCount": 0,
                 "n_messages": len(usage_msgs)}
        agg = {}
        for u in usage_msgs:
            for side in ("promptTokensDetails", "responseTokensDetails"):
                for d in u.get(side, []):
                    agg[(side, d["modality"])] = agg.get((side, d["modality"]), 0) + d["tokenCount"]
            usage["thoughtsTokenCount"] += u.get("thoughtsTokenCount", 0)
        for (side, mod), n in agg.items():
            usage[side].append({"modality": mod, "tokenCount": n})
    usd = cost_of(arm, usage, audio_s)
    result = {"arm": arm, "model": ARMS[arm]["model"], "setup": ARMS[arm]["setup"](seg)["setup"],
              "file": wav.name, "audio_s": round(audio_s, 2),
              "translation": "".join(translation), "input_transcription": "".join(input_tx),
              "usage": usage, "usd_est": round(usd, 5), "log": log}
    result["setup"].pop("systemInstruction", None)  # 凍結 prompt 在 prompts.py,不重複存
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    total = ledger_add(arm, wav.stem, usd)
    print(f"done {arm} {wav.name} tx={len(result['input_transcription'])}ch "
          f"zh={len(result['translation'])}ch ${usd:.4f} (累計 ${total:.2f})", flush=True)


async def main():
    arm = sys.argv[1]
    assert arm in ARMS, f"arm 必須是 {list(ARMS)}"
    args = sys.argv[2:]
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 3
    only = None
    if "--only" in args:
        only = {a for a in args[args.index("--only") + 1:] if not a.startswith("--")}
    outdir = ROOT / "results" / "raw" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    meta = {"arm": arm, "model": ARMS[arm]["model"], "handoff": "v12",
            "chunk_s": CHUNK_S, "realtime": "1x", "price_checked": "2026-10-04",
            "price_page_updated": "2026-10-01",
            "note": {"GT": "ja-JP,無詞表", "GTv": "ja-JP + 場景包全部詞條(content,無讀音)",
                     "GX": "translationConfig zh-Hant;此模型不吃 systemInstruction",
                     "G38T": "thinkingLevel low(minimal 被拒)",
                     "G38": "不接受 thinkingLevel", "G31": "exp1 arm A 原樣,同日對照組",
                     "G31r": "G31 靜默中斷那 6 檔以併發 1 重跑(handoff-v12 §6 偏離 2)"}[arm]}
    (outdir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    jobs = [(w, outdir / f"{w.stem}.json") for w in sorted(COND.glob("*.wav"))
            if (not only or w.stem in only) and not (outdir / f"{w.stem}.json").exists()]
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(3):
                if ledger_total() > BUDGET_USD:
                    print(f"FUSE 累計 ${ledger_total():.2f} > ${BUDGET_USD},跳過 {wav.name}", flush=True)
                    return
                try:
                    return await run_one(arm, wav, out)
                except Exception as e:  # noqa: BLE001
                    print(f"ERR {arm} {wav.name} attempt {attempt}: {type(e).__name__} {str(e)[:200]}", flush=True)
                    await asyncio.sleep(10 * (attempt + 1))
            print(f"FAILED {arm} {wav.name}", flush=True)

    await asyncio.gather(*[guarded(w, o) for w, o in jobs])


if __name__ == "__main__":
    asyncio.run(main())
