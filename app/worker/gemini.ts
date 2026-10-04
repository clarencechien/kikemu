/* Gemini generateContent(REST;CF Workers 無 Node SDK)。
   兩個用途:
   1. translateSentence — 聽譯 hop:定稿句 → 台灣正體(relay.ts 逐句呼叫)。
      systemInstruction 與 user template 一字不改自 scripts/prompts.py
      (exp1 凍結口譯 prompt:台灣用語 0 失誤、adequacy 4.71,不准改)。
   2. extractVocab — 場景包生成:來源文字 → 詞條 + 假名讀音(JSON mode),
      即 scripts/make_dict.py 的產品化(admin.ts pack-generate 呼叫)。 */

import type { Env } from './index';
import type { VocabEntry } from './vocab';
import { resolveLang } from './langs';

/** 凍結口譯 systemInstruction(scripts/prompts.py INTERPRETER_SYSTEM,逐字複製) */
export const INTERPRETER_SYSTEM =
  'あなたは観光ガイド音声の同時通訳者です。聞こえてくる日本語の解説を、' +
  '台湾で使われる繁體中文(台灣正體)に翻訳して出力してください。' +
  '専有名詞(神名、人名、地名、神社名、施設名)は漢字表記をそのまま使い、' +
  'カタカナの固有名詞は台湾で一般的な訳語、なければカタカナのままにしてください。' +
  '用語は台湾の習慣に従うこと(例:資訊、品質、影片、軟體、網路;' +
  '信息、质量、视频、软件、网络は使わない)。' +
  '訳文のみを出力し、説明・注釈・ふりがなは加えないこと。';

/** scripts/prompts.py TRANSLATE_USER_TEMPLATE(逐字複製,{transcript} 置換) */
const TRANSLATE_USER_TEMPLATE =
  '以下は音声認識による日本語の書き起こしです。上記の方針で台灣正體中文に翻訳してください。\n\n{transcript}';

/* 多語言:只把來源語名稱換掉,其餘一字不動。
   lang='ja' 時回傳的字串與 exp1 量測用的**完全相同**(下方 assert 式的寫法保證這件事),
   所以既有的 adequacy 4.71 / 台灣用語 0 失誤仍然適用;其他語言沿用同一套
   語域與在地化規則,但屬於未量測範圍。 */
const interpreterSystem = (lang: string) => {
  const { srcName } = resolveLang(lang);
  return srcName === '日本語'
    ? INTERPRETER_SYSTEM
    : INTERPRETER_SYSTEM.replace('聞こえてくる日本語の解説', `聞こえてくる${srcName}の解説`);
};
/** 中英夾雜:來源已是中文(SM 輸出簡體),要的是繁體化+台灣在地化+英文術語保留,
    不是「翻譯」。措辭沿用 exp2 translate_x.py 實測版本。 */
const CODEMIX_USER =
  '以下是語音辨識的書き起こし(中文夾雜英文術語)。請整理成通順的台灣正體中文,' +
  '英文術語維持英文原文不要翻譯。只輸出整理後的文字。\n\n{transcript}';

const translateUser = (lang: string, sentence: string) => {
  const { srcName } = resolveLang(lang);
  const tpl =
    lang === 'cmn_en'
      ? CODEMIX_USER
      : srcName === '日本語'
        ? TRANSLATE_USER_TEMPLATE
        : TRANSLATE_USER_TEMPLATE.replace('音声認識による日本語の', `音声認識による${srcName}の`);
  return tpl.replace('{transcript}', sentence);
};

const model = (env: Env) => env.TRANSLATE_MODEL || 'gemini-3.5-flash';

/* ── Thinking 稅 ──────────────────────────────────────────────
   thinking token 以**輸出價**計費(官方 pricing 頁明載),而 3.5-flash 預設 medium。
   逐句翻譯是機械性任務,不需要思考。同料 A/B(kikemu 凍結口譯 prompt + exp1 定稿句,
   2026-08-14 實測):

     設定              3.5-flash          3.6-flash
     (不設)            thoughts 29.1×     29.6×
     thinkingLevel:minimal      0×  ✓        0×  ✓
     thinkingBudget:128        18.5×       12.0×   ← 不等於 0,別用
     thinkingBudget:0             0×       400 拒收
     level + budget 同給   400「only one of thinking budget and thinking level」

   所以固定用 `thinkingLevel: 'minimal'`(唯一在兩代都真的歸零的寫法),
   且**永不與 budget 同給**。舊模型/未知欄位會回 400 → 拿掉 thinkingConfig 重試一次,
   寧可多付思考費也不要整個功能掛掉(sukemu 的 mediaResolution 是同款失敗形狀)。
   譯文品質無退化:三句抽樣人工比對,台灣用語與專名表記皆正確。
   env.THINKING_LEVEL 可覆寫('off' = 不送,回到預設 medium)。 */
const thinkingOf = (env: Env) => {
  const lv = env.THINKING_LEVEL || 'minimal';
  return lv === 'off' ? null : { thinkingLevel: lv };
};

/** 一次呼叫的 token 用量(thoughts 也要看得見——看不見的花費才是危險的花費) */
export type Usage = { prompt: number; output: number; thoughts: number; total: number };
const readUsage = (resp: any): Usage => {
  const u = resp?.usageMetadata ?? {};
  const prompt = u.promptTokenCount ?? 0;
  const output = u.candidatesTokenCount ?? 0;
  const thoughts = u.thoughtsTokenCount ?? 0;
  return { prompt, output, thoughts, total: prompt + output + thoughts };
};

/* ── 區域封鎖的代打 ──────────────────────────────────────────
   Google 不在香港提供 Gemini API(400 FAILED_PRECONDITION
   "User location is not supported for the API use.")。Workers 的子請求從
   **執行它的機房**出去,而 SessionRelay DO 又是在「叫醒它的那個請求」所在機房建立
   ——台灣部分行動網路的出口被路由到 HKG,結果是同一個帳號 Wi-Fi 有譯文、
   行動網路整場「譯文暫缺(gemini 400: User location is not supported…)」
   (2026-09-25 iOS 實機證實)。

   Cloudflare 沒有「指定 fetch 出口地區」這種東西;唯一能選機房的原語是
   DO 的 locationHint(只在**建立時**生效)。所以:

     · 正常路徑不動:直接打 Google(TPE 等機房 0 額外延遲)
     · 第一次收到區域 400 → 這個 isolate 記住,之後一律改走一個釘在
       GEMINI_PROXY_REGION(預設 wnam)的小 DO 代打;那句話當場重打一次
     · 代打 DO 只做 directPost,不會再遞迴代打

   代價只落在被封鎖的那些場:多一跳到美西,每句約 +150~250ms。
   translate / extractVocab / researchTerms 全走 post(),所以場景包生成一併受惠。
   DO 名稱含地區,改 var 就會在新地區建一顆新的(舊的閒置後自然回收)。 */
const REGION_BLOCKED = /location is not supported/i;
/** 這個 isolate 有沒有被 Google 以區域理由拒絕過。isolate 是 per-機房的,
 *  TPE 的永遠不會被設成 true;HKG 的設一次就夠。 */
let preferProxy = false;

const proxyRegion = (env: Env) => (env.GEMINI_PROXY_REGION || 'wnam') as DurableObjectLocationHint;
const viaProxy = (env: Env, body: unknown): Promise<Response> => {
  const region = proxyRegion(env);
  const stub = env.GEMINI_PROXY.get(env.GEMINI_PROXY.idFromName(`gemini-${region}`), { locationHint: region });
  return stub.fetch('https://do/generate', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
};

async function directPost(env: Env, body: unknown): Promise<Response> {
  return fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${model(env)}:generateContent`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-goog-api-key': env.GEMINI_API_KEY },
      body: JSON.stringify(body),
    },
  );
}

/** 測試開關:GEMINI_FORCE_PROXY=1 一律走代打(只放 .dev.vars)。
 *  台灣的開發環境不會被區域封鎖,沒有這個開關,代打路徑要等真的有人從 HKG 連線才會被執行到。 */
const useProxy = (env: Env) => !!env.GEMINI_PROXY && (preferProxy || env.GEMINI_FORCE_PROXY === '1');

async function post(env: Env, body: unknown): Promise<Response> {
  if (useProxy(env)) return viaProxy(env, body);
  const r = await directPost(env, body);
  if (r.status === 400 && env.GEMINI_PROXY && REGION_BLOCKED.test(await r.clone().text())) {
    preferProxy = true;
    console.warn(`[gemini] 本機房被 Google 以區域理由拒絕,此 isolate 之後改走代打 DO(${proxyRegion(env)})`);
    return viaProxy(env, body);
  }
  return r;
}

/** 代打 DO:釘在 Google 服務的地區,把同一個 generateContent 請求原樣轉出去、
 *  原樣轉回(status + body 不動,generate() 的判斷邏輯完全不用改)。
 *  另外代打 Live 的 WebSocket(`/live`,模式選單的「Gemini 對照」用):
 *  在這裡開上游、兩邊逐框對接。
 *  只有 Worker 內部透過 binding 打得到,沒有對外入口。 */
export class GeminiProxy {
  constructor(_state: DurableObjectState, private env: Env) {}

  async fetch(req: Request): Promise<Response> {
    if (req.headers.get('Upgrade') === 'websocket') return this.live();
    if (req.method !== 'POST') return new Response('POST only', { status: 405 });
    const body = await req.json();
    const r = await directPost(this.env, body); // 直打,絕不再代打(避免遞迴)
    return new Response(await r.text(), {
      status: r.status,
      headers: { 'content-type': r.headers.get('content-type') ?? 'application/json' },
    });
  }

  private async live(): Promise<Response> {
    const up = await directLiveSocket(this.env); // 直連,絕不再代打
    console.log('[gemini-proxy] 代打一條 Live session'); // 看得到代打真的發生(HKG 的場才會有)
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    up.accept();
    // 兩邊都設 arraybuffer:Google 的下行是**二進位** JSON 框,Blob 會讓轉送變成非同步、可能亂序
    server.binaryType = 'arraybuffer';
    up.binaryType = 'arraybuffer';
    const relay = (from: WebSocket, to: WebSocket) => {
      from.addEventListener('message', ev => {
        try {
          to.send(ev.data as string | ArrayBuffer);
        } catch {}
      });
      from.addEventListener('close', ev => {
        try {
          to.close(ev.code === 1005 || ev.code === 1006 ? 1000 : ev.code, ev.reason);
        } catch {}
      });
      from.addEventListener('error', () => {
        try {
          to.close(1011, 'proxy peer error');
        } catch {}
      });
    };
    relay(server, up);
    relay(up, server);
    return new Response(null, { status: 101, webSocket: client });
  }
}

/* ── Gemini Live(WebSocket)────────────────────────────────────
   跟上面的 HTTP 一樣會撞區域封鎖:Live 是從**執行它的機房**連出去的長連線。
   被拒時的形狀是 setup 之後被 close,reason 含 "location is not supported"。
   處理方式與 post() 同:第一次被拒 → 這個 isolate 改走代打 DO 的 /live。

   兩個實測過、而且都是「沒有錯誤訊息、就是一個字都沒有」那類的坑:
   · 下行框是**二進位**的 JSON(2026-10-04 探針:Python websockets 收到 bytes)
     → 一律解碼,不能只處理 string
   · 驗證用 x-goog-api-key header 可行(同日實測),金鑰不必放進 URL */
const LIVE_URL =
  'https://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent';

async function directLiveSocket(env: Env): Promise<WebSocket> {
  const resp = await fetch(LIVE_URL, { headers: { Upgrade: 'websocket', 'x-goog-api-key': env.GEMINI_API_KEY } });
  if (!resp.webSocket) throw new Error(`gemini live 連線失敗(HTTP ${resp.status})`);
  return resp.webSocket;
}

async function proxyLiveSocket(env: Env): Promise<WebSocket> {
  const region = proxyRegion(env);
  const stub = env.GEMINI_PROXY.get(env.GEMINI_PROXY.idFromName(`gemini-${region}`), { locationHint: region });
  const resp = await stub.fetch('https://do/live', { headers: { Upgrade: 'websocket' } });
  if (!resp.webSocket) throw new Error(`gemini live 代打連線失敗(HTTP ${resp.status})`);
  return resp.webSocket;
}

const td = new TextDecoder();
/** 把一則下行訊息變成物件;Blob 要等,所以回 Promise(呼叫端用 chain 保序) */
async function decodeFrame(data: unknown): Promise<any | null> {
  try {
    if (typeof data === 'string') return JSON.parse(data);
    if (data instanceof ArrayBuffer) return JSON.parse(td.decode(data));
    if (ArrayBuffer.isView(data)) return JSON.parse(td.decode(data as ArrayBufferView));
    if (data && typeof (data as Blob).text === 'function') return JSON.parse(await (data as Blob).text());
  } catch {}
  return null;
}

export type LiveHandlers = {
  onMessage: (m: any) => void;
  onClose: (code: number, reason: string) => void;
};

/** 開一條 Live session,等到 setupComplete 才 resolve。setup 失敗(含區域封鎖)會 reject;
 *  區域封鎖時自動改走代打再試一次。之後的下行訊息依序交給 onMessage。 */
export async function openGeminiLive(env: Env, setup: unknown, h: LiveHandlers): Promise<WebSocket> {
  const attempt = async (proxied: boolean): Promise<WebSocket> => {
    const ws = proxied ? await proxyLiveSocket(env) : await directLiveSocket(env);
    ws.accept();
    ws.binaryType = 'arraybuffer';
    return new Promise<WebSocket>((resolve, reject) => {
      let ready = false;
      let chain: Promise<void> = Promise.resolve();
      ws.addEventListener('message', ev => {
        chain = chain.then(async () => {
          const m = await decodeFrame(ev.data);
          if (!m) return;
          if (!ready) {
            if (m.setupComplete) {
              ready = true;
              resolve(ws);
            }
            return;
          }
          h.onMessage(m);
        });
      });
      ws.addEventListener('close', ev => {
        chain = chain.then(() => {
          if (!ready) {
            ready = true; // 只 reject 一次
            reject(Object.assign(new Error(`gemini live ${ev.code}: ${String(ev.reason).slice(0, 160)}`), { reason: ev.reason }));
          } else h.onClose(ev.code, ev.reason);
        });
      });
      ws.send(JSON.stringify(setup));
    });
  };
  if (useProxy(env)) return attempt(true);
  try {
    return await attempt(false);
  } catch (e) {
    const why = String((e as { reason?: string }).reason ?? (e as Error).message);
    if (env.GEMINI_PROXY && REGION_BLOCKED.test(why)) {
      preferProxy = true;
      console.warn(`[gemini] Live 在本機房被 Google 以區域理由拒絕,此 isolate 之後改走代打 DO(${proxyRegion(env)})`);
      return attempt(true);
    }
    throw e;
  }
}

/** PCM16 位元組 → base64(Live 的 realtimeInput.audio 要 base64) */
export function b64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** think=false:reasoning-shaped 的呼叫(如搜尋接地)不套用 minimal */
async function generate(env: Env, body: any, opts: { think?: boolean } = {}): Promise<any> {
  const think = opts.think === false ? null : thinkingOf(env);
  const withThinking = think
    ? { ...body, generationConfig: { ...(body.generationConfig ?? {}), thinkingConfig: think } }
    : body;

  let r = await post(env, withThinking);
  if (r.status === 400 && think) {
    // 未知欄位 → 拿掉 thinkingConfig 重試(通用防禦:400 就退回沒有該欄位的版本)
    console.warn('[gemini] thinkingConfig 被拒,退回不設思考重試');
    r = await post(env, body);
  }
  if (!r.ok) {
    // 只帶 status 與 Google 自己的 error.message 欄位,不塞原始 body。
    // 原本是把回應前 200 字整段接進 message,而 api() 的 catch 又把 message
    // 回給瀏覽器 —— 上游的錯誤格式一改(例如哪天把送出的請求回顯進錯誤裡),
    // 那條路就成了外洩通道。完整內容留在 console.error。
    // (PR #73 的自動合併曾把這段換回「前 200 字進 message」的舊版,而 relay 又把
    //  message 前 120 字當 zhError.reason 送到手機——等於把 PR #72 關掉的洩漏通道
    //  重新打開。這裡是唯一該碰原始 body 的地方,之後改動要保住這個邊界。)
    // 區域封鎖(例如從 HKG 機房出去)在這裡長這樣:
    //   gemini 400: User location is not supported for the API use.
    const raw = await r.text();
    console.error('[gemini]', r.status, raw.slice(0, 800));
    let detail = '';
    try {
      const j = JSON.parse(raw) as { error?: { message?: string } };
      if (typeof j.error?.message === 'string') detail = `: ${j.error.message.slice(0, 120)}`;
    } catch {
      /* 不是 JSON 就不帶細節 */
    }
    throw new Error(`gemini ${r.status}${detail}`);
  }
  return r.json();
}

const firstText = (resp: any): string | null =>
  resp?.candidates?.[0]?.content?.parts?.map((p: any) => p.text ?? '').join('').trim() || null;

/** 定稿句 → 台灣正體譯文(exp1 同款呼叫:temperature 0.2)+ 這次燒了多少 token */
export async function translateSentence(
  env: Env,
  sentence: string,
  lang = 'ja',
): Promise<{ zh: string; usage: Usage }> {
  const resp = await generate(env, {
    systemInstruction: { parts: [{ text: interpreterSystem(lang) }] },
    contents: [{ parts: [{ text: translateUser(lang, sentence) }] }],
    generationConfig: { temperature: 0.2 },
  });
  const zh = firstText(resp);
  if (!zh) throw new Error('gemini 沒有回譯文');
  return { zh, usage: readUsage(resp) };
}

/** 場景包詞條抽取 prompt(scripts/make_dict.py 的產品化;來源不限維基百科) */
const VOCAB_PROMPT = `あなたは音声認識用のカスタム語彙(custom dictionary)を作るアシスタントです。
以下は、ある観光ルートの訪問先に関する紹介文です。
観光ガイドの音声認識で誤認識されやすい固有名詞・専門語を抽出し、
音声認識エンジンに登録する語彙リストを作ってください。

対象: 神名、人名、神社・施設名、神事・祭事名、地名、駅名、社名、時代・年号、茶道等の専門用語。
各項目:
- "content": 正式表記(記事中の表記)
- "sounds_like": 読みの配列(全角ひらがな。読みが複数あれば複数)

必ず含めるもの:
- 紹介文の主題そのもの(神社名・公園名・駅名・人名など)
- 文中に登場する境内施設名(〜殿、〜宮など)、神名、神事・祭事名、関連人物名

一般語は含めない。最大150項目。JSONの配列のみ出力:
[{"content": "...", "sounds_like": ["..."]}]

## 紹介文
`;

/** 來源文字 → 原始詞條(格式驗證在 vocab.ts validateEntries) */
/* 關鍵字產包(pass A):用 Google 搜尋接地蒐集該地點的固有名詞與讀音。
   為什麼要接地:exp1 §2.4 記錄了「只用維基百科」的天花板——對正解專名的字面
   覆蓋只有 1/6 ~ 6/7,在地小眾詞(いとや百貨店、旧池田電報電話局)維基沒有。
   搜尋能碰到官方頁與在地資料,正是報告裡說「產品端可用官方頁面補充」那條路。

   實測行為:模型自己決定要不要查——冷門題目觸發 2 次搜尋回 5~7 筆來源;
   把主題釘死在最前面的這版 prompt,連大阪城這種它本來就熟的題目也會查
   (正式站那包:10 次搜尋、10 筆來源,含官方頁與文化廳)。早期版本回過 0 筆,
   所以來源筆數仍要回報給管理者看——0 筆代表沒有外部佐證,讀音錯了會反而傷辨識。

   搜尋工具不與 responseMimeType=application/json 併用(分兩趟比較穩,
   也沿用 sukemu P1/P2 的分趟省錢模式:pass B 只吃 pass A 的文字)。 */
/* 韓文版:用韓語下指令,讀音要諺文(Speechmatics ko 包已探針驗證接受)。
   兩個實測教訓照搬:主題本身要釘死在最前面、漢字與諺文表記各列一條。 */
const RESEARCH_KO = (keyword: string) =>
  `「${keyword}」에 대해 한국어 공식 홈페이지·관광 안내·백과사전을 검색하세요.\n` +
  `이 장소/주제의 오디오 가이드에서 실제로 읽히는 고유명사를 최대한 폭넓게 모으세요.\n\n` +
  `【최우선】먼저 「${keyword}」 자체의 정식 명칭을 맨 앞에 반드시 넣으세요.\n` +
  `【중요】한자 표기와 한글 표기가 모두 쓰이는 이름은 두 가지를 각각 별도 항목으로 넣으세요.\n\n` +
  `대상: 건물·시설명, 경내 각처 이름, 신·불 이름, 인명, 제례·행사명, 지명, 역명,\n` +
  `연호·시대명, 전문 용어, 주변 상점·거리 이름.\n\n` +
  `각 항목을 「정식표기(한글 읽기)」 형식으로 나열하세요. 읽기를 모르면 추측하지 말고 생략하세요.\n` +
  `설명은 불필요, 목록만.`;

const RESEARCH_JA = (keyword: string) =>
  `「${keyword}」について、日本語の公式サイト・観光案内・百科事典を検索してください。
` +
  `この場所/テーマの音声ガイドで実際に読み上げられる固有名詞を、できるだけ網羅的に集めてください。

` +
  `対象: 建物・施設名、境内の各所名、神名・仏名、人名、神事・祭事名、地名、駅名、
` +
  `年号・時代名、専門用語(茶道・建築・信仰など)、周辺の店舗・商店街名。

` +
  `各項目を「正式表記(ふりがな)」の形式で列挙してください。読みが不明なものは推測せず省くこと。
` +
  `解説は不要、一覧のみ。`;

const RESEARCH_PROMPT = (keyword: string, lang: string) =>
  (lang === 'ko' ? RESEARCH_KO : RESEARCH_JA)(keyword);

export type Research = { text: string; sources: { title: string; uri: string }[]; queries: string[] };

export async function researchTerms(env: Env, keyword: string, lang = 'ja'): Promise<Research> {
  // think 不關:pass A 要自己決定查什麼、查幾次,是 reasoning-shaped 的工作
  // (教訓文件的「關思考看任務形狀」反例規則)。pass B 的抽取才是機械性任務。
  const resp = await generate(env, {
    contents: [{ parts: [{ text: RESEARCH_PROMPT(keyword, lang) }] }],
    tools: [{ google_search: {} }],
    generationConfig: { temperature: 0.0 },
  }, { think: false });
  const text = firstText(resp) ?? '';
  if (!text) throw new Error('搜尋沒有回結果');
  const gm = resp?.candidates?.[0]?.groundingMetadata ?? {};
  const sources = ((gm.groundingChunks ?? []) as any[])
    .map(c => ({ title: String(c?.web?.title ?? ''), uri: String(c?.web?.uri ?? '') }))
    .filter(s => s.uri);
  return { text, sources, queries: (gm.webSearchQueries ?? []) as string[] };
}

/* 容錯解析:輸出被截斷時 JSON.parse 會整份失敗,但前面幾百個詞條其實是好的。
   先直接 parse,失敗就逐一撈出完整的 {…} 物件——scripts/make_dict.py 踩過同一個坑,
   當時也是加了容錯才把包生出來。寧可少幾條,不要整包掉。 */
function parseEntries(raw: string): VocabEntry[] {
  try {
    const items = JSON.parse(raw);
    if (Array.isArray(items)) return items as VocabEntry[];
  } catch {
    /* 往下走容錯路徑 */
  }
  const out: VocabEntry[] = [];
  for (const m of raw.matchAll(/\{[^{}]*\}/g)) {
    try {
      const o = JSON.parse(m[0]);
      if (o && typeof o.content === 'string') out.push(o as VocabEntry);
    } catch {
      /* 半截的物件跳過 */
    }
  }
  if (!out.length) throw new Error('詞條 JSON 解析失敗');
  return out;
}

export async function extractVocab(env: Env, sourceText: string, lang = 'ja'): Promise<VocabEntry[]> {
  // 讀音字集依語言:日文全形假名、韓文諺文(Speechmatics 各自接受,已探針驗證)
  const prompt =
    lang === 'ko'
      ? VOCAB_PROMPT.replace('全角ひらがな', 'ハングル').replace(
          '観光ガイドの音声認識',
          '観光ガイド(韓国語)の音声認識',
        )
      : VOCAB_PROMPT;
  const resp = await generate(env, {
    contents: [{ parts: [{ text: prompt + sourceText }] }],
    // 詞條多的地點(大阪城 149 條)輸出很長,不給額度會被截斷成壞掉的 JSON。
    // 注意 thinking token 也吃 maxOutputTokens 額度——這就是設到 16384 還被截斷的原因。
    // 實測(同 prompt,maxOutputTokens 故意設 300):預設思考 → thoughts 287 / output 9
    // / finishReason=MAX_TOKENS(JSON 壞掉);minimal → thoughts 0 / output 206 / STOP。
    // 現在 generate() 一律送 thinkingLevel:minimal,額度才真的都給輸出用。
    generationConfig: { temperature: 0.0, responseMimeType: 'application/json', maxOutputTokens: 16384 },
  });
  const raw = firstText(resp);
  if (!raw) throw new Error('gemini 沒有回詞條');
  return parseEntries(raw);
}
