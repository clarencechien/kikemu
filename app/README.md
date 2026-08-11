# kikemu app — 部署與開發手冊

聽日文導覽、出台灣正體字幕的 PWA。產品規格見 [`../docs/PRD.md`](../docs/PRD.md);
每一條技術路線都是本 repo 四個實驗的結論(`../results/report.md`),不是偏好。

## 架構

```
瀏覽器(PWA,單頁 index.html)
  mic → AudioWorklet(public/pcm-worklet.js,16kHz PCM16 100ms 框)
      → WS /ws?pack=hiraoka ─────────────┐
                                          ▼
Cloudflare Worker(worker/index.ts)
  ├─ 靜態資產(Workers Assets ./dist,run_worker_first:安全 headers 全覆蓋)
  ├─ Google OIDC 全 server-side(worker/auth.ts,kk_session HMAC cookie)
  ├─ R2 CONFIG bucket:config/allowlist.json・config/waitlist.json・vocab/{id}.json
  ├─ QUOTA DO(worker/quota.ts):每人每日聽譯秒數,UTC 00:00 = 台灣 08:00 重置
  └─ RELAY DO(worker/relay.ts,per-email)
       ├─ 上游:Speechmatics RT WS(ja/enhanced/partials/max_delay 2.0,
       │        additional_vocab = R2 vocab/{pack}.json;金鑰只存在 DO)
       ├─ 下行:{partial|final} → 瀏覽器字幕流
       └─ 定稿句 → Gemini generateContent(worker/gemini.ts,
                   凍結口譯 systemInstruction = scripts/prompts.py)→ {zh}
```

內容零留存:音訊不落地、字幕只在使用者裝置的 IndexedDB;R2 只有名單與詞表。

## 本機開發

```sh
npm install
npm run dev:worker   # wrangler dev(:8787,API/WS/DO)
npm run dev          # vite dev(:5173,/api 與 /ws 轉給 8787)
```

未設 `GOOGLE_CLIENT_ID` 時登入頁自動退回「開發用 Email 直登」(比對 R2 白名單;
bucket 不存在時預設放行 `clarence.chien@gmail.com`)。本機 secrets 放 `.dev.vars`
(已在 .gitignore):

```
SPEECHMATICS_API_KEY=...
GEMINI_API_KEY=...
```

型別與建置檢查:`npm run build`(= `tsc --noEmit` ×2 + `vite build`)。

## 一次性部署(runbook)

1. **R2 bucket**

   ```sh
   npx wrangler r2 bucket create kikemu-config
   ```

2. **Secrets**(逐一 `npx wrangler secret put <NAME>`;絕不進 repo):

   | Secret | 用途 |
   |---|---|
   | `SPEECHMATICS_API_KEY` | 聽(RT WS;只存在 RELAY DO) |
   | `GEMINI_API_KEY` | 譯 + 場景包詞條抽取 |
   | `GOOGLE_CLIENT_ID` | OIDC(設了即停用 Email 直登) |
   | `GOOGLE_CLIENT_SECRET` | OIDC token 交換 |
   | `SESSION_SECRET` | session HMAC。**fail-closed**:已設 OIDC 但缺它 → 全站鎖死 |
   | `TURNSTILE_SECRET` | 與 vars 的 `TURNSTILE_SITE_KEY` **成對**設定才啟用 |

3. **種子場景包**(exp1 語料的東大阪詞表,80 詞條):

   ```sh
   npm run seed:pack
   # = wrangler r2 object put kikemu-config/vocab/higashiosaka.json \
   #     --file seed/higashiosaka.json --remote --content-type application/json
   ```

   之後的場景包直接在 `/admin` 生成(貼來源文字 → Gemini 抽詞條 → 假名驗證 → R2)。

4. **部署**

   ```sh
   npm run deploy
   ```

   綁自訂網域後把 `wrangler.jsonc` 的 `CANONICAL_HOST` 填上再部署一次——**只填 hostname**(`kikemu.ai-apps.work`),不要含 `https://` 或尾斜線(程式會自動剝,但別依賴它)
   (`workers_dev`/`preview_urls` 已在設定碼層級關死)。

5. **名單**:首次登入的訪客自動進等候名單,`/admin` 一鍵核准(級別
   trial 15 分/beta 60 分/pro 180 分,或自訂每日秒數)。

## 已知風險(PRD §8,上線前必驗)

- **iOS 長時間連續收音**:kikemu 是連續 60 分鐘而非 PTT 短句,AudioContext
  可能被系統回收(manemu 踩過全套坑)——需要真機驗證與背景保活策略。
- **iOS 關閉瀏覽器降噪的實際效果**:`echoCancellation/noiseSuppression/autoGainControl:false`
  的 constraint 支援不完整,可能拿到處理過的音訊;上線前用 exp3 方法做一次 A/B。

## 診斷:出不來字的時候怎麼查

畫面上「有收到音、卻一個字都不出來」有三種可能,`scripts/probe-ws.mjs`
用 exp1 實測拿到 0.836 專名召回率的**已知良品音檔**繞過瀏覽器直接灌進 `/ws`,
一次分離出是哪一種:

```bash
cd app
node scripts/probe-ws.mjs --host https://kikemu.ai-apps.work \
  --cookie "kk_session=…"  `# 從 DevTools 複製;開發登入可改用 --email you@example.com` \
  --wav ../corpus/conditions/hig01_A1__N0.wav --pack higashiosaka
```

| 探針結果 | 結論 | 下一步 |
|---|---|---|
| 出得來日文定稿 | relay + Speechmatics + 詞表都正常 | 問題在瀏覽器送出的音訊,或現場講的不是日文 |
| 有 partial 無定稿 | 音訊有進去,句子沒收斂 | 看 `max_delay` 與現場是否一直有背景聲 |
| 完全沒有字 | 伺服器這側壞掉 | 查 `SPEECHMATICS_API_KEY`、額度、探針列出的 ERROR |

App 內另有兩個對照數字:狀態列的**本地音量條**(worklet 算的 RMS)與 relay 每秒
回報的**伺服器實收 RMS**。本地會動、伺服器接近零 = 音訊在傳輸中損壞;
兩邊都有值卻沒有字 = 真的是辨識問題(對照 exp1:8dB 人聲下 SM 仍有 0.627,
所以「完全零字」通常不是噪音,要先懷疑語言設定)。
