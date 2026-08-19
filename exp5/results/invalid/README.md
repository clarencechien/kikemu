# 作廢的結果(保留當證據,**不要進對照表**)

`score.py` 會自動掃 `results/raw/` 底下的每個目錄,所以作廢的結果不能留在那裡。

## `Xgma_12b_300s_overlimit/`

Gemma 4 12B,2026-08-19,Modal A100-40GB。**整段送 300 秒音訊。**

**作廢原因:違反模型卡的硬性規格。**
[模型卡](https://huggingface.co/google/gemma-4-12B-it) §7 明載:

> Audio supports a maximum length of **30 seconds**.

送 300 秒(超出 10 倍)之後輸出崩成重複迴圈:

```
thoughtthought
我現在正在寫一個關於一個關於一個關於一個關於…（重複 8152 字）
```

T1__M0 8201 字、T1__M3 8199 字,同一批檔其他 arm 是 1500~2800 字;
最長重複片段 8152 字,中文字比例 0.995。**沒有任何可用資訊。**

同時違反的還有模型卡 §4:「Audio content **after** the text in your prompt」——
當時 audio 放在 text 前面。

**教訓:跑一個沒跑過的模型之前,先把模型卡的「限制」章節讀完。**
這一輪之前已經把「T4 記憶體不夠 → 切塊」當成有代價的妥協寫進 notebook 與 README;
實際上 30 秒是這個模型訓練分佈的邊界,**整段送本來就不會有正確答案**,
切塊不是妥協而是規格要求。花費約 $0.54。
