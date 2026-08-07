#!/usr/bin/env python3
"""V2 arm G: Gemini Live end-to-end on the lecture conditions.

Thin wrapper around v1 scripts/run_gemini_live.py — same protocol, model and
logging; only the condition directory, output root, and systemInstruction
change (P0, the kikemu interpreter prompt, applied to zh+en lecture audio).
"""
import asyncio
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(V1))
import run_gemini_live as v1  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
v1.COND = ROOT / "corpus" / "conditions"


async def main():
    workers = 2
    only = None
    args = sys.argv[1:]
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if "--only" in args:
        only = set(a for a in args[args.index("--only") + 1:] if not a.startswith("--"))
    outdir = ROOT / "results" / "raw" / "G"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(w, outdir / f"{w.stem}.json") for w in sorted(v1.COND.glob("*.wav"))
            if (not only or w.stem in only) and not (outdir / f"{w.stem}.json").exists()]
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(3):
                try:
                    return await v1.run_one(v1.MODEL, wav, out)
                except Exception as e:
                    print(f"ERR {wav.name} attempt {attempt}: {type(e).__name__} {e}", flush=True)
                    await asyncio.sleep(10 * (attempt + 1))
            print(f"FAILED {wav.name}", flush=True)

    await asyncio.gather(*[guarded(w, o) for w, o in jobs])


if __name__ == "__main__":
    asyncio.run(main())
