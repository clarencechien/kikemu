# handoff-v16:OpenRouter 新一代 STT 批次 arm(使用者任務書原文 + 執行前決定)

> 原文由使用者提供(`kikemu-openrouter-stt-handoff.md`,2026-10-15),§0–§7 一字不改照錄;**§8 是偵察(Phase 0)之後、
> 跑任何 Phase 1 呼叫之前寫死的執行決定與判讀細則**,§9 偏離紀錄,§10 結果。


- 日期：2026-10-15
- repo：`clarencechien/kikemu`
- 先讀：`CLAUDE.md`、`results/report.md`、`results/stt-matrix.md`、`results/oracle_report.md`、handoff-v11（第二判準定義）、`dual_run.py`
- 範圍：**只跑批次**。即時/串流、speech-to-speech 不在本輪。

---

## 0. 為什麼做這輪

1. xAI（SpaceXSI）的 STT 已從 `grok-voice-transcribe-1.0` 換成 2.0：批次 $0.10/hr，支援 `zh`，有 `keyterm`（≤100 條）、詞級時間戳、diarization。若在中文上站得住，價格約為 Scribe 的一半，可能取代離線會議記錄 L1 配對中「專用 ASR」那一格。
2. OpenRouter 現在有統一的 `/api/v1/audio/transcriptions`，一把 key 可以跑 25 個 STT 模型，包含 Scribe v2、Gemini 3.5 Transcribe、Qwen3-ASR。補 arm 的成本很低。
3. 順帶取得「語音輸入法」構想需要的資料：Qwen3-ASR（hyday-talk 預設用的本機模型）和 Whisper turbo 在晶晶體上的表現。

**本輪要回答的兩個問題（Gate）：**

- **Gate A**：Grok STT 能否在「Gemini 批次 + 專用 ASR」配對中取代 Scribe v2？
- **Gate B**：哪些便宜或可本機化的模型（Qwen3-ASR、Whisper turbo）在晶晶體上沒有明顯輸給 Scribe？這是語音輸入法要不要做的前置資料。

---

## 1. 已查到的事實（2026-10-15）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| Grok STT 模型 | `grok-voice-transcribe-2.0`；1.0 於 2026-10-02 EOL，請求自動轉 2.0、同價 | xAI release notes | 已查 |
| Grok STT 直連 | `POST https://api.x.ai/v1/stt`（批次）、`wss://api.x.ai/v1/stt`（串流） | docs.x.ai | 已查 |
| Grok 中文 | 列出 `zh`（Mandarin）、`yue`（粵語）；**沒提繁體** | docs.x.ai | 已查 |
| Grok keyterm | `keyterm` 可重複，最多 100 條、每條 ≤50 字元 | docs.x.ai | 已查 |
| Grok 價格 | 批次 $0.10/hr、串流 $0.20/hr | xAI 4 月公告 | 已查 |
| OpenRouter 上的 Grok | slug `x-ai/grok-stt-1.0`，上架 2026-07-23 | OpenRouter 模型頁 | 已查 |
| **OpenRouter slug 背後是 1.0 還是 2.0** | 頁面沒寫 | — | **未查，Phase 0 要確認** |
| Grok Voice 3.0（S2S） | OpenRouter 上沒有；與本輪無關 | — | 不做 |
| OpenRouter STT 端點 | `POST https://openrouter.ai/api/v1/audio/transcriptions`，支援 JSON base64 或 OpenAI 相容 multipart | OpenRouter docs | 已查 |
| OpenRouter 限制 | multipart 上限 25 MB；**上游 60 秒逾時，長音檔要切段** | OpenRouter docs | 已查 |
| OpenRouter 參數 | `model`、`language`、`temperature`、`response_format`（json / verbose_json）、`timestamp_granularities`、`provider.options.<provider_slug>`（傳 provider 專屬參數，如 diarize、詞彙提示）。`prompt` 會被**忽略** | OpenRouter docs | 已查 |
| OpenRouter keyterm 傳遞 | 文件沒寫 keyterm 怎麼經 `provider.options` 傳 | — | **未查，Phase 0 要實測** |
| 計費回報 | 回應內含 `usage.seconds` 與 `usage.cost` | OpenRouter docs | 已查 |

### OpenRouter 上的 STT 模型與本輪處置

價格換算**假設**單位是每秒（Grok 換算後剛好 $0.10/hr，與官方一致，所以假設合理）。Phase 0 要用 `usage.cost` 驗證。

| 優先 | slug | 換算 $/hr（假設每秒） | 為什麼跑 |
|---|---|---|---|
| P1 | `x-ai/grok-stt-1.0` | 0.10 | Gate A 主角 |
| P1 | `elevenlabs/scribe-v2` | 0.11 | 對照組：比對經 OpenRouter 的結果與既有直連結果是否一致。**價格與筆記的 ~$0.22/hr 不符，原因待查**（可能沒開 diarize，或是 OpenRouter 的牌價） |
| P1 | `google/gemini-3.5-transcribe` | token 計價 | 新的專用轉錄產品，與 kikemu 用過的 Gemini 批次**不是同一個東西**，要另外量 |
| P1 | `qwen/qwen3-asr-1.7b` | 0.027 | Gate B；描述寫 22 種中文方言 |
| P1 | `qwen/qwen3-asr-0.6b` | 0.012 | Gate B；hyday-talk 的預設模型，可本機化 |
| P1 | `microsoft/mai-transcribe-2` | 單位不明（標 0.1） | 描述明寫 code switching，晶晶體直接相關 |
| P2 | `openai/whisper-large-v3-turbo` | 0.012 | Gate B 基準；Groq / Cloudflare 都有，延遲最低的雲端選項 |
| P2 | `openai/gpt-transcribe` | 0.27 | 補齊主流商用 |
| P2 | `meta/muse-voice-transcribe-1.0` | 0.18 | 新模型，描述提到 language biasing |
| P2 | `nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b` | 0.012 | 小模型、可本機化候選 |
| P2 | `assemblyai/universal-3-5-pro` | 0.45 | 補齊主流商用 |
| 跳過 | `deepgram/nova-3` | 0.26 | 已知 `multi` 模式不含中文，結構上做不了晶晶體（只跑 1 段確認仍是如此即可） |
| 跳過 | `elevenlabs/scribe-v2-medical`、`fish-audio/*`、`mistralai/voxtral-*`、`nvidia/parakeet-tdt-0.6b-v3`、`openai/whisper-1`、`openai/gpt-4o(-mini)-transcribe`、`google/chirp-3`、`microsoft/mai-transcribe-1.5` | — | 與本輪問題無關、有新版取代，或沒有中文的證據 |

---

## 2. Phase 0：偵察（先做，便宜）

每個 P1 模型只送 1~2 段短音檔，確認以下事項並記進 `results/openrouter-recon.md`：

1. **`x-ai/grok-stt-1.0` 背後是 1.0 還是 2.0**：看回應 header 或 metadata；必要時同一段音檔直連 `api.x.ai/v1/stt`（`model` 省略即預設 2.0）做比對。若無法判定，就**直連 xAI 當 Grok 的正式 arm**，OpenRouter 版只當參考。
2. **輸出字形**：各模型對 `zh` 輸出繁體、簡體還是混合。記錄原樣，不要先轉。
3. **keyterm 能不能經 OpenRouter 傳到 Grok 和 Scribe**：試 `provider.options` 底下 provider slug 的各種寫法，用含專名的段落看召回有沒有變化。傳不過去就記「經 OpenRouter 無法開」，**不要寫成「它不支援」**。
4. **價格單位**：用 `usage.cost` ÷ `usage.seconds` 反推，更新上表的 $/hr。
5. **既有語料的長度分布**：有沒有超過 60 秒的段落要切。

---

## 3. Phase 1：批次矩陣

### 語料

- **exp5（晶晶體）**：Gate A 與 Gate B 的主語料。
- **exp1 的 N1~N4，重點 N3（人群 8dB）**：Gate A 用，因為 Scribe 的優勢就在吵雜條件。
- **exp2 的 S1/S2 不跑**：它是 Breeze 的污染語料，對其他模型沒有額外資訊。

### 每個 arm 跑兩種設定

- `language` 不設（自動偵測）
- `language=zh`

晶晶體的槓桿是語言設定（+0.23~0.29），所以這兩格都要量，**不能只跑一種就下結論**。

### 詞表

- Grok 與 Scribe：Phase 0 確認 keyterm 傳得過去才加跑「有詞表」那格，使用既有場景包詞表。Grok 上限 100 條，超過時要記錄截斷規則。
- 其他模型：不開詞表。

### 計分

- 沿用 kikemu 既有計分腳本與正規化流程。**字形差異先統一再計分**（例如用 OpenCC 做 s2twp 或與既有流程一致的設定），並在報告中另列「原始輸出字形」欄位。不要讓繁簡差異混進分數。
- 輸出：每個 arm × 條件的分數、bootstrap CI、與 Gemini 批次的 pairwise Jaccard。

### Gate A 判讀（依 handoff-v11）

1. 在 exp5 與 N3 上，比較 Grok 與 Scribe 的分數，以及各自與 Gemini 配對的 Jaccard。
2. Jaccard 若打平（CI 含 0），改看第二判準：用 `dual_run.py` 算 Gemini+Grok 與 Gemini+Scribe 的**實質分歧數**與**專用 ASR 勝率**。
3. 取代條件（**三項同時成立**）：
   - Grok 在 N3 的分數不低於 Scribe 超過雜訊範圍
   - 實質分歧數不多於 Gemini+Scribe
   - 專用 ASR 勝率不低於 Gemini+Scribe
4. 任何一項不成立就維持 Scribe，並記錄差在哪。

### Gate B 判讀

- 對 Qwen3-ASR 0.6B / 1.7B、Whisper turbo、Nemotron：在 exp5 上與 Scribe 比，給出差距與 CI。
- 只報數字與排序，**不做「可以做輸入法」的結論**。那個結論還需要短句延遲和自發口語的資料，本輪沒有。

---

## 4. 紀律（照 `CLAUDE.md`）

- **「我們沒測」「我們沒開」「它沒有」是三件事。** 每次呼叫把實際送出的 request body（去掉音訊）寫進 `_meta.json`。**欄位沒送就不要出現在 meta 裡**（避免重演 diarize: False 的事故）。
- **OpenRouter 切段是新變因。** 若因 60 秒限制必須切段，所有 arm 必須用**同一份切段**，並把 Scribe 經 OpenRouter 的結果與既有直連結果並列。差異超過雜訊，就要先查清楚再往下判讀。
- **評估開源模型前先查訓練資料清單**（Qwen3-ASR、Nemotron）。查不到就在報告中寫「訓練資料未公開，無法排除污染」。
- **缺格不能當保守估計。** 跑失敗的格子標成缺格，不要用相鄰格子推論。
- **假說被推翻就只記現象**，不要立刻補第二個解釋。
- **xAI 自己的 WER benchmark 不引用為證據**，它的方法沒公開，而且以英文為主。

---

## 5. 安全與成本

- API key 放環境變數 `OPENROUTER_API_KEY`（必要時加 `XAI_API_KEY`），不要寫進 repo。
- 開 `provider.zdr: true`，若某 provider 因此拒收，就記錄下來，不要自動關掉。
- 預算保險絲：累計 `usage.cost` 超過 **US$5** 就停下回報。照上表估算，整輪應遠低於這個數。

---

## 6. 交付物

1. `results/openrouter-recon.md`：Phase 0 五項結果。
2. `results/openrouter-arms.md`：Phase 1 矩陣、Gate A / Gate B 判讀、未解事項。
3. 更新 `results/stt-matrix.md`：新增的 arm 與條件。**標註「經 OpenRouter」或「直連」。**
4. 若 Gate A 通過：在報告中寫出「要改離線會議記錄 L1 配對」的建議與證據，**但不要直接改任何定案文件**，由 Clarence 拍板。

## 7. 本輪明確不做

- 即時 / 串流 STT（包括 Grok 的 $0.20/hr 串流），批次結果不可外推到即時。
- Grok Voice 3.0 等 speech-to-speech 模型。
- 多引擎融合或 GER（已由 oracle 實驗排除）。
- 短句延遲量測（語音輸入法用），這需要另開一輪、另錄自發口語語料。

---

## 8. 執行前決定(Phase 0 之後、Phase 1 之前寫死)

偵察結果在 [`results/openrouter-recon.md`](results/openrouter-recon.md)。以下依偵察結果把 §3 落地;之後不改,偏離記 §9。

### 8.1 環境與呼叫

- 金鑰:OpenRouter = 環境變數 `or_key`;**沒有 xAI key**(無法直連 `api.x.ai`);Gemini 直連用 `gemini_key`
- **ZDR:不送**(使用者 2026-10-15「先不算 ZDR」)。grok / gemini-transcribe / gpt-transcribe / muse 沒有 ZDR 端點
- **不切段**:300 秒整檔在所有可用模型都 < 30 秒回應,60 秒逾時不構成限制
- **不重試**:失敗的格子就是缺格(§4「缺格不能當保守估計」)
- `temperature` 不送;`response_format` 預設(json)

### 8.2 Arm 與語料

| arm(目錄名前綴) | 模型 | 路徑 | exp5(M0/M3) | exp1(N0–N4) |
|---|---|---|---|---|
| `ORgrok` | x-ai/grok-stt-1.0 | 經 OpenRouter(**版本未定**,只當參考) | ✓ | ✓ |
| `ORscribe` | elevenlabs/scribe-v2 | 經 OpenRouter | ✓ | ✓ |
| `GDtrans` | gemini-3.5-transcribe | **直連** Gemini API(經 OpenRouter 一律 400) | ✓ | ✓ |
| `ORqwen17` / `ORqwen06` | qwen/qwen3-asr-1.7b / 0.6b | 經 OpenRouter | ✓ | — |
| `ORmai2` | microsoft/mai-transcribe-2 | 經 OpenRouter | ✓ | — |
| `ORwhisperT` | openai/whisper-large-v3-turbo | 經 OpenRouter | ✓ | — |
| `ORnemo` | nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b | 經 OpenRouter | ✓ | — |
| `ORgptT` | openai/gpt-transcribe | 經 OpenRouter | ✓ | — |
| `ORmuse` | meta/muse-voice-transcribe-1.0 | 經 OpenRouter | ✓ | — |
| `ORasm35` / `ORasm36` | assemblyai/universal-3-5-pro / 3-6-pro | 經 OpenRouter(3-6 是任務書之後才上架的後繼,一併跑) | ✓ | — |

- exp5 只跑 M0 / M3(= 既有批次 arm 的條件);exp1 只跑 Gate A 的三個 arm(任務書 §3:exp1 是 Gate A 用)
- exp1 加跑 N0(很便宜,而且 Gemini / Scribe 既有 arm 都有 N0)
- `deepgram/nova-3`:300 秒被拒「does not support large audio inputs」,本來就在跳過名單 → 不跑

### 8.3 語言設定(每個 arm 兩格)

- exp5:`_auto`(不送 language)與 `_zh`(OpenRouter `language: "zh"`;Gemini 直連 `languageCodes: ["cmn-Hans-CN"]`)
- **exp1 是日文**,任務書的 `zh` 改成 `_ja`(OpenRouter `language: "ja"`;Gemini 直連 `languageCodes: ["ja-JP"]`)

### 8.4 詞表

- **exp5 不開詞表**:exp5 沒有場景包;`terms.json` 是**計分目標清單**,拿來當詞表就是洩題。與既有 `Sbat_bi`(實際 0 條)一致
- exp1 加一格 `_ja_kt`:場景包 = `corpus/dict/speechmatics_vocab.json[picks[seg].domain]` 的 `content`,依檔案順序
  - `ORgrok_ja_kt`:`provider.options.xai.keyterm`,**取前 100 條**(xAI 上限;超過即截斷,丟掉的條目數記進每檔)
  - `GDtrans_ja_kt`:`customVocabulary` 全部(142 / 166 / 149 條,上限 1000)
  - `ORscribe_ja_kt`:**缺格**——經 OpenRouter 無法開詞表(偵察 §3)。直連帶 166 條的對照是既有 `Sbat_ja_full`

### 8.5 計分

- exp5:`exp5/scripts/score.py`(自動收 `exp5/results/raw/` 下所有 arm;OpenCC `s2t` 已在流程內 = 與既有流程一致的字形統一)。
  主指標 `recall_tolerant`(英文術語,實例加權)與 `ter`;原始字形另列
- exp1:`scripts/score.py`(新 arm 加進 `ARMS_JA` 與「無翻譯層」清單);主指標 `recall_tolerant`(67 個專名)
- **重跑 score 會用目前環境的 OpenCC(`opencc-python-reimplemented`)重算所有 arm**;既有 arm 的分數若因此變動,照實記 §9

### 8.6 Gate A(Grok 能否取代 Scribe,**結論只當參考**:Grok 版本未定)

比較對象:`ORgrok` vs `ORscribe`,同一條路徑(經 OpenRouter)、同一語言設定。
**主比較用「有設語言」那格**:exp5 `_zh`、exp1 `_ja`(兩邊都沒詞表,公平);`ORgrok_ja_kt` 只當附帶。
配對對象 = 既有 Gemini 批次:exp5 `Gbat`、exp1 `Abat`。

三項**同時**成立才算「可取代」(任務書 §3):

1. **N3 不輸超過雜訊**:exp1 N3 與 exp5 M3 各自算 Grok − Scribe 的 `recall_tolerant` 差,
   95% CI 用以段為群的 bootstrap(exp1 6 段、exp5 3 段,10,000 次)。**CI 上界 ≥ 0** 即成立(沒有確定輸)
2. **實質分歧數不多於 Gemini+Scribe**:用 `analysis/complementarity_pairs.py` 的定義(恰好一邊對的項目數),
   exp1 與 exp5 各算,**兩個語料都**要 `分歧(Gemini, Grok) ≤ 分歧(Gemini, Scribe)`
3. **專用 ASR 勝率不低於 Gemini+Scribe**:`a_win_rate` = 只有專用 ASR 對 /(只有專用 ASR 對 + 只有 Gemini 對),兩個語料都要 ≥

另報(不進判讀):Jaccard、`dual_run.py` 的 `substantive_divergence_ratio`(exp1)、`ORscribe` 與直連 `Sbat_ja` / `Sbat_bi` 的一致性。

### 8.7 Gate B(只報數字與排序,不下「可做輸入法」結論)

exp5 上 `ORqwen06`、`ORqwen17`、`ORwhisperT`、`ORnemo`(另附其餘 arm)對 `ORscribe` 的 `recall_tolerant` 差與 `ter` 差,
同一語言設定比(`_auto` 與 `_zh` 各一張),95% CI 以段為群(3 群,很寬,照報)。

### 8.8 一致性檢查(任務書 §4:經 OpenRouter 的 Scribe vs 既有直連)

`ORscribe_zh`(exp5)對 `Sbat_bi`、`ORscribe_ja`(exp1)對 `Sbat_ja_nokt`(若存在,否則 `Sbat_ja`)。
差超過「同一 arm 的雜訊範圍」就先查再判讀——雜訊範圍沒有既有數字,以 bootstrap CI 是否含 0 為準。

### 8.9 開源模型的訓練資料

Qwen3-ASR、Nemotron:跑前查模型卡;查不到訓練資料清單就寫「訓練資料未公開,無法排除污染」。

### 8.10 保險絲

OpenRouter 累計 `usage.cost` > **US$5** 即停(帳本 `results/raw/_v16_ledger.json`)。Gemini 直連另記(token 計)。
估計:OpenRouter ≈ $2.1、Gemini 直連 ≈ $0.6。

## 9. 偏離紀錄

(執行時填)

## 10. 結果

(執行後填)
