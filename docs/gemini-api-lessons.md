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

### 補測:audio 轉寫也一樣,而且 3.7-flash 不給你選(exp5,2026-08-18)

上面那句「未測:audio 轉寫任務形狀是否有同樣結論」現在測了。
exp5 拿同一批 5 分鐘中英夾雜音檔跑 `generateContent` 轉寫,六檔全跑:

| 模型 / 設定 | thoughts | output | thoughts/output | 每小時音訊成本 | 速度 |
|---|---|---|---|---|---|
| 3.5-flash `thinkingLevel: "minimal"` | **0** | 1319 | **0×** ✅ | **$0.26/hr** | 28.8× 實時 |
| 3.7-flash `thinkingLevel: "low"` | 25930 | 1386 | **18.7×** | **$2.69/hr** | 21.0× 實時 |

(單檔 T1__M0 的 usage;成本以 $1.50/M in、$9.00/M out、gemini-3.x-flash 級計,
2026-08-14 核實牌價。原始檔:`exp5/results/raw/Gbat/`、`exp5/results/raw/Gbat37/`。)

兩個要記住的點:

1. **轉寫是機械性任務,結論與翻譯一致**——minimal 歸零,非 minimal 就是十幾倍的稅。
2. **`gemini-3.7-flash` 不支援 `minimal`**,送出去直接 400:
   `Thinking level MINIMAL is not supported for this model. Please retry with other thinking level.`
   能退的最低是 `low`,而 low **不是關閉**(18.7×),成本因此跳 10 倍。
   所以「換更新的模型」不必然更省——升版前先確認它還吃不吃 minimal。

品質沒有退化:三句抽樣人工比對,台灣用語與專名表記皆正確,
`minimal` 甚至把「幻の堺幕府」譯得比預設思考版更貼(預設版譯成「傳說中的」)。

### Gemma 4 的兩個坑(2026-08-19,同一把 key)

同一個 Generative Language API 也服務 Gemma 4,但行為和 Gemini 不一樣:

1. **thinking 當一般 part 回傳,且不一定標 `thought: true`。**
   直接取 `parts[0].text` 會拿到一串英文推理分析,不是答案。
   必須 `"".join(p["text"] for p in parts if not p.get("thought"))`。
   Gemini 不會這樣。
2. **`thinkingLevel: "minimal"` Gemma 4 吃,而且真的歸零。**
   同一句翻譯:預設 493~692 thoughts、延遲 15.8~16.0s;
   minimal 後 **0 thoughts、1.9s**。鐵律 4 在 Gemma 上同樣成立——
   而且比 3.7-flash 好,後者直接拒收 minimal。
3. **會聽的變體拿不到。** `gemma-4-26b-a4b-it` 與 `gemma-4-31b-it`
   是這把 key 唯二服務的 Gemma 4,兩者送音訊都回
   400 `Audio input modality is not enabled for this model`。
   HF 上 tag 為 `any-to-any` 的 `E2B` / `E4B` / `12B` 才有音訊,
   但 AI Studio 與 OpenRouter 都沒有服務。

實測見 `results/report.md` §3F。

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

---

## 5. 參數棄用通知:`thinking_budget` 與 `temperature` / `top_p` / `top_k`(2026-10-07 收到)

**出處**:Google 寄給本專案的通知信「Parameter Deprecation」(使用者 2026-10-07 轉貼;
內容是 Google 的說法,本專案**沒有實測驗證**)。觸發原因是本專案近期的請求帶了 `temperature`
(產品的翻譯呼叫、handoff-v13 / v14 的實驗腳本與評審)。

### 通知說了什麼

| 參數 | 現在(3.x) | 之後推出的新模型 |
|---|---|---|
| `thinking_budget` | 被轉換成 `thinking_level` 照收 | **400 `INVALID_ARGUMENT`**,不再轉換 |
| `temperature` / `top_p` / `top_k` | **從 Gemini 3.6 Flash 起就已固定為預設值,自訂值沒有作用** | **送了就報錯** |

建議做法:改用 `thinking_level`(`minimal` / `low` / `medium` / `high`,各型號支援的等級不同)或不送;
三個取樣參數全部拿掉。通知範例用的是 `gemini-3.8-flash`,但**範圍是「之後推出的新模型」,不是只有 3.8**。
另提到 Interactions API 已 GA、`generateContent` 改稱 legacy(仍完整支援)——本專案沒有遷移計畫。

### kikemu 的現況(2026-10-07 盤點)

| | 產品 `app/worker/gemini.ts` | 實驗腳本 |
|---|---|---|
| `thinking_budget` | **沒送**(鐵律 4 早就規定用 `thinkingLevel`,`thinkingBudget` 只出現在註解的 A/B 表) | 沒有 |
| `temperature` | **3 處**:逐句翻譯 `0.2`、關鍵字產包 pass A 搜尋 `0.0`、pass B 抽詞 `0.0` | exp1 `translate_c.py` / `judge.py`、v13 / v14 等約 30 個檔 |
| `top_p` / `top_k` | 沒送 | 沒有 |
| 翻譯模型 | `TRANSLATE_MODEL = gemini-3.5-flash`(`wrangler.jsonc`) | |

**現在不會壞**:3.5-flash 不在「之後的新模型」裡。**換模型那天會壞**:`generate()` 遇到 400 只會
拿掉 `thinkingConfig` 重試一次(§4 的通用防禦),`temperature` 照送 → 連兩個 400 → 每句都是「譯文暫缺」。

### 對已發表數字的影響

1. **評審的「temperature 0」對 `gemini-3.6-flash` 沒有作用。** exp1 `scripts/judge.py`、handoff-v13 的逐句
   兩兩評審與整段評審、handoff-v14 的整段評審,都寫了 `temperature: 0`,文件也描述成「temperature 0」。
   照通知,3.6 Flash 起自訂取樣無效 → 那一位評審**其實是預設取樣,有隨機性**。另兩位評審
   `gemini-pro-latest` / `gemini-flash-lite-latest` 是別名,對應哪個型號沒公告,是否受影響**不確定**。
   - **結論不翻**:v13 / v14 都是同一批評審、同一天評兩邊,評審的隨機性兩邊都有;主要結論的差距
     (v14 顆粒度 +0.674,CI 下界 +0.368;v13 逐句淨勝 CI [+0.076, +0.155])都在多評審、多檔平均之後仍然成立
   - **但「同樣的輸入評兩次會得到同樣的分數」這個前提不成立**——小於 0.1~0.2 的整段 adequacy 差,本來就要打折
     (v14 R0 已記),現在多一個來源:評審本身
   - exp1 的 adequacy(4.71 等)也是這三位評審評的,同一個保留適用(報告侷限 32)
2. **翻譯模型 3.5-flash 的 `temperature: 0.2`**:3.5 比 3.6 早,通知沒說它受影響——v14「同一份逐字稿重翻,
   整段分數差 0.2」的翻譯隨機性說法**照舊**,但這點也是推定,沒有驗證 3.5 是否真的吃這個值

### 換模型時的檢查清單(還沒做)

- [ ] 拿掉 `gemini.ts` 三處 `temperature`(翻譯 / 搜尋 / 抽詞)
- [ ] 確認新模型支援 `thinkingLevel: "minimal"`(3.8-live-extended-thinking 就拒收 minimal,見 handoff-v12)
- [ ] 用 exp1 30 檔重量翻譯品質——拿掉 `temperature` 等於換了量測條件,exp1 / v13 / v14 的翻譯數字不再直接適用
- [ ] 實驗腳本的評審呼叫也拿掉 `temperature`,文件不再寫「temperature 0」
- [ ] (可選,不影響現行行為)`generate()` 的 400 fallback 一併拿掉取樣參數再試,讓意外換模型時不是全滅
