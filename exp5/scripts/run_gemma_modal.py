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
import os
import time
from pathlib import Path

import modal

# GPU 由環境變數選:C 那格要用 L4 跑 E4B,12B 仍用 A100。
# Modal 的 gpu= 是裝飾期常數,所以只能在 import 時決定,不能當 CLI 參數。
GPU = os.environ.get("KIKEMU_GEMMA_GPU", "A100-40GB")

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

# 模型卡 §6 的官方 ASR prompt,一字不改。
PROMPT_OFFICIAL = (
    "Transcribe the following speech segment in its original language. "
    "Follow these specific instructions for formatting the answer:\n"
    "* Only output the transcription, with no newlines.\n"
    "* When transcribing numbers, write the digits, i.e. write 1.7 and not "
    "one point seven, and write 3 instead of three.")

# 官方版**沒有**「保留原樣英文」這條,而 exp5 量的正是英文術語召回。
# 實測官方版會把 data 音譯成「大河」——所以必須跑兩個變體,
# 才分得出低召回是模型的極限還是 prompt 沒講。
PROMPT_KEEPEN = PROMPT_OFFICIAL + (
    "\n* The speaker mixes Mandarin Chinese and English. Keep every English "
    "word verbatim in Latin script; do not translate or transliterate them.")

PROMPTS = {"official": PROMPT_OFFICIAL, "keepen": PROMPT_KEEPEN}

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


# Modal 官方 lifecycle 寫法:@modal.enter 只在容器啟動時載一次權重,
# scaledown_window 讓容器在多次 `modal run` 之間保溫——否則每次抽驗都要重載。
@app.cls(image=image, gpu=GPU, timeout=60 * 60,
         volumes={"/cache": cache}, max_containers=1,
         scaledown_window=600)
class Gemma:
    model_id: str = modal.parameter(default="google/gemma-4-12B-it")

    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = "/cache/hf"
        import torch
        import transformers
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        t0 = time.time()
        self.processor = AutoProcessor.from_pretrained(self.model_id)
        # 官方 snippet 用 AutoModelForMultimodalLM + dtype="auto";
        # auto mapping 指到 Gemma4UnifiedForConditionalGeneration,是同一個類別。
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id, dtype="auto", device_map="auto").eval()
        cache.commit()          # 權重留在 Volume,下次不用再抓
        self.load_s = round(time.time() - t0, 1)
        self.tf, self.torch_v = transformers.__version__, torch.__version__

    @modal.method()
    def transcribe(self, wav_bytes: bytes, stem: str, prompt_key: str,
                   max_chunks: int = 0) -> dict:
        import io
        import librosa
        import torch

        processor, model, load_s = self.processor, self.model, self.load_s
        prompt = PROMPTS[prompt_key]
        audio, _ = librosa.load(io.BytesIO(wav_bytes), sr=16000, mono=True)

        def one(chunk):
            # 模型卡 §4:audio 放在 text **之後**
            msgs = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                 {"type": "audio", "audio": chunk}]}]
            inputs = processor.apply_chat_template(
                msgs, add_generation_prompt=True, tokenize=True,
                return_dict=True, return_tensors="pt").to(model.device)
            n = inputs["input_ids"].shape[-1]
            with torch.inference_mode():
                # 官方範例是 512。上限開太大只會讓跳針跑更久(4096 跑出 8152 字)。
                o = model.generate(**inputs, max_new_tokens=512)
            raw = processor.decode(o[0][n:], skip_special_tokens=False)
            # 官方解析器:自己切 <channel|> 是錯的(模型卡的 snippet 用這個)
            msg = processor.parse_response(raw, prefix=inputs["input_ids"])
            txt = msg.get("content") if isinstance(msg, dict) else str(msg)
            if isinstance(txt, list):
                txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
            return (txt or "").strip(), n

        step = CHUNK_SEC * 16000
        chunks = [audio[i:i + step] for i in range(0, len(audio), step)
                  if len(audio[i:i + step]) >= 16000]   # 不足 1 秒的尾巴丟掉
        if max_chunks:
            chunks = chunks[:max_chunks]   # --probe:抽驗用,不寫結果
        t0 = time.time()
        pieces, n_tok = [], 0
        for i, c in enumerate(chunks):
            ct = time.time()
            txt, n = one(c)
            pieces.append(txt)
            n_tok += n
            # 每個 chunk 印一行:沒有這個就要等整個檔跑完才知道有沒有壞掉,
            # 前兩輪就是這樣白燒的。
            print(f"    [{prompt_key}] {stem} chunk {i + 1}/{len(chunks)} "
                  f"({time.time() - ct:.0f}s) {len(txt)}字 | {txt[:70]}", flush=True)
        el = time.time() - t0
        return {"stem": stem, "transcript": "".join(pieces),
                "elapsed_sec": round(el, 1), "n_chunks": len(chunks),
                "prompt_tokens": int(n_tok), "load_sec": load_s,
                "transformers": self.tf, "torch": self.torch_v}


@app.local_entrypoint()
def main(model: str = "google/gemma-4-12B-it", arm: str = "Xgma_12b",
         prompt: str = "official", probe: int = 0, selftest: bool = False):
    """probe=N:只跑 T1__M0 的前 N 個 chunk、兩個 prompt 變體都跑,不寫結果。

    先驗再全跑——前兩輪都是等整個檔跑完才發現壞掉,白燒了 $1.26。
    """
    import hashlib

    def sha256(p_):
        h = hashlib.sha256()
        with open(p_, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    man = json.loads((ROOT / "corpus" / "audio_manifest.json").read_text())
    gemma = Gemma(model_id=model)

    if selftest:
        # 決定性對照:模型卡自己 snippet 裡的那個範例音檔。
        # 它若轉得好 → 設定正確,差是模型不擅長我們的語料;
        # 它若也差   → 還有地方沒弄對,不要把結果當成模型的能力。
        import urllib.request
        url = ("https://raw.githubusercontent.com/google-gemma/cookbook/refs/heads/"
               "main/apps/sample-data/journal1.wav")
        wav_bytes = urllib.request.urlopen(url).read()
        for key in PROMPTS:
            r = gemma.transcribe.remote(wav_bytes, "journal1", key, 2)
            print(f"\n=== [selftest/{key}] journal1.wav | {r['elapsed_sec']}s ===")
            print(r["transcript"][:700])
        return

    if probe:
        stem = "T1__M0"
        wav = COND / f"{stem}.wav"
        for key in PROMPTS:
            r = gemma.transcribe.remote(wav.read_bytes(), stem, key, probe)
            print(f"\n=== [{key}] {stem} 前 {probe} 個 chunk | "
                  f"{r['elapsed_sec']}s | {len(r['transcript'])} 字 ===")
            print(r["transcript"][:700])
        print("\n對照參考文本開頭:")
        print((ROOT / "corpus/reference/T1.txt").read_text()[:300])
        return

    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    stems = [f"{p_['seg']}__{c}" for p_ in picks for c in CONDS]
    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s_ for s_ in stems if not (raw / f"{s_}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    payload = []
    for s_ in todo:
        wav = COND / f"{s_}.wav"
        if sha256(wav) != man["conditions"][s_]["sha256"]:
            raise RuntimeError(f"{s_}.wav 與 manifest 不符,先跑 exp5/scripts/degrade.py")
        payload.append((wav.read_bytes(), s_, prompt, 0))
    print(f"上傳 {len(payload)} 個檔(sha256 全部與 manifest 相符),prompt={prompt}")

    meta = {"arm": arm, "model": model, "api": f"modal {GPU}",
            "load": "bf16(dtype=auto)", "chunk_sec": CHUNK_SEC, "whole_file": False,
            "chunk_reason": "模型卡 §7:audio 上限 30 秒,不是記憶體妥協",
            "modality_order": "text then audio(模型卡 §4)",
            "prompt_variant": prompt,
            "prompt_source": "模型卡 §6 官方 ASR 結構"
                             + ("(+ 保留英文指示)" if prompt == "keepen" else "(原文未改)"),
            "decode": "max_new_tokens=512, processor.parse_response()",
            "corpus": "exp5", "prompt": PROMPTS[prompt]}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    for r in gemma.transcribe.starmap(payload):
        s_ = r["stem"]
        wav = COND / f"{s_}.wav"
        (raw / f"{s_}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s_}.wav",
            "audio_s": round(wav.stat().st_size / (16000 * 2), 1),
            "transcript": r["transcript"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "prompt_tokens": r["prompt_tokens"], "n_chunks": r["n_chunks"],
                     "model_load_sec": r["load_sec"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": sha256(wav), "audio_matches_manifest": True},
        }, ensure_ascii=False, indent=1))
        print(f"  {s_}  {r['elapsed_sec']}s  {len(r['transcript'])} 字")
    print(f"\n→ {raw}")
