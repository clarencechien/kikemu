#!/usr/bin/env python3
"""exp5 arm G:Gemini Live 一體式,1× 推流。重用 exp2 的 wrapper,只換路徑。"""
import asyncio, importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "exp2_g_live", ROOT.parent / "exp2" / "scripts" / "run_g_live.py")
m = importlib.util.module_from_spec(spec); sys.modules["exp2_g_live"] = m
spec.loader.exec_module(m)
m.v1.COND = ROOT / "corpus" / "conditions"
m.ROOT = ROOT
asyncio.run(m.main())
