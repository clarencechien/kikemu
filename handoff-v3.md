# Handoff v3 — 快速前處理能不能把 Gemini Live 救回 N0?

接在 exp1/exp2 之後的第三個實驗。共用同一套 harness、同一批加噪檔、同一份正解與判定規則。

---

## 1. 要回答的問題

exp1 的結論是:Gemini Live 在人聲背景(N2/N3)下崩潰,所以架構只能選 SM。
但那是**音檔直入、零前處理**的條件。本實驗問:

1. **在模型前串一個「快速前處理」(因果式、可串流、CPU 即時),Gemini Live 能回到多少?**
   有沒有機會接近它自己的 N0 水準(0.836)?
2. **同樣的前處理餵給 SM,有沒有差?** —— 這是**差異反應檢定**,直接回答
   「SM 的抗噪是不是也只是在 endpoint 後、model 前串了前處理」:
   - 前處理讓 A 大幅回血、SM 不動 → SM 的優勢是「前處理性質」的,買得到,
     架構可以改回便宜的一跳
   - 兩邊都不動 → SM 的優勢在聲學模型本身(訓練時增強),前處理買不到
   - 兩邊都回血 → 前處理是獨立加分項,兩條路線都該加
3. **降噪在乾淨音源上會不會反而傷?**(假影檢定,N0/N1 對照)

**成敗判準先寫死:**
- N2/N3 的召回率**天花板不是 N0,是 N1(0.552)**——因為 N2/N3 = 殘響 **+** 人聲,
  快速降噪只除人聲、不除殘響。若 A+前處理在 N3 回到 ≈0.55,代表降噪**完全成功**,
  剩下的差距要靠去殘響(那是另一類、通常非因果的處理,見 §3)
- 「回到 N0」的完整版問題 = 降噪 + 去殘響都要解;本實驗以快速降噪為主,
  串流去殘響列為選配

---

## 2. 文獻與已知手法(先讀這節再動手)

### 2.1 現有數據已經告訴我們的

- **殺手是加成性人聲,不是頻譜失真**:A 相對自己 N0 的保留率——
  N1 純殘響 66%、N4 帶通+削波+殘響 70%、N2 人聲15dB **23%**、N3 人聲8dB **4%**
- **A 的失敗模式是整段放棄**(輸出歸零、逐檔重現),不是漸進聽錯——
  比較像前端 VAD/音訊編碼在連續人聲背景下判定「沒有可辨識語音」。
  這對前處理反而是好消息:只要讓 VAD 重新「看見」前景語音,可能整段救回
- **SM 批次 ≈ SM 即時**(全部 CI 含 0)——SM 如果有內建前處理,
  也是因果式的,不是靠 lookahead 的批次增強。這是對「SM 只是串了前處理」
  假說的第一條(間接)反證;本實驗做直接檢定

### 2.2 快速(因果、可串流)降噪的已知手法譜系

| 類型 | 代表 | 即時性 | 對「人聲背景」的已知極限 |
|---|---|---|---|
| 統計法 | 譜減(Boll 1979)、MMSE-STSA/LSA(Ephraim-Malah 1984/85)、OMLSA+IMCRA(Cohen 2001-03) | 極快,10ms 框 | 假設噪音比語音平穩;**對 babble(頻譜跟前景語音一樣)天生弱** |
| 譜門檻 | spectral gating(noisereduce) | 快,可分塊串流 | 同上;易產生 musical noise |
| 通訊級混合 | **WebRTC APM 的 NS 模組**(每一通瀏覽器通話都在用)、RNNoise(Valin 2018,微型 GRU,<1% CPU) | 產品級即時 | 為通話品質(人耳)調校,非為 ASR;對 babble 有限 |
| 神經降噪 | DeepFilterNet 1-3(2022-23,CPU 即時)、DTLN(2020)、Demucs-denoiser(2020,因果版)、NSNet2(MS DNS baseline) | CPU 即時(有宣稱) | DNS 榜單上最強;但見 2.3 的假影問題 |
| 目標說話者抽取 | **VoiceFilter-Lite(Google 2020)** | 串流,為 ASR 設計 | 需要目標聲紋註冊;論文明確為「ASR 前端」設計,且 **SNR 門控啟用**(只在偵測到重疊語音時才動作)——Google 自己就知道全程開著會傷 |
| 去殘響 | WPE(NTT;有 online 變體)、DFN 附帶部分去殘響 | 批次為主,online 版重 | 本實驗選配;N1→N0 的 0.28 差距歸它管 |

### 2.3 已知的坑:降噪常常讓 ASR 變差(假影問題)

這是文獻裡反覆出現的結論,不能跳過:

- CHiME 系列挑戰的老觀察:單通道增強**給人聽變好、給 ASR 反而變差**——
  多條件訓練(multi-condition training)的聲學模型見過噪音、沒見過降噪器的假影,
  processing artifacts 造成 train-test mismatch
- Iwamoto et al. 2022(*How bad are artifacts?*)量化了這件事,並提出便宜的解法:
  **假影感知混合(blend-back)**——輸出 α·增強 + (1−α)·原始(α≈0.7~0.8),
  保留一點原始訊號讓聲學模型「認得出來」。本實驗把它列為正式 arm
- Google 自家的 VoiceFilter-Lite 論文用 SNR 門控解決同一問題:
  乾淨時直通、吵時才啟用——printed evidence 說明「全程串一個降噪」不是好設計

**因此假影檢定(P-* 跑在 N0/N1 上)是本實驗的必要對照,不是選配。**

### 2.4 為什麼官方不(在 API 裡)做?

其實 Google **有做,只是不在這一層**:

1. **官方的預設是「前處理屬於採集端」。** Live API 的參考架構假設音訊來自
   WebRTC / 手機 OS 的採集管線,而那條管線**本來就內建** NS+AEC+AGC
   (瀏覽器 `getUserMedia` 的 `noiseSuppression: true` 預設開啟、
   Android `NoiseSuppressor`、iOS voice-processing IO)。
   我們的實驗把 wav 直接灌進 WebSocket,**繞過了所有採集端處理**——
   這正是 kikemu「手機遠場收音」的悲觀情境,但不是 Google 假設的情境
2. **Google 在產品層有伺服器級降噪**(Meet 的 noise cancellation、Pixel Recorder),
   但選擇放在產品層而非模型 API 層——因為降噪的正確強度取決於場景,
   API 層全域開啟對乾淨音源是傷害(見 2.3)
3. **模型層的主流解法是訓練時增強**(multi-condition + SpecAugment),
   不是推論時串模組;推論時串會加延遲、加算力、加假影風險
4. Live 的 VAD 是為**對話輪替**設計的(語意端點偵測),不是為「連續旁白 + 人群」;
   這解釋了整段放棄的病徵

**所以「官方推的作法」翻譯成我們的語境就是:在 client 端(進 WebSocket 前)
把 WebRTC 級的 NS 打開。** 本實驗的 P-webrtc arm 等於是把這件事補回來,
測它夠不夠;不夠再上神經降噪。

---

## 3. 前處理 arm(全部必須是因果式、可 10~20ms 框串流、CPU RTF < 0.3)

| arm | 手法 | 定位 |
|---|---|---|
| P0 | 無(既有數據) | 基準 |
| **P-webrtc** | WebRTC APM 噪音抑制(`webrtc-noise-gain`,10ms 框) | **官方採集端等價物**,第一優先 |
| **P-sg** | 譜門檻降噪(noisereduce non-stationary,1s 分塊、滾動噪音估計模擬串流) | 傳統法代表 |
| **P-rnn** | RNNoise(裝得起來就上;裝不起來記錄缺口) | 通訊級神經法代表 |
| **P-blend** | 最佳單一手法 × α=0.75 + 原始 × 0.25 | 假影感知混合(Iwamoto 2022) |
| (選配) P-wpe | online WPE 去殘響 + 最佳降噪 | 只在降噪成功、但卡在 N1 天花板時加測 |

實作要求:
- 全部離線預產生檔案(`corpus/conditions_pp/{seg}__{cond}__{pp}.wav`),
  但演算法本身必須是因果式——**量測並記錄 RTF**,超過 0.3 就除名
- 增益正規化與 16k 單聲道規格同 degrade.py;固定種子

## 4. 實驗矩陣(控制成本的兩階段)

**階段一(便宜,SM batch 篩選):** 全部 P-* × {N1, N2, N3} × 6 段 → SM batch(+詞表)
CER/召回,挑出對 SM **無害且**訊號品質最好的前兩名。順便直接得到
「前處理對 SM 有沒有幫助」的答案(差異反應檢定的 SM 側)。

**階段二(貴,Gemini Live 即時):**
- 最佳兩個 P-* × {N2, N3} × 6 段(主問題)
- 最佳 P-* × {N0, N1} × 6 段(假影檢定)
- P-blend × N3 × 6 段(若最佳手法在 N0/N1 顯示假影傷害)

Live 即時推流約 40~60 分鐘 wall-clock。

## 5. 指標與判讀

主指標:專有名詞召回率(tolerant,沿用 exp1 的 67 專名與判定規則)。次要:CER。

**判讀表(先寫死,避免事後解釋):**

| 觀察 | 結論 |
|---|---|
| A@N3+pp ≥ 0.50(≈N1 水準) | 快速降噪把「人聲崩潰」完全解掉;剩餘差距歸殘響管 |
| A@N3+pp 在 0.2~0.5 | 部分有效;kikemu 的 SNR 切換閾值可以放寬,但架構結論不變 |
| A@N3+pp < 0.2 | 快速降噪救不了 babble;維持原結論 |
| SM+pp − SM ≈ 0 且 A+pp − A >> 0 | SM 內部「等價前處理」假說成立(它有、而且比外掛好) |
| SM+pp − SM > 0 | SM 沒把這件事做滿;**兩條路線都該加前處理**,stt-matrix 要改 |
| A@N0+pp < A@N0 − 0.05 | 假影傷害實錘;任何部署都必須 SNR 門控(學 VoiceFilter-Lite),P-blend 上場 |

## 6. 產出

```
corpus/conditions_pp/        30~54 個前處理後音檔(不入庫)
scripts/preprocess.py        全部 P-* 實作 + RTF 量測
results/raw/<arm>_pp_<pp>/   各 run 完整回應
results/report.md            新增 §「前處理實驗(exp3)」
results/stt-matrix.md        視結果更新規則 2(音源乾淨度)
```

## 7. 成本

- SM batch 篩選:~54 檔 × 1.5 分鐘音訊,額度內
- Gemini Live:~30 run × 1.5 分鐘,< $1
- 總計 < $2,主要成本仍是即時推流 wall-clock

## 8. 開工順序

1. 裝 `webrtc-noise-gain`、`noisereduce`,試裝 RNNoise;每個手法先過 RTF 與聽感 sanity check
2. preprocess.py 產生全部 P-* × {N0,N1,N2,N3} 檔案
3. 階段一:SM batch 篩選 + SM 側差異反應
4. 階段二:Gemini Live 主實驗 + 假影檢定
5. 併入報告、更新 stt-matrix、給一句話結論

第 1 步的 sanity check 不能跳過:降噪器在 babble 上「聽起來有效」與否,
30 秒抽查就能省掉整輪白跑。
