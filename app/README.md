# kikemu app — 部署與開發手冊

聽外語導覽、出台灣正體字幕的 PWA。產品規格見 [`../docs/PRD.md`](../docs/PRD.md);
每一條技術路線都是本 repo 四個實驗的結論(`../results/report.md`),不是偏好。

## 架構

```
瀏覽器(PWA,單頁 index.html)
  mic → AudioWorklet(public/pcm-worklet.js,16kHz PCM16 100ms 框)
      → WS /ws?lang=ja&pack=<id> ────────┐
                                          ▼
Cloudflare Worker(worker/index.ts)
  ├─ 靜態資產(Workers Assets ./dist,run_worker_first:安全 headers 全覆蓋)
  ├─ Google OIDC 全 server-side(worker/auth.ts,kk_session HMAC cookie)
  ├─ R2 CONFIG bucket:config/allowlist.json・config/waitlist.json・vocab/{id}.json
  ├─ QUOTA DO(worker/quota.ts):每人每日聽譯秒數,UTC 00:00 = 台灣 08:00 重置
  └─ RELAY DO(worker/relay.ts,per-email)
       ├─ 上游:Speechmatics RT WS(enhanced/partials/max_delay 2.0,
       │        語言取自 worker/langs.ts;金鑰只存在 DO)
       │        additional_vocab = R2 vocab/{pack}.json,**語言相符才掛**
       ├─ 下行:{partial|final} + 每秒 {stat}(伺服器實收 RMS)
       └─ 定稿句 → Gemini generateContent(worker/gemini.ts,
                   凍結口譯 systemInstruction = scripts/prompts.py)→ {zh}
```

**語言是 `worker/langs.ts` 一處定義**(日本語 / 한국어 / English / 中文・English 夾雜),
前端下拉、`/api/config`、relay 的 Speechmatics 設定、場景包驗證字集都讀同一份。
語言與詞表在 session 開始時固定,中途要換 = 重連(Speechmatics 協定限制)。

`cmn_en` 雙語 pack **完全不輸出標點**,所以 relay 除了句末標點,另有長度(48 字)
與停滯(6 秒)兩個 flush 條件,否則整場會累積成一句才吐出來。

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

3. **種子場景包**(exp1 語料的東大阪詞表,80 詞條;探針複驗時要用):

   ```sh
   npm run seed:pack
   # = wrangler r2 object put kikemu-config/vocab/higashiosaka.json \
   #     --file seed/higashiosaka.json --remote --content-type application/json
   ```

   之後的場景包在 `/admin` 用**關鍵字**生成,見下節。

4. **部署**

   ```sh
   npm run deploy
   ```

   綁自訂網域後把 `wrangler.jsonc` 的 `CANONICAL_HOST` 填上再部署一次——**只填 hostname**(`kikemu.ai-apps.work`),不要含 `https://` 或尾斜線(程式會自動剝,但別依賴它)
   (`workers_dev`/`preview_urls` 已在設定碼層級關死)。

5. **名單**:首次登入的訪客自動進等候名單,`/admin` 一鍵核准(級別
   trial 15 分/beta 60 分/pro 180 分,或自訂每日秒數)。

## 場景包:在 `/admin` 用關鍵字生成

填**包 id**(小寫英數)、**中文別名**(使用者介面顯示用)、**語言**(日文 / 韓文)、
**關鍵字**(例:大阪城),按搜尋:

| 階段 | 端點 | 做的事 | 時間 |
|---|---|---|---|
| 預覽 | `POST /api/admin/pack-search` | pass A `google_search` 接地蒐集固有名詞與讀音 → pass B 結構化成 JSON → 依語言驗證讀音字集 | 約 60~100 秒 |
| 存檔 | `POST /api/admin/pack-save` | 直接收預覽過的詞條寫 R2,**不重跑搜尋** | ~50ms |

為什麼要先預覽:讀音錯的詞表會反過來傷辨識,所以要先看過詞條數、
**引用來源筆數**(0 筆 = 模型憑記憶答,沒有外部佐證)與警告再存。
存檔會把關鍵字、搜尋詞、來源一併寫進包裡(`source` 欄)可追溯。

兩個實測補丁寫死在程式裡:prompt 把主題本身釘在最前面、且要求漢字與假名/諺文
各列一條;程式再補一層——關鍵字本身若不在結果裡就強制插入(實測漏過「枚岡神社」,
導致整包對自己的主題沒有詞條)。

讀音字集依語言驗證(日文全形假名、韓文諺文,皆已探針確認 Speechmatics 接受),
不合格的讀音剔除、>6 字只警告不阻擋。貼來源文字的舊路徑
(`pack-generate`)仍在,適合有官方頁內文的時候。

### 實測:正式站第一包「大阪城」

| 指標 | 值 |
|---|---|
| 詞條 | 149(無重複) |
| 有讀音 | 142 / 149 |
| 搜尋詞 / 引用來源 | 10 / 10(osakacastlepark.jp 官方、bunka.go.jp 文化廳、osaka-info.jp…) |
| 讀音 >6 字(僅警告) | 78 |
| `ready` 回報 | 場景包:大阪城 / 149 詞 |

沒有讀音的 7 條裡,`大阪城`是**程式強制插入**的關鍵字本身(設計如此);
其餘 6 條是人名與碑名,模型自己略過。

**驗證擋不掉的兩種壞詞條**(這包裡各中一個,所以要先預覽):

| 壞法 | 實例 | 為什麼過得了驗證 |
|---|---|---|
| `content` 被模型「翻譯」 | `黄金 of 茶室`(應為 黄金の茶室) | 只驗 `sounds_like` 字集,不驗 `content` |
| 讀音混片假名 | `虎石` → `トらいし`(應為 とらいし) | `KANA_RE` 平/片假名都收,混寫也合法 |

要查一包的實際內容(API 不外露 `sounds_like` 與 `source` 生成軌跡):

```sh
npx wrangler r2 object get kikemu-config/vocab/<id>.json --remote --pipe | jq .
```

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
  --wav ../corpus/conditions/hig01_A1__N0.wav --lang ja --pack higashiosaka
```

`--lang` 預設 `ja`;`--pack` 的語言要與 `--lang` 相符,不符時 relay 會忽略詞表
(這是刻意的:別把假名詞條餵給韓文模型)。

| 探針結果 | 結論 | 下一步 |
|---|---|---|
| 出得來日文定稿 | relay + Speechmatics + 詞表都正常 | 問題在瀏覽器送出的音訊,或現場講的不是日文 |
| 有 partial 無定稿 | 音訊有進去,句子沒收斂 | 看 `max_delay` 與現場是否一直有背景聲 |
| 完全沒有字 | 伺服器這側壞掉 | 查 `SPEECHMATICS_API_KEY`、額度、探針列出的 ERROR |

App 內另有兩個對照數字:狀態列的**本地音量條**(worklet 算的 RMS)與 relay 每秒
回報的**伺服器實收 RMS**。本地會動、伺服器接近零 = 音訊在傳輸中損壞;
兩邊都有值卻沒有字 = 真的是辨識問題(對照 exp1:8dB 人聲下 SM 仍有 0.627,
所以「完全零字」通常不是噪音,要先懷疑語言設定)。
