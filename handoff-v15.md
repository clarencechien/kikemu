# handoff-v15:產品翻譯換到 3.6 Flash 之後重量一次

> 起因(2026-10-11):Google 停用 `gemini-3.5-flash`,呼叫自動轉到 `gemini-3.6-flash`(實測 `modelVersion` 已是 3.6,
> `docs/gemini-api-lessons.md` §5b)。產品翻譯已經在 3.6 上跑,但 exp1 4.71、產品逐句 4.06(v13)都是 3.5 的數字。
> 使用者:「1 和 2 一起做」——1 = 產品改成直接呼叫 3.6 並拿掉 `temperature`,2 = 在 3.6 上重量翻譯品質。
>
> **本檔在跑任何一筆付費呼叫之前寫定並 commit**;判讀規則之後不改,偏離記在 §7。

## 1. 要回答的問題

1. 產品逐句翻譯換成 3.6 之後,整段 adequacy 比 3.5 好、差、還是量不出差別?
2. 「台灣用語 0 失誤、簡體 0 字」在 3.6 上還成立嗎?
3. 延遲與花費變多少?
4. (附帶)評審自己的雜訊有多大?——侷限 32:3.6 起 temperature 沒作用,評審不保證可重現

## 2. 兩個 arm(同一批句子)

| arm | 翻譯 | 來源 |
|---|---|---|
| **S-3.5** | `gemini-3.5-flash`、`thinkingLevel: minimal`、`temperature 0.2` | **沿用** v13 的 B(`results/raw/v13/translate/*__B.json`,2026-10-04,`modelVersion` 全是 3.5) |
| **S-3.6** | `gemini-3.6-flash`、`thinkingLevel: minimal`、**不送** `temperature` | 新跑 = 改完之後的產品呼叫 |

共同:凍結的 `INTERPRETER_SYSTEM` + `TRANSLATE_USER_TEMPLATE`;句子 = v13 主集(exp1 C+ 30 檔、現行斷句,408 句)。
3.5 已經叫不到了,所以 S-3.5 只能沿用舊輸出——**兩邊不是同一天翻的**,延遲比較要打折。

## 3. 量測

**整段 adequacy**(主要):N0 + N3 共 12 檔,逐句譯文依序接起來,用 exp1 `scripts/judge.py` 的同一個 PROMPT。
**評審換掉**(與 exp1 / v13 / v14 不同,§7 記為設計上的偏離):
`gemini-3.1-pro-preview`、`gemini-3.5-flash-lite`、`gemini-2.5-pro`——**寫死型號 ID,不用 `*-latest` 別名**。
- 拿掉 `gemini-3.6-flash`:它現在就是翻譯模型,會自己評自己
- 前兩位就是 v13 / v14 當時別名實際對應的型號(延續性);`2.5-pro` 補第三票
- 非 Google 評審:OpenAI / OpenRouter 的 key 2026-10-11 實測都 401,沒有可用的
- **每位評審對同一份譯文評兩次**(兩次都送 temperature 0;結果用來量評審雜訊)
- S-3.5 也用這組新評審重評,兩邊同一天、同一組評審

**在地化**:408 句兩邊都掃——PRC 用語(`score.py` `TW_BAD`)、簡體字(`SIMPLIFIED_RE`)、
假名殘留與裸英文(`scan_style.py` 的規則)。

**延遲 / 花費**:S-3.6 每句牆鐘秒數(併發 1)、prompt / output / thoughts token;S-3.5 用 v13 記下的值。

## 4. 判讀規則(先寫死)

以「檔」為單位:每檔每 arm 先把 3 位評審 × 2 次 = 6 個分數平均,再算 Δ = S-3.6 − S-3.5;
95% CI 用以檔為群的 bootstrap(12 群,10,000 次)。

- **R1 品質**:CI 下界 > 0 → 3.6 較好;CI 上界 < 0 → **3.6 較差**;含 0 → 量不出差別
- **R2 在地化**:S-3.6 的 PRC 用語 = 0 **且** 簡體字 = 0 → 「台灣用語 0 失誤」在 3.6 上仍成立;否則照實改寫文件
- **R3 延遲**:S-3.6 的 p50 / p90 照報;不設門檻(3.5 已不能選)
- **評審雜訊**:同一評審同一份譯文兩次的分數差,報平均絕對差與完全一致率(描述用)

**對產品的意思(先寫好)**:3.5 已經叫不到,不管結果如何產品都只能用 3.6 或換別的模型。
- 量不出差別或較好 → 文件改成「產品逐句翻譯 = 3.6,分數 X(新評審組)」,收工
- **較差** → 照實寫進文件與侷限,另開題目評估其他模型;這一輪不換模型

## 5. 花費與保險絲

牌價(Google 通知 2026-10-11 與 pricing 頁 2026-10-01 版):3.6-flash $1.50 / $7.50(促銷 5 折中,保險絲用原價);
3.1-pro-preview $2.00 / $12.00(≤200k);2.5-pro $1.25 / $10.00;3.5-flash-lite $0.30 / $2.50;thinking 以輸出價計。

估計:翻譯 408 次 ≈ $0.15;評審 12 檔 × 2 arm × 3 評審 × 2 次 = 144 次,兩位 pro 帶 thinking 約 $0.025 / 次 ≈ $2.5。
**合計約 $2.7,保險絲 $5.00**(帳本 `results/raw/_v15_ledger.json`)。先打一筆翻譯、一筆 pro 評審對價再放大。

## 6. 產品改動(與量測同一個 PR)

- `app/wrangler.jsonc` `TRANSLATE_MODEL` 與 `gemini.ts` 預設值:`gemini-3.5-flash` → `gemini-3.6-flash`
- `gemini.ts` 三處 `temperature`(翻譯 / 關鍵字產包搜尋 / 抽詞)拿掉
- 對 3.6 而言 `temperature` 本來就沒作用(§5b),所以**產品行為不變**;這一步只是避免之後的新模型報錯

## 7. 偏離紀錄

1. **第三位評審換人**:`gemini-2.5-pro` 雖然在模型清單裡,實際呼叫回 404「This model ... is no longer available
   to new users」(2026-10-11,翻譯跑完、評審對價時才發現;那一筆沒有產生評分)。換成 **`gemini-3-flash-preview`**
   ——比 3.6 早(照通知推定 temperature 有效)、與翻譯模型不同型號;牌價 $0.50 / $3.00(pricing 頁 2026-10-01 版)。
   另一個可用的 `gemini-3.1-flash-lite` 較弱,沒選。換人發生在任何評分結果出來之前,不是看了結果才換
2. **翻譯對價**:兩筆 thoughts 0、$0.00036 / $0.00044(估 $0.0004);408 句全程 $0.16,`modelVersion` 全是 `gemini-3.6-flash`
3. **評審對價**:`gemini-3.1-pro-preview` 一筆 $0.0177、`gemini-3.5-flash-lite` 一筆 $0.0002,估價規則($0.02 / 次)偏保守,照跑

## 8. 結果

(執行後填)
