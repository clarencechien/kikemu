#!/usr/bin/env python3
"""handoff-v16 Phase 1:OpenRouter(+ Gemini 3.5 Transcribe 直連)批次 arm。設計寫死在 handoff-v16.md §8。

  python3 scripts/run_openrouter_arms.py --dry-run      # 只列工作與估價
  python3 scripts/run_openrouter_arms.py                # 全跑(已存在的檔跳過;失敗記缺格、不重試)
  python3 scripts/run_openrouter_arms.py --only ORgrok_ja --limit 1
  python3 scripts/run_openrouter_arms.py --exp exp5_zhwin          # 純中文 20 秒視窗補測

輸出:
  exp5 → exp5/results/raw/<arm>/<seg>__<cond>.json(欄位 transcript)
  exp1 → results/raw/<arm>/<seg>__<cond>.json(欄位 transcript)
  每個 arm 目錄一份 _meta.json:**只寫實際送出的欄位**(handoff §4)。
  失敗的格子不寫檔(不能讓計分把它當成空字串 = 0 分),記在 results/raw/_v16_failures.json。
帳本:results/raw/_v16_ledger.json(OpenRouter 用回應的 usage.cost;Gemini 直連用 token × 牌價)。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from openrouter_stt import transcribe  # noqa: E402

LEDGER = ROOT / "results" / "raw" / "_v16_ledger.json"
FAILURES = ROOT / "results" / "raw" / "_v16_failures.json"
FUSE_OR_USD = 5.0
GEMINI_PRICE = (2.00, 12.00)  # $/M in(audio)/ out;pricing 頁 2026-10-01 版
LOCK = threading.Lock()

# arm → (路徑, 模型)
MODELS = {
    "ORgrok": ("openrouter", "x-ai/grok-stt-1.0"),
    "ORscribe": ("openrouter", "elevenlabs/scribe-v2"),
    "GDtrans": ("gemini", "gemini-3.5-transcribe"),
    "ORqwen17": ("openrouter", "qwen/qwen3-asr-1.7b"),
    "ORqwen06": ("openrouter", "qwen/qwen3-asr-0.6b"),
    "ORmai2": ("openrouter", "microsoft/mai-transcribe-2"),
    "ORwhisperT": ("openrouter", "openai/whisper-large-v3-turbo"),
    "ORnemo": ("openrouter", "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b"),
    "ORgptT": ("openrouter", "openai/gpt-transcribe"),
    "ORmuse": ("openrouter", "meta/muse-voice-transcribe-1.0"),
    "ORasm35": ("openrouter", "assemblyai/universal-3-5-pro"),
    "ORasm36": ("openrouter", "assemblyai/universal-3-6-pro"),
    "XDgrok": ("xai", "grok-voice-transcribe-2.0"),  # §11:直連 xAI,明確指定 2.0
}
EXP1_ARMS = ["ORgrok", "ORscribe", "GDtrans", "XDgrok"]
FUSE_XAI_USD = 1.0
XAI_PRICE_HR = 0.10  # 批次牌價(任務書 §1;回應不含金額 → 用 duration 換算)
GEMINI_LANG = {"zh": "cmn-Hans-CN", "ja": "ja-JP"}
OR_PRICE_HR = {"x-ai/grok-stt-1.0": .10, "elevenlabs/scribe-v2": .11, "qwen/qwen3-asr-1.7b": .027, "qwen/qwen3-asr-0.6b": .012,
               "microsoft/mai-transcribe-2": .10, "openai/whisper-large-v3-turbo": .012,
               "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b": .012, "openai/gpt-transcribe": .27,
               "meta/muse-voice-transcribe-1.0": .18, "assemblyai/universal-3-5-pro": .30, "assemblyai/universal-3-6-pro": .30}


def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def jobs() -> list[dict]:
    out = []
    # exp5:M0 / M3,_auto 與 _zh,不開詞表(§8.4)
    for arm in MODELS:
        for setting, lang in (("auto", None), ("zh", "zh")):
            for seg in ("T1", "T2", "T3"):
                for cond in ("M0", "M3"):
                    out.append({"exp": "exp5", "arm": f"{arm}_{setting}", "base": arm, "lang": lang, "kt": False,
                                "seg": seg, "cond": cond, "wav": ROOT / f"exp5/corpus/conditions/{seg}__{cond}.wav",
                                "out": ROOT / f"exp5/results/raw/{arm}_{setting}/{seg}__{cond}.json"})
    # exp1:Gate A 三個 arm,N0–N4,_auto / _ja / _ja_kt(Scribe 經 OR 開不了詞表 → 沒有 _ja_kt,§8.4)
    picks = json.loads((ROOT / "corpus/picks.json").read_text())
    for arm in EXP1_ARMS:
        settings = [("auto", None, False), ("ja", "ja", False)] + ([] if arm == "ORscribe" else [("ja_kt", "ja", True)])
        for setting, lang, kt in settings:
            for seg in picks:
                for cond in ("N0", "N1", "N2", "N3", "N4"):
                    out.append({"exp": "exp1", "arm": f"{arm}_{setting}", "base": arm, "lang": lang, "kt": kt,
                                "seg": seg, "cond": cond, "domain": picks[seg]["domain"],
                                "wav": ROOT / f"corpus/conditions/{seg}__{cond}.wav",
                                "out": ROOT / f"results/raw/{arm}_{setting}/{seg}__{cond}.json"})
    # 補測(2026-10-15,§9 記一筆):exp5 純中文 20 秒視窗 × 8,只跑 _zh(Gate B 的主設定),不開詞表
    man = json.loads((ROOT / "exp5/corpus/zh_windows/manifest.json").read_text())["windows"]
    for arm in MODELS:
        for stem in man:
            seg, cond = stem.split("_", 1)
            out.append({"exp": "exp5_zhwin", "arm": f"Z{arm}_zh", "base": arm, "lang": "zh", "kt": False,
                        "seg": seg, "cond": cond, "wav": ROOT / f"exp5/corpus/zh_windows/{stem}.wav",
                        "out": ROOT / f"exp5/results/raw_zh/Z{arm}_zh/{stem}.json"})
    return out


VOCAB = json.loads((ROOT / "corpus/dict/speechmatics_vocab.json").read_text())


def _read_ledger() -> dict:
    L = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"openrouter_usd": 0.0, "gemini_usd": 0.0, "calls": 0}
    L.setdefault("xai_usd", 0.0)
    return L


def ledger() -> dict:
    # 讀也要上鎖:第一版沒鎖,另一條執行緒正在改寫時讀到空檔 → JSONDecodeError(2026-10-15 實際發生)
    with LOCK:
        return _read_ledger()


def add_ledger(or_usd: float = 0.0, g_usd: float = 0.0, x_usd: float = 0.0):
    with LOCK:
        L = _read_ledger()
        L["xai_usd"] = round(L["xai_usd"] + x_usd, 6)
        L["openrouter_usd"] = round(L["openrouter_usd"] + or_usd, 6)
        L["gemini_usd"] = round(L["gemini_usd"] + g_usd, 6)
        L["calls"] += 1
        tmp = LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps(L))
        tmp.replace(LEDGER)  # 原子替換


def add_failure(j: dict, info: dict):
    with LOCK:
        F = json.loads(FAILURES.read_text()) if FAILURES.exists() else []
        F.append({"arm": j["arm"], "file": f"{j['seg']}__{j['cond']}", "exp": j["exp"], **info, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        FAILURES.write_text(json.dumps(F, ensure_ascii=False, indent=1))


def gemini_direct(wav: Path, lang_code: str | None, vocab: list[str] | None) -> dict:
    body: dict = {"contents": [{"parts": [{"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav.read_bytes()).decode()}}]}]}
    cfg = {}
    if lang_code:
        cfg["languageCodes"] = [lang_code]
    if vocab:
        cfg["customVocabulary"] = vocab
    if cfg:
        body["generationConfig"] = {"audioTranscriptionConfig": cfg}
    sent = {k: v for k, v in body.items() if k != "contents"}
    sent["contents"] = "[audio/wav inline]"
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-transcribe:generateContent",
                                 data=json.dumps(body).encode(), headers={"x-goog-api-key": os.environ["gemini_key"], "Content-Type": "application/json"})
    t0 = time.time()
    try:
        d = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")[:300]
        except Exception:  # noqa: BLE001
            msg = ""
        return {"ok": False, "status": e.code, "latency_s": round(time.time() - t0, 2), "request": sent, "error": {"message": msg}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "latency_s": round(time.time() - t0, 2), "request": sent, "error": {"message": str(e)[:300]}}
    u = d.get("usageMetadata", {})
    usd = u.get("promptTokenCount", 0) / 1e6 * GEMINI_PRICE[0] + (u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)) / 1e6 * GEMINI_PRICE[1]
    cand = (d.get("candidates") or [{}])[0]
    if "content" not in cand:
        # 2026-10-15 實際發生:candidate 沒有 content(finishReason 非 STOP)。這是缺格,不是空字串
        return {"ok": False, "status": 200, "latency_s": round(time.time() - t0, 2), "request": sent, "usd": usd, "usage": u,
                "error": {"message": f"no content; finishReason={cand.get('finishReason')}", "finishReason": cand.get("finishReason"),
                          "promptFeedback": d.get("promptFeedback")}}
    parts = cand["content"]["parts"]
    text = "".join((p.get("audioTranscription") or {}).get("text", "") or p.get("text", "") for p in parts)
    return {"ok": True, "status": 200, "latency_s": round(time.time() - t0, 2), "request": sent, "text": text,
            "usage": u, "usd": usd, "modelVersion": d.get("modelVersion")}


def xai_direct(model: str, wav: Path, lang: str | None, vocab: list[str] | None) -> dict:
    """POST https://api.x.ai/v1/stt(multipart)。只送 file / model / language / keyterm(§11.3)"""
    fields = [("model", model)] + ([("language", lang)] if lang else []) + [("keyterm", v) for v in (vocab or [])]
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{wav.name}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + wav.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    sent = {"model": model, **({"language": lang} if lang else {}), **({"keyterm": vocab} if vocab else {}), "file": "[audio/wav]"}
    req = urllib.request.Request("https://api.x.ai/v1/stt", data=b"".join(parts),
                                 headers={"Authorization": "Bearer " + os.environ["xai_key"], "Content-Type": f"multipart/form-data; boundary={b}"})
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=300)
        d = json.load(r)
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "latency_s": round(time.time() - t0, 2), "request": sent,
                "error": {"message": e.read()[:300].decode(errors="replace")}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "latency_s": round(time.time() - t0, 2), "request": sent, "error": {"message": str(e)[:300]}}
    return {"ok": True, "status": 200, "latency_s": round(time.time() - t0, 2), "request": sent, "response": d,
            "trace_id": r.headers.get("x-trace-id")}


def run_one(j: dict) -> str:
    if j["out"].exists():
        return "skip"
    path, model = MODELS[j["base"]]
    vocab = None
    dropped = 0
    if j["kt"]:
        allv = [e["content"] for e in VOCAB[j["domain"]]]
        if path in ("openrouter", "xai"):  # xAI 上限 100,依檔案順序取前 100
            vocab, dropped = allv[:100], max(0, len(allv) - 100)
        else:
            vocab = allv[:1000]
    if ledger()["openrouter_usd"] >= FUSE_OR_USD:
        raise SystemExit(f"ABORT:OpenRouter 累計 ${ledger()['openrouter_usd']:.3f} ≥ 保險絲 ${FUSE_OR_USD}")
    if ledger()["xai_usd"] >= FUSE_XAI_USD:
        raise SystemExit(f"ABORT:xAI 累計 ${ledger()['xai_usd']:.3f} ≥ 保險絲 ${FUSE_XAI_USD}")
    secs = wav_seconds(j["wav"])
    if path == "xai":
        r = xai_direct(model, j["wav"], j["lang"], vocab)
        if not r["ok"]:
            add_failure(j, {"status": r["status"], "error": r.get("error"), "request": r["request"]})
            return f"FAIL {r['status']}"
        resp = r["response"]
        usd = (resp.get("duration") or secs) / 3600 * XAI_PRICE_HR
        add_ledger(x_usd=usd)
        rec = {"arm": j["arm"], "file": f"{j['seg']}__{j['cond']}", "audio_s": round(secs, 2), "transcript": resp.get("text") or "",
               "path": "xai-direct", "model": model, "request": r["request"], "detected_language": resp.get("language"),
               "duration": resp.get("duration"), "usd_est": round(usd, 6), "trace_id": r["trace_id"], "latency_s": r["latency_s"],
               "response_keys": sorted(resp)}
    elif path == "openrouter":
        r = transcribe(model, str(j["wav"]), language=j["lang"], provider_options=({"xai": {"keyterm": vocab}} if vocab else None), timeout=240)
        if not r["ok"]:
            add_failure(j, {"status": r["status"], "error": r.get("error"), "request": r["request"]})
            return f"FAIL {r['status']}"
        resp = r["response"]
        usd = (resp.get("usage") or {}).get("cost") or 0.0
        add_ledger(or_usd=usd)
        rec = {"arm": j["arm"], "file": f"{j['seg']}__{j['cond']}", "audio_s": round(secs, 2), "transcript": resp.get("text") or "",
               "path": "openrouter", "model": model, "request": r["request"], "usage": resp.get("usage"),
               "generation_id": (r.get("headers") or {}).get("X-Generation-Id"), "latency_s": r["latency_s"]}
    else:
        r = gemini_direct(j["wav"], GEMINI_LANG.get(j["lang"]) if j["lang"] else None, vocab)
        if not r["ok"] and r.get("usd"):
            add_ledger(g_usd=r["usd"])  # 沒產出也可能被計費
        if not r["ok"]:
            add_failure(j, {"status": r["status"], "error": r.get("error"), "request": r["request"]})
            return f"FAIL {r['status']}"
        add_ledger(g_usd=r["usd"])
        rec = {"arm": j["arm"], "file": f"{j['seg']}__{j['cond']}", "audio_s": round(secs, 2), "transcript": r["text"],
               "path": "gemini-direct", "model": model, "modelVersion": r["modelVersion"], "request": r["request"],
               "usage": r["usage"], "usd": round(r["usd"], 6), "latency_s": r["latency_s"]}
    if vocab is not None:
        rec["keyterms_sent"] = len(vocab)
        if dropped:
            rec["keyterms_dropped_over_cap"] = dropped
    j["out"].parent.mkdir(parents=True, exist_ok=True)
    j["out"].write_text(json.dumps(rec, ensure_ascii=False))
    write_meta(j, model, path, vocab)
    return "ok"


def write_meta(j: dict, model: str, path: str, vocab):
    meta_p = j["out"].parent / "_meta.json"
    if meta_p.exists():
        return
    sent = {"model": model}
    if j["lang"]:
        if path == "gemini":
            sent["languageCodes"] = [GEMINI_LANG[j["lang"]]]
        else:
            sent["language"] = j["lang"]
    if vocab is not None:
        sent[{"openrouter": "provider.options.xai.keyterm", "xai": "keyterm(重複欄位)", "gemini": "customVocabulary"}[path]] = (
            "scene pack corpus/dict/speechmatics_vocab.json[domain], file order" + (", all" if path == "gemini" else ", first 100 (xAI cap)"))
    meta = {"arm": j["arm"], "handoff": "handoff-v16.md", "corpus": j["exp"],
            "path": {"openrouter": "經 OpenRouter /api/v1/audio/transcriptions", "gemini": "直連 Gemini API generateContent",
                     "xai": "直連 xAI POST /v1/stt(multipart)"}[path],
            "sent": sent,
            "note": "只列實際送出的欄位;沒列 = 沒送(zdr、temperature、diarize、prompt、response_format 皆未送)"}
    if j["base"] == "XDgrok":
        meta["version_note"] = "明確送 model=grok-voice-transcribe-2.0(伺服器會驗證 model,不存在的 ID 回 404);回應沒有版本欄位,依使用者指示當作 2.0(handoff-v16 §11.2)"
        meta["note"] = "只列實際送出的欄位;沒列 = 沒送(diarize、format、timestamps 皆未送)"
    if j["base"] == "ORgrok":
        meta["version_caveat"] = "OpenRouter slug x-ai/grok-stt-1.0,上游是 1.0 還是 2.0 無法判定(results/openrouter-recon.md §1)"
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--exp", default="")
    ap.add_argument("--base", default="", help="只跑某個模型(MODELS 的 key,例:XDgrok)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    J = [j for j in jobs() if (not a.only or j["arm"] == a.only) and (not a.exp or j["exp"] == a.exp) and (not a.base or j["base"] == a.base)]
    todo = [j for j in J if not j["out"].exists()]
    if a.limit:
        todo = todo[: a.limit]
    est_or = sum(wav_seconds(j["wav"]) / 3600 * OR_PRICE_HR[MODELS[j["base"]][1]] for j in todo if MODELS[j["base"]][0] == "openrouter")
    est_g = sum(wav_seconds(j["wav"]) / 3600 * 0.30 for j in todo if MODELS[j["base"]][0] == "gemini")
    est_x = sum(wav_seconds(j["wav"]) / 3600 * XAI_PRICE_HR for j in todo if MODELS[j["base"]][0] == "xai")
    print(f"工作 {len(J)},待跑 {len(todo)};估 OpenRouter ${est_or:.2f} + Gemini 直連 ${est_g:.2f} + xAI 直連 ${est_x:.2f};帳本 {ledger()}")
    if a.dry_run:
        from collections import Counter
        print(Counter(j["arm"] for j in todo))
        return
    if ledger()["xai_usd"] + est_x > FUSE_XAI_USD:
        raise SystemExit("ABORT:xAI 估價超過保險絲")
    if ledger()["openrouter_usd"] + est_or > FUSE_OR_USD:
        raise SystemExit("ABORT:估價超過保險絲")
    # 同一個 arm 內循序,不同 arm 並行(避免單一供應商被併發壓到)
    by_arm: dict[str, list] = {}
    for j in todo:
        by_arm.setdefault(j["arm"], []).append(j)

    def run_arm(arm):
        res = []
        for j in by_arm[arm]:
            res.append(run_one(j))
        return arm, res

    with cf.ThreadPoolExecutor(a.workers) as ex:
        for arm, res in ex.map(run_arm, sorted(by_arm)):
            print(f"  {arm:16} {res.count('ok')} ok / {sum(1 for r in res if r.startswith('FAIL'))} fail / {res.count('skip')} skip  帳本 {ledger()}", flush=True)


if __name__ == "__main__":
    main()
