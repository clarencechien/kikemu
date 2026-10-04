# Handoff v12 — Gemini 新一代 Live 模型 × exp1,與「模式選單」

**目的:回答「Gemini 出了好多更新,現在全用 Gemini 會不會不一樣」,用數字說話;
然後把「導覽 / 對話 / Gemini 對照」做成選單。**

觸發:使用者用 kikemu 聽 YouTube / Threads 街訪效果很差(逐句判讀見對話紀錄),
問能不能加切換開關,並要求「3.5、3.8 如果有都要試」。

---

## 0. 前置:音檔重建(已完成,2026-10-04)

音檔依授權不進版控(PR #72),本機 checkout 沒有 `corpus/conditions/*.wav`。
新增 `scripts/rebuild_exp1_audio.py`(repo 原本缺 mp3 → wav 這一步的紀錄),
重建後**三個指紋全部相符**:

| 指紋 | 結果 |
|---|---|
| 30 檔時長 vs 舊結果 `audio_s` | 30/30 相符(到 0.01 秒) |
| `degrade.py` 各條件 RMS vs committed `degrade_meta.json` | 30/30 相符(4 位小數);RIR、T60、噪音、seed 全同 |
| SM 批次重跑 `sakai06__N0`,轉寫 vs 舊 `Cb` | **逐字相同** |

→ 新數字可以與 exp1 既有數字直接比。

## 1. Step 1 探針結果(已完成,`results/probe_gemini_live*.json`)

| 模型 | 能不能接 | 關鍵事實 |
|---|---|---|
| `gemini-3.1-flash-live-preview` | ✅ AUDIO | exp1 arm A 量的那顆,行為與 8 月一致;TEXT 被拒 |
| `gemini-3.5-transcribe-live` | ✅ **只收 TEXT** | **interim(暫定)+ final 兩層**;`languageCodes`、`customVocabulary`(≤1000,官方建議 ≤100,**沒有讀音欄位**)都收;**不回 usageMetadata** |
| `gemini-3.5-live-translate-preview` | ✅ AUDIO | **不吃 systemInstruction**(凍結 prompt 無效,文件明載);目標語言要放 `generationConfig.translationConfig.targetLanguageCode: "zh-Hant"`(不設就輸出英文) |
| `gemini-3.8-live` | ✅ AUDIO | 版本字串與 3.1 相同,**但不是別名**:內部名稱不同、回報 thoughts token;**不接受任何 thinkingLevel** |
| `gemini-3.8-live-extended-thinking` | ✅ AUDIO + `thinkingLevel: low` | **必須**給 thinking level,`minimal` 被拒(鐵律 4 的 minimal 在這顆做不到) |

**官方文件(live-api/live-transcribe,2026-10-04 讀)寫明的限制:**
transcribe-live 單場**最長 10 分鐘**、**即時不支援語者分離**、**沒有詞級時間戳**(只有語句級)。

**牌價(ai.google.dev/pricing,頁面 2026-10-01 更新,2026-10-04 查):**

| 模型 | 牌價 | 每小時音訊(估) |
|---|---|---|
| 3.1 / 3.8 / 3.8-ET Live | audio in $0.005/min、audio out $0.018/min、text out $4.50/M | ≤ ~$1.4(含語音輸出) |
| **3.5 Transcribe Live** | in $0.005/min + out $0.004/min,官方 blended **~$0.009/min** | **~$0.54** |
| 3.5 Live Translate | in $0.0053/min + **audio out $0.0315/min** | **~$2.21** |
| 對照:SM 即時 | — | $0.24(stt-matrix 既有) |

## 2. Step 2 的 arm(exp1 30 檔,1× 實時推流,0.5 秒 chunk)

| arm | 模型 | 設定 | 量什麼 |
|---|---|---|---|
| `G31` | 3.1-flash-live-preview | exp1 arm A 原樣 | **同日對照組**:檢查 8 月以來的漂移 |
| `G38` | 3.8-live | exp1 arm A 原樣 | 一體式 |
| `G38T` | 3.8-live-extended-thinking | arm A + thinking low | 一體式 |
| `GX` | 3.5-live-translate-preview | translationConfig zh-Hant | 一體式(prompt 無效) |
| `GT` | 3.5-transcribe-live | ja-JP,無詞表 | **只有耳朵** |
| `GTv` | 3.5-transcribe-live | ja-JP + 場景包全部詞條(content,無讀音) | **只有耳朵 + 詞表** |

**主指標**:原文轉寫的 tolerant 專名召回(67 專名 × 5 條件,micro),與 exp1 完全同一套 `score.py`。
**副指標**:逐條件、N3 空輸出數;一體式的譯文跑台灣用語 / 簡體掃描(`scan_style.py`);
GT/GTv 記暫定與定稿的時間。**沒量**:譯文 adequacy(沒有評審面板)。

**對照基準(既有數字)**:SM 即時無詞表 `C` **0.639**、SM 即時 + 完整詞表 + 假名 `Cplus` **0.791**、
SM 即時 + 50 詞無讀音 `Cplus50` **0.699**、舊 Live `A` **0.439**。

| 比較 | 變因 |
|---|---|
| **GT − C** | **引擎**(兩邊都沒詞表)← 乾淨的對照 |
| GTv − Cplus | 引擎 + 讀音(Gemini 沒有讀音欄位)← 混變因,不可單獨引用 |
| GTv − GT | Gemini 詞表的價值 |
| G38 / G38T / GX − G31 | 新一體式 vs 舊一體式(同日) |

## 3. 判讀規則(**先寫死,在看到任何 step 2 數字之前**)

**R1 漂移**:|G31 − 0.439| ≤ 0.05 → 8 月的數字仍可當基準;> 0.05 → 記錄漂移,
所有比較改用同日的 G31,不引用 8 月的 A。

**R2 Gemini 耳朵**(決定選單「Gemini 對照」用哪一顆、怎麼標示):

| 條件 | 結論 |
|---|---|
| GTv ≥ 0.741(Cplus − 0.05)**且** GTv 的 N3 ≥ 0.50 | 與產線同級,選單標「可用」 |
| GT ≥ 0.639(≥ C) | 引擎不輸無詞表的 SM,選單標「對照」 |
| 以上皆否 | 選單標「僅供對照,實用不建議」 |

選單的 Gemini 耳朵一律用 GT / GTv 中召回較高的設定(架構:只換耳朵,譯仍走凍結 prompt,鐵律 3 不動)。

**R3 一體式要不要成為第四個選項**:G38 / G38T / GX 中最好的一個,
整體 ≥ 0.70 **且** N3 ≥ 0.40 → 加入選單;否則只記錄,不加。

**R4 主線**:只有某個 Gemini arm 整體勝 Cplus ≥ +0.05 **且** N3 不輸 → 建議**另開任務**重評主線。
本任務**不改**導覽預設。

**保險絲**:全部 arm 合計牌價估計 ≤ **$8**(估計約 $5.5,見 §1 牌價 × 43.8 分);
單檔連續 3 次錯誤即放棄該檔並記錄。

## 4. Step 3(選單)

三個模式,一個選單(不是兩個獨立開關——Gemini 即時沒有語者分離,四種組合有一種不存在):

| 模式 | 聽 | 斷句 | 譯 | 誰看得到 |
|---|---|---|---|---|
| **導覽**(預設) | SM 單人 | 現行 | 3.5-flash 逐句、凍結 prompt | 所有人 |
| **對話** | SM + `diarization: speaker` | **換人就切**;字數上限不切在字中間 | 同上 | 所有人 |
| **Gemini 對照** | 3.5-transcribe-live(R2 決定設定) | 定稿即切 | 同上 | **只有 admin** |

工程限制:transcribe-live 單場 10 分鐘 → 到點輪替連線;Live 長連線也會遇到 HKG 區域封鎖 →
被拒時改由釘在 `GEMINI_PROXY_REGION` 的 DO 代為建立上游;Gemini 耳朵不回 usage →
以音訊秒數 × 牌價記帳(鐵律 5)。

## 5. 驗收

- [ ] step 2 六個 arm 各 30 檔(或標明缺哪幾檔、為什麼)
- [ ] 判讀照 §3,不臨場改
- [ ] 選單三模式 build 通過;能本機端到端就跑 `probe-ws`
- [ ] 報告、stt-matrix、README、app/README 同步;數字對得上 `results/`

## 6. 偏離紀錄

1. **GX 的花費估算錯了 9 倍,差點觸發全域保險絲。** 第一版把 live-translate 的
   `promptTokensDetails` 裡的 TEXT 明細也算錢,估出 $0.34/min(全 30 檔 ≈ $14.7);
   但它的 `promptTokenCount` 只等於 AUDIO 那一項,官方也註明「依音訊 token 計費」。
   改成只算音訊之後是 **$0.0363/min**,與官方 blended $0.0368/min 吻合。
   處置:GX 中途停掉(其他五個 arm 不受影響)、修正規則、重算已完成的 4 檔、重啟。
   **教訓**:保險絲是全域的,一個 arm 估錯會把其他 arm 一起餓死——估價規則要先用
   一檔對官方牌價驗證,再放大跑。

2. **G31 出現「靜默中斷」,而且只有 G31 有。** 跑到一半(各 arm 約 14/30 檔)檢查時發現:
   G31 有 6 檔在**非 N3** 條件下,模型完成第一個 turn 之後就不再回任何東西
   (沒有錯誤、沒有關閉,只是安靜),例如 hig01_A1__N1 在 18.5 秒之後的 93 秒全空。
   8 月的 A(同一顆模型)非 N3 是 0 檔;其他五個新 arm 非 N3 也是 0 檔。
   N3(人聲 8dB)的靜默各 arm 都有、8 月的 A 也有 3 檔,那是已知的崩潰,**不算在這裡**。

   **兩個解釋都說得通**:(a) 我同時開了 12 條 Live session(8 月只開 2 條)造成的測試假象;
   (b) 3.1 preview(官方現在標為 legacy preview)本身退化。

   **判讀規則(在重跑之前寫下)**:六個 arm 跑完後,把 G31 這 6 檔以**併發 1** 重跑到
   `G31r`。
   - 重跑**不再中斷** → 判為測試假象:R1 用「G31 以 G31r 取代這 6 檔」計算,
     並同時列出未取代的數字
   - 重跑**仍然中斷** → 判為模型行為:R1 用原始 G31,中斷率本身記為發現
   - 一半一半 → 兩個數字都列,R1 的結論標「無法判定」

## 7. 偏離紀錄(續)

(執行後填)
