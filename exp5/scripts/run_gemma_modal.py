#!/usr/bin/env python3
"""exp5 arm `Xgma_12b`:Gemma 4 12B Unified 的 ASR,跑在 Modal 的 GPU 上。

**為什麼有這支(而不是只有 Colab notebook):**
Colab 那條路已經耗掉五輪來回,全部是環境問題——升級套件把 Colab 內建的
torch / numpy 打壞、缺 gitignore 的噪音素材、T4 記憶體不夠。Modal 的 image
是釘死的,而且**可以從這個 session 直接驅動**,不需要有人坐在瀏覽器前面點。

設定:
  · **切 30 秒**——模型卡 §7 的硬性上限,不是記憶體妥協(見下方常數區)
  · **官方 ASR prompt**(模型卡 §6),不沿用 Gbat arm 的 prompt:
    每個引擎都該用它自己的最佳呼叫方式,否則量到的是「誰比較耐受別人的 prompt」
  · audio 放在 text **之後**(模型卡 §4)
  · enable_thinking=False(等同 API 端的 thinkingLevel:"minimal")
  · 音檔**由本機上傳**,不在容器裡重新產生:本機這份已對過
    exp5/corpus/audio_manifest.json 的 sha256,與其他 arm 跑的是同一份位元。

前一版整段送 300 秒,輸出崩成 8152 字的重複迴圈(證據:
exp5/results/invalid/Xgma_12b_300s_overlimit/),花費約 $0.54。

用法:
    modal token set --token-id ... --token-secret ...   # 或 MODAL_TOKEN_ID/SECRET
    modal run exp5/scripts/run_gemma_modal.py
    modal run exp5/scripts/run_gemma_modal.py --model google/gemma-4-E4B-it --arm Xgma_e4b

成本(Modal 牌價,2026-08-19 查):A100-40GB $0.000583/s ≈ $2.10/hr。
六個檔 = 60 個 30 秒 chunk。權重快取在 Volume,重跑不用再下載。
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
CONDS = ["M0", "M3"]

# ── 三條規則全部照模型卡,不要自己發明 ──────────────────────────────────
# https://huggingface.co/google/gemma-4-12B-it
#
# §7 Audio and Video Length:
#     "Audio supports a maximum length of **30 seconds**."
#   → 送 300 秒的下場實測過:輸出崩成 8152 字的重複迴圈,零可用資訊
#     (證據留在 exp5/results/invalid/Xgma_12b_300s_overlimit/)。
#     **切 30 秒不是記憶體妥協,是規格要求。**
# §4 Modality order:
#     "Audio content **after** the text in your prompt."
#   → audio 必須放在 text 之後。
# §6 官方 ASR prompt 結構:直接用它的,不要用別的 arm 的 prompt。
#     每個引擎都該用它自己的最佳呼叫方式,否則量到的是「誰比較耐受別人的 prompt」。
CHUNK_SEC = 30

PROMPT = ("Transcribe the following speech segment in its original language "
          "into text.\n\n"
          "Follow these specific instructions for formatting the answer:\n"
          "* Only output the transcription, with no newlines.\n"
          "* When transcribing numbers, write the digits, i.e. write 1.7 and not "
          "one point seven, and write 3 instead of three.")

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
    import re
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

    def one(chunk):
        # 模型卡 §4:audio 放在 text **之後**
        msgs = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                             {"type": "audio", "audio": chunk}]}]
        inputs = processor.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
            enable_thinking=False).to(model.device)
        n = inputs["input_ids"].shape[-1]
        with torch.inference_mode():
            o = model.generate(**inputs, max_new_tokens=1024, do_sample=False)
        # skip_special_tokens=True 會把 <|channel> 標記吃掉、只留下 "thought"
        # 這個字,所以先留著標記切,再清乾淨。
        raw = processor.decode(o[0][n:], skip_special_tokens=False)
        body = raw.split("<channel|>")[-1]
        body = re.sub(r"<\|?[a-z_]+\|?>", "", body)
        return body.strip(), n

    step = CHUNK_SEC * 16000
    chunks = [audio[i:i + step] for i in range(0, len(audio), step)
              if len(audio[i:i + step]) >= 16000]   # 不足 1 秒的尾巴丟掉
    t0 = time.time()
    pieces, n_tok = [], 0
    for c in chunks:
        txt, n = one(c)
        pieces.append(txt)
        n_tok += n
    el = time.time() - t0
    return {"stem": stem, "transcript": "".join(pieces),
            "elapsed_sec": round(el, 1), "n_chunks": len(chunks),
            "prompt_tokens": int(n_tok), "load_sec": load_s,
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
            "load": "bf16", "chunk_sec": CHUNK_SEC, "whole_file": False,
            "chunk_reason": "模型卡 §7:audio 上限 30 秒,不是記憶體妥協",
            "modality_order": "text then audio(模型卡 §4)",
            "prompt_source": "模型卡 §6 官方 ASR 結構(與 Gbat arm 的 prompt 不同)",
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
                     "n_chunks": r["n_chunks"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(wav), "audio_matches_manifest": True},
        }, ensure_ascii=False, indent=1))
        print(f"  {s}  {r['elapsed_sec']}s  {len(r['transcript'])} 字")
    print(f"\n→ {raw}")
