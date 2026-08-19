# notebooks/ — 需要 GPU 的 arm

kikemu 的實驗大多打 API,需要自己跑模型的有兩個(都是 open weights):
**Breeze ASR 25** 與 **Gemma 4 12B Unified**。Chromebook 沒有 GPU,所以做成 Colab notebook。

| notebook | 模型 | 語料 | 手動步驟 |
|---|---|---|---|
| `breeze_asr25.ipynb` | Breeze ASR 25 | exp2(李宏毅課程) | 要先放音檔到雲端硬碟 |
| `breeze_asr25_exp5.ipynb` | Breeze ASR 25 | exp5(podcast) | **無**,RSS 自動抓 |
| `gemma4_12b_exp5.ipynb` | Gemma 4 12B Unified | exp5(podcast) | **無**,RSS 自動抓 |

---

## Gemma 4 12B Unified(X-gemma arm)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/clarencechien/kikemu/blob/claude/improve-experiment-credibility-c8ekb2/notebooks/gemma4_12b_exp5.ipynb)

**在測什麼:** Gemma 4 的**第一跳(聽)**。第二跳(譯)已經量過——
26B/31B 能打平甚至略勝 gemini-3.5-flash,成本只有 1/22(報告 §3F)。
但**會聽的變體在任何 API 上都拿不到**:

```
gemma-4-26b-a4b-it / gemma-4-31b-it  →  400 Audio input modality is not enabled
```

AI Studio 的 50 個模型、OpenRouter 的 415 個模型裡,Gemma 4 都只有那兩個純文字/視覺變體。
有音訊的是 HF 上 tag `any-to-any` 的 `E2B` / `E4B` / **`12B-it`**,只能自己跑。

**用 exp5 語料**,因為 Breeze、Gemini 批次、Gemini Live、Speechmatics 四個 arm
都在同一批檔案上跑過——這是唯一能直接四方對照的地方。

### 操作:開啟 → 執行階段 → 全部執行。沒有手動步驟。

最後一格會直接印出對照表。跑完把 zip 交回來就能寫進報告。

### 挑執行階段

`gemma-4-12B-it` 是 bf16 **23.9 GB** 權重,notebook 會依 GPU 記憶體自動選策略:

| GPU | 策略 | 備註 |
|---|---|---|
| **A100 40G / H100** | bf16 原生 | 與模型卡一致,最乾淨,**建議用這個** |
| L4 24G / A10 | 4-bit NF4 | 量化不是原生設定,會寫進 meta |
| T4 16G(免費) | 4-bit NF4 | 可能仍 OOM;真的不行就把 `MODEL_ID` 改成 `google/gemma-4-E4B-it` |

**跑多久未量測**——沒有人在 GPU 上跑過這個組合。6 個檔 × 5 分鐘音訊,
Breeze 在 T4 上是每檔約 102 秒,Gemma 4 12B 比它大一個數量級,請預留時間。

### 三個已經先驗過的坑(所以 notebook 裡直接寫對了)

1. **`Gemma4UnifiedProcessor` 需要 `torchvision`**——它連帶 import image processor,
   少裝會噴 `ModuleNotFoundError: No module named 'torchvision'`,而不是一個看得懂的錯誤。
2. **thought channel 要濾掉。** Gemma 4 用 `<channel|>` 分隔思考與答案,
   不濾會把推理文字當成轉寫存進去。這個坑在 API 端已經踩過一次
   (見 [`docs/gemini-api-lessons.md`](../docs/gemini-api-lessons.md))。
   chat template 的 `enable_thinking` **預設就是 False**,等同 API 端的
   `thinkingLevel: "minimal"`,**不要改成 True**(CLAUDE.md 鐵律 4)。
3. **prompt 與 exp5 的 `Gbat` arm 逐字相同。** 唯一變數要是模型,不是 prompt。

### 判讀時要記得的偏差

exp5 的參考轉寫由 `gemini-3.5-flash` 產生,**Gemini 系 arm 有主場優勢**。
Gemma 4 與 Breeze、SM 一樣都是外人,所以:

- ✅ **Gemma vs Breeze、Gemma vs SM** — 乾淨,兩邊都不與參考同源
- ⚠️ **Gemma vs Gemini** — 對 Gemini 有利,不能直接等價比較(報告 §3E.7)

---

## Breeze ASR 25(X-breeze arm)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/clarencechien/kikemu/blob/claude/improve-experiment-credibility-c8ekb2/notebooks/breeze_asr25.ipynb)

**在測什麼:** [Breeze ASR 25](https://huggingface.co/MediaTek-Research/Breeze-ASR-25)
(MediaTek Research,Whisper-large-v2 微調、針對台灣華語與中英夾雜優化、Apache 2.0)
在 exp2 的**同一批語料與聲學條件**上,英文術語召回率能不能贏 Speechmatics。
它沒有 keyterm 機制,所以量的是「純靠模型本身」能走多遠——這正好是
exp2 沒回答到的那一格:詞表值多少錢我們知道了,**模型調校**值多少錢還不知道。

---

## 操作手冊(第一次:約 3 分鐘手動 + 10 分鐘等)

### 步驟 1(只做一次)把音檔放進雲端硬碟

在 Google 雲端硬碟根目錄開一個資料夾 **`kikemu-corpus`**,把 `S1.wav`
`S2.wav` `S3.wav` 丟進去。

> **為什麼要你手動放:** 這三個檔是李宏毅老師課程的切片,依授權不隨 repo 散布
> (`.gitignore` 擋掉),而 YouTube 對資料中心 IP 有 bot 驗證——**Colab 也是 Google
> 的資料中心 IP,`yt-dlp` 在上面同樣會被擋**(本機實測:`Sign in to confirm you're
> not a bot`)。所以自動下載這條路不可靠,由你放一次最穩,之後每次重跑都會沿用。
>
> Phase A **至少**要 `S1.wav` 與 `S3.wav`;三個都放也可以(而且建議),
> notebook 會把找到的全部搬進去,之後改跑 Phase B 就不用再回頭補檔。

### 步驟 2 點上面的 Colab 徽章

### 步驟 3 確認執行階段有 GPU

「執行階段 → 變更執行階段類型 → **T4 GPU**」。
沒有 GPU 的話 notebook 第一格就會直接報錯停下來——不會靜默用 CPU 跑到天荒地老。

### 步驟 4 執行階段 → 全部執行

中途只會停一次:授權 Google 雲端硬碟的彈窗。其餘全自動:

| Cell | 做的事 | 時間 |
|---|---|---|
| 1 | GPU 檢查、裝套件 | ~1 分 |
| 2 | 參數(不用改) | — |
| 3 | clone repo、讀音檔、抓噪音素材(172MB)、跑 `degrade.py`、**核對 SHA256** | ~3 分(素材會快取回雲端硬碟,第二次起省下) |
| | ↑ 失敗時會把 `degrade.py` 的 stderr 原樣印出來,不會只丟一個 exit code | |
| 4 | 載入 Breeze(fp16) | ~2 分 |
| 5 | 推論:Phase A 是 3 檔 × 2 種語言設定 = 6 次 | ~5-10 分 |
| 6 | 印出三段轉錄文字供肉眼判讀 | — |
| 7 | 打包下載 zip | — |

**結果**:`x-breeze-A-<時間>.zip`,解開後把 `Xbrz_auto/` `Xbrz_zh/` 兩個資料夾
放進 `exp2/results/raw/`,再跑 `python3 exp2/scripts/score.py`,
X-breeze 就會與既有 arm 出現在同一張表裡。

---

## 這個 notebook 為什麼可以信

- **聲學條件不是在 notebook 裡另外寫一套**,而是呼叫 repo 原本那支
  `exp2/scripts/degrade.py`。另寫一套 = 噪音對不上 = 跟其他 arm 沒得比。
- **每個檔都對 SHA256**(`exp2/corpus/audio_manifest.json`)。`degrade.py` 已驗證
  為位元決定性:同樣的 `wav/` + `noise_src/` 重跑,12 個條件檔 sha256 全部相同。
  指紋相符 = 你手上的音檔與 X / G 各 arm 當初實際跑的**是同一份**。
- **噪音素材對 sha256**(MIT IR Survey 11.7MB、DEMAND OOFFICE 89MB / OMEETING 83MB),
  URL 是公開來源,本機已驗證可下載且大小相符。
- **模型釘 commit**(`REVISION=cffe7ccb…`),`_meta.json` 記下 transformers / torch
  版本與 GPU 型號,可重現。
- **`chunk_length_s=0`**(模型卡建議的 sequential),並在載入後 assert 它真的生效。
  chunked 比較快,但那樣量到的是分塊策略的差異,不是模型本身。
- **輸出欄位對齊既有 arm**:`score.py` 讀的是 `transcript`(不是 `text`),
  檔名是 `<seg>__<cond>.json`。這份 schema 已用假資料實跑 `score.py` 驗證過,
  能正常產出 recall 與失敗模式分類。
- **可續跑**:已有結果的檔會跳過,Colab 斷線重連直接接著跑。

## 🚨 訓練資料污染(已證實,不是疑慮)

Breeze ASR 25 的訓練資料含 **`NTUML2021`(11 小時真實中英夾雜)**——
**那就是本評測 S1 / S2 的來源課程**。這個模型是在我們的評測語料上微調過的。

所以 **S1 與 S2 的數字不能用來評斷通用能力**,再跑統計也救不回來。
目前唯一乾淨的觀察是 S3(生成式AI導論 2024,不在訓練集),
而它在 S3-M0 輸給 Gemini Live(0.875 vs 0.938)。

要讓這個 arm 的結論成立,**必須**補領域外語料;細節見 `results/report.md` §3D。

## Phase A 怎麼判讀

快篩只回答一件事:**M3(混響 + 12dB 多人交談底噪)會不會崩。**
Breeze 的中文訓練資料全部是合成語音,真實聲學條件下的穩健性是最大未知數。
崩掉的樣子是輸出歸零、整段重複同一句、或英文術語全滅。
**M3 崩了就不必做 Phase B**,在報告記一筆「合成訓練資料在真實噪音下不穩」即可。

對照基準(exp2 既有數字,tolerant 召回,三段合計;由 `results/scores.json` 現算):

| arm | M0 乾淨 | M1 混響 | M2 +辦公室 20dB | M3 +交談 12dB |
|---|---|---|---|---|
| X_cmn(SM 單語 `cmn`,無詞表) | 0.521 | 0.573 | 0.563 | 0.469 |
| **X_bi**(SM `cmn_en` 雙語,無詞表)← 主要對照 | **0.813** | 0.812 | 0.792 | **0.563** |
| Xsli_bi(SM 雙語 + 投影片詞表) | 0.833 | 0.844 | 0.865 | 0.594 |
| G(Gemini Live 一體式) | 0.927 | 0.802 | 0.792 | 0.396 |

**X_bi 是最直接的對照**(同樣沒有詞表,純引擎能力)。X-breeze 要有意義,
至少得在 M0 摸到 0.81;M3 的 0.563 則是它會不會崩的照妖鏡——
注意一體式的 G 在 M3 掉到 0.396,而它在 M0 是全場最高的 0.927。
**乾淨條件下的領先不保證噪音下站得住**,這正是要跑 M3 的原因。
