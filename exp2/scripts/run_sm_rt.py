#!/usr/bin/env python3
"""V2 Speechmatics realtime runner.

Arms (vocab x language mode), all realtime WS at 1x pacing:
  X_cmn        language=cmn,   no vocab
  X_multi      language=multi, no vocab
  Xdom_cmn / Xdom_multi     + domain vocab (Google ML glossary)
  Xsli_cmn / Xsli_multi     + per-segment slides vocab

Usage: run_sm_rt.py <arm> [--workers N] [--only stems...]
Writes exp2/results/raw/<arm>/<seg>__<cond>.json
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
VOCAB = ROOT / "corpus" / "vocab"
S_KEY = os.environ["s_key"]
URL = "wss://eu2.rt.speechmatics.com/v2"
CHUNK_S = 0.5
SR = 16000

ARMS = {
    "X_cmn": ("cmn", None),
    "X_multi": ("multi", None),
    "Xdom_cmn": ("cmn", "domain"),
    "Xdom_multi": ("multi", "domain"),
    "Xsli_cmn": ("cmn", "slides"),
    "Xsli_multi": ("multi", "slides"),
}


def transcription_config(arm: str, seg: str) -> dict:
    lang, vocab_kind = ARMS[arm]
    cfg = {
        "language": lang,
        "enable_partials": True,
    }
    if lang != "multi":
        # multi rejects operating_point and max_delay; use its defaults
        cfg["operating_point"] = "enhanced"
        cfg["max_delay"] = 2.0
    if vocab_kind == "domain":
        cfg["additional_vocab"] = json.loads((VOCAB / "domain.json").read_text())
    elif vocab_kind == "slides":
        cfg["additional_vocab"] = json.loads((VOCAB / f"slides_{seg}.json").read_text())
    return cfg


async def run_one(arm: str, wav: Path, out: Path):
    x, sr = sf.read(wav, dtype="int16")
    assert sr == SR
    pcm = x.tobytes()
    chunk = int(SR * CHUNK_S) * 2
    log = []
    t0 = time.monotonic()

    def ev(kind, **kw):
        log.append({"t": round(time.monotonic() - t0, 3), "kind": kind, **kw})

    async with websockets.connect(
        URL, additional_headers={"Authorization": f"Bearer {S_KEY}"}, max_size=2**24
    ) as ws:
        await ws.send(json.dumps({
            "message": "StartRecognition",
            "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": SR},
            "transcription_config": transcription_config(arm, wav.stem.split("__")[0]),
        }))
        ev("StartRecognition_sent")

        async def feeder():
            seq = sent = 0
            next_t = time.monotonic()
            while sent < len(pcm):
                await ws.send(pcm[sent: sent + chunk])
                sent += chunk
                seq += 1
                ev("audio", audio_s=round(min(sent, len(pcm)) / 2 / SR, 3))
                next_t += CHUNK_S
                await asyncio.sleep(max(0, next_t - time.monotonic()))
            await ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": seq}))
            ev("EndOfStream_sent")

        feed = asyncio.create_task(feeder())
        msgs = []
        try:
            async for raw in ws:
                m = json.loads(raw)
                ev(m.get("message", "?"),
                   transcript=m.get("metadata", {}).get("transcript"),
                   start_time=m.get("metadata", {}).get("start_time"),
                   end_time=m.get("metadata", {}).get("end_time"))
                msgs.append(m)
                if m.get("message") == "EndOfTranscript":
                    break
                if m.get("message") == "Error":
                    raise RuntimeError(json.dumps(m, ensure_ascii=False))
        finally:
            feed.cancel()

    finals = [m["metadata"]["transcript"] for m in msgs if m.get("message") == "AddTranscript"]
    out.write_text(json.dumps({
        "arm": arm, "file": wav.name,
        "audio_s": round(len(pcm) / 2 / SR, 2),
        "transcript": "".join(finals),
        "log": log,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"done {arm} {wav.name} ({len(''.join(finals))} ch)", flush=True)


async def main():
    arm = sys.argv[1]
    assert arm in ARMS, arm
    workers = 2
    only = None
    args = sys.argv[2:]
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if "--only" in args:
        only = set(a for a in args[args.index("--only") + 1:] if not a.startswith("--"))
    outdir = ROOT / "results" / "raw" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(w, outdir / f"{w.stem}.json") for w in sorted(COND.glob("*.wav"))
            if (not only or w.stem in only) and not (outdir / f"{w.stem}.json").exists()]
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(4):
                try:
                    return await run_one(arm, wav, out)
                except Exception as e:
                    print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                    await asyncio.sleep(8 * (attempt + 1))
            print(f"FAILED {wav.name}", flush=True)

    await asyncio.gather(*[guarded(w, o) for w, o in jobs])


if __name__ == "__main__":
    asyncio.run(main())
