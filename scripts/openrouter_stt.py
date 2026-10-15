#!/usr/bin/env python3
"""經 OpenRouter `/api/v1/audio/transcriptions` 跑批次 STT(kikemu-openrouter-stt-handoff)。

一支共用的呼叫函式:實際送出的 request body(去掉音訊)原樣回傳,呼叫端寫進 _meta / 每檔 JSON。
**沒送的欄位不會出現在紀錄裡**——handoff §4、CLAUDE.md「我們沒開 ≠ 它沒有」。

金鑰:環境變數 `or_key`(handoff 寫 OPENROUTER_API_KEY,本環境的名稱是 or_key;兩個都認)。
ZDR:2026-10-15 使用者決定「先不算 ZDR」→ 本輪**不送** `provider.zdr`(grok / gemini-3.5-transcribe /
gpt-transcribe / muse 在 zdr=true 時 404,見 results/openrouter-recon.md)。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/audio/transcriptions"
KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ["or_key"]


def transcribe(model: str, wav_path: str, *, language: str | None = None, response_format: str | None = None,
               provider_options: dict | None = None, timeout: int = 180) -> dict:
    """回傳 {ok, status, latency_s, request(去音訊), response 或 error}。不重試——失敗就是一格缺格。"""
    body: dict = {"model": model, "input_audio": {"data": base64.b64encode(open(wav_path, "rb").read()).decode(), "format": "wav"}}
    if language:
        body["language"] = language
    if response_format:
        body["response_format"] = response_format
    if provider_options:
        body["provider"] = {"options": provider_options}
    sent = {k: v for k, v in body.items() if k != "input_audio"}
    sent["input_audio"] = {"format": "wav", "file": os.path.basename(wav_path)}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        hdr = {k: v for k, v in r.headers.items() if k.lower().startswith(("x-generation", "x-openrouter", "openrouter"))}
        return {"ok": True, "status": r.status, "latency_s": round(time.time() - t0, 2), "request": sent, "headers": hdr, "response": json.load(r)}
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            err = json.loads(raw).get("error", {})
        except Exception:  # noqa: BLE001
            err = {"message": raw[:300].decode(errors="replace")}
        return {"ok": False, "status": e.code, "latency_s": round(time.time() - t0, 2), "request": sent,
                "error": {"message": str(err.get("message", ""))[:300], "metadata": err.get("metadata")}}
    except Exception as e:  # noqa: BLE001 — 逾時 / 連線中斷
        return {"ok": False, "status": None, "latency_s": round(time.time() - t0, 2), "request": sent, "error": {"message": str(e)[:300]}}
