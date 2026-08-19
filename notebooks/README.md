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

### 分好幾天跑

參數格的 `USE_DRIVE = True`(預設開)會把結果同步到雲端硬碟
`MyDrive/kikemu-results/<ARM>/`,**每跑完一個 chunk 就存一次**。

| 斷在哪 | 重開後從哪接 |
|---|---|
| 檔與檔之間 | 跳過已完成的檔 |
| **一個檔的第 3 個 chunk** | **從第 3 個 chunk 接**,前兩個不重跑 |
| 執行階段被刪掉 | 一樣——進度在雲端硬碟,不在 `/content` |

所以最多只會損失「正在跑的那一個 chunk」。

**但每個 session 仍要重抓 24GB 模型權重**(免費版雲端硬碟只有 15GB,放不下),
所以每次重開的固定成本大約十幾分鐘。分越多天跑,這筆重複成本越高——
真的要跑滿六個檔,還是換 L4 / A100 一次跑完比較划算。

### 挑執行階段

`gemma-4-12B-it` 是 bf16 **23.9 GB** 權重,notebook 會依 GPU 記憶體自動選策略:

| GPU | 12B 能不能跑 | 策略 |
|---|---|---|
| **A100 40G / H100** | ✅ | bf16 原生,整段送——**唯一乾淨可比的組合** |
| L4 24G / A10 | ✅ | 4-bit NF4,整段送(量化會寫進 meta) |
| **T4 16G(免費)** | ⚠️ **整段送會 OOM** | 自動改 60 秒切塊,或改用 `google/gemma-4-E4B-it` |

**T4 為什麼不行(算術問題,不是設定問題):**
5 分鐘音訊 ≈ **7500 個 audio token**。T4 是 compute 7.5,吃不到 SDPA 的
flash / memory-efficient backend,會退回 math backend 把注意力矩陣整個實體化:

```
7500² × 16 heads × 4 bytes ≈ 3.4 GB      ← 實測 OOM 就是要不到這一塊
```

**權重塞得進去(4-bit 約 7 GB),注意力塞不進去。**
notebook 偵測到 <20 GB 會自動把 `CHUNK_SEC` 設成 60。

> ⚠️ **切塊的數字不能和整段的混比。** 模型看不到跨塊上下文,對專名不利,
> 而且其他所有 arm(Breeze、Gemini 批次、SM)都是整段處理。
> `chunk_sec` 與 `whole_file` 會寫進每筆結果的 meta,報告裡必須標明。
> 要拿乾淨可比的數字,**請用 L4 或 A100**。

### 跑多久(2026-08-19 起有實測值)

| 組合 | 每檔 5 分鐘音訊 | 6 檔合計 | 可行嗎 |
|---|---|---|---|
| **T4 + 12B + 60s 切塊 + bf16** | ~83~125 分 | **8~12 小時** | ❌ 免費版約 4 小時就斷線,跑不完 |
| T4 + 12B + 60s 切塊 + **fp16** | 未量測(理論上快 3~10×) | ? | ⚠️ 可試,但 Gemma 在 fp16 有 NaN 前科 |
| **A100 + 12B + 30s 切塊** | **~3.3 分**(20s/chunk × 10) | **~20 分** | ✅ **實測**(Modal,2026-08-19) |
| **L4 + E4B + 30s 切塊** | **~1.8 分** | **~11 分** | ✅ **實測**,而且準確度比 12B 好一個數量級 |
| (對照)Breeze on Colab T4 | 102 秒 | ~10 分 | ✅ |
| (對照)Breeze on **Modal** T4 / L4 / A100 | **79s** / 66s / 59s | — | ✅ 同卡不同環境差 31%,見 `results/report.md` §3E.2c |

> 📌 **2026-08-19 起,Gemma / Breeze 的正式數字都改在 Modal 上跑**
> (`exp5/scripts/run_gemma_modal.py`、`scripts/run_breeze_modal.py` 等)。
> 這份 Colab 手冊仍然有效,但 Colab 那條路耗掉五輪來回全是環境問題,
> **要重跑請優先用 Modal**——image 釘死、可從 agent session 直接驅動、
> 而且 `--probe` / `--selftest` 都在腳本裡。
>
> ⚠️ **另一件 Colab 手冊沒提到的事**:Gemma 4 三個型號的
> `generation_config.json` 預設 `do_sample: true, temperature: 1.0`。
> **不指定就是隨機取樣**,同一個檔重跑會得到不同結果(實測過:一次 119 字、
> 一次直接 `<eos>`)。要可重現就傳 `do_sample=False`,或明講數字是單次取樣。

**T4 慢的主因是 dtype**:T4 是 compute 7.5,**沒有 bf16 tensor core**,
指定 `bfloat16` 會走軟體模擬。notebook 現在依 compute capability 自動選:
`>= 8.0` 用 bfloat16(模型卡原生設定),`< 8.0` 用 float16(吃得到 T4 的 fp16 tensor core)。

> ⚠️ **Gemma 家族在 fp16 下有數值溢位(NaN)的前科。**
> 第 6 格的健全性檢查會抓到空輸出/亂碼——**那一格沒過就不要信結果**,
> 老實換 L4 / A100 跑 bf16。

### 五個坑(前三個先驗過,第四、五個是實際在 Colab 上跑才露出來的)

1. **不要用 pip 動 Colab 內建的科學堆疊。** numpy / scipy / numba / librosa /
   torch / torchvision 在 Colab 是**互相配對好的**,升級任何一個都會連鎖爆炸,
   而且 **pip 裝過就回不去,restart runtime 沒用**——只能
   「執行階段 → 中斷連線並刪除執行階段」重來。這條踩過兩次:

   | 做了什麼 | 噴什麼 |
   |---|---|
   | `pip install -U torchvision` | `RuntimeError: operator torchvision::nms does not exist` |
   | `pip install -U librosa` | 連帶升 `numba` → 動到 numpy → `ImportError: cannot import name '_center' from numpy._core.umath` |

   `Gemma4UnifiedProcessor` 確實需要 `torchvision`(連帶 import image processor),
   但 **Colab 本來就有**。librosa / soundfile 同理。
   notebook 現在**只裝缺的**(`transformers>=5.15`、`accelerate`、`bitsandbytes`),
   並把 `numpy==<現有版本>` 一起送進 pip 當作釘子,裝完再把整條鏈 import 一次驗證。
2. **thought channel 要濾掉。** Gemma 4 用 `<channel|>` 分隔思考與答案,
   不濾會把推理文字當成轉寫存進去。這個坑在 API 端已經踩過一次
   (見 [`docs/gemini-api-lessons.md`](../docs/gemini-api-lessons.md))。
   chat template 的 `enable_thinking` **預設就是 False**,等同 API 端的
   `thinkingLevel: "minimal"`,**不要改成 True**(CLAUDE.md 鐵律 4)。
3. **prompt 與 exp5 的 `Gbat` arm 逐字相同。** 唯一變數要是模型,不是 prompt。
4. **乾淨環境沒有 `corpus/noise_src/`。** MIT IR(12MB)+ DEMAND(172MB)
   被 `.gitignore` 擋掉,所以 Colab 一定缺,`degrade.py` 會噴
   `FileNotFoundError: .../mit_ir.zip`。
   **已修在 `exp2/scripts/degrade.py::ensure_noise_src()`**——缺哪個抓哪個、
   對 sha256,URL 存在 `exp2/corpus/audio_manifest.json` 的 `noise_src` 區段。
   修在腳本而不是 notebook,是因為只補在某一本 notebook 裡,下一個入口還是會踩
   (實際踩過:exp5 的兩本 notebook 都漏了同一格)。
5. **4-bit 時音訊塔要留在 bf16。** `Gemma4UnifiedMultimodalEmbedder.forward` 只在
   `weight.dtype.is_floating_point` 為真時才把 float32 的 `input_features` 轉型;
   量化後 weight 是 uint8,轉型不會發生。notebook 用
   `llm_int8_skip_modules=['embed_audio','embed_vision','lm_head']` 保留它們
   (只佔幾百 MB),並在載入後印出 dtype 供確認。只影響 L4 / T4;A100 bf16 沒這問題。

### 🚨 呼叫方式:照模型卡,不要自己發明(2026-08-19,燒了 $1.26 才學會)

前兩次都跑出垃圾。逐項對照[模型卡](https://huggingface.co/google/gemma-4-12B-it)的
官方 snippet 之後,查出**哪些是真的差異、哪些是我誤判**:

| 我原本的寫法 | 官方 | 是不是原因 |
|---|---|---|
| 整段送 300 秒 | §7「maximum length of **30 seconds**」 | ✅ **是**。輸出崩成 8152 字重複迴圈 |
| audio 放在 text **前** | §4「Audio content **after** the text」 | ✅ 是 |
| 自己寫的中文 prompt | §6 官方 ASR 結構 | ⚠️ 見下 |
| 手工切 `<channel\|>` 字串 | **`processor.parse_response(response, prefix=...)`** | ✅ **是**。官方有專用解析器 |
| `max_new_tokens=4096` | 官方範例 `512` | ✅ 是。上限越高,跳針跑越久 |
| `skip_special_tokens=True` | 官方 `False` | ✅ 是。True 會把 `<\|channel>` 吃掉只留 "thought" 這個字 |
| 傳 numpy array | 官方傳 URL 字串 | ❌ **不是**。實測兩者產出的 `input_features` **完全相同** |
| `Gemma4UnifiedForConditionalGeneration` | `AutoModelForMultimodalLM` | ❌ 不是。auto mapping 就是指到同一個類別 |
| `enable_thinking=False` | 官方沒傳 | ❌ 不是。template 預設就是 False(而且這個 kwarg 其實沒被吃進去) |

**模型其實聽得到。** 第二次抽驗的 chunk 2 輸出「那個**大河**,那個大河是怎麼拿出來的?
這整段說到有」,對照參考文本「那個 **data** 存在 disk 是怎麼拿出來的,這整段送到 user
面前」——內容對得上,只是把 `data` 音譯成「大河」。

**所以剩下的真問題是 prompt。** 官方 ASR prompt 沒有「保留原樣英文」這條指示,
而 exp5 量的正是英文術語召回。這一格必須跑**兩個變體**才分得清:

| 變體 | prompt | 回答什麼問題 |
|---|---|---|
| `Xgma_12b` | 官方原版 | 模型的預設行為 |
| `Xgma_12b_kw` | 官方 + 「keep English terms verbatim」 | 低召回是模型的極限,還是 prompt 沒講 |

### Modal 路徑(`exp5/scripts/run_gemma_modal.py`)

不想開 Colab 的話走這條,而且可以從 Claude Code session 直接驅動。

- **模型常駐用 `@app.cls` + `@modal.enter()`**(Modal 官方 lifecycle 寫法),
  搭配 `scaledown_window` 讓容器在多次 `modal run` 之間保溫——
  否則每次抽驗都要重載一次權重。
- `--probe N`:只跑第一個檔的前 N 個 chunk 並印出來,不寫結果。**先驗再全跑。**
- 每個 chunk 印一行進度。前兩次都是「等整個檔跑完才發現壞掉」白燒的。
- 音檔由本機上傳並先對 manifest sha256,不在容器裡重新產生。

**成本實測**:A100-40GB $2.10/hr。12B 每個 30 秒 chunk 約 **106~116 秒**
——比實時還慢 3.5 倍。六個檔 = 60 chunk ≈ 1.8 小時 ≈ **$3.9**。
這個成本結構本身就值得記一筆:即使品質過關,它在非即時場景也贏不了
Gemini `generateContent` 的 $0.26/hr 全速,只剩「必須地端」那一格有意義。

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

---

## 改完 notebook 一定要跑這個

```bash
python3 notebooks/check_notebooks.py
```

抓語法錯與「把變數用在定義它的那一格之前」。

**為什麼需要它:** 開發機沒有 GPU,notebook 的 GPU 路徑一格都跑不了,
只能靠使用者在 Colab 上回報錯誤才知道壞掉。已經這樣浪費過四輪:

| # | 症狀 | 類型 | 這支抓不抓得到 |
|---|---|---|---|
| 1 | `operator torchvision::nms does not exist` | 升級了 Colab 內建套件 | ❌ 執行期 |
| 2 | `FileNotFoundError: mit_ir.zip` | 缺 gitignore 掉的資料 | ❌ 執行期 |
| 3 | `cannot import name '_center'` | pip 連鎖動到 numpy | ❌ 執行期 |
| 4 | `NameError: CHUNK_SEC is not defined` | 變數用在定義之前 | ✅ **抓得到** |
| 5 | `CUDA out of memory`(T4 整段 5 分鐘) | 記憶體算術 | ❌ 執行期 |

第 4 個是純靜態問題,本來就不該讓使用者花一輪去發現。
其餘四個是環境差異,只能靠把踩過的坑寫進上面的清單來避免重複。
