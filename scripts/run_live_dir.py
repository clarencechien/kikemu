#!/usr/bin/env python3
"""Exp4 stage 2: Gemini Live on directional-profile conditions.

Usage: run_live_dir.py <profile> [--exp 1|2] [--conds N2,N3] [--workers N]
Writes results/raw/A_dir_<profile>/ (exp1) or exp2/results/raw/G_dir_<profile>/.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_gemini_live as v1

ROOT = Path(__file__).resolve().parent.parent


async def main():
    prof = sys.argv[1]
    exp = "1"
    conds = {"N2", "N3"}
    workers = 2
    args = sys.argv[2:]
    if "--exp" in args:
        exp = args[args.index("--exp") + 1]
    if "--conds" in args:
        conds = set(args[args.index("--conds") + 1].split(","))
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if exp == "1":
        srcdir = ROOT / "corpus" / "conditions_dir"
        outdir = ROOT / "results" / "raw" / f"A_dir_{prof}"
    else:
        srcdir = ROOT / "exp2" / "corpus" / "conditions_dir"
        outdir = ROOT / "exp2" / "results" / "raw" / f"G_dir_{prof}"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for wav in sorted(srcdir.glob(f"*__{prof}.wav")):
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
