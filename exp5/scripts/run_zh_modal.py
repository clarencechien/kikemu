#!/usr/bin/env python3
"""純中文 20 秒視窗上的 Gemma 4 12B(arm `Zgma`)。

這是拆變因的最後一格:

| 組 | 語言 | 切塊 | Gemma 4 12B |
|---|---|---|---|
| exp5 主表 | 中英夾雜 | 30s × 10 | 0.056 |
| exp1 控制組 | 純日文 | 不用切 | 0.323 |
| **本組** | **純中文(台灣華語)** | **不用切** | ← 要量的 |

前兩組差 6 倍,但混了「語言」與「切塊」兩個變因。本組把語言換成中文、
一樣不切塊,就能看出**台灣華語本身**是不是難點。

指標用 **CER**,不是術語召回——這些視窗刻意挑成沒有英文,沒有術語可以召回。

用法:
    modal run exp5/scripts/run_zh_modal.py
"""
import json
import os
import time
from pathlib import Path

import modal

# GPU 由環境變數選:C 那格要用 L4 跑 E4B,12B 仍用 A100。
# Modal 的 gpu= 是裝飾期常數,所以只能在 import 時決定,不能當 CLI 參數。
GPU = os.environ.get("KIKEMU_GEMMA_GPU", "A100-40GB")

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "corpus" / "zh_windows"

# 模型卡 code snippet 那一句,與 exp5 / exp1 兩輪逐字相同。
# exp5 已證實在官方 prompt 後面自加指示會讓輸出變壞,所以一個字都不改。
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
app = modal.App("kikemu-gemma4-zh")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu=GPU, timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1, scaledown_window=600)
class Gemma:
    model_id: str = modal.parameter(default="google/gemma-4-12B-it")
    greedy: bool = modal.parameter(default=False)

    @modal.enter()
    def load(self):
        import os
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
    def run(self, wav_bytes: bytes, stem: str) -> dict:
        import io
        import librosa
        import torch

        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
        assert len(audio) / 16000 <= 30.0
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "audio", "audio": audio}]}]     # 模型卡 §4:audio 在 text 之後
        inputs = self.processor.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt").to(self.model.device)
        n = inputs["input_ids"].shape[-1]
        t0 = time.time()
        # 預設是模型卡自帶的 generation_config:do_sample=True、temperature=1.0。
        # greedy=True 時改成貪婪解碼,用來分開「模型真的不吐」與「取樣抖動」
        # (handoff-v8 §4 C-ter)。兩者都會寫進 meta。
        kw = {"do_sample": False} if self.greedy else {}
        with torch.inference_mode():
            o = self.model.generate(**inputs, max_new_tokens=512, **kw)
        el = time.time() - t0
        raw = self.processor.decode(o[0][n:], skip_special_tokens=False)
        msg = self.processor.parse_response(raw, prefix=inputs["input_ids"])
        txt = msg.get("content") if isinstance(msg, dict) else str(msg)
        if isinstance(txt, list):
            txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
        txt = (txt or "").strip()
        print(f"    {stem} ({el:.0f}s) {len(txt)}字 | {txt[:70]}", flush=True)
        return {"stem": stem, "transcript": txt, "elapsed_sec": round(el, 1),
                "prompt_tokens": int(n), "load_sec": self.load_s,
                "transformers": self.tf, "torch": self.torch_v}


@app.local_entrypoint()
def main(model: str = "google/gemma-4-12B-it", arm: str = "Zgma",
         greedy: bool = False):
    man = json.loads((WIN / "manifest.json").read_text())["windows"]
    raw = ROOT / "results" / "raw_zh" / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s for s in man if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "model": model, "api": f"modal {GPU}",
            "load": "bf16(dtype=auto)", "whole_file": True, "chunk_sec": 0,
            "corpus": "exp5 純中文 20 秒視窗", "prompt": PROMPT,
            "prompt_source": "模型卡官方模板,未自行增修",
            "decode": "max_new_tokens=512, processor.parse_response()",
            "sampling": ("greedy(do_sample=False)" if greedy
                         else "模型卡預設 do_sample=True, temperature=1.0, "
                              "top_k=64, top_p=0.95 —— 單次取樣,未量測變異")}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    gemma = Gemma(model_id=model, greedy=greedy)
    payload = [((WIN / f"{s}.wav").read_bytes(), s) for s in todo]
    print(f"送出 {len(payload)} 個純中文 20 秒視窗")
    for r in gemma.run.starmap(payload):
        s = r["stem"]
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav", "audio_s": 20.0,
            "transcript": r["transcript"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "prompt_tokens": r["prompt_tokens"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": man[s]["sha256"]},
        }, ensure_ascii=False, indent=1))
    print(f"\n→ {raw}")
