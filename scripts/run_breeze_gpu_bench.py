#!/usr/bin/env python3
"""handoff-v8 §2 D:Breeze ASR 25 在 T4 / L4 / A100-40GB 上的速度基準。

**為什麼要這格:** `stt-matrix.md` 報地端成本時只有兩個點——T4(2.9× 實時)
與 4 核 CPU(0.14× 實時),中間全靠猜。三個點才畫得出曲線,而且真正要回答的
不是「哪張卡快」,是**哪張卡每小時音訊最便宜**(§4 D 的判讀規則)。

**比速度更重要的那件事**(也寫在 §4 D):同 revision、同 dtype、同
`chunk_length_s`,三張卡的轉寫**應該逐字相同**。若不同,那是「跨 GPU 不可
重現」,會動搖所有 Breeze 數字的可信度——所以這支會逐字比對並印出結果。

設定與 exp2 Phase A / handoff-v8 A 完全相同,只有 GPU 不同。
每張卡跑**兩次**同一個檔:第一次含 warmup(CUDA context、cuDNN autotune),
第二次才是穩態——只報第二次,並把兩次都留在結果裡。

用法:
    modal run scripts/run_breeze_gpu_bench.py
    modal run scripts/run_breeze_gpu_bench.py --gpus T4        # 只跑一張
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
# exp5 的 T1__M0:5 分鐘、乾淨。挑 exp5 而不是 exp1 的檔,因為 stt-matrix
# 現有的兩個速度點(T4 102s / CPU 2089s)也是在 5 分鐘的 exp5 檔上量的,
# 換檔就跟既有數字接不起來。
WAV = ROOT / "exp5" / "corpus" / "conditions" / "T1__M0.wav"
MODEL_ID = "MediaTek-Research/Breeze-ASR-25"
REVISION = "cffe7ccb404d025296a00758d0a33468bec3a9d0"
# 2026-08-19 查 Modal 牌價($/秒)
RATE = {"T4": 0.000164, "L4": 0.000222, "A100-40GB": 0.000583}

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.9.0", "transformers>=5.15", "accelerate>=1.0",
                 "librosa>=0.10", "soundfile>=0.12", "numpy<2.3")
)
app = modal.App("kikemu-breeze-bench")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


def _bench(gpu_name: str, wav_bytes: bytes) -> dict:
    """在容器裡跑:載入 → warmup 一次 → 穩態一次。"""
    import io
    import librosa
    import torch
    import transformers
    from transformers import (AutomaticSpeechRecognitionPipeline,
                              WhisperForConditionalGeneration, WhisperProcessor)
    import os
    os.environ["HF_HOME"] = "/cache/hf"

    t0 = time.time()
    proc = WhisperProcessor.from_pretrained(MODEL_ID, revision=REVISION)
    model = WhisperForConditionalGeneration.from_pretrained(
        MODEL_ID, revision=REVISION, dtype=torch.float16).to("cuda").eval()
    asr = AutomaticSpeechRecognitionPipeline(
        model=model, tokenizer=proc.tokenizer,
        feature_extractor=proc.feature_extractor,
        chunk_length_s=0, dtype=torch.float16, device="cuda")
    assert asr._preprocess_params.get("chunk_length_s") in (0, None)
    load_s = time.time() - t0

    audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
    dur = len(audio) / 16000
    runs = []
    for i in range(2):
        t1 = time.time()
        r = asr(audio.copy(), return_timestamps=True)
        torch.cuda.synchronize()
        runs.append({"run": i, "elapsed_sec": round(time.time() - t1, 2),
                     "text": r["text"].strip()})
        print(f"  [{gpu_name}] run{i} {runs[-1]['elapsed_sec']}s", flush=True)
    cache.commit()
    return {"gpu": gpu_name, "device_name": torch.cuda.get_device_name(0),
            "audio_s": round(dur, 1), "load_sec": round(load_s, 1),
            "runs": runs, "transcript": runs[-1]["text"],
            "steady_sec": runs[-1]["elapsed_sec"],
            "transformers": transformers.__version__, "torch": torch.__version__}


# Modal 的 gpu= 是裝飾期常數,所以三張卡就是三個 function——
# 用迴圈動態生成會讓 stub 名字對不上,寫死三個最不容易出錯。
@app.function(image=image, gpu="T4", timeout=3600, volumes={"/cache": cache})
def bench_t4(wav: bytes) -> dict:
    return _bench("T4", wav)


@app.function(image=image, gpu="L4", timeout=3600, volumes={"/cache": cache})
def bench_l4(wav: bytes) -> dict:
    return _bench("L4", wav)


@app.function(image=image, gpu="A100-40GB", timeout=3600, volumes={"/cache": cache})
def bench_a100(wav: bytes) -> dict:
    return _bench("A100-40GB", wav)


FNS = {"T4": bench_t4, "L4": bench_l4, "A100-40GB": bench_a100}


@app.local_entrypoint()
def main(gpus: str = "T4,L4,A100-40GB"):
    import hashlib

    wav = WAV.read_bytes()
    sha = hashlib.sha256(wav).hexdigest()
    picked = [g.strip() for g in gpus.split(",") if g.strip()]
    print(f"檔案 {WAV.name}  sha256 {sha[:16]}…  卡:{', '.join(picked)}")

    out = {"_": "handoff-v8 §2 D:Breeze 三卡速度基準",
           "file": WAV.name, "audio_sha256": sha,
           "model": MODEL_ID, "revision": REVISION, "dtype": "float16",
           "chunk_length_s": 0, "rate_usd_per_sec": RATE,
           "rate_checked": "2026-08-19", "gpus": {}}
    for g in picked:
        out["gpus"][g] = FNS[g].remote(wav)

    dst = ROOT / "results" / "gpu_bench.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"\n{'GPU':<12}{'穩態':>9}{'實時倍率':>10}{'$/hr 音訊':>12}{'載入':>8}")
    print("-" * 51)
    best = None
    for g, r in out["gpus"].items():
        rt = r["audio_s"] / r["steady_sec"]
        # 每小時音訊的 GPU 成本 = 費率($/秒 GPU 時間)× (3600 / 實時倍率)
        usd_hr = RATE[g] * 3600 / rt
        print(f"{g:<12}{r['steady_sec']:>8.1f}s{rt:>9.2f}×{usd_hr:>11.3f}"
              f"{r['load_sec']:>7.0f}s")
        if best is None or usd_hr < best[1]:
            best = (g, usd_hr)
    print(f"\n每小時音訊最便宜:**{best[0]}** ${best[1]:.3f}")

    # §4 D:比速度更重要的那條檢查
    texts = {g: r["transcript"] for g, r in out["gpus"].items()}
    uniq = set(texts.values())
    if len(uniq) == 1:
        print("跨 GPU 轉寫逐字相同 ✅(len="
              f"{len(next(iter(uniq)))} 字)")
    else:
        print("⚠️ 跨 GPU 轉寫**不一致** —— 依 §4 D 這比速度重要,先查這個:")
        for g, t in texts.items():
            print(f"  {g}: {len(t)}字 | {t[:80]}")
    print(f"\n→ {dst}")
