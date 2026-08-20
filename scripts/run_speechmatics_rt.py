#!/usr/bin/env python3
"""Arm C / C+ listener: Speechmatics realtime WebSocket, Japanese.

- Audio is streamed at TRUE realtime (1x) in 0.5 s PCM chunks, so partial/
  final timing and rewrite behavior match live conditions.
- Every server message is logged with a wall-clock timestamp plus the amount
  of audio pushed so far, enabling latency and rewrite-rate metrics.
- C+ passes additional_vocab (see make_dict.py output); C passes none.

Usage: run_speechmatics_rt.py <arm:C|Cplus> [--workers N] [--only seg__cond ...]
Writes results/raw/<arm>/<seg>__<cond>.json  (skips existing).
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
S_KEY = os.environ["s_key"]
URL = "wss://eu2.rt.speechmatics.com/v2"
CHUNK_S = 0.5
SR = 16000


PICKS = json.loads((ROOT / "corpus" / "picks.json").read_text())


def transcription_config(arm: str, seg_id: str) -> dict:
    cfg = {
        "language": "ja",
        "operating_point": "enhanced",
        "enable_partials": True,
        "max_delay": 2.0,
    }
    if arm.startswith("Cplus"):
        vocab = json.loads((ROOT / "corpus" / "dict" / "speechmatics_vocab.json").read_text())
        v = vocab[PICKS[seg_id]["domain"]]
        # handoff-v10 S4 的詞表對齊控制組:Scribe **即時**的上限是 50 詞且沒有
        # 讀音欄位,要跟它比引擎,SM 這邊也必須砍到同樣 50 條、拿掉 sounds_like。
        if arm == "Cplus50":
            v = [{"content": t["content"]} for t in v[:50]]
        cfg["additional_vocab"] = v
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
        start = {
            "message": "StartRecognition",
            "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": SR},
            "transcription_config": transcription_config(arm, wav.stem.split("__")[0]),
        }
        await ws.send(json.dumps(start))
        ev("StartRecognition_sent")

        async def feeder():
            seq = 0
            sent = 0
            next_t = time.monotonic()
            while sent < len(pcm):
                await ws.send(pcm[sent : sent + chunk])
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
    result = {
        "arm": arm,
        "file": wav.name,
        "audio_s": round(len(pcm) / 2 / SR, 2),
        "transcript": "".join(finals),
        "log": log,
    }
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"done {arm} {wav.name} ({len(result['transcript'])} ch)", flush=True)


async def main():
    arm = sys.argv[1]
    workers = 2
    only = None
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1 :])
    outdir = ROOT / "results" / "raw" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for wav in sorted(COND.glob("*.wav")):
        stem = wav.stem
        if only and stem not in only:
            continue
        out = outdir / f"{stem}.json"
        if out.exists():
            continue
        jobs.append((wav, out))
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(3):
                try:
                    return await run_one(arm, wav, out)
                except Exception as e:
                    print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))
            print(f"FAILED {wav.name}", flush=True)

    await asyncio.gather(*[guarded(w, o) for w, o in jobs])


if __name__ == "__main__":
    asyncio.run(main())
