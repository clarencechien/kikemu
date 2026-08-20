# kikemu — 給接手的人 / agent

聽外語導覽、出台灣正體字幕的 PWA(`app/`)+ 五個引擎選型實驗、一份 oracle 分析,
以及在 Modal 上補測的地端線(Breeze 日文與噪音曲線、Gemma 4 三個型號、GPU 成本)
(`scripts/`、`exp2/`、`exp5/`、`analysis/`、`results/`)。
先讀 [`README.md`](README.md) 的現況表,再看這頁的規矩。

## 這個 repo 的鐵律

1. **設定的唯一事實來源是 git,不是 dashboard。**
   `app/wrangler.jsonc` 的 `vars` 在部署時會**蓋掉** Cloudflare dashboard 上的明文變數
   (只有 `wrangler secret put` 的 Secret 倖存)。改設定改檔案,不要改 dashboard。

2. **語言清單只有一處:`app/worker/langs.ts`。**
   前端下拉、`/api/config`、relay 的 Speechmatics 設定、詞表驗證字集全都讀它。
   加語言只改那一個檔案。

3. **口譯 prompt 是凍結的。**
   `app/worker/gemini.ts` 的 `INTERPRETER_SYSTEM` 與 `scripts/prompts.py` **逐字相同**。
   exp1 量到的 adequacy 4.71 / 台灣用語 0 失誤是在那份字串上量的——改了字串,
   報告裡的數字就不再適用。多語言版只換來源語名稱,`ja` 走原字串一個字不動。

4. **機械性任務一律 `thinkingLevel: "minimal"`。**
   thinking token 以輸出價計費,實測逐句翻譯 thoughts/output **29×**。
   細節與 A/B 數據見 [`docs/gemini-api-lessons.md`](docs/gemini-api-lessons.md)。
   `thinkingBudget: 128` **不等於關閉**(實測仍 12~18×),不要用;
   level 與 budget **永不同時給**(400)。

5. **會花錢的東西要有保險絲,而且計量單位要對齊計費單位。**
   使用者配額是「秒」,Gemini 是「token」——秒數擋不住 token 花費。
   新增付費呼叫時,同時想:重試上限、每場上限、記帳、可視。

6. **數字要指得出出處。** README、PRD、report 裡的每個數字都應該對得上
   `results/` 裡的原始檔。抄外部牌價一律附**查價日期**與**模型級別**
   (kikemu 踩過:把 3.5-flash 抄成 flash-lite 價,低估 3.6 倍還擴散到三份文件;
   也踩過:exp5 的術語實例數從凍結前的舊詞表抄成 128,實際是 158,擴散到四份文件)。

7. **評估沒跑過的模型,先跑它官方樣本當基準線。**
   「會聽」不等於「聽得懂你的語料」——Gemma 4 12B 在模型卡的英文樣本上完美,
   在自發口語台灣華語上 CER 2.231(同家族 E4B 在同一批片段是 0.312,見鐵律 9)。而且模型卡的「限制」章節要讀完再動手
   (kikemu 違反過三條:30 秒音訊上限、audio 要放 text 之後、不要改官方 prompt),
   為此白燒 $1.26。細節見 `results/report.md` §3F 與 `notebooks/README.md`。

8. **「上限 +X」的數字要問「選錯時的下限是多少」。**
   oracle / 天花板類分析逐項取 OR,假設你事後知道誰對——真實系統沒有那個信號,
   選錯時會**比最佳單一方案更差**。它是篩選工具,不是收益估計。
   細節見 `results/oracle_report.md` §9。

9. **模型的結論要寫上「哪個型號、哪種語言」。寫寬了就是外推,而且會擴散。**
   kikemu 犯過兩次,兩次都寫進了三份以上的文件才被實測推翻:
   - 「Breeze 是地端推薦」→ 它是**中文**微調,日文只有 0.415(對 SM+詞表 0.791)。
   - 「Gemma 4 不能聽」→ 三個型號測完才看清楚:**12B 吐太多**(退化生成,
     輸出 2.2 倍長)、**E2B 不吐**(貪婪解碼下純中文 8/8 空輸出)、
     **E4B 剛好**(exp5 0.684、純中文 CER 0.312,中文可用)。
     **有甜蜜點,不是單調曲線;參數量往大往小都不是能力的代理指標。**

   要寫成家族級或通用結論,就要有跨型號、跨語言的實測撐;沒有就標明範圍。
   細節見 `results/report.md` §2.1c、§3F.2g 與 `results/stt-matrix.md` §6。

## 改完怎麼驗

```bash
cd app && npm run build          # tsc --noEmit ×2 + vite build
node scripts/probe-ws.mjs --host https://kikemu.ai-apps.work \
  --email you@example.com --wav ../corpus/conditions/sakai06__N0.wav --lang ja
```

評測語料就是**迴歸測試基準**(exp1 在這批音檔上拿 0.836)。探針已經抓到過四個真 bug,
都是「畫面上看起來只是沒反應」的類型——沒有已知良品音檔會很難查。詳見
[`app/README.md`](app/README.md) 的診斷章節。

**部署了卻沒生效 → 先想 Service Worker**:只有 `/assets/`、`/icons/` 是快取優先,
其餘同源檔案網路優先;真要清就把 `app/public/sw.js` 的 `CACHE` 版本號 +1。

## 目錄速查

| 路徑 | 是什麼 |
|---|---|
| `app/worker/` | relay DO(Speechmatics)、quota DO、admin、場景包、Gemini |
| `app/src/` | 單頁 UI(vanilla TS + Vite) |
| `app/scripts/` | `probe-ws.mjs`(端到端探針)、`make-icons.mjs` |
| `scripts/` | exp1/3/4 的實驗腳本;`run_*_modal.py` = Modal 上的 GPU arm |
| `exp2/` | exp2(中英夾雜) |
| `exp5/scripts/` | exp5 腳本;`noise_curve.py`、`score_zh.py`、`debug_e2b_empty.py` |
| `handoff-v8.md` | Modal 補測任務書:判讀規則先寫死 + 驗收 + 偏離紀錄(已全數執行) |
| `results/report.md` | 五個實驗合併報告 + 43 條侷限(8 條已解除,保留刪節線) |
| `results/stt-matrix.md` | 跨專案 STT 選型決策矩陣(照情境查該用什麼) |
| `results/oracle_report.md` | oracle 天花板:融合/GER 值不值得做(結論:不做) |
| `analysis/` | oracle 與互補性的純計算腳本,不呼叫任何 API |
| `docs/PRD.md` | 產品規格(畫面、安全基線、配額與成本) |
| `docs/related-work.md` | 同類專案掃描(2026-08)+ 「即時場景怎麼改」;文末有與 `results/` 的對帳表 |
| `docs/offline-meeting-architecture.md` | 案外案:離線會議記錄架構(非即時/多講者/中文)。文末有對帳表,改了六處 |

## 別做的事

- 不要把音訊或字幕存到伺服器(PRD §2:內容零留存,R2 只有名單與詞表)。
- 不要在 `getUserMedia` 打開瀏覽器降噪(exp3 實證那條鏈是負資產)。
- 不要用未量測的說法覆蓋量測過的結論——不確定就標「未驗證」,別讓它讀起來像數據。
- 不要刪掉已解除的侷限,改成刪節線並註明是哪次實驗解除的——結論怎麼演進要看得出來。
- 不要用 `pkill -f` 或比對 `/proc/*/cmdline` 去殺行程而不排除自己與父行程——
  包住指令的 wrapper 也會match,結果把自己殺掉(exit 144)。這個坑一天踩了三次。
- 不要把**施測紀錄**讀成**能力宣稱**。`_meta.json` 寫 `"diarize": false` 的意思是
  「這次沒開」,不是「它沒有」——kikemu 就這樣把「Scribe 沒有 diarization」寫進
  四份文件,還變成決策樹的一個分岔(而那個欄位根本沒送進 request)。
  講產品能力就去查 spec 並留下出處,例:`scripts/check_diarization_support.py`
  → `results/diarization_support.json`。**「我們沒測」「我們沒開」「它沒有」是三件事。**
- 自架開源模型前先看 `generation_config.json`:Gemma 4 三個型號都預設
  `do_sample: true, temperature: 1.0`,不指定就是隨機取樣,同一個檔重跑會不一樣。
