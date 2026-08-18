#!/usr/bin/env python3
"""領域外語料候選的晶晶體密度探針。

不猜、直接量:抓一段 → 切 5 分鐘 → Speechmatics cmn_en 轉寫 → 算拉丁段密度,
與 exp2 現有語料(S1 10.4、S2 4.0、S3 4.8 個/分鐘)對照。
音檔只在暫存區用完即丟,不落 repo。
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

import requests
import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
S_KEY = os.environ["s_key"]
BASE = "https://asr.api.speechmatics.com/v2"
SP = Path("/tmp/claude-0/-home-user-kikemu/5605051a-5678-5cbe-839f-18cc17c6d816/scratchpad/probe")
SP.mkdir(exist_ok=True)


def slice_audio(url: str, tag: str, start: int, dur: int = 300) -> Path:
    """從節目中段切一段 16k mono wav(避開開場白與廣告)。
       先整檔下載再切:ffmpeg 直接吃這些 CDN 的重導向 URL 會 segfault。"""
    out = SP / f"{tag}.wav"
    if out.exists():
        return out
    mp3 = SP / f"{tag}.mp3"
    if not mp3.exists():
        with requests.get(url, stream=True, timeout=180,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            with open(mp3, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    subprocess.run(
        [FF, "-y", "-ss", str(start), "-i", str(mp3), "-t", str(dur),
         "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)],
        check=True, capture_output=True,
    )
    return out


def transcribe(wav: Path) -> str:
    conf = {"type": "transcription",
            "transcription_config": {"language": "cmn_en", "operating_point": "enhanced"}}
    with open(wav, "rb") as f:
        r = requests.post(f"{BASE}/jobs", headers={"Authorization": f"Bearer {S_KEY}"},
                          files={"data_file": (wav.name, f, "audio/wav"),
                                 "config": (None, json.dumps(conf))}, timeout=120)
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(120):
        time.sleep(5)
        s = requests.get(f"{BASE}/jobs/{job}", headers={"Authorization": f"Bearer {S_KEY}"},
                         timeout=60).json()["job"]["status"]
        if s == "done":
            break
        if s == "rejected":
            raise RuntimeError("SM rejected")
    t = requests.get(f"{BASE}/jobs/{job}/transcript?format=txt",
                     headers={"Authorization": f"Bearer {S_KEY}"}, timeout=120)
    return t.content.decode("utf-8")


def density(text: str, minutes: float):
    runs = [r for r in re.findall(r"[A-Za-z][A-Za-z0-9\-\.\+#]*(?:\s+[A-Za-z][A-Za-z0-9\-\.\+#]*)*", text)
            if len(r) > 1]
    zh = len(re.findall(r"[一-鿿]", text))
    uniq = {r.lower() for r in runs}
    return len(runs) / minutes, zh, runs, uniq


if __name__ == "__main__":
    cands = json.loads(Path(sys.argv[1]).read_text())
    for c in cands:
        wav = slice_audio(c["url"], c["tag"], c.get("start", 600))
        txt = transcribe(wav)
        (SP / f"{c['tag']}.txt").write_text(txt)
        d, zh, runs, uniq = density(txt, 5.0)
        print(f"\n### {c['tag']} — {c['name']}")
        print(f"  拉丁段 {len(runs)} 個 = **{d:.1f} 個/分鐘**(exp2 標尺 S1 10.4 / S2 4.0 / S3 4.8)")
        print(f"  中文 {zh} 字 / 不重複英文詞 {len(uniq)} 種")
        print(f"  詞例: {', '.join(sorted(uniq, key=len, reverse=True)[:14])}")
