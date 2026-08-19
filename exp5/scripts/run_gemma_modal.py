#!/usr/bin/env python3
"""exp5 arm `Xgma_12b`:Gemma 4 12B Unified 的 ASR,跑在 Modal 的 GPU 上。

**為什麼有這支(而不是只有 Colab notebook):**
Colab 那條路已經耗掉五輪來回,全部是環境問題——升級套件把 Colab 內建的
torch / numpy 打壞、缺 gitignore 的噪音素材、T4 記憶體不夠。Modal 的 image
是釘死的,而且**可以從這個 session 直接驅動**,不需要有人坐在瀏覽器前面點。

與 notebook 的差異只有執行環境,受測設定完全相同:
  · 同一份凍結的 prompt(與 exp5 的 Gbat arm 逐字相同)
  · enable_thinking=False(等同 API 端的 thinkingLevel:"minimal")
  · 整段送,不切塊——與其他所有 arm 一致
  · 音檔**由本機上傳**,不在容器裡重新產生:本機這份已對過
    exp5/corpus/audio_manifest.json 的 sha256,與其他 arm 跑的是同一份位元。

用法:
    modal token set --token-id ... --token-secret ...   # 或 MODAL_TOKEN_ID/SECRET
    modal run exp5/scripts/run_gemma_modal.py
    modal run exp5/scripts/run_gemma_modal.py --model google/gemma-4-E4B-it --arm Xgma_e4b

成本(Modal 牌價,2026-08-19 查):A100-40GB $0.000583/s ≈ $2.10/hr。
六個檔估 20~30 分鐘 GPU 時間 ≈ **$0.7~1.1**。權重快取在 Volume,重跑不用再下載。
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
CONDS = ["M0", "M3"]

PROMPT = ("逐字轉寫這段音訊。說話者混用中文與英文,英文詞請原樣保留,不要翻譯。"
          "不要摘要、不要加說話者標記、不要加時間碼,只輸出轉寫文字。")

# 版本全部釘死——Colab 那五輪的教訓就是「浮動的相依關係會自己壞掉」。
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.9.0",
        "torchvision==0.24.0",       # Gemma4UnifiedProcessor 連帶需要
        "transformers>=5.15",
        "accelerate>=1.0",
        "librosa>=0.10",
        "soundfile>=0.12",
        "numpy<2.3",
    )
)
app = modal.App("kikemu-gemma4-asr")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.function(image=image, gpu="A100-40GB", timeout=60 * 60,
              volumes={"/cache": cache},
              max_containers=1)
def transcribe(wav_bytes: bytes, stem: str, model_id: str) -> dict:
    import io
    import os
    os.environ["HF_HOME"] = "/cache/hf"

    import librosa
    import torch
    import transformers
    from transformers import AutoProcessor, Gemma4UnifiedForConditionalGeneration

    global _M
    if "_M" not in globals():
        t0 = time.time()
        proc = AutoProcessor.from_pretrained(model_id)
        model = Gemma4UnifiedForConditionalGeneration.from_pretrained(
            model_id, dtype=torch.bfloat16, device_map="cuda").eval()
        cache.commit()          # 權重留在 Volume,下次不用再抓
        _M = (proc, model, round(time.time() - t0, 1))
    processor, model, load_s = _M

    audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
    msgs = [{"role": "user", "content": [{"type": "audio", "audio": audio},
                                         {"type": "text", "text": PROMPT}]}]
    inputs = processor.apply_chat_template(
        msgs, add_generation_prompt=True, tokenize=True,
        return_dict=True, return_tensors="pt",
        enable_thinking=False).to(model.device)
    n_in = inputs["input_ids"].shape[-1]
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
    el = time.time() - t0
    txt = processor.decode(out[0][n_in:], skip_special_tokens=True)
    # Gemma 4 用 <channel|> 閉合 thought channel;答案是最後一段。
    txt = txt.split("<channel|>")[-1].strip()
    return {"stem": stem, "transcript": txt, "elapsed_sec": round(el, 1),
            "prompt_tokens": int(n_in), "load_sec": load_s,
            "transformers": transformers.__version__, "torch": torch.__version__}


@app.local_entrypoint()
def main(model: str = "google/gemma-4-12B-it", arm: str = "Xgma_12b"):
    man = json.loads((ROOT / "corpus" / "audio_manifest.json").read_text())
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    stems = [f"{p['seg']}__{c}" for p in picks for c in CONDS]

    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    import hashlib

    def sha256(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    payload = []
    for s in todo:
        wav = COND / f"{s}.wav"
        digest = sha256(wav)
        if digest != man["conditions"][s]["sha256"]:
            raise RuntimeError(f"{s}.wav 與 manifest 不符,先跑 exp5/scripts/degrade.py")
        payload.append((wav.read_bytes(), s, model))
    print(f"上傳 {len(payload)} 個檔(sha256 全部與 manifest 相符)")

    meta = {"arm": arm, "model": model, "api": "modal A100-40GB",
            "load": "bf16", "chunk_sec": 0, "whole_file": True,
            "enable_thinking": False, "do_sample": False, "prompt": PROMPT,
            "corpus": "exp5"}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    for r in transcribe.starmap(payload):
        s = r["stem"]
        wav = COND / f"{s}.wav"
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav",
            "audio_s": round(wav.stat().st_size / (16000 * 2), 1),
            "transcript": r["transcript"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "prompt_tokens": r["prompt_tokens"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(wav), "audio_matches_manifest": True},
        }, ensure_ascii=False, indent=1))
        print(f"  {s}  {r['elapsed_sec']}s  {len(r['transcript'])} 字")
    print(f"\n→ {raw}")
