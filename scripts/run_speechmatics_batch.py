#!/usr/bin/env python3
"""Batch-API counterfactual: same 30 condition files through Speechmatics
batch (not realtime), with and without the custom dictionary.

Purpose: separate two confounded factors in arm C/C+.
  - How much of the error is the *realtime constraint* (partial hypotheses,
    bounded lookahead, max_delay=2.0) versus the acoustics?
  - Does the dictionary still pay off when the engine has full lookahead?

Arms written here: Cb (batch, no vocab), Cbplus (batch, + vocab).
Same audio, same language/operating_point, same dictionary as C/C+.

Usage: run_speechmatics_batch.py <Cb|Cbplus> [--workers N]
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
S_KEY = os.environ["s_key"]
BASE = "https://asr.api.speechmatics.com/v2"
PICKS = json.loads((ROOT / "corpus" / "picks.json").read_text())


def config_for(arm: str, seg_id: str) -> dict:
    tc = {"language": "ja", "operating_point": "enhanced"}
    if arm == "Cbplus":
        vocab = json.loads((ROOT / "corpus" / "dict" / "speechmatics_vocab.json").read_text())
        tc["additional_vocab"] = vocab[PICKS[seg_id]["domain"]]
    return {"type": "transcription", "transcription_config": tc}


def run_one(arm: str, wav: Path, out: Path):
    conf = config_for(arm, wav.stem.split("__")[0])
    t0 = time.monotonic()
    r = requests.post(
        f"{BASE}/jobs",
        headers={"Authorization": f"Bearer {S_KEY}"},
        files={
            "data_file": (wav.name, wav.open("rb"), "audio/wav"),
            "config": (None, json.dumps(conf)),
        },
        timeout=180,
    )
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(180):
        time.sleep(5)
        r = requests.get(f"{BASE}/jobs/{job}", headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60)
        st = r.json()["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "deleted", "expired"):
            raise RuntimeError(f"{job} {st}: {r.text}")
    else:
        raise TimeoutError(job)
    r = requests.get(
        f"{BASE}/jobs/{job}/transcript?format=txt",
        headers={"Authorization": f"Bearer {S_KEY}"},
        timeout=60,
    )
    r.raise_for_status()
    out.write_text(
        json.dumps(
            {
                "arm": arm,
                "file": wav.name,
                "job_id": job,
                "wall_s": round(time.monotonic() - t0, 1),
                "transcript": r.content.decode("utf-8").strip(),
                "log": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"done {arm} {wav.name}", flush=True)


def main():
    arm = sys.argv[1]
    workers = 2
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    outdir = ROOT / "results" / "raw" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(w, outdir / f"{w.stem}.json") for w in sorted(COND.glob("*.wav"))
            if not (outdir / f"{w.stem}.json").exists()]

    def guarded(pair):
        wav, out = pair
        for attempt in range(3):
            try:
                return run_one(arm, wav, out)
            except Exception as e:
                print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        print(f"FAILED {wav.name}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(guarded, jobs))


if __name__ == "__main__":
    main()
