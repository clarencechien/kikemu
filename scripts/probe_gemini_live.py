#!/usr/bin/env python3
"""handoff-v12 Step 1:Gemini Live 候選模型的能力探針。

**每個模型只送一段 20 秒的 exp1 乾淨音檔**,把伺服器回的每一則訊息原樣落檔,
回答「這顆模型能不能接進 kikemu」需要的事實:

  · setup 收不收(哪些欄位會被拒)
  · systemInstruction(凍結口譯 prompt)吃不吃
  · 有沒有 inputTranscription(原文)、outputTranscription / modelTurn(譯文)
  · 訊息節奏:轉寫是增量 chunk 還是整段、有沒有 turnComplete
  · usageMetadata 的形狀(計費單位,鐵律 5)

會試兩種 setup:A = exp1 arm A 原樣(AUDIO 回應 + 雙向轉寫 + 凍結 prompt);
B = 最小設定(TEXT 回應,只開 inputAudioTranscription)——
專用轉寫 / 翻譯模型可能不接受 A 的某些欄位。

花費:5 模型 × ≤2 設定 × 20 秒音訊,牌價級估計 < $0.05。

用法:
    python3 scripts/probe_gemini_live.py               # 全部
    python3 scripts/probe_gemini_live.py gemini-3.8-live
輸出:results/probe_gemini_live.json
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
KEY = os.environ["gemini_key"]
URL = ("wss://generativelanguage.googleapis.com/ws/"
       "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
CLIP = ROOT / "corpus" / "conditions" / "hig01_A1__N0.wav"
CLIP_S = 20.0
SR = 16000
CHUNK_S = 0.5
OUT = ROOT / "results" / "probe_gemini_live.json"

MODELS = [
    "gemini-3.1-flash-live-preview",       # exp1 arm A 量的那一顆(基準)
    "gemini-3.5-transcribe-live",
    "gemini-3.5-live-translate-preview",
    "gemini-3.8-live",
    "gemini-3.8-live-extended-thinking",
]

SETUPS = {
    "A_exp1": lambda m: {"setup": {
        "model": f"models/{m}",
        "generationConfig": {"responseModalities": ["AUDIO"]},
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }},
    "B_text_min": lambda m: {"setup": {
        "model": f"models/{m}",
        "generationConfig": {"responseModalities": ["TEXT"]},
        "inputAudioTranscription": {},
    }},
}


VOCAB = json.loads((ROOT / "corpus" / "dict" / "speechmatics_vocab.json").read_text())
PACK100 = [t["content"] for t in VOCAB["higashiosaka"]][:100]  # 官方:≤1000,最佳 ≤100


def a_with(m, think=None):
    s = SETUPS["A_exp1"](m)
    if think:
        s["setup"]["generationConfig"]["thinkingConfig"] = {"thinkingLevel": think}
    return s


# 第二輪(2026-10-04,依官方文件 live-api/live-transcribe、live-translate、thinking)
SETUPS2 = {
    # transcribe-live:只收 TEXT;語言提示 + 詞表(custom_vocabulary,無讀音欄位)
    "T_ja": lambda m: {"setup": {"model": f"models/{m}",
        "generationConfig": {"responseModalities": ["TEXT"]},
        "inputAudioTranscription": {"languageCodes": ["ja-JP"]}}},
    "T_ja_vocab": lambda m: {"setup": {"model": f"models/{m}",
        "generationConfig": {"responseModalities": ["TEXT"]},
        "inputAudioTranscription": {"languageCodes": ["ja-JP"], "customVocabulary": PACK100}}},
    # live-translate:文件明說不吃 instructions;目標語言用 translationConfig
    "X_zhHant": lambda m: {"setup": {"model": f"models/{m}",
        "generationConfig": {"responseModalities": ["AUDIO"]},
        "inputAudioTranscription": {}, "outputAudioTranscription": {},
        "translationConfig": {"targetLanguageCode": "zh-Hant", "echoTargetLanguage": False}}},
    "X_zhHant_gc": lambda m: {"setup": {"model": f"models/{m}",   # 文件 WS 範例的巢狀寫法
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "inputAudioTranscription": {}, "outputAudioTranscription": {},
                             "translationConfig": {"targetLanguageCode": "zh-Hant",
                                                   "echoTargetLanguage": False}}}},
    # 3.8 系列:鐵律 4 先試 minimal,不收再退 low
    "A_think_minimal": lambda m: a_with(m, "minimal"),
    "A_think_low": lambda m: a_with(m, "low"),
}

ROUND2 = [
    ("gemini-3.5-transcribe-live", "T_ja"),
    ("gemini-3.5-transcribe-live", "T_ja_vocab"),
    ("gemini-3.5-live-translate-preview", "X_zhHant"),
    ("gemini-3.5-live-translate-preview", "X_zhHant_gc"),
    ("gemini-3.8-live", "A_think_minimal"),
    ("gemini-3.8-live-extended-thinking", "A_think_minimal"),
    ("gemini-3.8-live-extended-thinking", "A_think_low"),
]


def short(m: dict) -> dict:
    """訊息摘要:保留結構與文字,把音訊 base64 換成長度。"""
    s = json.loads(json.dumps(m))
    for p in s.get("serverContent", {}).get("modelTurn", {}).get("parts", []):
        if "inlineData" in p:
            p["inlineData"] = {"mimeType": p["inlineData"].get("mimeType"),
                               "bytes_b64": len(p["inlineData"].get("data", ""))}
    return s


async def probe(model: str, setup_name: str) -> dict:
    x, sr = sf.read(CLIP, dtype="int16")
    assert sr == SR
    pcm = x[: int(SR * CLIP_S)].tobytes()
    chunk = int(SR * CHUNK_S) * 2
    t0 = time.monotonic()
    msgs, kinds = [], {}
    rec = {"model": model, "setup": setup_name}
    try:
        async with websockets.connect(f"{URL}?key={KEY}", max_size=2**24,
                                      open_timeout=20) as ws:
            await ws.send(json.dumps({**SETUPS, **SETUPS2}[setup_name](model)))
            first = json.loads(await asyncio.wait_for(ws.recv(), 20))
            rec["setup_reply"] = short(first)
            if "setupComplete" not in first:
                rec["ok"] = False
                return rec

            async def feed():
                sent, nt = 0, time.monotonic()
                while sent < len(pcm):
                    await ws.send(json.dumps({"realtimeInput": {"audio": {
                        "data": base64.b64encode(pcm[sent:sent + chunk]).decode(),
                        "mimeType": "audio/pcm;rate=16000"}}}))
                    sent += chunk
                    nt += CHUNK_S
                    await asyncio.sleep(max(0, nt - time.monotonic()))
                await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))

            ft = asyncio.create_task(feed())
            last = time.monotonic()
            while True:
                if ft.done() and time.monotonic() - last > 10:
                    break
                if time.monotonic() - t0 > CLIP_S + 60:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), 3)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                t = round(time.monotonic() - t0, 2)
                keys = sorted(list(m.get("serverContent", {}).keys()) +
                              [k for k in m if k != "serverContent"])
                for k in keys:
                    kinds[k] = kinds.get(k, 0) + 1
                msgs.append({"t": t, **short(m)})
                if m.get("serverContent") or "usageMetadata" in m:
                    last = time.monotonic()
            ft.cancel()
    except websockets.exceptions.ConnectionClosed as e:
        rec["closed"] = {"code": e.code, "reason": str(e.reason)[:300]}
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"

    sc = [m.get("serverContent", {}) for m in msgs]
    rec["ok"] = rec.get("ok", "setup_reply" in rec and "setupComplete" in rec["setup_reply"])
    rec["message_kinds"] = kinds
    rec["input_transcription"] = "".join(s.get("inputTranscription", {}).get("text", "") for s in sc)
    rec["n_interim"] = sum(1 for s in sc if s.get("interimInputTranscription", {}).get("text"))
    rec["output_transcription"] = "".join(s.get("outputTranscription", {}).get("text", "") for s in sc)
    rec["model_text"] = "".join(p.get("text", "") for s in sc
                                for p in s.get("modelTurn", {}).get("parts", []))
    rec["n_input_chunks"] = sum(1 for s in sc if s.get("inputTranscription", {}).get("text"))
    rec["first_input_t"] = next((m["t"] for m in msgs
                                 if m.get("serverContent", {}).get("inputTranscription", {}).get("text")), None)
    rec["usage_last"] = next((m["usageMetadata"] for m in reversed(msgs) if "usageMetadata" in m), None)
    rec["messages_head"] = msgs[:12]
    return rec


async def main():
    if "--round2" in sys.argv:
        jobs = ROUND2
        out = OUT.with_name("probe_gemini_live_round2.json")
    else:
        jobs = [(m, s) for m in (sys.argv[1:] or MODELS) for s in SETUPS]
        out = OUT
    results = []
    for m, s in jobs:
        r = await probe(m, s)
        results.append(r)
        print(f"{m:<38}{s:<16} ok={r.get('ok')} "
              f"in={len(r.get('input_transcription',''))}ch interim={r.get('n_interim',0)} "
              f"outTx={len(r.get('output_transcription',''))}ch "
              f"text={len(r.get('model_text',''))}ch "
              f"{r.get('closed') or r.get('error') or ''}", flush=True)
    out.write_text(json.dumps({"clip": CLIP.name, "clip_s": CLIP_S,
                               "checked": time.strftime("%Y-%m-%d"),
                               "results": results}, ensure_ascii=False, indent=1))
    print("→", out.relative_to(ROOT))


if __name__ == "__main__":
    asyncio.run(main())
