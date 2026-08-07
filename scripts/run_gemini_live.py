#!/usr/bin/env python3
"""Arm A: Gemini Live (BidiGenerateContent WS), audio in -> zh-TW text out.

- Audio streamed at TRUE realtime in 0.5 s PCM chunks.
- inputAudioTranscription enabled: gives the model's own Japanese ASR view,
  scored for proper-noun recall / CER exactly like arm C's transcript.
- Translation text (modelTurn) is the arm's product output.
- All server messages logged with wall-clock + audio-pushed timestamps.

Usage: run_gemini_live.py [--model M] [--workers N] [--only stems...]
Writes results/raw/A/<seg>__<cond>.json
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
from prompts import INTERPRETER_SYSTEM

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
G_KEY = os.environ["gemini_key"]
MODEL = "models/gemini-3.1-flash-live-preview"
URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
CHUNK_S = 0.5
SR = 16000
TAIL_QUIET_S = 12.0


async def run_one(model: str, wav: Path, out: Path):
    x, sr = sf.read(wav, dtype="int16")
    assert sr == SR
    pcm = x.tobytes()
    chunk = int(SR * CHUNK_S) * 2
    log = []
    t0 = time.monotonic()

    def ev(kind, **kw):
        log.append({"t": round(time.monotonic() - t0, 3), "kind": kind, **kw})

    translation = []
    input_tx = []
    usage = None

    async with websockets.connect(f"{URL}?key={G_KEY}", max_size=2**24) as ws:
        setup = {
            "setup": {
                "model": model,
                "generationConfig": {"responseModalities": ["AUDIO"]},
                "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }
        await ws.send(json.dumps(setup))
        first = json.loads(await ws.recv())
        if "setupComplete" not in first:
            raise RuntimeError(f"setup failed: {first}")
        ev("setupComplete")

        stop_reading = asyncio.Event()

        async def feeder():
            sent = 0
            next_t = time.monotonic()
            while sent < len(pcm):
                b64 = base64.b64encode(pcm[sent : sent + chunk]).decode()
                await ws.send(json.dumps({
                    "realtimeInput": {"audio": {"data": b64, "mimeType": "audio/pcm;rate=16000"}}
                }))
                sent += chunk
                ev("audio", audio_s=round(min(sent, len(pcm)) / 2 / SR, 3))
                next_t += CHUNK_S
                await asyncio.sleep(max(0, next_t - time.monotonic()))
            await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            ev("audioStreamEnd_sent")

        feed = asyncio.create_task(feeder())
        last_sub = time.monotonic()  # last substantive (transcription/audio) msg
        hard_cap = len(pcm) / 2 / SR + 120
        gen_complete = False
        try:
            while True:
                if time.monotonic() - t0 > hard_cap:
                    ev("hard_cap_exit")
                    break
                if feed.done() and (
                    gen_complete or time.monotonic() - last_sub > TAIL_QUIET_S
                ):
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                sc = m.get("serverContent", {})
                if "usageMetadata" in m:
                    usage = m["usageMetadata"]
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
                parts = sc.get("modelTurn", {}).get("parts", [])
                for p in parts:
                    if p.get("text"):
                        translation.append(p["text"])
                        ev("modelText", text=p["text"])
                        last_sub = time.monotonic()
                    elif p.get("inlineData"):
                        ev("audioOut", n=len(p["inlineData"].get("data", "")))
                        last_sub = time.monotonic()
                if sc.get("turnComplete"):
                    ev("turnComplete")
                if sc.get("generationComplete"):
                    ev("generationComplete")
                    if feed.done():
                        gen_complete = True
                if m.get("goAway"):
                    ev("goAway")
                    break
        finally:
            if not feed.done():
                feed.cancel()

    result = {
        "arm": "A",
        "model": model,
        "file": wav.name,
        "audio_s": round(len(pcm) / 2 / SR, 2),
        "translation": "".join(translation),
        "input_transcription": "".join(input_tx),
        "usage": usage,
        "log": log,
    }
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"done A {wav.name} (tx={len(result['input_transcription'])}ch, zh={len(result['translation'])}ch)", flush=True)


async def main():
    model = MODEL
    workers = 2
    only = None
    args = sys.argv[1:]
    if "--model" in args:
        model = args[args.index("--model") + 1]
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if "--only" in args:
        only = set(args[args.index("--only") + 1 :])
    outdir = ROOT / "results" / "raw" / "A"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for wav in sorted(COND.glob("*.wav")):
        if only and wav.stem not in only:
            continue
        out = outdir / f"{wav.stem}.json"
        if out.exists():
            continue
        jobs.append((wav, out))
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(3):
                try:
                    return await run_one(model, wav, out)
                except Exception as e:
                    print(f"ERR {wav.name} attempt {attempt}: {type(e).__name__} {e}", flush=True)
                    await asyncio.sleep(10 * (attempt + 1))
            print(f"FAILED {wav.name}", flush=True)

    await asyncio.gather(*[guarded(w, o) for w, o in jobs])


if __name__ == "__main__":
    asyncio.run(main())
