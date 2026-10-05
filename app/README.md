# kikemu app — 部署與開發手冊

聽外語導覽、出台灣正體字幕的 PWA。產品規格見 [`../docs/PRD.md`](../docs/PRD.md);
每一條技術路線都是本 repo 五個實驗的結論(`../results/report.md`),不是偏好。

## 架構

```
瀏覽器(PWA,單頁 index.html)
  mic → AudioWorklet(public/pcm-worklet.js,16kHz PCM16 100ms 框)
      → WS /ws?lang=ja&pack=<id> ────────┐
                                          ▼
Cloudflare Worker(worker/index.ts)
  ├─ 靜態資產(Workers Assets ./dist)
  ├─ 安全 headers:在 fetch() 出口統一套,涵蓋所有回應
  │   (2026-09-04 修正——先前只包 ASSETS.fetch(),/api/* 的 JSON、
  │    /auth/* 的 302 與 canonical 的 301 一條標頭都沒有)
  ├─ Google OIDC 全 server-side(worker/auth.ts,kk_session HMAC cookie)
  ├─ R2 CONFIG bucket:config/allowlist.json・config/waitlist.json・vocab/{id}.json
  ├─ QUOTA DO(worker/quota.ts):每人每日聽譯秒數,UTC 00:00 = 台灣 08:00 重置
  └─ RELAY DO(worker/relay.ts,per-email)
       ├─ 上游(worker/upstream.ts,依模式選):
       │    導覽 → Speechmatics RT WS(enhanced/partials/max_delay 2.0,
       │           語言取自 worker/langs.ts;金鑰只存在 DO)
       │           additional_vocab = R2 vocab/{pack}.json,**語言相符才掛**
       │    對話 → 同上 + diarization: speaker(換人就斷句)
       │    Gemini 對照(僅 admin)→ gemini-3.5-transcribe-live
       │           (customVocabulary = 同一包的 content,無讀音)
       ├─ 下行:{partial|final(+speaker)} + 每秒 {stat}(伺服器實收 RMS)
       └─ 定稿句 → Gemini generateContent(worker/gemini.ts,
                   凍結口譯 systemInstruction = scripts/prompts.py)→ {zh}
                   ——三個模式都走同一個譯,只有耳朵不同
  GEMINI_PROXY DO(worker/gemini.ts GeminiProxy,釘在 GEMINI_PROXY_REGION):
       被 Google 以區域理由拒絕時代打——HTTP 的 generateContent 與 Live 的 WebSocket 都代
```

**模式是 `worker/modes.ts` 一處定義**,`/api/config` 送前端。一個選單三個模式,
不是「單人/多人」×「SM/Gemini」兩個開關:Gemini Transcribe Live 即時**不支援語者分離**
(官方文件),那個組合不存在。

| 模式 | 聽 | 斷句 | 誰看得到 | 量測狀態 |
|---|---|---|---|---|
| **導覽**(預設) | SM 單人 | 共用斷句(見下) | 所有人 | exp1 主線(SM 設定與 2026-10 之前逐欄相同) |
| **對話** | SM + 語者分離 | 共用斷句 + 換人就切 | 所有人 | 語者分離準確度**未量測**(侷限 26) |
| **Gemini 對照** | gemini-3.5-transcribe-live | 共用斷句 + 暫定也重設停頓計時 | **只有 admin**(`/ws` 伺服器端擋,不靠前端藏) | handoff-v12 |

**共用斷句(2026-10-04 起三個模式相同)**:句末標點切句;殘句超過 48 字切在最後一個軟斷點
(、, 空白),找不到才等到 96 字硬切;講者**停住** 6 秒(最後一則定稿起算)就把殘句送出去。
舊版的 6 秒是從「殘句開始累積」起算,講超過 6 秒的句子不管有沒有停都會被攔腰切斷——
9/25 試酒紀錄的「純米大吟醸|です。」「なるん|ですが。」就是這樣來的。
`analysis/segmentation_replay.py` 拿 exp1 的 SM 即時定稿(30 檔)重播:

| | 句數 | 收在字中間 | ≤4 字碎句 | 譯文等待 p50 / p90 / max |
|---|---:|---:|---:|---|
| 舊(2026-10-04 前的導覽) | 538 | 164(30%) | 53(10%) | 3.6 / 6.6 / 7.0 秒 |
| 新 | 408 | 0(0%) | 3(1%) | 4.6 / 9.5 / 15.5 秒 |

代價是譯文晚 1~3 秒(灰字的暫定照常即時顯示)。只換長度那道沒有效果(30% → 30%),
主因是時間那道。數字出處 `results/segmentation_replay.json`。

**Gemini 對照多一條:暫定也重設停頓計時。** 原本假設「Gemini 定稿 = 講者停頓 = 斷點」,每則定稿直接當一句;
10-04 的試酒紀錄 23 句有 14 句沒收句,推翻了這個假設(講者的「え、」「あの」停頓也會讓它定稿)。
但它的定稿間隔中位 7.6 秒,比 6 秒的停頓保險還長,只看定稿會把「還在講」當成停住,所以要用暫定
(約每 0.5 秒一則)當「還在講」的訊號。重播 GTv 30 檔:收在字中間 32%(舊)→ 19%(只套共用)→ **8%**;
代價是譯文等待 p90 多 10 秒(不含 Gemini 自己的定稿延遲)。對照模式應該只差在耳朵,斷句不該是另一個變因。

**Gemini 對照的工程細節**(都踩過或照官方限制寫的):
單場上限 10 分鐘 → 9 分鐘主動換線、收到 `goAway` 也換;下行是**二進位** JSON 框(不解碼就一個字都沒有);
沒有詞級時間戳 → `t` 給 0,前端不算這個模式的延遲;不回 usageMetadata → 收尾 log 用音訊秒數 × 官方
blended $0.009/min 記帳(`耳朵≈$…`);setupComplete 等 10 秒,直連等不到就改走代打再試一次,
還是等不到就明講(不再無聲卡在「連線中」)。

**「超過 10 秒都沒有句子」是它的節奏,不是壞掉**(`analysis/final_cadence.py` →
`results/final_cadence.json`,同一批 30 檔、1× 即時):Gemini 只在講者停頓時給定稿,
安靜(N0)時定稿間隔中位 7.6 秒、p90 16.8 秒;人聲背景(N3)開場第一則定稿中位要 **30 秒**、
最久 63 秒。SM 是每 0.5 秒一則。譯文由定稿觸發,所以這就是譯文會空白的時間。
灰字暫定在 N0 會一直動;N3 連暫定都很少——畫面狀態列會直接講「背景有人聲時它常整段不出字」。

**本機驗證三個模式**(需要 `.dev.vars` 有 SM 與 Gemini 金鑰、`DEV_LOGIN=1`,
`ADMIN_EMAILS` 設一個測試地址才能開 Gemini 模式;**用 `localhost` 不要用 `127.0.0.1`**——
canonical-host 檢查只豁免前者):

```bash
npx wrangler dev --port 8787
node scripts/probe-ws.mjs --host http://localhost:8787 --email <ADMIN_EMAILS 裡的地址> \
  --wav ../corpus/conditions/sakai06__N0.wav --lang ja --mode gemini   # guide / dialog / gemini
```

代打路徑在台灣不會被觸發。要驗證它,`.dev.vars` 加 `GEMINI_FORCE_PROXY=1`,
wrangler 日誌出現 `[gemini-proxy] 代打一條 Live session` 才算有走到。
⚠️ 改 `.dev.vars` 要**整個重開** wrangler——直接 kill 外層 `npx` 不會殺掉 `workerd` 子行程,
新的 wrangler 會綁不到埠,而探針會悄悄打到舊的那一個(實際發生過)。

**語言是 `worker/langs.ts` 一處定義**(日本語 / 한국어 / English / 中文・English 夾雜),
前端下拉、`/api/config`、relay 的 Speechmatics 設定、場景包驗證字集都讀同一份。
語言與詞表在 session 開始時固定,中途要換 = 重連(Speechmatics 協定限制)。

`cmn_en` 雙語 pack **完全不輸出標點**,所以 relay 除了句末標點,另有長度(48 字,切在空白)
與停頓(6 秒沒有新定稿)兩個 flush 條件,否則整場會累積成一句才吐出來。

內容零留存:音訊不落地、字幕只在使用者裝置的 IndexedDB;R2 只有名單與詞表。


## 安全標頭

在 `worker/index.ts` 的 `fetch()` 出口統一套(`withSec`),所以**每一個回應**都有 ——
靜態頁、`/api/*` 的 JSON、`/auth/*` 的 302、canonical 的 301 都一樣。
WebSocket 的 101 升級回應例外:重包會把 `webSocket` 那一半丟掉,`/ws` 會變成空殼。

`Strict-Transport-Security` 也由 Worker 送一份。zone 層可能已經開了,
但 **repo 裡沒有任何東西證明那件事**,而 dashboard 的設定改掉不會有人發現 ——
自己送一份是零成本的縱深。要確認 zone 那一份:Cloudflare → SSL/TLS → Edge Certificates → HSTS。

## 本機開發

```sh
npm install
npm run dev:worker   # wrangler dev(:8787,API/WS/DO)
npm run dev          # vite dev(:5173,/api 與 /ws 轉給 8787)
```

「開發用 Email 直登」(`POST /api/login`,比對 R2 白名單;bucket 不存在時預設放行
`clarence.chien@gmail.com`)要**兩道閘門同時成立**才會開:`.dev.vars` 裡設了
`DEV_LOGIN=1`,而且 host 是 `localhost` / `127.0.0.1`。

> ⚠️ 這裡刻意**不**用「有沒有設 `GOOGLE_CLIENT_ID`」當判準。舊版是那樣寫的,
> 結果「兩把都沒設」這個組合沒被涵蓋:`/api/login` 開著、session 又用公開的
> `dev-insecure-secret` 簽章,任何人送一個 email 就是 admin —— 而 `ADMIN_EMAILS`
> 那個地址還寫在公開 repo 裡。正式站在 2026-09-04 被實測就是這個狀態。
> 判準必須由開發環境自己舉手,不能綁在另一個也可能忘記設的 secret 上。

本機 secrets 放 `.dev.vars`(已在 .gitignore):

```
DEV_LOGIN=1
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
   | `GOOGLE_CLIENT_ID` | OIDC。**正式站必設**;沒設 = `/auth/login` 回 404,站台鎖住 |
   | `GOOGLE_CLIENT_SECRET` | OIDC token 交換 |
   | `SESSION_SECRET` | session HMAC。**fail-closed**:非本機開發環境缺它 → 全站鎖死 |
   | `TURNSTILE_SECRET` | 與 vars 的 `TURNSTILE_SITE_KEY` **成對**設定才啟用 |

3. **種子場景包**(exp1 語料的東大阪詞表,80 詞條;探針複驗時要用):

   ```sh
   npm run seed:pack
   # = wrangler r2 object put kikemu-config/vocab/higashiosaka.json \
   #     --file seed/higashiosaka.json --remote --content-type application/json
   ```

   之後的場景包在 `/admin` 用**匯入 md**(建議)或**關鍵字**生成,見下節。

4. **部署**

   ```sh
   npm run deploy
   ```

   綁自訂網域後把 `wrangler.jsonc` 的 `CANONICAL_HOST` 填上再部署一次——**只填 hostname**(`kikemu.ai-apps.work`),不要含 `https://` 或尾斜線(程式會自動剝,但別依賴它)
   (`workers_dev`/`preview_urls` 已在設定碼層級關死)。

   DO migration 由 `wrangler.jsonc` 的 `migrations` 帶著走,部署時自動套用
   (v1:`QuotaCounter`/`SessionRelay`;v2:`GeminiProxy`,Gemini 區域封鎖的代打)。
   `GEMINI_PROXY_REGION`(預設 `wnam`)也在 vars 裡——改地區會在新地區建一顆新 DO,
   舊的閒置後自然回收;**別選 `apac`**,可能又落在 HKG。

5. **名單**:首次登入的訪客自動進等候名單,`/admin` 一鍵核准(級別
   trial 15 分/beta 60 分/pro 180 分,或自訂每日秒數)。

## 場景包:建議用外部 LLM 產 md 再匯入

**為什麼**:產品內的關鍵字產包是照寺社觀光寫的——搜尋對象沒有「商品名・銘柄」,抽詞 prompt
又寫死「一般語は含めない」。2026-10-04 的試酒導覽:酒廠・品牌・酒藏都對了(三次全錯 → 全對),
但酒款「万穂」與行業術語「日本酒度」沒進包,照樣聽錯(`results/report.md` §2.1g)。
外部有搜尋與深度思考的模型可以查得更深(官網商品頁、多頁),prompt 也能照主題調。

**流程**:`/admin` →「匯入 md」→ 輸入**關鍵字**(要去的地方)、選填補充資料 → **複製 prompt** 或手機上
**分享到 App** → 貼給 ChatGPT / Gemini / Claude(開搜尋與深度思考)→ 回來的 md 貼上或上傳 →
**預覽**(解析 + 同一條驗證 pipeline,不呼叫任何模型、不花錢)→ 確認後存(`pack-save`,`source.kind = 'import'`)。

| 環節 | 檔案 | 做的事 |
|---|---|---|
| prompt | `public/vocab-prompt.md` | **純 prompt**,最後一行是 `## 主題`;管理頁把關鍵字(與補充資料、韓語註記)接在最下面。要外部模型收商品名與行業術語、讀音不確定就留空、只輸出固定格式 |
| 複製 | `public/admin.js` `buildPrompt` | 頁面載入時就先抓 prompt——iOS Safari 只允許在點擊當下寫剪貼簿,點了才 fetch 會失去授權;被擋時把 prompt 全選放進唯讀框讓人長按複製 |
| 解析 | `worker/packmd.ts` | front matter(lang / name / alias)+ `\| 表記 \| 読み \| 種別 \| 出典 \|` 表格。寬鬆:前面多一句話、包在 ``` 裡、讀音中間有空白都收;**看不懂的列一定列出來**(不默默吞) |
| 驗證 | `worker/vocab.ts` `validateEntries` | 與關鍵字產包同一條 pipeline(假名字集、混寫正規化、去重) |
| 端點 | `POST /api/admin/pack-import` | 只預覽不存;出典網址過 `safeSources`(只收 http(s)) |

第一版的坑(2026-10-05 使用者回報):prompt 檔裡有使用說明,複製時用「從這裡開始複製」的標記去切,
但說明文字本身也引用了那串標記,結果從說明那裡就開始切,說明被一起複製出去;而且要在手機上手改 `{{主題}}`。
現在 prompt 檔不放任何說明,關鍵字由管理頁接上。

⚠️ **匯入版的辨識效果還沒量過**。上面那段只說明「關鍵字版漏了什麼」;
外部模型產的包是不是真的比較好,要用同一段試酒影片掛兩個包各錄一次比(專名與術語逐一對)。

## 場景包:在 `/admin` 用關鍵字生成(較快,會漏商品名與行業術語)

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

貼來源文字的舊路徑(`pack-generate`)仍在,適合有官方頁內文的時候。

### 驗證 pipeline(`worker/vocab.ts`)

抽出來的詞條一律過四段,每段各自回報 **fix / warn / drop**,預覽卡與存檔結果
都會把「pipeline 對這包做了什麼」列給管理者看:

| 段 | 做的事 |
|---|---|
| `trim` | content 去空白;讀音 NFKC(半形假名 `ｵｵｻｶ` → `オオサカ`)+ 去空白,空的丟掉 |
| `content` | 抓被 CJK 夾住的**小寫**英文——`of` / `no` 還原成 `の`(韓文 `의 `),其餘只警告 |
| `reading` | 混寫假名正規化(`トらいし` → `とらいし`)→ 字集不合剔除 → 去重 → >6 字警告 |
| `dedupe` | 同一表記只留一條,讀音併入不丟 |

字集依語言(日文全形假名、韓文諺文,皆已探針確認 Speechmatics 接受)。
`content` 段只抓小寫是刻意的:同一包裡 `JO-TERRACE OSAKA`、`RUNNING BASE大阪城`、
`もりのみやキューズモールBASE` 都是真的店名,全大寫,不能誤殺。
自動修正**一定列出來給人看**——程式有可能把某個真的叫這個名字的詞「修」壞。

pipeline 是冪等的(修過的包再跑一次 fix=0),所以 `pack-save` 存檔前會再跑一次
(預覽送回來的詞條不能信),既有的包則用清單上的**「重驗」**按鈕
(`POST /api/admin/pack-revalidate`)重跑——規則後來補強不會自動套用到
已經在 R2 的包,重驗只改詞條,alias / lang / `source` 生成軌跡原封不動。

**fix 是就地改寫,不是刪除**:會少詞的只有 `✂ drop`(讀音字集不合、重複表記
合併)。`虎石` 那種「詞條對、只有讀音壞」的狀況,整條丟掉反而會失去這個
專名的加成,所以只改讀音。正式站那包重驗後逐條比對驗證過這件事(見下)。

### 實測:正式站第一包「大阪城」

| 指標 | 值 |
|---|---|
| 詞條 | 149(無重複) |
| 有讀音 | 142 / 149 |
| 搜尋詞 / 引用來源 | 10 / 10(osakacastlepark.jp 官方、bunka.go.jp 文化廳、osaka-info.jp…) |
| 讀音 >6 字(僅警告) | 78 |
| 重驗結果 | `in 149 → out 149・✎2 ✂0`(已套用到正式站) |
| `ready` 回報 | 場景包:大阪城 / 149 詞 |

沒有讀音的 7 條裡,`大阪城`是**程式強制插入**的關鍵字本身(設計如此);
其餘 6 條是人名與碑名,模型自己略過。

**這包催生了 pipeline 的 `content` 段與混寫假名正規化**——它上線時帶著兩個
舊版驗證擋不掉的壞詞條,已按「重驗」修掉並逐條比對確認過:

| 壞法 | 舊版為什麼過得了 | 重驗後 |
|---|---|---|
| `content` 被模型「翻譯」 | 只驗 `sounds_like` 字集,不驗 `content` | `黄金 of 茶室` → `黄金の茶室`(讀音 `おうごんのちゃしつ` 保留) |
| 讀音混片假名 | `KANA_RE` 平/片假名都收,混寫也合法 | `虎石` 的 `トらいし` → `とらいし`(詞條保留) |

重驗前後整包 diff:**149 → 149,只有這 2 條變**,其餘 147 條連陣列位置都相同;
有讀音仍是 142 條、`name`/`alias`/`lang` 未動、`source` 生成軌跡位元組級相同。

要查一包的實際內容(API 不外露 `sounds_like` 與 `source` 生成軌跡):

```sh
npx wrangler r2 object get kikemu-config/vocab/<id>.json --remote --pipe | jq .
```

## 已知風險(PRD §8,上線前必驗)

- **iOS 長時間連續收音**:kikemu 是連續 60 分鐘而非 PTT 短句,AudioContext
  可能被系統回收(manemu 踩過全套坑)——需要真機驗證與背景保活策略。
  **已加螢幕常亮**(`src/ui/wakelock.ts`,Screen Wake Lock API):按開始時取得、
  切 App 回來自動重新取得、停止或任何失敗路徑都會放掉;拿不到會明講,狀態列持續提醒。
  邏輯在 Chromium 用可控的假 wakeLock 驗過(取得 / 切背景被收回 / 回前景重取 / 重入不疊監聽 /
  停止後不再要 / 被拒只提示一次);**iOS 真機(尤其加入主畫面模式)未驗**。
- **iOS 關閉瀏覽器降噪的實際效果**:`echoCancellation/noiseSuppression/autoGainControl:false`
  的 constraint 支援不完整,可能拿到處理過的音訊;上線前用 exp3 方法做一次 A/B。
- ~~**行動網路出口被路由到 HKG,Gemini 整場被拒**~~ → **已修**(2026-09-25,`GeminiProxy` 代打,
  見下方診斷章節)。殘餘風險:DO `locationHint` 是 best-effort 不是保證——若代打也被拒,
  卡片會寫出原因、Logs 看得到,不會靜默。

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

**部署了卻沒生效?先想 Service Worker。** `/admin.js`、`/pcm-worklet.js` 這些
檔名不帶 hash 的檔案,舊版 sw.js 走「快取優先」會把它們永久凍結——症狀是
**HTML 是新的、行為是舊的**(實際發生過:admin 的語言下拉在新 HTML 裡存在,
卻被舊版 JS 漏掉而永遠空白)。現在只有 `/assets/`、`/icons/` 快取優先,
其餘同源檔案一律網路優先。若還是懷疑快取,DevTools → Application →
Service Workers 勾 Update on reload,或把 `public/sw.js` 的 `CACHE` 版本號 +1
(activate 會清掉所有舊快取,已安裝的客戶端會自己痊癒)。

App 內另有兩個對照數字:狀態列的**本地音量條**(worklet 算的 RMS)與 relay 每秒
回報的**伺服器實收 RMS**。本地會動、伺服器接近零 = 音訊在傳輸中損壞;
兩邊都有值卻沒有字 = 真的是辨識問題(對照 exp1:8dB 人聲下 SM 仍有 0.627,
所以「完全零字」通常不是噪音,要先懷疑語言設定)。

### 原文有、譯文沒有(而且只有某一台手機這樣)

這是第四種,跟上面三種不同:**Speechmatics 那一跳是好的,壞的是 Gemini 那一跳。**
2026-09 實際發生過:同一個帳號,iOS 只有原文、Android 兩行都有。

**先看卡片上寫什麼。** 譯文那一行不是空的,而是三種之一:

| 卡片上的字 | 意思 |
|---|---|
| `…`(灰色,一直不變) | 翻譯呼叫**沒回來**——伺服器端還在等,或 WebSocket 已經斷了但畫面沒察覺 |
| `譯文暫缺(gemini 400: User location is not supported…)・點擊重試` | **區域封鎖**。見下 |
| `譯文暫缺(gemini 429…)` / `(gemini 5xx…)` | 額度或上游故障,與手機無關 |

括號裡那段是伺服器端失敗原因的前 120 字(`relay.ts` 的 `zhError.reason`),
**現場在手機上就看得到,不用開 dashboard。**

**區域封鎖的機制**(為什麼會「一台好、一台壞」):

翻譯呼叫是從 `SessionRelay` 這個 Durable Object 打出去的,而 DO 是 **per-email**
(`idFromName(email)`),會在「把它叫醒的那個請求」所在的 Cloudflare 機房建立、
閒置回收之後又在下一個叫醒它的機房重建。之後 Gemini 的子請求就從 DO 所在機房出去。
Google 的 Gemini API **不服務香港**(回 400 `FAILED_PRECONDITION`,
`User location is not supported for the API use`)。台灣有些行動網路的出口
會被路由到 **HKG** 機房,Wi-Fi 通常落在 TPE——所以同一個帳號、
**手機走行動網路壞、換 Wi-Fi 就好**,看起來像 iOS/Android 的差別,其實是路徑的差別。

**怎麼確認:** Cloudflare dashboard → Workers & Pages → kikemu → **Logs**
(`observability` 已開),搜 `[relay]`——每一場結束都會印 `colo=XXX`;
搜 `[relay][zh]` 看逐句失敗原因;搜 `[gemini]` 看 HTTP 狀態與正文前 200 字。
**colo=HKG 且 reason 是 location is not supported → 就是這個。**
`[relay]` 那行另有 `上游 暫定 N / 定稿 M`:**M=0 是引擎沒回字**(Gemini 在人聲背景下的常態),
M>0 但手機上沒字才是 relay 或前端的問題——兩件事不要混在一起查。

**現場解法(舊版):** 關行動數據改連 Wi-Fi、或反過來,**重新開一場**(DO 要被重建才會換機房;
同一場裡點「重試」沒用,它還在同一個 DO)。

**根治(2026-09-25 已做,`gemini.ts` GeminiProxy):** Cloudflare 沒有「指定 fetch 出口地區」
這種東西,唯一能選機房的原語是 DO 的 `locationHint`(只在建立時生效)。所以:
正常路徑不動、直打 Google;**第一次收到區域 400,那個機房的 isolate 就記住,
之後一律改走一顆釘在 `GEMINI_PROXY_REGION`(預設 `wnam`)的小 DO 代打**,
那句話當場重打一次。代價只落在被封鎖的那些場(多一跳到美西,每句約 +150~250ms);
TPE 的 isolate 永遠不會切。翻譯與場景包生成都走同一個 `post()`,一併受惠。
Logs 搜 `[gemini] 本機房被 Google 以區域理由拒絕` 就看得到切換發生。
⚠️ 地區別選 `apac`——可能又落在 HKG。
