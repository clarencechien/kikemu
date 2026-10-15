# OpenRouter 新一代 STT 批次 arm(Phase 1,2026-10-15)

任務書與執行前決定:[`handoff-v16.md`](../handoff-v16.md)(§8 判讀規則在任何分數出來之前寫死,偏離記 §9)。
偵察:[`openrouter-recon.md`](openrouter-recon.md)。

| 出處 | 內容 |
|---|---|
| `scripts/run_openrouter_arms.py` | 384 次呼叫(+ 重跑 12 次);每檔記實際送出的 request(去掉音訊) |
| `exp5/results/raw/OR*_{auto,zh}/`、`GDtrans_*` | exp5 原始輸出;`results/raw/OR*_*`、`GDtrans_*` = exp1 |
| `exp5/results/scores.json`、`term_outcomes.json`、`results/scores.json`、`noun_outcomes.json` | 既有計分腳本重跑(既有 arm 的列 0 變動) |
| `analysis/openrouter_gates.py` → `results/openrouter_gates.json` | Gate A / B、一致性、逐條件矩陣、敏感度——**本頁所有數字的出處** |
| `results/raw/_v16_ledger.json`、`_v16_failures.json` | 花費帳本、失敗格 |

**路徑標示**:「經 OR」= 經 OpenRouter `/api/v1/audio/transcriptions`;「直連」= 直接打供應商 API。
**全部是批次**,不可外推到即時(任務書 §7)。

---

## TL;DR

1. **Gate A:維持 Scribe。** Grok(經 OR,上游版本未定 → 本結論只當參考)三條取代條件**在兩個語料上全部不成立**:
   吵雜條件確定輸(exp5 M3 −0.25、exp1 N3 −0.18,CI 都不含 0),和 Gemini 配對的分歧數約是 Scribe 的 2 倍,
   而多出來的分歧幾乎都是「Gemini 對、Grok 錯」(專用 ASR 勝率 0.21 / 0.09,Scribe 是 0.62 / 0.31)。
   **不建議改離線會議記錄 L1 配對。**
2. **Gate B(exp5 晶晶體,`language=zh`,對 Scribe 經 OR 0.930)**:Qwen3-ASR 1.7B **−0.11**、Whisper turbo **−0.21**、
   Qwen3-ASR 0.6B **−0.28**、Nemotron 0.6B **−0.83**(幾乎全滅)。四個都確定輸或 CI 上界剛好 0。只報數字,
   **不下「可以做輸入法」的結論**(沒有短句延遲與自發口語資料)。
3. 跟 Scribe **量不出差別**的只有兩個商用模型:**MAI-Transcribe-2**(−0.013,TER 還略好)與 **Muse Voice**(−0.006)。
4. **Gemini 3.5 Transcribe(直連)有兩種失敗形狀**:空輸出(exp1 3 格,`finishReason: STOP`)與
   **重複迴圈**(exp5 一格把「因为他」吐了 10,217 次、20,676 output token,那一格就花 $0.26 ≈ 正常的 10 倍)。
   沒設 `maxOutputTokens` 的話,**輸出 token 沒有上限**——要接它就要有輸出上限(鐵律 5)。
5. 花費:OpenRouter **$1.86**(含重跑)+ 偵察 ≈ $0.2;Gemini 直連 **$1.19**。保險絲 $5 沒碰到。

---

## 1. exp5 矩陣(晶晶體,3 段 × M0/M3,`recall_tolerant` 實例加權 / TER)

各 arm 的原始字形見偵察 §2;計分前一律 OpenCC `s2t`(既有流程)。詞表:**全部不開**(handoff-v16 §8.4)。

| arm | 模型 | 路徑 | $/hr | auto M0 | auto M3 | **auto pooled** | **zh pooled** | zh TER |
|---|---|---|---|---|---|---|---|---|
| `Gbat`(既有) | Gemini 3.5 Flash 批次 | 直連 | 0.26 | 0.987 | 0.817 | 0.902 | — | 0.122 |
| `Sbat_bi`(既有) | Scribe v2 | 直連(2026-08) | 0.22 | 0.943 | 0.899 | 0.921 | — | 0.148 |
| `ORscribe` | elevenlabs/scribe-v2 | 經 OR | 0.11 | 0.937 | 0.886 | 0.911 | **0.930** | 0.149 |
| `ORmuse` | meta/muse-voice-transcribe-1.0 | 經 OR | 0.18 | 0.949 | 0.892 | 0.921 | 0.924 | 0.155 |
| `ORmai2` | microsoft/mai-transcribe-2 | 經 OR | 0.10 | 0.924 | 0.911 | 0.918 | 0.918 | **0.139** |
| `ORgptT` | openai/gpt-transcribe | 經 OR | 0.27 | 0.911 | 0.854 | 0.883 | 0.889 | 0.151 |
| `GDtrans` | gemini-3.5-transcribe | **直連**(經 OR 一律 400) | ≈0.32 | 0.937 | **0.468** ⚠️ | 0.703 ⚠️ | 0.864 | 0.155 |
| `ORqwen17` | qwen/qwen3-asr-1.7b | 經 OR | 0.027 | 0.873 | 0.766 | 0.820 | 0.817 | 0.153 |
| `ORasm36` | assemblyai/universal-3-6-pro | 經 OR | 0.30 | 0.867 | 0.766 | 0.817 | 0.813 | 0.175 |
| `ORasm35` | assemblyai/universal-3-5-pro | 經 OR | 0.30 | 0.835 | 0.760 | 0.798 | 0.791 | 0.185 |
| `ORgrok` | x-ai/grok-stt-1.0(**版本未定**) | 經 OR | 0.10 | 0.867 | 0.652 | 0.760 | 0.769 | 0.199 |
| `ORwhisperT` | openai/whisper-large-v3-turbo | 經 OR | 0.012 | 0.817 | 0.658 | 0.737 | 0.722 | 0.236 |
| `ORqwen06` | qwen/qwen3-asr-0.6b | 經 OR | 0.012 | 0.722 | 0.582 | 0.652 | 0.649 | 0.177 |
| `ORnemo` | nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b | 經 OR | 0.012 | 0.304 | 0.171 | 0.237 | **0.104** | 0.280 |

$/hr:經 OR 的是偵察 §4 用 `usage.cost ÷ usage.seconds` 實測;`Gbat` / `Sbat_bi` 是既有文件的數字;`GDtrans` 是本輪實際帳單換算
(正常格每 300 秒 $0.027;牌價 $2/M 音訊輸入、$12/M 輸出,2026-10-15 查),**迴圈那一格是 $0.263**。

⚠️ `GDtrans_auto` 的 M3 / pooled / TER(平均 3.418)被**一格重複迴圈**拖垮(§4)。拿掉 T1__M3 那格:pooled 0.901、TER 0.136
(描述用;依任務書「缺格不能當保守估計」,正式數字照列含迴圈的那一版)。

**語言設定的槓桿在這批模型上幾乎消失**:除了 Gemini Transcribe(auto 迴圈)與 Nemotron(zh 反而更差),
auto 與 zh 的差都在 ±0.02 內。exp2 量到的 +0.23~0.29 是 Speechmatics 的 `cmn_en` 雙語包 vs 單語包,不是這些模型的行為——
**我們沒在這批模型上找到同樣的槓桿,不代表語言設定對它們無效**(只量了 3 段)。

## 2. exp1 矩陣(日文導覽,6 段 × N0–N4,67 個專名 `recall_tolerant` / CER)

| arm | 路徑 | 詞表 | N0 | N1 | N2 | **N3** | N4 | pooled | CER 中位 |
|---|---|---|---|---|---|---|---|---|---|
| `Abat`(既有,Gemini 3.5 Flash 批次) | 直連 | — | 0.940 | 0.896 | 0.836 | 0.567 | 0.896 | 0.827 | 0.086 |
| `Sbat_ja`(既有,Scribe 直連) | 直連 | 50 條 | 0.866 | 0.881 | 0.776 | **0.642** | 0.836 | 0.800 | 0.099 |
| `ORscribe_ja` | 經 OR | 開不了 | 0.821 | 0.851 | 0.716 | 0.507 | 0.821 | 0.743 | 0.099 |
| `ORscribe_auto` | 經 OR | 開不了 | 0.836 | 0.836 | 0.731 | 0.507 | 0.791 | 0.740 | 0.102 |
| `ORgrok_ja` | 經 OR | — | 0.612 | 0.627 | 0.463 | 0.328 | 0.612 | 0.528 | 0.150 |
| `ORgrok_auto` | 經 OR | — | 0.642 | 0.627 | 0.463 | 0.328 | 0.597 | 0.531 | 0.147 |
| `ORgrok_ja_kt` | 經 OR | 前 100 條(截掉 42–66 條) | 0.702 | 0.672 | 0.508 | 0.343 | 0.672 | 0.579 | 0.126 |
| `GDtrans_auto` | 直連 | — | 0.642 ⚠️ | 0.731 | 0.657 | 0.388 ⚠️ | 0.746 | 0.633 | 0.127 |
| `GDtrans_ja` | 直連 | — | 0.791 | 0.776 | 0.672 | 0.388 ⚠️ | 0.791 | 0.684 | 0.115 |
| `GDtrans_ja_kt` | 直連 | 全部(142–166 條) | 0.896 | 0.910 | 0.776 | 0.567 | 0.881 | **0.806** | 0.103 |

⚠️ = 含空輸出的格(`ikeda02__N3` auto/ja、`ikeda03__N0` auto;§4)。照空字串計分;拿掉那幾格:`GDtrans_auto` 0.684、`GDtrans_ja` 0.700。
`ORscribe_ja_kt` 是**缺格**:經 OR 開不了 Scribe 的詞表(偵察 §3)——**不是 Scribe 不支援**。

附帶(不進 Gate):**Gemini 3.5 Transcribe 帶詞表**(0.806)和 Scribe 直連帶 50 條(0.800)、Gemini 批次(0.827)同一級;
詞表對它的槓桿很大(+0.12),Grok 帶詞表只 +0.05(而且被截到 100 條)。

## 3. Gate A:Grok 能否取代 Scribe → **維持 Scribe**

主比較 = 有設語言的那格(exp5 `_zh`、exp1 `_ja`;兩邊都沒詞表)。配對對象 = 既有 Gemini 批次(`Gbat` / `Abat`)。
跨條件 pooled(§9 記一筆)。

| | exp5(晶晶體) | exp1(日文) |
|---|---|---|
| 吵雜條件召回 Gemini / Grok / Scribe | M3:0.817 / 0.658 / 0.911 | N3:0.567 / 0.328 / 0.508 |
| **規則 1**:吵雜條件 Grok − Scribe(段群 bootstrap) | **−0.253** [−0.279, −0.185](3 群) | **−0.179** [−0.333, −0.025](6 群) |
| **規則 2**:分歧數(恰好一邊對)Gemini+Grok vs Gemini+Scribe | **72** vs 37 | **122** vs 74 |
| 　其中「只有專用 ASR 對 / 只有 Gemini 對」 | 15 / 57 vs 23 / 14 | 11 / 111 vs 23 / 51 |
| **規則 3**:專用 ASR 勝率 | **0.208** vs 0.622 | **0.090** vs 0.311 |
| Jaccard(兩邊都錯 / 至少一邊錯,另報) | 0.182 vs 0.178 | 0.278 vs 0.321 |
| 三條 | ✗ ✗ ✗ | ✗ ✗ ✗ |

exp5 的分歧數以術語實例數加權。附帶 `ORgrok_ja_kt`(帶 100 條詞表):N3 −0.164 [−0.300, −0.049]、分歧 115、勝率 0.139——
**加詞表也沒翻盤**。

**差在哪**:不是「Grok 跟 Gemini 錯在同一處」(Jaccard 跟 Scribe 差不多:exp5 0.18 vs 0.18,exp1 0.28 vs 0.32),而是 **Grok 自己錯得多**——
多出來的分歧幾乎全部是 Gemini 對、Grok 錯,對複核者來說是**白標紅**。Scribe 的價值在吵雜時補 Gemini
(exp5 M3 Scribe 比 Gemini 高 0.095),Grok 在吵雜時比 Gemini 還低。

**範圍**:x-ai/grok-stt-1.0 **經 OpenRouter、上游版本未定**(1.0 或 2.0,偵察 §1;沒有 xAI key 可直連比對),
中文晶晶體 3 段 + 日文導覽 6 段、批次、沒開 diarization。**不是「Grok STT 不行」的通用結論**,
直連 2.0 若要正式下結論需要另跑一次。價格 $0.10/hr 是事實,但在這兩批語料上換不到 Scribe 的位置。

## 4. Gate B:便宜 / 可本機化模型 vs Scribe(exp5)

同一語言設定、同一路徑(都經 OR)比。CI = 3 段群 bootstrap,**群太少,CI 很粗**(上界剛好 0.0 是只有 3 群的離散效應)。

| arm | 本機化 | `zh`:召回差 vs Scribe | `zh`:TER 差 | `auto`:召回差 | `auto`:TER 差 |
|---|---|---|---|---|---|
| Qwen3-ASR 1.7B | 可(開源權重) | **−0.114** [−0.148, 0.000] | +0.004 [−0.008, +0.015] | −0.092 [−0.120, 0.000] | +0.001 |
| Whisper large-v3 turbo | 可 | **−0.209** [−0.245, −0.130] | +0.087 [+0.046, +0.127] | −0.174 [−0.207, −0.093] | +0.083 |
| Qwen3-ASR 0.6B | 可 | **−0.282** [−0.370, −0.222] | +0.028 [+0.013, +0.044] | −0.260 [−0.296, −0.222] | +0.025 |
| Nemotron 3.5 ASR 0.6B | 可 | **−0.826** [−0.904, −0.593] | +0.131 [+0.093, +0.168] | −0.674 [−0.740, −0.500] | +0.120 |
| (附)MAI-Transcribe-2 | 否 | −0.013 [−0.111, +0.010] | −0.010 [−0.023, +0.002] | +0.006 | −0.011 [−0.024, −0.002] |
| (附)Muse Voice 1.0 | 否 | −0.006 [−0.074, +0.019] | +0.006 | +0.010 | −0.001 |
| (附)GPT-Transcribe | 否 | −0.041 [−0.074, +0.019] | +0.002 | −0.029 | +0.001 |
| (附)Gemini 3.5 Transcribe | 否 | −0.067 [−0.167, −0.039] | +0.006 | −0.209 ⚠️ | +3.27 ⚠️ |
| (附)AssemblyAI U-3.6 Pro / 3.5 Pro | 否 | −0.117 / −0.139 | +0.026 / +0.036 | −0.095 / −0.114 | +0.024 / +0.031 |
| (附)Grok STT | 否 | −0.161 [−0.188, −0.111] | +0.049 | −0.152 | +0.034 |

**排序(`zh` 召回)**:Scribe 0.930 > Muse 0.924 > MAI-2 0.918 > GPT-T 0.889 > Gemini-T 0.864 > **Qwen3 1.7B 0.817** >
U-3.6 0.813 > U-3.5 0.791 > Grok 0.769 > **Whisper turbo 0.722** > **Qwen3 0.6B 0.649** > **Nemotron 0.104**。

觀察(只記現象):
- **Qwen3-ASR 1.7B 的術語召回輸 0.11,TER 卻和 Scribe 打平**——一般字錯差不多,輸在英文術語。
  0.6B 再掉 0.17。Whisper turbo 兩項都輸
- **Nemotron 在晶晶體上崩潰**:輸出幾乎沒有英文術語;`zh`(0.104)比 auto(0.237)更差。它的模型卡把 Mandarin 列在
  「broad-coverage」層(zh-CN),沒有提到句內中英切換
- 本機化的四個裡,**沒有一個在 exp5 上跟 Scribe 量不出差別**

**訓練資料**(任務書 §4;2026-10-15 查模型卡,**查詢發生在 Phase 1 跑完之後**,§9 記一筆):
- Qwen3-ASR(0.6B / 1.7B):模型卡只寫「large-scale speech training data」,**沒有資料集清單 → 訓練資料未公開,無法排除污染**
  ([模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B))
- Nemotron 3.5 ASR:列了 Granary、Common Voice、MLS、FLEURS、VoxPopuli,**加上「NVIDIA Riva multilingual ASR training set
  (Proprietary)」→ 專有部分未公開,無法排除污染**([模型卡](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b))。
  它在 exp5 上是最低分,污染若有也沒有幫到它
- 商用模型的訓練資料都未公開(與既有 arm 相同)

## 5. Gemini 3.5 Transcribe 的兩種失敗形狀(直連)

| 形狀 | 格子 | 現象 | 花費 |
|---|---|---|---|
| **空輸出** | exp1 `ikeda02__N3`(auto、ja)、`ikeda03__N0`(auto) | HTTP 200、`finishReason: STOP`、candidate **沒有 `content`**、沒有 `promptFeedback` | 輸入照收(帳本有記) |
| **重複迴圈** | exp5 `GDtrans_auto/T1__M3` | 第 422 字起「因为他」連續 **10,217 次**,轉寫 31,082 字;`candidatesTokenCount` **20,676**(同模型其餘 11 格 956–1,096) | **$0.263**(這一格占 Gemini 直連總花費 22%) |

- 空輸出 3 格都沒設語言或設 `ja-JP`、都沒詞表;同一檔帶詞表的 `_ja_kt` 都有輸出。**原因沒查**,只記現象
- 迴圈只出現在**沒設語言**的那格;同一檔 `languageCodes: ["cmn-Hans-CN"]` 正常(1,916 字)。只有 1 例,不推論成規則
- 本輪沒送 `maxOutputTokens`。**接這個模型要設輸出上限**,不然一格就能吃掉 10 倍預算——與鐵律 5「保險絲的計量單位要對齊計費單位」同一型
- exp5 `GDtrans_auto/T3__M3` 輸出是**繁體**(其餘 Gemini Transcribe 輸出都是簡體),沒設語言時字形不穩定

## 6. 一致性:經 OR 的 Scribe vs 既有直連

| 語料 | 比較 | 召回 | 差(經 OR − 直連) |
|---|---|---|---|
| exp5 | `ORscribe_zh` vs `Sbat_bi`(2026-08 直連,0 條詞表) | 0.930 vs 0.921 | +0.009 [+0.005, +0.019] |
| exp5 | 同一 arm 重跑(`ORscribe_zh` vs `ORscribe_zh_r2`,同日) | 0.930 vs 0.937 | −0.006 [−0.019, 0.000] |
| exp1 | `ORscribe_ja` vs `Sbat_ja`(直連,**50 條詞表**) | 0.743 vs 0.800 | −0.057 [−0.075, −0.039] |

- exp5:經 OR 比直連高 0.009~0.016,和同 arm 重跑的雜訊(0.006)同一級。**沒有切段**,所以沒有切段這個變因;
  剩下的可能是兩個月間模型更新或隨機性——**沒查,只記現象**。不影響 Gate A(規則用的是同一路徑的 Grok vs Scribe)
- exp1:差 −0.057,但**混了詞表**(直連那格帶 50 條,經 OR 開不了;`Sbat_ja_nokt` 不存在)。拆不開,不判讀
- 實務意思:**經 OR 用 Scribe 就等於沒有詞表**。exp1 上那是 0.06 的召回(N3 −0.134)

## 7. 未解事項

1. **Grok 的上游版本**:OpenRouter permaslug 只到 `x-ai/grok-stt-20260723`。要正式結論需要 xAI key 直連 2.0 重跑 Gate A
2. **Gemini 3.5 Transcribe 的空輸出與迴圈**:原因未查;經 OpenRouter 一律 400 的原因也未查
3. **Scribe 經 OR 的詞表**:`keyterms` 被白名單丟掉;沒找到其他名稱(偵察 §3)。也沒驗證經 OR 開 `diarize` 的品質
4. **exp5 參考稿以兩路 Gemini(`gemini-3.5-flash` / `gemini-3.6-flash`)的一致處為底**、拉丁字母分歧人工裁決(`exp5/scripts/build_reference.py`,既有侷限):對 Gemini 系的 arm(`Gbat`、`GDtrans`)可能有利。Gate A 的比較是 Grok vs Scribe,
   兩邊都非 Gemini,這條不影響方向
5. **exp5 只有 3 段**:所有 exp5 CI 都是 3 群 bootstrap,很粗
6. **開源模型的訓練資料**:Qwen3-ASR 未公開、Nemotron 含專有資料 → 污染無法排除
7. **經 OR 與直連的 Scribe 價差**(偵察 §4:經 OR $0.11/hr vs 筆記 ~$0.22/hr):原因未查
8. **語音輸入法要的資料本輪沒有**:短句延遲、自發口語、本機推論速度——Gate B 只是晶晶體上的批次準確度

## 8. 對定案文件的意思

- **Gate A 不通過 → 離線會議記錄 L1 配對維持「Gemini 批次 + Scribe v2」**,`docs/offline-meeting-architecture.md` 不動
- `results/stt-matrix.md` 加 §6d(本頁的摘要,標「經 OR / 直連」)
