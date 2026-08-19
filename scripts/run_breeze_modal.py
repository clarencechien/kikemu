#!/usr/bin/env python3
"""exp1 追加 arm `Xbrz_ja`:Breeze ASR 25 在**日文**上,五個聲學條件全跑。

handoff-v8 §1 A。**為什麼這一格排第一:**

1. Breeze 是 kikemu 唯一推薦的地端引擎,但證據全部來自中文語料
   (exp2 有訓練污染、exp5 是中文領域外)。**日文完全沒測過**,
   而 kikemu 的產品本業就是日文導覽。
2. exp1 的 **N3(人群 8dB)是目前唯一能觸發「整段放棄」的條件**:
   Gemini Live 在那格 0.030、Gemma 4 12B 0.077 且有一個檔輸出完全是空的。
   Breeze 會不會也崩,是 `results/stt-matrix.md` §6 標「未測」的那一條。
3. exp1 的參考來自大阪觀光局頁面,**不是任何受測引擎產生的**,無主場偏差。

設定與 **exp2 Phase A 完全相同**(T4/fp16、`chunk_length_s=0`、revision 釘死、
`language` 不指定),這樣三個語料的 Breeze 數字才是同一把尺量的。
exp5 那次跑在 CPU/fp32 是環境所迫,不是基準。

用法:
    modal run scripts/run_breeze_modal.py --probe 1     # 抽驗一個檔
    modal run scripts/run_breeze_modal.py               # 全跑 30 檔
"""
import json
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
CONDS = ["N0", "N1", "N2", "N3", "N4"]
MODEL_ID = "MediaTek-Research/Breeze-ASR-25"
REVISION = "cffe7ccb404d025296a00758d0a33468bec3a9d0"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.9.0", "transformers>=5.15", "accelerate>=1.0",
                 "librosa>=0.10", "soundfile>=0.12", "numpy<2.3")
)
app = modal.App("kikemu-breeze-ja")
cache = modal.Volume.from_name("kikemu-hf-cache", create_if_missing=True)


@app.cls(image=image, gpu="T4", timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1, scaledown_window=600)
class Breeze:
    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = "/cache/hf"
        import torch
        import transformers
        from transformers import (AutomaticSpeechRecognitionPipeline,
                                  WhisperForConditionalGeneration, WhisperProcessor)
        t0 = time.time()
        proc = WhisperProcessor.from_pretrained(MODEL_ID, revision=REVISION)
        model = WhisperForConditionalGeneration.from_pretrained(
            MODEL_ID, revision=REVISION, dtype=torch.float16).to("cuda").eval()
        self.asr = AutomaticSpeechRecognitionPipeline(
            model=model, tokenizer=proc.tokenizer,
            feature_extractor=proc.feature_extractor,
            chunk_length_s=0, dtype=torch.float16, device="cuda")
        # 模型卡建議 sequential;chunked 量到的會是分塊策略的差異,不是模型
        assert self.asr._preprocess_params.get("chunk_length_s") in (0, None), \
            "chunk_length_s 沒生效"
        cache.commit()
        self.load_s = round(time.time() - t0, 1)
        self.tf, self.torch_v = transformers.__version__, torch.__version__
        self.gpu = torch.cuda.get_device_name(0)

    @modal.method()
    def run(self, wav_bytes: bytes, stem: str) -> dict:
        import io
        import librosa
        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)
        t0 = time.time()
        r = self.asr(audio.copy(), return_timestamps=True)
        el = time.time() - t0
        txt = r["text"].strip()
        print(f"    {stem} ({el:.0f}s) {len(txt)}字 | {txt[:70]}", flush=True)
        return {"stem": stem, "transcript": txt, "elapsed_sec": round(el, 1),
                "audio_s": round(len(audio) / 16000, 1),
                "segments": [{"start": c["timestamp"][0], "end": c["timestamp"][1],
                              "text": c["text"]} for c in r.get("chunks", [])],
                "load_sec": self.load_s, "transformers": self.tf,
                "torch": self.torch_v, "gpu": self.gpu}


@app.local_entrypoint()
def main(arm: str = "Xbrz_ja", probe: int = 0):
    import hashlib

    def sha256(p_):
        h = hashlib.sha256()
        with open(p_, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    picks = list(json.loads((ROOT / "corpus" / "picks.json").read_text()))
    stems = [f"{s}__{c}" for s in picks for c in CONDS]
    brz = Breeze()

    if probe:
        # 抽驗:先看輸出形狀對不對再全跑(handoff-v8 §3 執行紀律第 3 步)
        for stem in ["sakai06__N0", "hig01_A1__N3"][:probe + 1]:
            r = brz.run.remote((COND / f"{stem}.wav").read_bytes(), stem)
            print(f"\n=== {stem} | {r['audio_s']}s 音訊 | {r['elapsed_sec']}s ===")
            print(r["transcript"][:400])
            print("\n對照參考:")
            print((ROOT / "corpus/reference" / f"{stem.split('__')[0]}.txt")
                  .read_text()[:200])
        return

    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "model": MODEL_ID, "revision": REVISION,
            "dtype": "float16", "device": "cuda", "gpu_class": "T4",
            "chunk_length_s": 0, "return_timestamps": True, "language_arg": None,
            "corpus": "exp1(日文導覽)",
            "note": "設定與 exp2 Phase A 相同(T4/fp16),不是 exp5 那次的 CPU/fp32"}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    payload = [((COND / f"{s}.wav").read_bytes(), s) for s in todo]
    print(f"送出 {len(payload)} 個檔")
    for r in brz.run.starmap(payload):
        s = r["stem"]
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav", "audio_s": r["audio_s"],
            "transcript": r["transcript"], "segments": r["segments"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "model_load_sec": r["load_sec"], "gpu": r["gpu"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(COND / f"{s}.wav")},
        }, ensure_ascii=False, indent=1))
    print(f"\n→ {raw}")
