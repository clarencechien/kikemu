#!/usr/bin/env python3
"""exp5 的 M0–M3:直接呼叫 exp2/scripts/degrade.py 的 build()。

**刻意不自己實作。** 另外複製一份就等於換了聲學條件(RIR、噪音檔、SNR、種子
任一不同都會讓數字不可比),那 exp5 就失去「與 exp2 同一把尺」的意義。

用 importlib 以 exp2_degrade 之名載入,不能直接 `from degrade import`:
exp2 那支自己也有 `from degrade import ...`(指向 v1 的 scripts/degrade.py),
同名匯入會撞成循環匯入。
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXP2 = ROOT.parent / "exp2" / "scripts" / "degrade.py"

spec = importlib.util.spec_from_file_location("exp2_degrade", EXP2)
exp2_degrade = importlib.util.module_from_spec(spec)
sys.modules["exp2_degrade"] = exp2_degrade
spec.loader.exec_module(exp2_degrade)

if __name__ == "__main__":
    segs = [p["seg"] for p in json.loads((ROOT / "corpus" / "picks.json").read_text())]
    exp2_degrade.build(wav_dir=ROOT / "corpus" / "wav",
                       out_dir=ROOT / "corpus" / "conditions", segs=segs)
