# Gemini API 教訓(kikemu 版)

跨專案通用版由 ytplayer 維護:[`ytplayer/docs/gemini-api-lessons.md`](https://github.com/clarencechien/ytplayer/blob/main/docs/gemini-api-lessons.md)。
這頁只寫**在 kikemu 身上量到的**與**kikemu 現在怎麼做**,不重複通用論證。

---

## 1. Thinking 稅:本專案最貴的一課

thinking token **以輸出價計費**(官方 pricing 頁明載),而 `gemini-3.5-flash` 預設 medium。
kikemu 的逐句翻譯是機械性任務,思考完全是白付的。

### 同料 A/B(2026-08-14 實測)

用 kikemu 凍結的口譯 prompt(`worker/gemini.ts` = `scripts/prompts.py`)+ exp1 sakai06
的三句真實定稿句,兩代模型各跑一輪:

| thinkingConfig | 3.5-flash | 3.6-flash |
|---|---|---|
| (不設) | thoughts/output **29.1×** | **29.6×** |
| `thinkingLevel: "minimal"` | **0×** ✅ | **0×** ✅ |
| `thinkingBudget: 128` | 18.5× | 12.0× |
| `thinkingBudget: 0` | 0× | **400 拒收** |
| level + budget 同給 | \_400「only one of thinking budget and thinking level」\_ | 同左 |

**這推翻了通用版 §1 的一句話。** 通用版寫「`budget: 128` 兩者通吃且實際 thoughts=0」——
在 kikemu 的翻譯任務上 **budget 128 不等於 0**(還燒了 492~757 thoughts)。
`thinkingBudget` 是預算不是硬上限,實際 thoughts 可以超過它。
**只有 `thinkingLevel: "minimal"` 在兩代都真的歸零。**
(未測:audio 轉寫任務形狀是否有同樣結論。)

品質沒有退化:三句抽樣人工比對,台灣用語與專名表記皆正確,
`minimal` 甚至把「幻の堺幕府」譯得比預設思考版更貼(預設版譯成「傳說中的」)。

### kikemu 現在怎麼做

`worker/gemini.ts` 的 `generate()` 是單一 helper,三個呼叫點共用:

| 呼叫 | thinking | 為什麼 |
|---|---|---|
| `translateSentence` | **minimal** | 逐句翻譯,機械性任務 |
| `extractVocab`(pass B) | **minimal** | 文字 → JSON 抽取,機械性任務 |
| `researchTerms`(pass A) | **不關** | 要自己決定查什麼、查幾次 = reasoning-shaped |

400 fallback:`thinkingConfig` 被拒 → 拿掉重試一次(寧可多付思考費,不要整個功能掛掉)。
`THINKING_LEVEL=off` 可整個停用注入。

### 兩個附帶效應

- **`extractVocab` 的 JSON 截斷**:thinking token 確實會吃 `maxOutputTokens` 額度。
  把 `maxOutputTokens` 故意設成 300 跑同一個抽取 prompt:

  | | finishReason | thoughts | output |
  |---|---|---|---|
  | 預設思考 | **MAX_TOKENS**(JSON 壞掉) | 287 | **9** |
  | `minimal` | STOP(JSON 完整) | 0 | 206 |

  思考吃掉 287/300 的額度,只剩 9 個 token 給輸出——這就是設到 16384 還被截斷的
  機制。關掉思考後額度才真的都給輸出用(容錯 parser 仍保留當保險)。
- **Live API 不收這筆稅**:exp1/exp3/exp4 的 A 組(`gemini-3.1-flash-live-preview`)
  共 108 次呼叫,`thoughtsTokenCount` **全部是 0**。thinking 稅是 generateContent 的問題。

---

## 2. 成本表抄錯價,而且擴散了(已修)

初版 `results/report.md` §5 把 `gemini-3.5-flash` 記成 **$0.30/M in、$2.50/M out** ——
那是 **flash-lite** 的價。真價 **$1.50/$9.00**,低估 **3.6 倍**,並一路寫進
`docs/PRD.md` 的單位經濟與 `/admin` 的成本估算。

token 數本身沒抄錯:當時記的「6.6 萬 output tokens」其實是
7,123 output + **59,305 thoughts**,已經含思考了,只是乘錯單價。

| translate hop | 每小時 |
|---|---|
| 報告初版(lite 價) | $0.23 ❌ |
| 真價、thinking 預設開 | **$0.84** |
| 真價、`thinkingLevel: minimal` | **$0.11** |

thinking 佔該 hop **87%**。修完之後兩跳合計 **$0.35/hr**,與一體式的 $0.22/hr
(+音訊輸出)是同一數量級——原本「貴一倍」的說法不再成立。

**規矩**:成本表一律標**查價日期**與**模型級別**;抄價前先確認是 flash 還是 flash-lite;
促銷價(3.6/3.7-flash 目前 $0.75/$3.75,2027-01-01 漲回)不寫進單位經濟。

---

## 3. 保險絲:秒數擋不住 token

kikemu 對使用者的計量單位是**牆鐘秒**(`QUOTA_TIERS`),但 Gemini 是按 **token** 收費。
同樣一小時,句子被切得越碎、呼叫數越多——秒數沒超標,錢照燒。

| 層 | kikemu 現況 |
|---|---|
| 1 供應商端上限 | ⬜ **要人去 AI Studio Spend 頁設**,程式管不到 |
| 2 每步重試上限 | ✅ `MAX_RETRY_PER_SEQ = 3`(`retryZh` 每點一次就是一筆付費呼叫) |
| 3 每件工作 token 上限 | ✅ `SESSION_TOKEN_CAP`(0 = 不限但**照樣計數**) |
| 4 全域日預算 | ⚠️ QUOTA DO 已逐日累計 `tokens` / `calls`,尚未設全域上限 |
| + 花費可視 | ✅ `/admin` 今日欄顯示「分鐘 · token/句數 · 估算 NT$」;relay 每場 log |
| + 先存檔再花下一筆 | ✅ 實驗腳本 checkpoint(`results/raw/` 存在即跳過) |
| + 實驗腳本預算 | ✅ `judge.py` 有 `DRY_RUN=1` 與 `BUDGET_USD`(預設 $3),跑前估價、跑中累計 |

`scripts/aggregate.py` 會把 `results/raw/**` 裡早就存著的 `usageMetadata` 彙總成
prompt / output / **thoughts** / USD 一張表——數據一直都在,只是以前沒人讀。

---

## 4. 其他 kikemu 踩過的

- **模型 ID 先驗證**:`gemini-3-pro-preview` 曾 404(`judge.py` 註解留碑),
  「聽起來應該有」的 ID 一律先 `GET /v1beta/models`。
- **Live API 的音訊輸出 token 在 `usageMetadata` 低報**(report.md §7 侷限已記)——
  Live 的花費要從帳單側驗證,`aggregate.py` 表裡 A 組的 USD 是**下限**。
- **未知 `generationConfig` 欄位 → 400 → 拿掉該欄位重試** 是通用防禦,
  `generate()` 的 thinking fallback 就是這個形狀。
