#!/usr/bin/env python3
"""純中文 20 秒視窗上的 Gemini 批次與 Speechmatics 批次(對照組)。

與 `run_zh_modal.py`(Gemma)跑同一批視窗、同一份參考,指標是 CER。
每個引擎都用**它自己的最佳呼叫方式**——SM 用 cmn_en 雙語包、Gemini 用
exp5 Gbat arm 的 prompt、Gemma 用模型卡官方 prompt。混用別人的 prompt
量到的會是「誰比較耐受別人的 prompt」,不是聽力。
"""
import base64, json, os, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "corpus" / "zh_windows"
OUT = ROOT / "results" / "raw_zh"
G_KEY, S_KEY = os.environ["gemini_key"], os.environ["s_key"]
G_MODEL = "gemini-3.5-flash"
G_PROMPT = ("逐字轉寫這段音訊。說話者混用中文與英文,英文詞請原樣保留,不要翻譯。"
            "不要摘要、不要加說話者標記、不要加時間碼,只輸出轉寫文字。")


def gemini(wav: Path) -> tuple[str, float]:
    body = {"contents": [{"role": "user", "parts": [
        {"text": G_PROMPT},
        {"inline_data": {"mime_type": "audio/wav",
                         "data": base64.b64encode(wav.read_bytes()).decode()}}]}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"},
                             "temperature": 0.0}}
    t0 = time.time()
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{G_MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY}, json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    txt = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]
                  if "text" in p and not p.get("thought"))
    return txt.strip(), round(time.time() - t0, 1)


def sm(wav: Path) -> tuple[str, float]:
    BASE = "https://asr.api.speechmatics.com/v2"
    cfg = {"type": "transcription",
           "transcription_config": {"language": "cmn_en", "operating_point": "enhanced"}}
    t0 = time.time()
    r = requests.post(f"{BASE}/jobs", headers={"Authorization": f"Bearer {S_KEY}"},
                      files={"data_file": (wav.name, wav.read_bytes()),
                             "config": (None, json.dumps(cfg))}, timeout=120)
    r.raise_for_status()
    job = r.json()["id"]
    while True:
        st = requests.get(f"{BASE}/jobs/{job}", headers={"Authorization": f"Bearer {S_KEY}"},
                          timeout=60).json()
        if "job" not in st:
            raise RuntimeError(f"job {job} 查不到(可能已被刪除):{st}")
        if st["job"]["status"] != "running":
            break
        time.sleep(3)
    tr = requests.get(f"{BASE}/jobs/{job}/transcript?format=txt",
                      headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60)
    # SM 不回 charset header,requests 會預設 ISO-8859-1 把 UTF-8 解成亂碼。
    # 一定要自己指定,否則中文全毀(踩過)。
    return tr.content.decode("utf-8").strip(), round(time.time() - t0, 1)


def main() -> None:
    man = json.loads((WIN / "manifest.json").read_text())["windows"]
    for arm, fn, meta in (("Zg", gemini, {"model": G_MODEL, "prompt": G_PROMPT,
                                          "thinkingLevel": "minimal"}),
                          ("Zsm", sm, {"engine": "speechmatics batch",
                                       "language": "cmn_en",
                                       "operating_point": "enhanced"})):
        d = OUT / arm
        d.mkdir(parents=True, exist_ok=True)
        (d / "_meta.json").write_text(json.dumps(
            {"arm": arm, "corpus": "exp5 純中文 20 秒視窗", **meta},
            ensure_ascii=False, indent=1))
        for stem in man:
            f = d / f"{stem}.json"
            if f.exists():
                continue
            txt, el = fn(WIN / f"{stem}.wav")
            f.write_text(json.dumps(
                {"arm": arm, "file": f"{stem}.wav", "audio_s": 20.0,
                 "transcript": txt,
                 "meta": {**meta, "elapsed_sec": el,
                          "audio_sha256": man[stem]["sha256"]}},
                ensure_ascii=False, indent=1))
            print(f"  [{arm}] {stem} {el}s {len(txt)}字 | {txt[:60]}", flush=True)


if __name__ == "__main__":
    main()
