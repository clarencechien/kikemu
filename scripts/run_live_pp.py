#!/usr/bin/env python3
"""Exp3 stage 2: Gemini Live on preprocessed conditions.

Usage: run_live_pp.py <pp> [--conds N2,N3] [--workers N]
Reads corpus/conditions_pp/{seg}__{cond}__{pp}.wav,
writes results/raw/A_pp_<pp>/{seg}__{cond}.json (same schema as arm A).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_gemini_live as v1

ROOT = Path(__file__).resolve().parent.parent
PPDIR = ROOT / "corpus" / "conditions_pp"


async def main():
    pp = sys.argv[1]
    conds = {"N2", "N3"}
    workers = 2
    args = sys.argv[2:]
    if "--conds" in args:
        conds = set(args[args.index("--conds") + 1].split(","))
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    outdir = ROOT / "results" / "raw" / f"A_pp_{pp}"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for wav in sorted(PPDIR.glob(f"*__{pp}.wav")):
        seg, cond, _ = wav.stem.split("__")
        if cond not in conds:
            continue
        out = outdir / f"{seg}__{cond}.json"
        if not out.exists():
            jobs.append((wav, out))
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
