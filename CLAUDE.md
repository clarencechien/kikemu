# kikemu — 給接手的人 / agent

聽外語導覽、出台灣正體字幕的 PWA(`app/`)+ 五個引擎選型實驗與一份 oracle 分析
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

7. **「上限 +X」的數字要問「選錯時的下限是多少」。**
   oracle / 天花板類分析逐項取 OR,假設你事後知道誰對——真實系統沒有那個信號,
   選錯時會**比最佳單一方案更差**。它是篩選工具,不是收益估計。
   細節見 `results/oracle_report.md` §9。

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
| `scripts/` | exp1/3/4 的實驗腳本 |
| `exp2/` | exp2(中英夾雜) |
| `results/report.md` | 五個實驗合併報告 + 21 條侷限 |
| `results/stt-matrix.md` | 跨專案 STT 選型決策矩陣(照情境查該用什麼) |
| `results/oracle_report.md` | oracle 天花板:融合/GER 值不值得做(結論:不做) |
| `analysis/` | oracle 與互補性的純計算腳本,不呼叫任何 API |
| `docs/PRD.md` | 產品規格(畫面、安全基線、配額與成本) |

## 別做的事

- 不要把音訊或字幕存到伺服器(PRD §2:內容零留存,R2 只有名單與詞表)。
- 不要在 `getUserMedia` 打開瀏覽器降噪(exp3 實證那條鏈是負資產)。
- 不要用未量測的說法覆蓋量測過的結論——不確定就標「未驗證」,別讓它讀起來像數據。
