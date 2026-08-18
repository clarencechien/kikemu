#!/usr/bin/env python3
"""exp5 arm X_bi:Speechmatics realtime cmn_en(無詞表),1× 推流。

直接重用 exp2/scripts/run_sm_rt.py 的實作(同一份 config、同樣的 1× pacing、
同樣的重試),只把語料與輸出目錄換掉。另寫一份就等於換了一個 arm 的定義。
"""
import asyncio, importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "exp2_sm_rt", ROOT.parent / "exp2" / "scripts" / "run_sm_rt.py")
m = importlib.util.module_from_spec(spec); sys.modules["exp2_sm_rt"] = m
spec.loader.exec_module(m)
m.COND = ROOT / "corpus" / "conditions"
m.ROOT = ROOT
asyncio.run(m.main())
