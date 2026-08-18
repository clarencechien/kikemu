#!/usr/bin/env python3
"""依 picks.json 取得 exp5 的三段切片(RSS → MP3 → 5 分鐘 16k mono wav)。

音訊不進 repo(同 exp1/exp2 慣例):只存 RSS enclosure URL、視窗位移與 sha256,
任何人都能用這支腳本重建同一份檔案。視窗由 pick_windows.py 依固定規則選出,
在任何 arm 跑之前就決定好了。
"""
import hashlib, json, subprocess, sys
from pathlib import Path

import imageio_ffmpeg
import requests

FF = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "corpus" / "wav"
CACHE = Path("/tmp/exp5-cache")


def fetch_mp3(url: str, seg: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    mp3 = CACHE / f"{seg}.mp3"
    if mp3.exists():
        return mp3
    # 直接把 CDN 的重導向 URL 餵給 ffmpeg 會 segfault,先整檔抓下來
    with requests.get(url, stream=True, timeout=300,
                      headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with open(mp3, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return mp3


def main():
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    WAV.mkdir(parents=True, exist_ok=True)
    for p in picks:
        out = WAV / f"{p['seg']}.wav"
        if not out.exists():
            mp3 = fetch_mp3(p["url"], p["seg"])
            subprocess.run(
                [FF, "-y", "-ss", str(p["start"]), "-i", str(mp3), "-t", str(p["dur"]),
                 "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)],
                check=True, capture_output=True,
            )
        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        want = p.get("sha256")
        mark = "" if not want else ("  ✅ 與 manifest 相符" if sha == want else "  ⚠️ 指紋不符")
        print(f"  {p['seg']}.wav  {out.stat().st_size/1e6:.1f}MB  {sha[:16]}{mark}")
        p["sha256"] = sha
    (ROOT / "corpus" / "picks.json").write_text(json.dumps(picks, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
