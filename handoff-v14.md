# handoff-v14:產品逐句翻譯 4.06 vs exp1 整段 4.72——差在顆粒度還是 thinking?

> 起因:handoff-v13 §8 附帶發現。同一批評審、同一天評,exp1 C+ 的整段譯文 N0 4.72,
> 產品逐句譯文(v13 的 B)4.06。兩者差兩個變因:
> **顆粒度**(exp1 整段一次翻 / 產品逐句翻)與 **thinking**(exp1 沒送 thinkingConfig = 模型預設 /
> 產品 `thinkingLevel: "minimal"`)。使用者:「先做 b」。
>
> **本檔在跑任何一筆付費呼叫之前寫定並 commit**;判讀規則之後不改,偏離記在 §6。

## 1. 設計:2 × 2,同一份 ASR 逐字稿

| | thinking 預設(不送 thinkingConfig) | thinking minimal |
|---|---|---|
| **整段**(整份逐字稿一次翻) | **W-def**(= exp1 C+ 的做法,今天重翻) | **W-min** |
| **逐句**(現行產品斷句,每句一次翻) | **S-def** | **S-min**(= v13 的 B,沿用,不重翻) |

共同條件:`gemini-3.5-flash`、temperature 0.2、凍結的 `INTERPRETER_SYSTEM` 與 `TRANSLATE_USER_TEMPLATE`
(`scripts/prompts.py`,與 `translate_c.py`、產品逐字相同)。

- **整段**的輸入 = `results/raw/Cplus/{seg}__{cond}.json` 的 `transcript`(exp1 `translate_c.py` 同一欄)
- **逐句**的輸入 = v13 主集的句子(同一批 C+ AddTranscript 事件、`segmentation_replay.py` new 規則)
- 逐句譯文直接依序接起來(不加分隔)再評——與 v13 相同

**資料**:exp1 N0 + N3 共 12 檔(= exp1 judge.py 與 v13 的整段評審範圍)。

## 2. 量測

- **整段 adequacy**:exp1 `scripts/judge.py` 的同一個 PROMPT、同三個評審
  (gemini-3.6-flash / gemini-pro-latest / gemini-flash-lite-latest),對照驗證過的日文參考。
  S-min 沿用 v13 的評審結果(同一天、同一批評審)
- **漂移檢查**:今天重翻的 W-def vs 8 月 exp1 C+ 的譯文(v13 §7-5 已用同批評審重評:N0 4.722 / N3 2.333)
- **逐句的成本與延遲**(產品相關):S-def vs S-min 每句的牆鐘秒數、thoughts token、花費。
  S-min 沿用 v13 的量測;S-def 同樣併發 1、在這個容器量

## 3. 判讀規則(先寫死)

以「檔」為單位:每檔每格先把三個評審的分數平均,再算效應;95% CI 用**以檔為群**的 bootstrap(12 群,10,000 次)。

- **主效應**:顆粒度 = mean(W) − mean(S);thinking = mean(def) − mean(min)(各自平均另一因子的兩個水準)
- **交互作用** = (W-def − W-min) − (S-def − S-min)
- 某因子 **CI 下界 > 0** → 「它是落差的原因之一」;CI 含 0 → 「量不出它的作用」
- **總落差** = W-def − S-min;另報各主效應占總落差的比例(只是描述)
- **R0 漂移**:W-def(今天)與 exp1 C+(8 月譯文、今天評)差的絕對值 ≤ 0.15 → 今天的整段重翻可以代表 exp1;
  超過就在結論裡標明「整段的數字今天重現不出來」

**對產品的意義(先寫好,結果出來照填)**:
- 只有 thinking 有作用 → 值得評估「逐句翻譯開 thinking」:要另外看 S-def 的延遲與花費(鐵律 4:thinking 以輸出價計)
- 只有顆粒度有作用 → 逐句翻譯本身就是上限,調 thinking 沒用;要改善得給翻譯更長的上下文(另開題目)
- 都有 → 兩件分開評估
- 都量不出 → 回頭檢查量尺(逐句接起來的譯文在整段量尺上可能被不公平地扣分)

## 4. 花費與保險絲

牌價同 v13 §6 與 §7-1(pricing 頁 2026-10-01 版):3.5-flash $1.50 / $9.00 每百萬 in / out,thinking 以輸出價計。

估計:整段翻譯 24 次(thinking 預設的長回應較貴)≈ $0.15;S-def 約 165 句 × ~$0.004(thinking 預設,
鐵律 4 實測 thoughts/output 約 29×)≈ $0.7;整段評審 3 格 × 12 檔 × 3 評審 = 108 次 ≈ $0.9。
**合計約 $1.8,保險絲 $4.00**(帳本 `results/raw/_v14_ledger.json`)。S-def 先打一筆對價再放大。

## 5. 不做的事

- 不改產品(這輪只回答「差在哪」)
- 不加新的 thinking 等級(只比預設與 minimal——那正是 exp1 與產品的差別)

## 6. 偏離紀錄

(執行時填)

## 7. 結果

(執行後填)
