#!/usr/bin/env python3
"""exp1 追加 arm `Xgma_ja`:Gemma 4 12B 在**純日文、不用切塊**的條件下。

**這是診斷用的控制組,不是主表數字。**

exp5 量到 Gemma 4 12B 只有 0.056(報告 §3F.2b),但那一格同時混了四個變因:

    ① 中英夾雜  ② 5 分鐘要切 10 塊(塊間無上下文)  ③ 台灣華語  ④ 模型能力

這組把前三個都拿掉:

    · `sakai05` 29.9s / `sakai06` 27.9s —— **都在模型卡 §7 的 30 秒上限內,
      一次送完,零切塊 handicap**(exp1 其他四段是 97~158 秒,不能用)
    · 純日文導覽旁白,沒有語碼轉換
    · 參考文本來自大阪觀光局頁面,**不是任何受測引擎產生的**,無主場偏差

若這組表現好 → exp5 的 0.056 是語料/切塊造成的;
若這組也差 → 是模型本身。

**代價:n 很小**——13 個專名(sakai05 7 個、sakai06 6 個)× 5 個聲學條件。
所以它只能拿來排除假設,不能當成能力的定量估計。

prompt 跑兩個官方變體(都在模型卡裡,**不是我自己加的**——
exp5 已證實在官方 prompt 後面自加指示會讓輸出變壞):

    original : "in its original language"(與 exp5 那輪逐字相同,跨語料可比)
    japanese : "in Japanese into Japanese text"(模型卡的 {LANGUAGE} 模板)

用法:
    modal run scripts/run_gemma_modal_exp1.py                    # original
    modal run scripts/run_gemma_modal_exp1.py --prompt japanese --arm Xgma_ja_jp
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
SEGS = ["sakai05", "sakai06"]        # 只有這兩段在 30 秒內
CONDS = ["N0", "N1", "N2", "N3", "N4"]

_FORMAT = ("Follow these specific instructions for formatting the answer:\n"
           "* Only output the transcription, with no newlines.\n"
           "* When transcribing numbers, write the digits, i.e. write 1.7 and not "
           "one point seven, and write 3 instead of three.")
PROMPTS = {
    # 模型卡 code snippet 裡那一句,與 exp5 的 Xgma_12b 逐字相同
    "original": "Transcribe the following speech segment in its original language. " + _FORMAT,
    # 模型卡 §6「prompt structures」的 {LANGUAGE} 模板,填 Japanese
    "japanese": "Transcribe the following speech segment in Japanese into Japanese text.\n\n" + _FORMAT,
}

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.9.0", "torchvision==0.24.0", "transformers>=5.15",
                 "accelerate>=1.0", "librosa>=0.10", "soundfile>=0.12", "numpy<2.3")
)
app = modal.App("kikemu-gemma4-exp1")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu="A100-40GB", timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1, scaledown_window=600)
class Gemma:
    model_id: str = modal.parameter(default="google/gemma-4-12B-it")

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
    def transcribe(self, wav_bytes: bytes, stem: str, prompt_key: str) -> dict:
        import io
        import librosa
        import torch

        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
        dur = len(audio) / 16000
        assert dur <= 30.0, f"{stem} 是 {dur:.1f}s,超過模型卡的 30 秒上限"

        # 模型卡 §4:audio 放在 text **之後**
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": PROMPTS[prompt_key]},
            {"type": "audio", "audio": audio}]}]
        inputs = self.processor.apply_chat_template(
            msgs, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt").to(self.model.device)
        n = inputs["input_ids"].shape[-1]
        t0 = time.time()
        with torch.inference_mode():
            o = self.model.generate(**inputs, max_new_tokens=512)
        el = time.time() - t0
        raw = self.processor.decode(o[0][n:], skip_special_tokens=False)
        msg = self.processor.parse_response(raw, prefix=inputs["input_ids"])
        txt = msg.get("content") if isinstance(msg, dict) else str(msg)
        if isinstance(txt, list):
            txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
        txt = (txt or "").strip()
        print(f"    [{prompt_key}] {stem} ({el:.0f}s) {len(txt)}字 | {txt[:70]}",
              flush=True)
        return {"stem": stem, "transcript": txt, "elapsed_sec": round(el, 1),
                "audio_s": round(dur, 1), "prompt_tokens": int(n),
                "load_sec": self.load_s, "transformers": self.tf,
                "torch": self.torch_v}


@app.local_entrypoint()
def main(model: str = "google/gemma-4-12B-it", arm: str = "Xgma_ja",
         prompt: str = "original"):
    import hashlib

    def sha256(p_):
        h = hashlib.sha256()
        with open(p_, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    stems = [f"{s}__{c}" for s in SEGS for c in CONDS]
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "model": model, "api": "modal A100-40GB",
            "load": "bf16(dtype=auto)", "whole_file": True, "chunk_sec": 0,
            "chunk_note": "這兩段都在模型卡 §7 的 30 秒上限內,不需切塊",
            "modality_order": "text then audio(模型卡 §4)",
            "prompt_variant": prompt, "prompt": PROMPTS[prompt],
            "prompt_source": "模型卡官方模板,未自行增修",
            "decode": "max_new_tokens=512, processor.parse_response()",
            "corpus": "exp1(日文導覽,純單語)"}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    gemma = Gemma(model_id=model)
    payload = [((COND / f"{s}.wav").read_bytes(), s, prompt) for s in todo]
    print(f"送出 {len(payload)} 個檔(全部 < 30 秒,單發不切塊),prompt={prompt}")

    for r in gemma.transcribe.starmap(payload):
        s = r["stem"]
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav", "audio_s": r["audio_s"],
            "transcript": r["transcript"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "prompt_tokens": r["prompt_tokens"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(COND / f"{s}.wav")},
        }, ensure_ascii=False, indent=1))
    print(f"\n→ {raw}")
