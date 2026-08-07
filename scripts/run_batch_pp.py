#!/usr/bin/env python3
"""Exp3 stage 1: preprocessed conditions through Speechmatics batch (+dict).

Reuses run_speechmatics_batch.run_one; arm name Cbplus_pp_<pp> keeps results
separated. Segment's domain dict applied as in Cbplus.
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_speechmatics_batch as B

ROOT = Path(__file__).resolve().parent.parent
PPDIR = ROOT / "corpus" / "conditions_pp"


def main():
    pps = sys.argv[1:] or ["webrtc", "sg", "blend"]
    jobs = []
    for pp in pps:
        outdir = ROOT / "results" / "raw" / f"Cbplus_pp_{pp}"
        outdir.mkdir(parents=True, exist_ok=True)
        for wav in sorted(PPDIR.glob(f"*__{pp}.wav")):
            out = outdir / (wav.stem.rsplit("__", 1)[0] + ".json")
            if out.exists():
                continue
            jobs.append((wav, out))

    def guarded(pair):
        wav, out = pair
        import time
        for attempt in range(3):
            try:
                # arm name passed as Cbplus so the dict is applied
                return B.run_one("Cbplus", wav, out)
            except Exception as e:
                print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        print(f"FAILED {wav.name}", flush=True)

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(guarded, jobs))


if __name__ == "__main__":
    main()
