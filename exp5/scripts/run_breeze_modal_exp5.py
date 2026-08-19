#!/usr/bin/env python3
"""handoff-v8 §1 B:Breeze × exp5 **全部四個聲學條件**,T4/fp16。

兩個目的:

1. **補完噪音曲線。** exp5 只跑了 M0/M3 兩端,「掉幅 0.070」其實只是兩點連線;
   M1/M2 補上才知道中間有沒有塌陷。
2. **解掉 exp5 侷限 17。** 既有的 M0/M3 是 CPU/fp32(本機無 GPU 所迫),
   這次是 T4/fp16——**連 M0/M3 一起重跑**,一來避免同一張表混兩種 dtype,
   二來直接量出兩者差多少(那條侷限標著「未量測」)。

寫進新 arm `Xbrz_gpu`,**不覆蓋既有的 `Xbrz_auto`**——兩份都留著才比得出 dtype 差異。
設定其餘與 exp2 Phase A 相同(chunk_length_s=0、revision cffe7ccb、language 不指定)。

用法:
    modal run exp5/scripts/run_breeze_modal_exp5.py::exp5_all
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import modal
from run_breeze_modal import Breeze, app, MODEL_ID, REVISION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
CONDS = ["M0", "M1", "M2", "M3"]


# 這支重用 scripts/run_breeze_modal.py 的 app 與 Breeze class,
# 所以 entrypoint 不能也叫 main(同一個 app 內不可重名)。
@app.local_entrypoint()
def exp5_all(arm: str = "Xbrz_gpu"):
    import hashlib

    def sha256(p_):
        h = hashlib.sha256()
        with open(p_, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    man = json.loads((ROOT / "corpus" / "audio_manifest.json").read_text())
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    stems = [f"{p['seg']}__{c}" for p in picks for c in CONDS]

    raw = ROOT / "results" / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "model": MODEL_ID, "revision": REVISION,
            "dtype": "float16", "device": "cuda", "gpu_class": "T4",
            "chunk_length_s": 0, "return_timestamps": True, "language_arg": None,
            "corpus": "exp5", "conds": CONDS,
            "note": "T4/fp16,與 exp2 Phase A 同設定;既有的 Xbrz_auto 是 CPU/fp32,"
                    "兩份都留著才比得出 dtype 差異(exp5 侷限 17)"}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    brz = Breeze()
    payload = []
    for s in todo:
        wav = COND / f"{s}.wav"
        if sha256(wav) != man["conditions"][s]["sha256"]:
            raise RuntimeError(f"{s}.wav 與 manifest 不符,先跑 exp5/scripts/degrade.py")
        payload.append((wav.read_bytes(), s))
    print(f"送出 {len(payload)} 個檔(sha256 全部與 manifest 相符)")

    for r in brz.run.starmap(payload):
        s = r["stem"]
        (raw / f"{s}.json").write_text(json.dumps({
            "arm": arm, "file": f"{s}.wav", "audio_s": r["audio_s"],
            "transcript": r["transcript"], "segments": r["segments"],
            "meta": {**meta, "elapsed_sec": r["elapsed_sec"],
                     "model_load_sec": r["load_sec"], "gpu": r["gpu"],
                     "transformers": r["transformers"], "torch": r["torch"],
                     "audio_sha256": man["conditions"][s]["sha256"],
                     "audio_matches_manifest": True},
        }, ensure_ascii=False, indent=1))
    print(f"\n→ {raw}")
