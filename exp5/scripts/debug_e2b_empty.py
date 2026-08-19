#!/usr/bin/env python3
"""診斷:E2B 在 8 個純中文 20 秒視窗裡有 6 個輸出**完全是空的**。

寫進報告之前必須先分清楚兩件事(鐵律 7 的同一條紀律):

    (a) 模型真的什麼都不吐(生成了就 EOS)——那是模型性質,可以報
    (b) 模型吐了東西,是我們的 `parse_response` 把它丟掉——那是 bug,報了就是錯的

所以這支把**同一個視窗**跑一次,原樣印出:

    · raw:`processor.decode(..., skip_special_tokens=False)` 的完整字串
    · parsed:`processor.parse_response()` 之後拿到什麼
    · 生成的 token 數(扣掉 prompt)

對照組跑一個 E2B **有**輸出的視窗,以及同一個空視窗餵給 E4B,
三者並排就能定位。

用法:
    modal run exp5/scripts/debug_e2b_empty.py
"""
import json
import os
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "corpus" / "zh_windows"
GPU = os.environ.get("KIKEMU_GEMMA_GPU", "L4")

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
app = modal.App("kikemu-gemma4-debug")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu=GPU, timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1, scaledown_window=300)
class Gemma:
    model_id: str = modal.parameter(default="google/gemma-4-E2B-it")

    @modal.enter()
    def load(self):
        os.environ["HF_HOME"] = "/cache/hf"
        import torch  # noqa: F401
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        self.processor = AutoProcessor.from_pretrained(self.model_id)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id, dtype="auto", device_map="auto").eval()
        cache.commit()

    @modal.method()
    def probe(self, wav_bytes: bytes, stem: str) -> dict:
        import io
        import librosa
        import torch

        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "audio", "audio": audio}]}]
        inputs = self.processor.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt").to(self.model.device)
        n = inputs["input_ids"].shape[-1]
        t0 = time.time()
        with torch.inference_mode():
            o = self.model.generate(**inputs, max_new_tokens=512)
        gen = o[0][n:]
        raw = self.processor.decode(gen, skip_special_tokens=False)
        clean = self.processor.decode(gen, skip_special_tokens=True)
        msg = self.processor.parse_response(raw, prefix=inputs["input_ids"])
        txt = msg.get("content") if isinstance(msg, dict) else str(msg)
        if isinstance(txt, list):
            txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
        return {"stem": stem, "model": self.model_id,
                "n_generated": int(gen.shape[-1]), "elapsed": round(time.time() - t0, 1),
                "raw": raw, "clean_skip_special": clean,
                "parsed": (txt or ""), "audio_s": round(len(audio) / 16000, 1)}


@app.local_entrypoint()
def main():
    man = json.loads((WIN / "manifest.json").read_text())["windows"]
    empty = "T2_30s"      # E2B 空、E4B 有內容
    ok = "T2_155s"        # E2B 有內容(對照組)
    cases = [("google/gemma-4-E2B-it", empty), ("google/gemma-4-E2B-it", ok),
             ("google/gemma-4-E4B-it", empty)]
    for model, stem in cases:
        r = Gemma(model_id=model).probe.remote((WIN / f"{stem}.wav").read_bytes(), stem)
        print(f"\n{'=' * 70}\n{model}  ×  {stem}  ({r['audio_s']}s 音訊)")
        print(f"生成 token 數:{r['n_generated']}   耗時 {r['elapsed']}s")
        print(f"--- raw(含特殊 token,前 400 字元)---\n{r['raw'][:400]!r}")
        print(f"--- skip_special_tokens=True ---\n{r['clean_skip_special'][:200]!r}")
        print(f"--- parse_response 之後 ---\n{r['parsed'][:200]!r}")
        print(f"參考:{man[stem]['reference'][:80]}")
