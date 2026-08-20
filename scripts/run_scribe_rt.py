#!/usr/bin/env python3
"""handoff-v10 S4:ElevenLabs Scribe v2 Realtime(WebSocket)。

**這是主線那格**:kikemu 的產線是 Speechmatics 串流,Scribe v2 Realtime 是它的
直接競爭者(~150ms、WebSocket、90+ 語言)。批次那格已經量到 Scribe 的引擎
在日文上勝 SM +0.096(報告 §2.1d),但 **Gemini 的教訓正好是「批次好不代表即時好」**
(批次 0.567 vs Live 0.030),所以一定要單獨量。

**與 `run_speechmatics_rt.py` 對齊的地方**(否則兩邊的數字不能比):

  · 音訊以**真實 1× 速度**推流,0.5 秒 PCM 一塊
  · 每一則伺服器訊息都記下 wall-clock 與「已推入的音訊秒數」
  · log 的事件名沿用 SM 那支的命名(`audio` / `AddPartialTranscript` /
    `AddTranscript`),這樣 `score.py` 的 `sm_latency` / `sm_rewrite_rate`
    不用改就能吃

**協定(2026-08-20 probe 實測):**

  model_id = scribe_v2_realtime         ← `scribe_v2` 會被拒(1008 invalid_request)
  送 {"message_type":"input_audio_chunk","audio_base_64":...,"sample_rate":16000}
  收 partial_transcript                  只有 text,**沒有時間戳**
     committed_transcript_with_timestamps  有詞級 start/end ✅

**因此暫定延遲與 SM 不可直接比**(見 handoff-v10 §4c),定稿延遲與改寫率可以比。

用法:
    python3 scripts/run_scribe_rt.py Srt_ja            # 50 詞(與即時上限一致)
    python3 scripts/run_scribe_rt.py Srt_ja_nokt --no-keyterms
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import soundfile as sf
import websockets

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
KEY = os.environ["ELEVENLABS_API_KEY"]
BASE = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
MODEL = "scribe_v2_realtime"
CHUNK_S = 0.5
SR = 16000
# 即時的官方上限:50 詞 × 20 字元。SM 是 1000 詞 + 假名讀音,
# 所以詞表對齊控制組(Cplus50)也要砍到同樣 50 條、拿掉 sounds_like。
KEYTERM_CAP = 50
PICKS = json.loads((ROOT / "corpus" / "picks.json").read_text())
VOCAB = json.loads((ROOT / "corpus" / "dict" / "speechmatics_vocab.json").read_text())


def url_for(seg_id: str, use_kt: bool) -> tuple[str, int]:
    q = [("model_id", MODEL), ("language_code", "ja"),
         ("audio_format", f"pcm_{SR}"), ("commit_strategy", "vad"),
         ("include_timestamps", "true")]
    kt = []
    if use_kt:
        kt = [t["content"] for t in VOCAB[PICKS[seg_id]["domain"]]][:KEYTERM_CAP]
        q += [("keyterms", k) for k in kt]
    return BASE + "?" + urllib.parse.urlencode(q), len(kt)


async def run_one(arm: str, wav: Path, out: Path, use_kt: bool):
    x, sr = sf.read(wav, dtype="int16")
    assert sr == SR
    pcm = x.tobytes()
    chunk = int(SR * CHUNK_S) * 2
    url, n_kt = url_for(wav.stem.split("__")[0], use_kt)
    log = []
    t0 = time.monotonic()

    def ev(kind, **kw):
        log.append({"t": round(time.monotonic() - t0, 3), "kind": kind, **kw})

    async with websockets.connect(url, additional_headers={"xi-api-key": KEY},
                                  max_size=2 ** 24) as ws:
        ev("session_open", n_keyterms=n_kt)

        async def feeder():
            sent = 0
            next_t = time.monotonic()
            while sent < len(pcm):
                blk = pcm[sent:sent + chunk]
                sent += chunk
                await ws.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(blk).decode(),
                    "sample_rate": SR}))
                ev("audio", audio_s=round(min(sent, len(pcm)) / 2 / SR, 3))
                next_t += CHUNK_S
                await asyncio.sleep(max(0, next_t - time.monotonic()))
            # 最後一塊帶 commit,把尾巴逼出來
            await ws.send(json.dumps({"message_type": "input_audio_chunk",
                                      "audio_base_64": "", "commit": True,
                                      "sample_rate": SR}))
            ev("commit_sent")

        feed = asyncio.create_task(feeder())
        committed, msgs = [], []
        audio_s = len(pcm) / 2 / SR
        try:
            # 音訊推完之後再等 20 秒收尾;VAD 策略下 commit 可能落後
            async with asyncio.timeout(audio_s + 20):
                async for raw in ws:
                    m = json.loads(raw)
                    k = m.get("message_type", "?")
                    msgs.append(m)
                    if k == "partial_transcript":
                        # SM 的命名,讓 score.py 不用改。**沒有 end_time**
                        # ——暫定延遲與 SM 不可直接比(handoff-v10 §4c)。
                        ev("AddPartialTranscript", transcript=m.get("text"))
                    elif k == "committed_transcript_with_timestamps":
                        w = m.get("words") or []
                        committed.append(m.get("text", ""))
                        ev("AddTranscript", transcript=m.get("text"),
                           start_time=w[0]["start"] if w else None,
                           end_time=w[-1]["end"] if w else None,
                           n_words=len(w))
                    elif k == "committed_transcript":
                        ev("committed_plain", transcript=m.get("text"))
                    elif k in ("error", "Error"):
                        raise RuntimeError(json.dumps(m, ensure_ascii=False))
                    else:
                        ev(k)
        except TimeoutError:
            ev("timeout_stop")
        finally:
            feed.cancel()

    out.write_text(json.dumps({
        "arm": arm, "file": wav.name, "audio_s": round(audio_s, 2),
        "transcript": "".join(committed),
        "meta": {"engine": "ElevenLabs Scribe v2 Realtime", "model_id": MODEL,
                 "n_keyterms": n_kt, "keyterm_cap": KEYTERM_CAP,
                 "commit_strategy": "vad", "include_timestamps": True,
                 "chunk_s": CHUNK_S, "rate_usd_per_hour": 0.39,
                 "rate_checked": "2026-08-20",
                 "partial_latency_note": "partial_transcript 無時間戳,"
                                         "暫定延遲與 SM 不可直接比(handoff-v10 §4c)"},
        "log": log,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"done {arm} {wav.name} ({len(''.join(committed))} ch, "
          f"{sum(1 for e in log if e['kind']=='AddPartialTranscript')} partials, "
          f"{len(committed)} commits)", flush=True)


async def main():
    arm = sys.argv[1]
    use_kt = "--no-keyterms" not in sys.argv
    workers = 2
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    outdir = ROOT / "results" / "raw" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(w, outdir / f"{w.stem}.json") for w in sorted(COND.glob("*.wav"))
            if not (outdir / f"{w.stem}.json").exists()]
    sem = asyncio.Semaphore(workers)

    async def guarded(wav, out):
        async with sem:
            for attempt in range(3):
                try:
                    return await run_one(arm, wav, out, use_kt)
                except Exception as e:
                    print(f"ERR {wav.name} attempt {attempt}: {e}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))

    print(f"{len(jobs)} 檔,{workers} 條並行,1× 實時推流")
    await asyncio.gather(*(guarded(w, o) for w, o in jobs))
    print(f"→ {outdir}")


if __name__ == "__main__":
    asyncio.run(main())
