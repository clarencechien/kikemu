# 晶晶體(中英夾雜)聽譯評測報告 — exp2

> 回答 handoff-v2:同一個詞表機制在中英夾雜技術演講上是否有效?
> 投影片詞表 vs 領域詞表?哪些英文不該翻?

## 0. TL;DR

(跑完填)

---

## 1. 方法與信度控制

### 1.1 語料

台大李宏毅課程 3 段 × 5 分鐘(主題不重疊):

| 段 | 課程 | 內容 | 切片 |
|---|---|---|---|
| S1 | ML2021 自注意力機制(上) | Self-attention 導入(drug discovery、graph、one-hot) | 6:00–11:00 |
| S2 | ML2021 GAN(一) | network/distribution/generator(含輝夜動畫離題段) | 7:00–12:00 |
| S3 | 生成式AI導論2024 第1講 | ChatGPT/Transformer/Stable Diffusion | 15:00–20:00 |

- 投影片:`speech.ee.ntu.edu.tw` 官方 PDF 直接下載(self_v7 / gan_v10 / 0223_intro_gai)
- 音訊:YouTube 官方影片在本環境被 bot 驗證擋下,改經公開鏡像取得**同一錄影**
  (S1 時長 1698s 與官方上傳完全一致,逐檔驗證);音檔僅私下評測、不入庫不散布
- S3 切片位置是在任何 arm 執行前,以術語密度探測(300-600s 段僅 8 個英文串,
  900-1200s 段 13 個)後決定的
- 授權注意:課程公開 ≠ 開放授權,報告僅引用短句

### 1.2 標準答案(無官方文字可對照,人工核對比重最高的一步)

- 三路獨立轉寫:Speechmatics batch(cmn)、gemini-3.5-flash、gemini-3.6-flash
- **合併規則(寫死於 build_reference.py):以兩個 Gemini 的 token 級一致部分為基底**;
  80 個分歧塊逐條人工檢視(`corpus/reference/review.md`),裁決寫入 `overrides.json`
- **SM-cmn 不參與英文拼寫的表決**(記錄在案的偏離):它在受測維度上系統性弱勢
  (COVID-19 → 「CoffeeNight」、network → 「news」),讓它投票會污染正解;
  但它的**中文**是可靠的第三意見,用於裁決中文區域:
  - 「英年早逝」「火影忍者」「爆雷」——三方證據一致後修正
  - **關鍵裁決:講者說的是「函式」不是 "function"**(gm35 把 10 處中文「函式」
    英文化成 function;SM 聽到 8 次 函式/函数 佐證)——這同時暴露了
    「用 LLM 做逐字稿會被它的正字法偏好污染」這個方法論陷阱
- 數字讀法(one-hot 向量「1 0 0 0」)按實際唸法修正

### 1.3 術語清單(主要指標正解,凍結)

從已驗證逐字稿機械抽取**實際講出來的**英文串(連續拉丁 token),
排除純填充詞(know, so, ok)與數學式唸法(f x、ax b):

- S1:26 個術語 51 次(label×9, graph×6, saw×4, sentiment analysis×3…)
- S2:10 個 20 次(network×5, distribution×3, model×3…)
- S3:6 個 16 次(ChatGPT×8, AI×3, Transformer×2, Stable Diffusion, Midjourney, DALL-E)

判定規則:strict = 大小寫不敏感完全相符;tolerant = 另接受連字號/空白折疊與
凍結變體(covid 19/covid、one hot、chat gpt…)。變體在任何 arm 執行前凍結。
每個術語標注 no_translate(架構名/專名/縮寫)或 may_translate(一般技術詞),
分類規則機械化 + 凍結手表。

### 1.4 詞表來源(本實驗核心變因)

| 詞表 | 來源 | 條數 | 洩漏控制 |
|---|---|---|---|
| domain | Google ML Glossary 公開頁(765 詞目→645 條) | 645 | **絕不接觸投影片與逐字稿** |
| slides_S1/S2/S3 | 該堂課官方投影片 PDF 文字 → Gemini 抽取 | 42/81/22 | 與逐字稿高度重疊是產品場景,非洩漏 |

### 1.5 聲學條件 M0-M3

- RIR:MIT IR Survey,**規則 = Classroom 空間中 T60 最接近 0.8s** →
  `h100_Classroom`(T60=0.721s)。(無限制的最接近值是浴室 —— 衰減對、空間錯,
  規則因此加上空間類別約束,並記錄)
- M2 = M1 + DEMAND OOFFICE(辦公室實錄)@ SNR 20dB;M3 = M1 + DEMAND
  OMEETING(會議交談實錄)@ SNR 12dB;語音活動段 RMS、固定種子,同 v1
- 3 段 × 4 條件 = 12 檔

### 1.6 實驗 arm

| arm | 聽 | 詞表 | 語言模式 |
|---|---|---|---|
| X_cmn | SM 即時 | 無 | cmn 單語 |
| X_bi | SM 即時 | 無 | **cmn_en 中英雙語 pack** |
| Xdom_bi | SM 即時 | domain | cmn_en |
| Xsli_bi | SM 即時 | slides | cmn_en |
| Xsli_cmn | SM 即時 | slides | cmn(測詞表能否救單語模式) |
| G | Gemini Live 一路(3.1-flash-live) | — | — |

**記錄在案的替代**:handoff-v2 指定 `language: multi`;本帳號的即時 multi 模型
(melia-1)回覆 "currently not supported"。Speechmatics 另有**專用中英雙語 pack
`cmn_en`**,且支援 enhanced/max_delay/additional_vocab 全參數 —— 比 multi 更貼合
句內語碼轉換場景,故雙語 arm 使用 cmn_en。

全部即時 WS、1× 真實速度、0.5s chunk、全訊息時戳落檔。translate 層與 G 共用
kikemu 的口譯 systemInstruction(P0);P1 = P0 + 「詞表術語維持英文」規則。

---

## 2. 結果

(跑完填)

---

## 3. 侷限

1. 音訊經公開鏡像取得(官方 YouTube 對資料中心 IP 擋下載);時長逐檔核對一致
2. 參考文本的英文拼寫仲裁依賴投影片與雙 Gemini 一致性,無人耳終審;
   函式/function 一類的系統性裁決已個別記錄證據
3. 三段語料、86 個術語實例 —— 段落層級統計力有限,CI 以術語實例 cluster 處理
4. G 的日文… 中文聽寫是 Live API 附屬輸出(inputTranscription),與 v1 同一侷限
5. cmn_en 替代 multi:結論適用於「有專用雙語 pack」的引擎;泛多語 pack 未測
6. 譯文層 P0/P1 只在 X 系列測(G 是一體式,無法拆層)
