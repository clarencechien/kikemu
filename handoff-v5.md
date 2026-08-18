# Handoff — X-breeze arm(Breeze ASR 25)

給 Claude Code。目標是在 kikemu repo 裡建一個 **Colab notebook**,讓 Breeze ASR 25 能加入晶晶體評測的 arm 之一。

**你不會執行它。** 使用者在 Chromebook 上,notebook 由他在 Colab 按執行。你的工作是把 notebook 寫到能一鍵跑完、且輸出格式能直接餵進既有的 `score.py`。

---

## 1. 為什麼要加這個 arm

Breeze ASR 25(MediaTek Research)是 Whisper-large-v2 微調,**針對台灣華語口音與中英夾雜優化**,Apache 2.0 授權。

官方宣稱:相較 Whisper 精準度提升近 10%,**中英語轉換提升 56%**。

這正好是晶晶體評測要驗的那條軸。而且 Apache 2.0 + 可地端,如果表現好,它同時解掉廠區線的資料落地需求。

**要驗證的:**
1. 相較商用引擎(Speechmatics)在中英夾雜上的英文術語召回率
2. **在噪音下會不會崩** —— 它的中文訓練資料**全部是合成語音**,真實聲學條件下的穩健性是最大未知數
3. 它沒有 keyterm 機制,所以無法測詞表;它代表的是「純靠模型本身」能走多遠

---

## 2. 兩個階段,先做第一個

### Phase A(先做):三段快篩

只跑 3 個檔,回答「值不值得跑完整矩陣」:

- S1-M0(乾淨,ML 術語密集)
- S1-M3(加混響 + 多人交談底噪 12dB)
- S3-M0(較口語的那段,乾淨)

**如果 M3 那段崩掉(輸出歸零、或英文術語全滅),就不必做 Phase B**,直接在報告裡記一筆「合成訓練資料在真實噪音下不穩」即可。

### Phase B:完整 arm

3 段 × 4 條件 = 12 檔,產出與其他 arm 同格式的 JSON。

---

## 3. Notebook 規格

路徑:`notebooks/breeze_asr25.ipynb`

在 README 放上一鍵開啟連結:
```
https://colab.research.google.com/github/<user>/kikemu/blob/<branch>/notebooks/breeze_asr25.ipynb
```

### Cell 結構

**Cell 1 — 環境與 GPU 檢查**
- `!nvidia-smi`,確認拿到 GPU
- 若無 GPU 就明確 raise,不要靜默退回 CPU(large-v2 在 CPU 上會跑到天荒地老)
- 安裝 `transformers`、`torch`、`librosa`、`soundfile`

**Cell 2 — 參數區(集中在最上面,方便使用者改)**
```python
REPO       = "<user>/kikemu"
BRANCH     = "<branch>"
PHASE      = "A"          # "A" 快篩 / "B" 完整
MODEL_ID   = "MediaTek-Research/Breeze-ASR-25"
DTYPE      = "float16"    # 評測用,不要用 int8
PUSH_BACK  = False        # True 時需要 PAT
```

**Cell 3 — 取得音檔**
- `git clone` repo(淺層),取 `corpus/conditions/` 下的檔案
- 若音檔未入 repo(檔案大),改為從 `corpus/manifest.json` 的 URL 下載後**現場加噪**——重用 `scripts/degrade.py`,不要在 notebook 裡重寫一套,否則兩邊的噪音不一致,結果無法跟其他 arm 比較
- 一律 16 kHz 單聲道

**Cell 4 — 載入模型**
```python
from transformers import pipeline
asr = pipeline("automatic-speech-recognition",
               model=MODEL_ID, torch_dtype=torch.float16, device=0)
```

**Cell 5 — 推論(關鍵設定,見 §4)**

**Cell 6 — 輸出**
- 寫成 `results/raw/x-breeze/<seg>_<cond>.json`
- 同時寫一份 `results/raw/x-breeze/_meta.json`:模型 commit hash、`transformers` 版本、GPU 型號、dtype、每檔耗時
- Phase A 額外在畫面上印出三段的文字,方便使用者直接用眼睛判斷

**Cell 7 — 回寫(選用)**
- `PUSH_BACK=True` 時用 PAT commit 回 repo
- 預設 False,並提示使用者可以直接下載 JSON

---

## 4. 必須照做的推論設定

**`chunk_length_s=0`(sequential modeling)。** 模型卡明確指出這樣效果最好。chunked 模式比較快,但那會讓你量到的是分塊策略的差異,不是模型本身。**評測要保真,不要為了跑快改設定。**

**`language` 與 `task` 不要硬指定成 `zh` + `transcribe` 就算了** —— 這是中英夾雜情境,語言標記的處理方式會直接影響英文術語會不會被中文化。**請把「不指定 language」與「指定 zh」兩種都跑一次**,在報告裡分開列。這本身就是一個發現。

**`return_timestamps=True`**,後續要對齊與計算術語位置。

**不要用 `initial_prompt` 塞術語表。** 那不是 keyterm 偏置,效果不穩定,而且會讓這個 arm 失去「純模型能力」的對照意義。若之後要測,另開 `X-breeze-prompt` arm。

**dtype 用 `float16`。** 不要用 int8 量化 —— 那會混入量化損失這個變因。

---

## 5. 輸出格式

**必須與其他 arm 一致**,讓 `score.py` 不用改就能吃:

```json
{
  "arm": "x-breeze",
  "segment": "S1",
  "condition": "M3",
  "text": "完整轉錄文字",
  "segments": [{"start": 0.0, "end": 3.2, "text": "..."}],
  "meta": {
    "model": "MediaTek-Research/Breeze-ASR-25",
    "revision": "<commit hash>",
    "language_arg": null,
    "dtype": "float16",
    "elapsed_sec": 42.1
  }
}
```

**先去讀既有的 `run_<arm>.py` 確認實際欄位名,以那邊為準。** 上面是示意,不要照抄後造成 `score.py` 解析失敗。

---

## 6. 報告要補的欄位

沿用晶晶體 handoff 的指標,X-breeze 這個 arm 額外要記:

- **失敗模式分佈**(F1 諧音化 / F2 誤認他字 / F3 語言崩潰 / F4 遺漏)—— 這個 arm 特別可能出現 F3,因為 Whisper 的語言標記機制在中英夾雜下容易翻車
- `language=zh` vs 不指定 的差異
- 相對於 X(Speechmatics 無詞表)的差值 —— 這是「台灣調校 vs 商用引擎」的直接對比
- 相對於 X-slides(Speechmatics + 投影片詞表)的差值 —— 回答「模型調校 vs 詞表」哪個比較值錢

---

## 7. 必須寫進報告的偏誤警告

**Breeze ASR 25 的致謝中包含李宏毅教授的指導,而本評測的語料正是他的課程。**

中文訓練資料雖為合成,但團隊對 ML 課程的語域與術語分佈極為熟悉,**X-breeze 在此語料上的表現很可能偏高**。

處理方式:
1. 報告中明確標註此潛在偏誤
2. **加一段領域外的中英夾雜對照語料**(真實科技業會議錄音或 Podcast,語域非 ML),看優勢是否仍在
3. 若只在 ML 語料上領先,結論應寫成「領域內表現優異」,而非「通用台灣華語引擎更好」

**這一條不可省略。** 少了它,整份報告的結論會站不住。

---

## 8. 備援路線(Phase A 有戲再考慮)

若 Colab 的 runtime 不穩或要跑多輪,可改為**轉成 faster-whisper(CTranslate2)在 GitHub Actions 的 CPU 上跑**:

- `ct2-transformers-converter` 轉檔
- large-v2 在 CPU 上約 2~3 倍實時,一小時音訊在免費 runner 的 6 小時上限內
- **量化務必用 `float32` 或 `int8_float32`,不要純 `int8`**
- 好處:全自動、可重現、進 CI

**但不要一開始就做這條。** Phase A 只要三個檔,Colab 十分鐘搞定,轉檔的時間不值得先花。

---

## 9. 驗收

- [ ] Notebook 從 GitHub 一鍵開啟即可執行,不需要手動改路徑
- [ ] 無 GPU 時明確報錯,不靜默退回 CPU
- [ ] `chunk_length_s=0` 確實生效
- [ ] 兩種 language 設定都有輸出
- [ ] JSON 格式與既有 arm 一致,`score.py` 不用改
- [ ] `_meta.json` 記錄了模型 revision 與環境,可重現
- [ ] Phase A 會在畫面上印出三段文字供肉眼判斷
- [ ] README 有一鍵開啟連結與「這個 arm 在測什麼」的兩句話說明

## 10. 先確認再動手

1. **音檔在不在 repo 裡?** 若不在,notebook 要能重跑加噪流程,且必須重用 `scripts/degrade.py` 與相同的亂數種子。
2. **既有 arm 的輸出欄位名為何?** 以那邊為準,不要照抄本文件的示意。
3. 領域外的對照語料要用什麼?這個可以晚一步決定,但 Phase B 之前要有答案。
