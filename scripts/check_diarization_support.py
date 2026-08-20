#!/usr/bin/env python3
"""查證各家 STT 的 **diarization 能力欄位**,寫成可追溯的證據檔。

**為什麼有這支:** `stt-matrix.md` 曾經有四處寫「diarization 只有 SM 有」,
那是**錯的**,而且錯法很典型——`run_scribe_batch.py` 在 `_meta.json` 記了
`"diarize": False`(意思是「這次沒開」),被讀成「它沒有這個功能」,
然後擴散到 README 的決策句與決策樹的一個分支。

所以能力宣稱要有出處。這支**不花錢**(只讀公開的 API spec,不送音訊),
跑完把結果寫進 `results/diarization_support.json`,文件引用那個檔。

⚠️ **這支只證明「參數存在」,不證明「分得準」。**
本專案**沒有多語者語料**,兩家的 diarization 品質**一個數字都沒量過**
(侷限 26)。不要把這個檔的內容寫成品質結論——那正是鐵律 9 講的外推。

用法:
    python3 scripts/check_diarization_support.py
    python3 scripts/check_diarization_support.py --print   # 只印不寫檔
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "diarization_support.json"

EL_OPENAPI = "https://api.elevenlabs.io/openapi.json"
EL_STT_PATH = "/v1/speech-to-text"

# Speechmatics 沒有公開可直接抓的 OpenAPI/AsyncAPI(2026-08-20 試過三個 URL 全 404,
# 見下面的 sm_note)。所以 SM 那格**標為未查證**,不要憑印象填。
SM_TRIED = ["https://asr.api.speechmatics.com/v2/openapi.json",
            "https://docs.speechmatics.com/openapi.json",
            "https://raw.githubusercontent.com/speechmatics/speechmatics-python/main/asyncapi.yml"]

# channel 也要抓:會議如果每人一支麥,**分軌比 diarization 可靠得多**,
# 那是另一條路而不是同一條(見 stt-matrix §6c)。
KEY_RE = re.compile(r"diar|speaker|channel", re.I)


def elevenlabs() -> dict:
    spec = requests.get(EL_OPENAPI, timeout=60).json()
    post = spec["paths"][EL_STT_PATH]["post"]
    names = re.findall(r'"#/components/schemas/([^"]+)"',
                       json.dumps(post["requestBody"]))
    req = {}
    for n in names:
        for prop, v in spec["components"]["schemas"][n].get("properties", {}).items():
            if KEY_RE.search(prop):
                req[prop] = {"type": v.get("type") or "anyOf",
                             "default": v.get("default"),
                             "desc": (v.get("description") or "").strip()[:200]}
    # 回傳的詞物件有沒有 speaker_id,決定它是不是**詞級**的語者標註
    word = spec["components"]["schemas"].get("SpeechToTextWordResponseModel", {})
    return {"request_params": req,
            "word_fields": list(word.get("properties", {})),
            "word_level_speaker_id": "speaker_id" in word.get("properties", {}),
            "source": EL_OPENAPI,
            "note": "批次端點。即時 WebSocket 不在這份 OpenAPI 裡,**未查證**。"}


def speechmatics() -> dict:
    tried = {}
    for u in SM_TRIED:
        try:
            tried[u] = requests.get(u, timeout=30).status_code
        except Exception as e:                                    # noqa: BLE001
            tried[u] = f"error: {type(e).__name__}"
    return {"request_params": None, "status": "未查證",
            "tried": tried,
            "note": "抓不到公開 spec。本專案的 SM runner 從未開過 diarization"
                    "(`run_speechmatics_batch.py` 的 transcription_config 只有 "
                    "language 與 operating_point),所以連『我們送過什麼』都沒有紀錄。"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", dest="only_print")
    a = ap.parse_args()

    data = {"checked": "2026-08-20",
            "what_this_proves": "參數存在與否,不是分得準不準(侷限 26)",
            "elevenlabs_scribe_batch": elevenlabs(),
            "speechmatics": speechmatics()}

    el = data["elevenlabs_scribe_batch"]
    print(f"ElevenLabs Scribe 批次:{len(el['request_params'])} 個 diarization 相關參數")
    for k, v in el["request_params"].items():
        print(f"  {k:<24} default={v['default']}")
    print(f"  詞級 speaker_id:{'✅' if el['word_level_speaker_id'] else '❌'}"
          f"  ({', '.join(el['word_fields'])})")
    print(f"\nSpeechmatics:{data['speechmatics']['status']}")
    for u, s in data["speechmatics"]["tried"].items():
        print(f"  {s}  {u}")

    if a.only_print:
        return
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
