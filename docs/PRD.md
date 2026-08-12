# kikemu きけむ — PRD

**聞く(きく,聽)+ む。** 打開 kikemu,它替你聽日文導覽,台灣正體字幕即時浮現——
神社的神名、茶室的典故,一個都不漏。

與 [manemu](https://manemu.ai-apps.work)(紅,說)、sukemu(青,看)同一命名與產品家族:
**kikemu 是琥珀(聽)**。

---

## 1. 為什麼是這個架構(實驗的產物,不是偏好)

本 repo 的四個實驗(見 `results/report.md`)直接決定了 app 的每一條技術路線:

| 決策 | 依據(實驗) |
|---|---|
| 聽 = **Speechmatics 即時**,不用一體式 | exp1:人聲背景 8dB 下 Gemini Live 崩潰(0.030),SM+詞表 0.627;差距隨噪音單調放大 |
| **地點詞表是核心資產**,隨 session 載入 | exp1:C+−C 全條件 +0.12~0.19、CI 排除 0;成本 $0.035/景點、延遲零代價 |
| 譯 = **Gemini 3.5 Flash**,共用口譯 systemInstruction | exp1:台灣用語 0 失誤、adequacy 4.71;prompt 已解決在地化與不譯規則(exp2 P1 零增量) |
| **不做**前端降噪 | exp3:外掛 DSP 對兩引擎都是零到負,WebRTC NS 還傷乾淨音源 |
| 拾音指引進 UI(貼近音源提示) | exp4:指向性紅利歸 SM(+0.19),領夾麥/近講讓 SM 貼到天花板 |
| 詞表中途不換,換地點 = 重連 | Speechmatics 限制:additional_vocab 隨 session config 送出 |

## 2. 使用者故事(MVP 範圍)

1. 遊客在枚岡神社打開 kikemu(PWA,已登入),選「東大阪・枚岡神社」場景包,按「開始聽」
2. 導覽員開講。**0.4~0.8 秒**內日文暫定字浮現(灰),**~2.3 秒**定稿;
   定稿句立刻出現琥珀色台灣正體譯文
3. 「アメノコヤネノミコト」「注連縄掛神事」這類詞被場景包接住,不會變成亂碼
4. 結束後,整場字幕留在**手機本地**(IndexedDB),可回看、可**逐場匯出**
   (TXT 對照 / CSV 兩欄,皆含原文與譯文)、可清除;伺服器不留任何內容——
   正因為沒有雲端副本,匯出是使用者唯一的留存管道

非目標(MVP 不做):語音播放(TTS)、離線模型、多人共享 session、
自動 GPS 選包、iOS 以外瀏覽器的極限相容。

## 3. 畫面規格

沿用家族單頁結構:一份 `index.html` 含登入畫面與 app 畫面(`body[data-screen]` 切換),
`/admin` 獨立靜態頁。無 client router。

### 3.1 登入畫面

- 與 manemu/sukemu 同版式:名稱、一句話、隱私三行(原音不落地・字幕只存你的裝置・伺服器零內容)、
  Google 登入鈕(未設 OIDC 時退回開發用 Email 直登)、Turnstile(成對設定才啟用)
- **「看看介面 →」登入前預覽**:播一段錄好的 demo 字幕流(來自評測語料的真實輸出),
  結構上碰不到引擎(獨立函式 + 伺服器端 401,manemu 同款兩層)

### 3.2 主畫面(聽譯)

```
┌──────────────────────────────┐
│ kikemu v1   [場景包▾] [🕘] [⚙] │  ← 頂列:版號、場景包選擇、歷史、管理(admin only)
├──────────────────────────────┤
│                              │
│   （字幕流,由下往上捲動)      │  ← 每句一張卡:
│   ┌────────────────────────┐ │     ja 原文(ink,partial 時 ghost 色 + 底線游標)
│   │ 枚岡神社の主祭神は…      │ │     zh 譯文(--brand 琥珀,定稿後浮現)
│   │ 枚岡神社的主祭神是…      │ │
│   └────────────────────────┘ │
├──────────────────────────────┤
│  ● 開始聽 / ■ 停止    ⚡1.2s  │  ← 底列:大按鈕 + 延遲 chip + 今日剩餘分鐘
└──────────────────────────────┘
```

- **場景包預設不選**(「(不用場景包)」)。詞表只在「講的就是這個地點」時有價值
  (exp1:+0.12~0.19),自動掛上清單第一個包等於替使用者猜地點,猜錯就是把一堆
  不相干的專名推給引擎。換語言時一併重載清單並回到不選。
- **開始/停止是 toggle,不是 PTT**——導覽是連續旁白(與 manemu 的按住說話相反)
- partial 以灰字即時改寫(實測改寫率 16%,視覺上用 opacity 過渡不閃爍)
- 定稿句觸發翻譯;譯文逐句浮現在原文下方,**琥珀色只給譯文**(sukemu 鐵律的 kikemu 版)
- 拾音指引:開始聽時 toast 一次「離音源越近越清楚——貼近導覽員或喇叭」(exp4 結論的 UI 化)
- 斷線/配額用盡:卡片內明講原因(manemu 的「不假裝成功」原則)
- **歷史逐場匯出**:每筆紀錄有 TXT / CSV 兩顆鈕。TXT 是「原文 / 譯文」對照式,
  含時間、語言、場景包、句數的檔頭;CSV 是兩欄式(BOM + CRLF,Excel 不會亂碼),
  給要進試算表校對的人。譯文缺漏的句子標「(未翻出)」,不靜默略過。

### 3.3 `/admin`(僅 `ADMIN_EMAILS`)

沿用家族三段式 + kikemu 專屬第四段:

1. **等候名單**:一鍵核准(選級別)/ 忽略
2. **已核准**:級別、自訂每日秒數、今日用量(分鐘 + 估算 NT$)、移除(admin 帳號不可移除)
3. **診斷**:clientlog 最近 10 筆(預設關閉,cron 30 天清除)
4. **場景包管理**(kikemu 專屬):
   - 列表:名稱、詞條數、更新時間
   - 新增/更新:貼上來源文字或 URL(如維基百科條目)→ Gemini 抽取詞條 + 假名讀音
     → **格式驗證**(sounds_like 全形假名;>6 字標警告不阻擋,exp1 記載的不確定性)→ 存 R2
   - 這就是實驗裡 `make_dict.py` 的產品化,含 prompt 與原始回應留檔

## 4. 設計系統

**手寫 CSS、無框架**,tokens 一份(`src/styles/tokens.css`),
版式語彙承襲家族:2px 圓角、data-attribute 驅動狀態、`safe-area-inset`、
`prefers-reduced-motion` 全動畫防護。

```css
:root{
  --paper:#F7F5F1; --ink:#22201B; --ink-2:#6B675E; --line:#E2DED6;
  --brand:#B8860B;      /* 琥珀:品牌與「譯文專用」——不給按鈕、不給裝飾 */
  --brand-soft:#F5EDDC;
  --ghost:#A39E93;      /* partial 暫定字 */
  --ok:#2E7D4F; --warn:#9A6B00; --chip:#EFECE5;
}
@media (prefers-color-scheme: dark){ :root{
  --paper:#17150F; --ink:#EAE6DD; --ink-2:#98938A; --line:#2E2B23;
  --brand:#E8B84B; --brand-soft:#332B14; --ghost:#5F5B52;
  --ok:#63B588; --warn:#D9A93F; --chip:#242118;
}}
```

- 亮暗雙模(manemu 式 media-query,無手動切換);字幕閱讀場景以紙色為底
- 字體:`--doc` Noto Sans TC/JP(字幕本體)、`--chrome` IBM Plex Sans Condensed(UI 標籤)、
  `--data` IBM Plex Mono(延遲、秒數,tabular-nums)
- **`--brand` 琥珀只出現在譯文與品牌標**,原文永遠是 `--ink`——
  一眼分得出「導覽員說的」與「kikemu 給的」

## 5. 音訊管線與 relay 協定

```
mic → AudioWorklet(16kHz PCM16, 100ms frames)   ← 沿用 manemu pcm-worklet.js 模式
    → WS /ws?pack=hiraoka → Worker Durable Object(SessionRelay,per-email)
        → Speechmatics RT WS(ja, enhanced, partials, max_delay 2.0,
                              additional_vocab = R2 vocab/{pack}.json)
        ← AddPartialTranscript / AddTranscript
    ← {type:"partial"|"final", t, text}
    定稿句 → DO 內呼叫 Gemini generateContent(凍結口譯 systemInstruction)
    ← {type:"zh", forT, text}
```

- `getUserMedia({echoCancellation:false, noiseSuppression:false, autoGainControl:false})`
  ——exp3 實證瀏覽器降噪鏈是負資產;iOS 實際行為要真機驗證(已知風險,見 §8)
- SM 金鑰**只存在 DO**;瀏覽器永遠只連 `/ws`(manemu 同款,Speechmatics 看到的是 CF 出口)
- 配額:DO 計每日**聽譯秒數**,連線即計、SM Error 不計;斷線自動停錶
- 熔斷:單場 hard cap 60 分鐘;WS 靜默 30 秒自動收斂;SM 4xx 直接回報不重試
- 翻譯以**定稿句**為單位增量呼叫(非整場重譯);失敗句顯示「譯文暫缺・點擊重試」

## 6. 安全基線(sukemu `cf-security-baseline` 全套)

- Google OIDC 全 server-side(JWKS 驗章、nonce、`email_verified`);
  **fail-closed**:OIDC 已設但 SESSION_SECRET 缺/是 dev 值 → 全站鎖死而非收偽造 session
- HMAC 簽章 session cookie(`kk_session`,HttpOnly/Secure/SameSite=Lax,7 天)
- 白名單 R2 `config/allowlist.json` 每請求重讀、改檔即生效;未列入自動進等候名單
- Turnstile 成對才啟用(單邊設定 = 自動停用 + log 警告)
- CSP 無 `unsafe-inline` script、`frame-ancestors 'none'`、nosniff、
  `workers_dev:false`、`preview_urls:false`、CANONICAL_HOST 301/403
- 所有 `/api/*` 與 `/ws` 同源檢查;body 上限(場景包來源文字 ≤64KB)
- **內容零留存**:R2 只有 config 與(選用)診斷 breadcrumbs;
  音訊不落地、字幕只在使用者 IndexedDB
- Secrets:`SPEECHMATICS_API_KEY`、`GEMINI_API_KEY`、`GOOGLE_CLIENT_SECRET`、
  `SESSION_SECRET`、`TURNSTILE_SECRET`——只走 `wrangler secret put`

## 7. 配額與成本

實測單位經濟(報告 §5):SM $0.24/hr + Gemini 譯 ~$0.23/hr ≈ **$0.47/hr ≈ NT$0.25/分**。

`QUOTA_TIERS = {"admin":0,"pro":10800,"beta":3600,"trial":900}`(每日秒數,台灣 08:00 重置)
——trial 15 分鐘足夠一場短導覽試用;admin 頁以 NT$0.25/分 估算成本。

## 8. 已知風險(先寫下來,不是實作後才發現)

1. **iOS 長時間連續收音**:manemu 已踩過 AudioContext 被回收的全套坑(fuses 模式可搬),
   但 kikemu 是連續 60 分鐘而非 PTT 短句——需要真機驗證,預期要背景保活策略
2. **關閉瀏覽器降噪在 iOS 的實際效果**:constraint 支援不完整,可能拿到處理過的音訊
   ——上線前做一次 A/B(exp3 的方法直接複用)
3. Speechmatics `multi`/雙語 pack 在日文場景不適用,但未來擴語言時沿 exp2 結論選 pack
4. 免費額度計費制剛改(2026-08-01 credit 制)——上量前確認餘額與費率(見對話紀錄)

## 9. 里程碑

- **M1 walking skeleton**:登入 + 相對 relay(SM 直通、無詞表)+ 單頁字幕流 → 真機聽一段
- **M2 產品化**:場景包(admin 生成 + session 載入)、翻譯 hop、配額、歷史
- **M3 上線**:Turnstile、canonical host、PWA 安裝、demo 預覽、拾音指引、成本儀表

## 10. Repo 佈局

```
app/                       ← 產品本體(本 PRD 的實作)
  index.html  public/{admin.html,manifest.json,sw.js,pcm-worklet.js}
  src/{main.ts,api.ts,db.ts,types.ts,ui/*,styles/*}
  worker/{index.ts,auth.ts,relay.ts,gemini.ts,vocab.ts,quota.ts,admin.ts}
  wrangler.jsonc  package.json  vite.config.ts
corpus/ scripts/ results/ exp2/   ← 評測(既有,架構決策的證據)
docs/PRD.md                ← 本文件
```
