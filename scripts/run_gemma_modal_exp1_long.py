#!/usr/bin/env python3
"""handoff-v8 §2 E:把 exp1 的 `Xgma_ja` 從 2 段補到 6 段。

現有的 `Xgma_ja` 只有 sakai05/06 —— 挑那兩段是因為它們 < 30 秒,
**在模型卡的音訊上限內,可以整檔單發、零切塊 handicap**。代價是
n 很小(13 個專名),「日文 0.323」這個數字撐不太住。

這支補上其餘四段(97~158 秒,**要切 30 秒**),補滿後是 67 個專名。

**副產品比主結果更有價值**(§4 E 已寫死怎麼判):同一個模型、同一批語料、
同一個官方 prompt,只有**切塊與否**不同——這是全專案唯一能單獨量出
「切塊 handicap」的設計。但它**不是隨機分派**(短段與長段的內容、語速、
專名密度本來就不同),所以是觀察性證據,結論只能寫「與……一致」。

寫進**同一個** arm 目錄 `results/raw/Xgma_ja/`,因為模型、prompt、
GPU、解碼參數全部相同,只有切塊路徑不同——而那一項逐檔記在各自的 meta 裡
(`n_chunks`:1 = 整檔單發)。

用法:
    modal run scripts/run_gemma_modal_exp1_long.py --probe 1   # 抽驗一個檔
    modal run scripts/run_gemma_modal_exp1_long.py             # 全跑 20 檔
"""
import json
import os
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
# sakai05/06 已在既有的 Xgma_ja 裡(整檔單發),這支只補長的四段
SEGS = ["hig01_A1", "hig02_B12", "ikeda02", "ikeda03"]
CONDS = ["N0", "N1", "N2", "N3", "N4"]
GPU = os.environ.get("KIKEMU_GEMMA_GPU", "A100-40GB")

# 模型卡 §7:音訊上限 30 秒。送 300 秒的下場實測過(輸出崩成重複迴圈,
# 證據在 exp5/results/invalid/)。切塊是規格要求,不是記憶體妥協。
CHUNK_SEC = 30

# 與既有 Xgma_ja(prompt_variant=original)逐字相同,否則兩批接不起來。
PROMPT = ("Transcribe the following speech segment in its original language. "
          "Follow these specific instructions for formatting the answer:\n"
          "* Only output the transcription, with no newlines.\n"
          "* When transcribing numbers, write the digits, i.e. write 1.7 and not "
          "one point seven, and write 3 instead of three.")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.9.0", "torchvision==0.24.0", "transformers>=5.15",
                 "accelerate>=1.0", "librosa>=0.10", "soundfile>=0.12", "numpy<2.3")
)
app = modal.App("kikemu-gemma4-exp1-long")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu=GPU, timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1, scaledown_window=600)
class Gemma:
    model_id: str = modal.parameter(default="google/gemma-4-12B-it")

    @modal.enter()
    def load(self):
        os.environ["HF_HOME"] = "/cache/hf"
        import torch  # noqa: F401
        import transformers
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        t0 = time.time()
        self.processor = AutoProcessor.from_pretrained(self.model_id)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id, dtype="auto", device_map="auto").eval()
        cache.commit()
        self.load_s = round(time.time() - t0, 1)
        self.tf, self.torch_v = transformers.__version__, torch.__version__

    @modal.method()
    def transcribe(self, wav_bytes: bytes, stem: str, max_chunks: int = 0) -> dict:
        import io
        import librosa
        import torch

        processor, model = self.processor, self.model
        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)

        def one(chunk):
            msgs = [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "audio", "audio": chunk}]}]     # 模型卡 §4:audio 在後
            inputs = processor.apply_chat_template(
                msgs, add_generation_prompt=True, tokenize=True,
                return_dict=True, return_tensors="pt").to(model.device)
            n = inputs["input_ids"].shape[-1]
            with torch.inference_mode():
                o = model.generate(**inputs, max_new_tokens=512)
            raw = processor.decode(o[0][n:], skip_special_tokens=False)
            msg = processor.parse_response(raw, prefix=inputs["input_ids"])
            txt = msg.get("content") if isinstance(msg, dict) else str(msg)
            if isinstance(txt, list):
                txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
            return (txt or "").strip(), n

        step = CHUNK_SEC * 16000
        chunks = [audio[i:i + step] for i in range(0, len(audio), step)
                  if len(audio[i:i + step]) >= 16000]     # 不足 1 秒的尾巴丟掉
        if max_chunks:
            chunks = chunks[:max_chunks]
        t0 = time.time()
        pieces, n_tok = [], 0
        for i, c in enumerate(chunks):
            ct = time.time()
            txt, n = one(c)
            pieces.append(txt)
            n_tok += n
            print(f"    {stem} chunk {i + 1}/{len(chunks)} "
                  f"({time.time() - ct:.0f}s) {len(txt)}字 | {txt[:60]}", flush=True)
        return {"stem": stem, "transcript": "".join(pieces),
                "elapsed_sec": round(time.time() - t0, 1), "n_chunks": len(chunks),
                "audio_s": round(len(audio) / 16000, 1), "prompt_tokens": int(n_tok),
                "load_sec": self.load_s, "transformers": self.tf,
                "torch": self.torch_v}


@app.local_entrypoint()
def main(model: str = "google/gemma-4-12B-it", arm: str = "Xgma_ja",
         probe: int = 0):
    import hashlib

    def sha256(p_):
        h = hashlib.sha256()
        with open(p_, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    gemma = Gemma(model_id=model)

    if probe:
        # 抽驗:先看實際輸出再全跑(§3 執行紀律第 3 步)
        stem = "ikeda02__N0"
        r = gemma.transcribe.remote((COND / f"{stem}.wav").read_bytes(), stem, probe)
        print(f"\n=== {stem} 前 {probe} 個 chunk | {r['elapsed_sec']}s ===")
        print(r["transcript"][:400])
        print("\n對照參考(開頭):")
        print((ROOT / "corpus/reference" / "ikeda02.txt").read_text()[:200])
        return

    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    stems = [f"{s}__{c}" for s in SEGS for c in CONDS]
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "model": model, "api": f"modal {GPU}",
            "load": "bf16(dtype=auto)", "whole_file": False,
            "chunk_sec": CHUNK_SEC,
            "chunk_reason": "模型卡 §7:音訊上限 30 秒",
            "modality_order": "text then audio(模型卡 §4)",
            "prompt_variant": "original", "prompt": PROMPT,
            "prompt_source": "模型卡官方模板,未自行增修",
            "decode": "max_new_tokens=512, processor.parse_response()",
            "corpus": "exp1(日文導覽)長段四段,需切塊",
            "note": "同 arm 的 sakai05/06 是整檔單發(n_chunks=1);"
                    "切塊與否逐檔記在各自 meta 的 n_chunks"}

    payload = [((COND / f"{s}.wav").read_bytes(), s) for s in todo]
    print(f"送出 {len(payload)} 個檔(每檔切 {CHUNK_SEC} 秒)")
    for r in gemma.transcribe.starmap(payload):
        s = r["stem"]
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav", "audio_s": r["audio_s"],
            "transcript": r["transcript"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "n_chunks": r["n_chunks"], "prompt_tokens": r["prompt_tokens"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(COND / f"{s}.wav")},
        }, ensure_ascii=False, indent=1))
    print(f"\n→ {raw}")
