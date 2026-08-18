#!/usr/bin/env python3
"""exp5 Breeze arm — CPU 版(與 notebooks/breeze_asr25_exp5.ipynb 同一套參數)。

為什麼有這支:這台機器沒有 GPU,但 arm 不能因此缺席。
與 notebook 的**唯一**差異是 device/dtype(cuda+fp16 → cpu+fp32),
model revision、chunk_length_s=0、return_timestamps、language_arg=None 全部相同,
且都寫進每個結果 JSON 的 meta,報告裡要照實說明這條差異。

用法:
    python3 exp5/scripts/run_breeze_cpu.py            # 跑 M0/M3 六個檔
    python3 exp5/scripts/run_breeze_cpu.py --only T1__M0
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COND = ROOT / "exp5/corpus/conditions"
RAW = ROOT / "exp5/results/raw/Xbrz_auto"

MODEL_ID = "MediaTek-Research/Breeze-ASR-25"
REVISION = "cffe7ccb404d025296a00758d0a33468bec3a9d0"
CONDS = ["M0", "M3"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None, help="只跑這些 stem")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    import librosa
    import torch
    import transformers
    from transformers import (AutomaticSpeechRecognitionPipeline,
                              WhisperForConditionalGeneration, WhisperProcessor)

    torch.set_num_threads(args.threads)

    man = json.loads((ROOT / "exp5/corpus/audio_manifest.json").read_text())
    picks = json.loads((ROOT / "exp5/corpus/picks.json").read_text())
    targets = [f"{p['seg']}__{c}" for p in picks for c in CONDS]
    if args.only:
        targets = [t for t in targets if t in args.only]

    RAW.mkdir(parents=True, exist_ok=True)
    todo = [t for t in targets if not (RAW / f"{t}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    t0 = time.time()
    processor = WhisperProcessor.from_pretrained(MODEL_ID, revision=REVISION)
    model = WhisperForConditionalGeneration.from_pretrained(
        MODEL_ID, revision=REVISION, dtype=torch.float32).to("cpu").eval()
    asr = AutomaticSpeechRecognitionPipeline(
        model=model, tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        chunk_length_s=0, dtype=torch.float32, device="cpu")
    assert asr._preprocess_params.get("chunk_length_s") in (0, None), "chunk_length_s 沒生效"
    print(f"載入完成 {time.time() - t0:.0f}s | revision={REVISION[:12]}", flush=True)

    meta_env = {
        "model": MODEL_ID, "revision": REVISION, "dtype": "float32",
        "chunk_length_s": 0, "return_timestamps": True, "language_arg": None,
        "transformers": transformers.__version__, "torch": torch.__version__,
        "device": "cpu", "threads": args.threads, "corpus": "exp5",
        "note": "notebook 版跑 T4/fp16;本檔為無 GPU 環境的 CPU/fp32 複刻,其餘參數相同",
    }
    (RAW / "_meta.json").write_text(json.dumps(meta_env, ensure_ascii=False, indent=1))

    for stem in todo:
        wav = COND / f"{stem}.wav"
        audio, _ = librosa.load(wav, sr=16000, mono=True)
        t0 = time.time()
        r = asr(audio.copy(), return_timestamps=True)
        el = time.time() - t0
        digest = sha256(wav)
        (RAW / f"{stem}.json").write_text(json.dumps({
            "arm": "Xbrz_auto", "file": f"{stem}.wav",
            "audio_s": round(len(audio) / 16000, 1),
            "transcript": r["text"].strip(),
            "segments": [{"start": c["timestamp"][0], "end": c["timestamp"][1],
                          "text": c["text"]} for c in r.get("chunks", [])],
            "meta": {**meta_env, "elapsed_sec": round(el, 1), "audio_sha256": digest,
                     "audio_matches_manifest": digest == man["conditions"][stem]["sha256"]},
        }, ensure_ascii=False, indent=1))
        print(f"  {stem}  {el:.0f}s  {len(r['text'])} 字", flush=True)


if __name__ == "__main__":
    main()
