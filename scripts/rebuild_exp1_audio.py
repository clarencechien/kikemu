#!/usr/bin/env python3
"""重建 exp1 音檔(音檔依授權不進版控,見 README「音檔與授權」)。

**這個 repo 原本缺這一步的紀錄**:`fetch.py` 只解析頁面產生 `articles.json`,
`degrade.py` 從 `corpus/wav/` 讀,但「mp3 → 16 kHz 單聲道 wav」與兩個噪音/RIR
素材的下載從沒寫成腳本。2026-10-04 為 handoff-v12 重跑時補上。

步驟(全部寫進 gitignored 路徑):
  1. 依 committed 的 `corpus/articles.json` + `picks.json` 下載 6 個 mp3 → corpus/raw/
     (自訂 UA、逐檔間隔 3 秒,不並行,與 fetch.py 相同的禮貌設定)
  2. ffmpeg 轉 16 kHz / 單聲道 / PCM16 → corpus/wav/
  3. 下載 MIT IR Survey(Audio.zip → mit_ir.zip)與 DEMAND PCAFETER_16k.zip
     → corpus/noise_src/(DEMAND 附 md5,下載後驗證)
  4. 之後跑 `python3 scripts/degrade.py` 產生 corpus/conditions/*.wav
  5. `python3 scripts/rebuild_exp1_audio.py --verify` 對三個指紋:
       · 每檔時長 vs 舊結果 results/raw/Cplus/*.json 的 audio_s
       · degrade.py 算的各條件 RMS vs committed 的 degrade_meta.json(4 位小數)
     (第三個指紋——SM 批次重跑一檔、與舊 Cb 轉寫逐字比對——要花錢,另外跑)

⚠️ 原始轉檔參數沒有紀錄,這裡用 ffmpeg 預設重取樣器。**位元組級相同不保證**,
所以才要指紋;指紋對不上就不能拿新舊數字直接比。
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
RAW, WAV, NSRC = CORPUS / "raw", CORPUS / "wav", CORPUS / "noise_src"
UA = "Mozilla/5.0 (kikemu-eval; private evaluation; contact clarence.chien@gmail.com)"
MIT_ZIP = "https://mcdermottlab.mit.edu/Reverb/IRMAudio/Audio.zip"
DEMAND = ("https://zenodo.org/api/records/1227121/files/PCAFETER_16k.zip/content",
          "99927d148128254141a9417d051510bb")


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg  # pip install imageio-ffmpeg(靜態二進位)
    return imageio_ffmpeg.get_ffmpeg_exe()


def download(url: str, dest: Path, md5: str | None = None) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  已有 {dest.relative_to(ROOT)}")
        return
    print(f"  下載 {url}")
    with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=120) as r:
        r.raise_for_status()
        h = hashlib.md5()
        with open(dest, "wb") as f:
            for b in r.iter_content(1 << 20):
                f.write(b)
                h.update(b)
    if md5 and h.hexdigest() != md5:
        dest.unlink()
        raise SystemExit(f"md5 不符:{dest.name} 期望 {md5} 實得 {h.hexdigest()}")


def build() -> None:
    for d in (RAW, WAV, NSRC):
        d.mkdir(parents=True, exist_ok=True)
    arts = {a["slug"]: a for a in json.loads((CORPUS / "articles.json").read_text())}
    picks = json.loads((CORPUS / "picks.json").read_text())
    exe = ffmpeg()
    print("1–2. mp3 → 16 kHz 單聲道 wav")
    for seg, p in picks.items():
        url = arts[p["slug"]]["sections"][p["sections"][0]]["mp3"]
        mp3 = RAW / f"{seg}.mp3"
        if not mp3.exists():
            download(url, mp3)
            time.sleep(3.0)
        out = WAV / p["wav"]
        subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(mp3),
                        "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)], check=True)
        print(f"  {seg} → {out.relative_to(ROOT)}")
    print("3. 噪音 / RIR 素材")
    download(MIT_ZIP, NSRC / "mit_ir.zip")
    download(DEMAND[0], NSRC / "PCAFETER_16k.zip", DEMAND[1])
    print("\n下一步:python3 scripts/degrade.py && python3 scripts/rebuild_exp1_audio.py --verify")


def verify() -> bool:
    """degrade.py 跑完會**覆寫** degrade_meta.json(各條件的整段 RMS,4 位小數)。
    所以指紋 = 「重建後的 meta」對「git HEAD 裡 committed 的 meta」逐值比對,
    加上每檔時長對舊結果的 audio_s。"""
    import soundfile as sf

    new = json.loads((CORPUS / "conditions" / "degrade_meta.json").read_text())
    old_meta = json.loads(subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:corpus/conditions/degrade_meta.json"],
        capture_output=True, text=True, check=True).stdout)
    ok = True
    for k in ("rir", "rir_t60", "noise", "seed"):
        same = new[k] == old_meta[k]
        ok &= same
        print(f"  {k:<8} {'✅' if same else '❌'} {new[k]}")
    print(f"\n{'檔':<20}{'時長(新)':>10}{'時長(舊)':>10}{'RMS(新)':>10}{'RMS(舊)':>10}")
    for seg, conds in old_meta["conditions"].items():
        for cond, rms_old in conds.items():
            f = CORPUS / "conditions" / f"{seg}__{cond}.wav"
            info = sf.info(f)
            dur = round(info.frames / info.samplerate, 2)
            dur_old = json.loads((ROOT / "results/raw/Cplus" / f"{seg}__{cond}.json").read_text())["audio_s"]
            rms_new = new["conditions"][seg][cond]
            good = abs(dur - dur_old) < 0.011 and rms_new == rms_old
            ok &= good
            print(f"{seg+'__'+cond:<20}{dur:>10.2f}{dur_old:>10.2f}{rms_new:>10.4f}{rms_old:>10.4f}  {'✅' if good else '❌'}")
    print("\n指紋", "全部相符 ✅" if ok else "有不符 ❌ —— 新舊數字不可直接比")
    return ok


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(0 if verify() else 1)
    build()
