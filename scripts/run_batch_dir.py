#!/usr/bin/env python3
"""Exp4 stage 1: directional profiles through Speechmatics batch (+dict).

Arm name Cbplus_dir_<profile>. Reads corpus/conditions_dir.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_speechmatics_batch as B

ROOT = Path(__file__).resolve().parent.parent
DIRD = ROOT / "corpus" / "conditions_dir"


def main():
    profiles = sys.argv[1:] or ["card", "conf", "lav"]
    jobs = []
    for prof in profiles:
        outdir = ROOT / "results" / "raw" / f"Cbplus_dir_{prof}"
        outdir.mkdir(parents=True, exist_ok=True)
        for wav in sorted(DIRD.glob(f"*__{prof}.wav")):
            out = outdir / (wav.stem.rsplit("__", 1)[0] + ".json")
            if not out.exists():
                jobs.append((wav, out))

    def guarded(pair):
        wav, out = pair
        for attempt in range(3):
            try:
                return B.run_one("Cbplus", wav, out)
            except Exception as e:
                print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        print(f"FAILED {wav.name}", flush=True)

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(guarded, jobs))


if __name__ == "__main__":
    main()
