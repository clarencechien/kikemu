/* SessionRelay(Durable Object):瀏覽器 WS ↔ Speechmatics RT WS 的中繼。
   模式沿 manemu app/src/relay.mjs:per-email DO(同用戶天然序列化)、
   金鑰只存在 DO、靜默收斂與計費保險絲。kikemu 差異:
   - 上游是 Speechmatics(ja / enhanced / partials / max_delay 2.0,
     additional_vocab = R2 vocab/{pack}.json;exp1 定案),不是 Gemini Live
   - 瀏覽器二進位 PCM 框「原樣」轉發(SM 收 raw binary = AddAudio)
   - 定稿句在 DO 內逐句呼叫 Gemini 翻譯 hop(gemini.ts,凍結口譯 prompt)
   - 配額計「聽譯秒數」:RecognitionStarted 起錶、斷線停錶;
     SM Error 且整場沒有任何定稿 → 不計費(不假裝成功也不收錢)

   下行協定(JSON):
     {type:"ready", pack, packName, vocabCount}
     {type:"partial", t, text}            t = SM 音訊時間軸秒數(端到端延遲用)
     {type:"final", seq, t, text}         一句定稿
     {type:"zh", forSeq, text}            該句譯文
     {type:"zhError", forSeq}             翻譯失敗(前端顯示「譯文暫缺・點擊重試」)
     {type:"error", message}              SM/系統錯誤(4xx 不重試,明講原因)
     {type:"done", reason, seconds, usedSeconds, limitSeconds, charged}
   上行:二進位 = 16kHz PCM16 100ms 框;JSON {type:"end"} 收尾、
     {type:"retryZh", seq} 重試某句翻譯。 */

import type { Env } from './index';
import { translateSentence } from './gemini';
import { readPack } from './vocab';
import { resolveLang } from './langs';
import type { Usage } from './quota';

/** `clarence.chien@gmail.com` → `cla…@gmail.com`。日誌裡夠用來辨識,又不是完整 PII。 */
export const maskEmail = (e: string) => {
  const at = e.indexOf('@');
  if (at < 1) return '???';
  return `${e.slice(0, Math.min(3, at))}…${e.slice(at)}`;
};

const SM_URL = 'https://eu2.rt.speechmatics.com/v2';
/** WS 靜默 30 秒自動收斂(PRD §5 熔斷) */
const IDLE_MS = 30_000;
/** 同一個帳號同時進行中的 session 上限。這是 PTT 型產品,一次只會講一句;
 *  留 2 是為了容忍「斷線重連時舊連線還沒收掉」的短暫重疊。 */
const MAX_LIVE_SESSIONS = 2;
/** 靜音熔斷:有在送音框(所以 IDLE_MS 不會觸發)但伺服器端 RMS 一直接近 0。
 *  手機放口袋忘了停、或腳本直接送靜音 PCM 都是這樣,而 Speechmatics 是按
 *  連線秒數計費的,不會因為沒有語音就不收錢。
 *  取 5 分鐘是刻意偏寬:導覽中走路換場的沉默不該被打斷,而多算的那幾分鐘
 *  遠比「誤殺正在用的人」便宜。真的要更省再往下調。 */
const SILENCE_MS = 300_000;
const SILENCE_RMS = 50;
/** 句尾標點:定稿句切分依據(逐句觸發翻譯,非整場重譯) */
const SENT_END = /(?<=[。!?!?])/;
/** 同一句最多重試幾次(每次都是一筆付費呼叫) */
const MAX_RETRY_PER_SEQ = 3;
/* 有些語言包完全不輸出標點(實測 cmn_en 只給空格分詞),只靠標點斷句會讓
   整場累積成一大句、到收尾才吐出來——等於沒有增量字幕。所以另加長度與時間
   兩道保險:超過字數、或殘句擱太久,就當一句切出去。 */
const MAX_PENDING_CHARS = 48;
const PENDING_STALE_MS = 6_000;

/** 一場進行中的 session。chargeStart 是它已經燒掉、但還沒寫回 QuotaCounter 的起點 */
type LiveSession = { chargeStart: number };

export class SessionRelay {
  // DO 介面要求 (state, env);本 DO 無持久狀態(配額在 QuotaCounter),state 不用
  constructor(_state: DurableObjectState, private env: Env) {}

  /* 進行中的 session。DO 是 per-email 的,但 pipe() 是 fire-and-forget,
     所以同一個實例可以同時掛著任意多條 WS —— 這個 Set 就是把「進行中」
     這件事變成可以判斷的狀態。只存在記憶體:DO 被回收代表沒有連線在跑,
     一起消失是對的。 */
  private live = new Set<LiveSession>();

  /** 進行中的 session 已經燒掉、但還沒寫回 QuotaCounter 的秒數總和。
   *  少了這一項,額度檢查看到的永遠是「上一場結束時」的數字。 */
  private liveSeconds(): number {
    const now = Date.now();
    let s = 0;
    for (const e of this.live) if (e.chargeStart) s += (now - e.chargeStart) / 1000;
    return s;
  }

  private quotaStub(email: string) {
    return this.env.QUOTA.get(this.env.QUOTA.idFromName(email));
  }
  private async usedToday(email: string): Promise<number> {
    try {
      const u = await (await this.quotaStub(email).fetch('https://do/usage')).json<Usage>();
      return u.seconds;
    } catch {
      return 0;
    }
  }

  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    if (req.headers.get('Upgrade') !== 'websocket') return new Response('expected websocket', { status: 426 });

    const email = url.searchParams.get('email') || '';
    const limit = Number(url.searchParams.get('limit') || 0);
    const pack = url.searchParams.get('pack') || '';
    const lang = url.searchParams.get('lang') || 'ja';

    // 併發閘門。扣款要等 session 結束才寫回 QuotaCounter,所以同一個 cookie
    // 同時開 N 條時,每條進門讀到的 used 都是同一個舊值 —— 沒有這一段,
    // 每日額度實際上等於「額度 × 並行數」。
    if (this.live.size >= MAX_LIVE_SESSIONS) {
      return Response.json(
        { error: 'too_many_sessions', liveSessions: this.live.size },
        { status: 429 },
      );
    }

    // 配額保險絲:進門先擋(0 = 無上限);session 中另有 watchdog 逐秒檢查。
    // 已寫回的 used 之外,還要加上進行中 session 尚未結算的秒數。
    const used = await this.usedToday(email);
    if (limit > 0 && used + this.liveSeconds() >= limit) {
      return Response.json(
        { error: 'quota_exceeded', usedSeconds: Math.round(used + this.liveSeconds()), limitSeconds: limit },
        { status: 429 },
      );
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    // 關鍵:不設的話 Workers 會把二進位訊息以 Blob 交付,型別檢查全部落空、
    // 每一框音訊被靜靜丟掉(SM 連得上、收得到 EndOfStream,就是一個字都沒有)。
    server.binaryType = 'arraybuffer';
    // 從這一刻起算「進行中」。pipe() 設定完就 resolve(session 之後靠事件推進),
    // 所以不能用 .finally() 移除 —— 正常路徑一律由 finish() 負責移除,
    // 這裡的 catch 只處理「還沒接上 finish 就拋錯」的設定期失敗。
    const entry: LiveSession = { chargeStart: 0 };
    this.live.add(entry);
    this.pipe(server, { email, limit, used, pack, lang, entry }).catch(e => {
      this.live.delete(entry);
      try {
        server.send(JSON.stringify({ type: 'error', message: String(e?.message ?? e).slice(0, 200) }));
        server.close();
      } catch {}
    });
    return new Response(null, { status: 101, webSocket: client });
  }

  private async pipe(
    client: WebSocket,
    {
      email,
      limit,
      used,
      pack,
      lang,
      entry,
    }: {
      email: string;
      limit: number;
      used: number;
      pack: string;
      lang: string;
      entry: LiveSession;
    },
  ) {
    // fail-closed:金鑰缺就明講,不連上游、不計費
    if (!this.env.SPEECHMATICS_API_KEY) throw new Error('SPEECHMATICS_API_KEY 未設定(wrangler secret put)');
    if (!this.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY 未設定(wrangler secret put)');

    // 場景包隨 session config 送出(Speechmatics 限制:中途不可換,換包 = 重連)
    const smLang = resolveLang(lang).code;
    // 包的語言必須與本場語言相符才掛——把日文假名詞條餵給韓文模型只會添亂
    const loaded = pack ? await readPack(this.env, pack) : null;
    const vocabPack = loaded && (loaded.lang || 'ja') === smLang ? loaded : null;
    const vocab = (vocabPack?.entries ?? []).slice(0, 1000);

    // 上游:Speechmatics RT。需要 Authorization header → 走 fetch-upgrade
    //(Workers 原生 new WebSocket() 不能帶自訂 header)。
    const resp = await fetch(SM_URL, {
      headers: { Upgrade: 'websocket', Authorization: `Bearer ${this.env.SPEECHMATICS_API_KEY}` },
    });
    const upstream = resp.webSocket;
    if (!upstream) throw new Error(`Speechmatics 連線失敗(HTTP ${resp.status})`);
    const up: WebSocket = upstream; // pushAudio 是函式宣告,拿不到上面的 narrowing
    upstream.accept();

    const hardCapMs = Number(this.env.SESSION_HARD_CAP_S || 3600) * 1000;
    const t0 = Date.now();
    let chargeStart = 0; // RecognitionStarted 起錶;0 = 還沒開始計費
    let closed = false;
    let gotFinal = false;
    let ended = false; // 收到 client {type:"end"} 之後只等 SM 收尾
    let lastFrameAt = Date.now();
    let audioSeq = 0;
    let lastVoicedAt = Date.now(); // 最後一次伺服器端 RMS 高過門檻的時刻
    let rmsSum = 0; // 伺服器端實收音量的滑動平均(近 30 框)
    let rmsN = 0;
    let sentSeq = 0;
    let pending = ''; // 已定稿但還沒斷句的文字
    let pendingT = 0;
    let pendingSince = 0; // 殘句開始累積的時刻(無標點語言的時間保險)
    const sentences = new Map<number, string>(); // seq → 原文(retryZh 用)
    let inflight = 0; // 未完成的翻譯呼叫數(收尾要等它們落地)
    /* 花錢保險絲(教訓:無人看管 × 花錢 × 重試 × 失敗不可見 = 事故)。
       秒數配額擋不住 token 花費——一場句子被切很碎的導覽,秒數沒超標但呼叫數暴增。
       SESSION_TOKEN_CAP=0 或未設 = 不設上限,但**照樣計數**(看得見才管得住)。 */
    const tokenCap = Number(this.env.SESSION_TOKEN_CAP || 0);
    let spentTokens = 0;
    let spentCalls = 0;
    let tokenCapHit = false;
    const retries = new Map<number, number>(); // seq → 已重試次數

    const send = (msg: unknown) => {
      try {
        client.send(JSON.stringify(msg));
      } catch {}
    };

    const translate = (seq: number, text: string) => {
      if (tokenCapHit) return void send({ type: 'zhError', forSeq: seq });
      inflight++;
      translateSentence(this.env, text, smLang)
        .then(({ zh, usage }) => {
          // 花費即時可視:thoughts 也算進去(它以輸出價計費)
          spentTokens += usage.total;
          spentCalls++;
          if (tokenCap && spentTokens >= tokenCap) {
            tokenCapHit = true;
            send({ type: 'error', message: '本場翻譯 token 已達上限,後續只會有原文字幕' });
          }
          send({ type: 'zh', forSeq: seq, text: zh });
        })
        .catch(() => send({ type: 'zhError', forSeq: seq }))
        .finally(() => inflight--);
    };

    /** 定稿累積 → 完整句切出去翻譯,殘句留在 pending */
    const onFinal = (text: string, t: number) => {
      pending += text;
      pendingT = t;
      const parts = pending.split(SENT_END);
      pending = parts.length && !/[。!?!?]$/.test(parts[parts.length - 1]) ? parts.pop()! : '';
      for (const s of parts) {
        const sentence = s.trim();
        if (!sentence) continue;
        gotFinal = true;
        const seq = ++sentSeq;
        sentences.set(seq, sentence);
        send({ type: 'final', seq, t, text: sentence });
        translate(seq, sentence);
      }
      // 無標點語言:字數到了就切,不然永遠等不到句號
      if (pending.length >= MAX_PENDING_CHARS) {
        flushPending();
      } else {
        if (pending && !pendingSince) pendingSince = Date.now();
        if (!pending) pendingSince = 0;
        // 殘句仍以 partial 樣式顯示,不留白
        send({ type: 'partial', t, text: pending });
      }
    };

    const flushPending = () => {
      const sentence = pending.trim();
      pending = '';
      pendingSince = 0;
      if (!sentence) return;
      gotFinal = true;
      const seq = ++sentSeq;
      sentences.set(seq, sentence);
      send({ type: 'final', seq, t: pendingT, text: sentence });
      translate(seq, sentence);
    };

    const finish = async (reason: string, charge = true) => {
      if (closed) return;
      closed = true;
      clearInterval(watchdog);
      try {
        await settle(reason, charge);
      } finally {
        // 一定要等結算完才退出「進行中」:下面等譯文收尾最多 8 秒、還要寫回
        // QuotaCounter,這段期間這一場的秒數還沒落地,得繼續被 liveSeconds()
        // 算進去,不然同時間進來的另一場會少看到它。放 finally 是為了
        // 「結算途中拋錯也不會留下永遠佔著名額的殭屍」。
        this.live.delete(entry);
      }
    };

    const settle = async (reason: string, charge: boolean) => {
      flushPending();
      // 給未落地的譯文最多 5 秒收尾(逐句 hop 通常 1–2 秒)
      const grace = Date.now() + 8000; // 長句翻譯可能要 5 秒以上
      while (inflight > 0 && Date.now() < grace) await new Promise(r => setTimeout(r, 100));
      // 計費:SM 連線中的秒數。
      // ⚠ 這裡原本還要求 gotFinal(整場至少切出一句定稿)才計費。那個豁免的本意是
      // 「上游自己失敗的那一場不該算在使用者頭上」,但它被套到了**所有**結束原因,
      // 於是 idle / hard-cap / 使用者關頁面 只要沒講出一句話就完全不計費 ——
      // 而 Speechmatics 是按連線秒數收費的,靜音一樣要付錢。結果是一個免額度、
      // 可無限重複的迴圈(送靜音 PCM 撐到 hard cap,當日帳面永遠是 0)。
      // 豁免現在只留給真正的上游失敗:finish('sm-error', gotFinal) 用 charge 參數帶進來。
      const seconds = chargeStart ? (Date.now() - chargeStart) / 1000 : 0;
      const charged = charge && chargeStart > 0;
      // token 用量與秒數分開判斷:就算這場不計秒(上游失敗,或還沒開始計費),
      // 已經打出去的翻譯呼叫還是花了錢,一定要記進當日帳
      if (charged || spentTokens) {
        await this.quotaStub(email)
          .fetch('https://do/add', {
            method: 'POST',
            body: JSON.stringify({ seconds: charged ? seconds : 0, tokens: spentTokens, calls: spentCalls }),
          })
          .catch(() => {});
      }
      // email 遮罩。observability.enabled 是 true,所以這一行會進 Cloudflare
      // Workers Logs(預設保留數日)。這與「內容零留存」不衝突(沒有字幕內容),
      // 但完整 email 是 PII,而這行的用途只是「哪一個使用者、燒了多少」——
      // 前三碼加網域就足以在幾個受邀使用者裡辨識,不需要留完整地址。
      console.log(`[relay] ${maskEmail(email)} ${reason} ${Math.round(seconds)}s 翻譯 ${spentCalls} 句 / ${spentTokens} tokens`);
      send({
        type: 'done',
        reason,
        seconds: Math.round(seconds),
        usedSeconds: Math.round(used + (charged ? seconds : 0)),
        limitSeconds: limit,
        charged,
      });
      try {
        upstream.close();
      } catch {}
      try {
        client.close();
      } catch {}
    };

    // 熔斷 watchdog:hard cap 60 分鐘、WS 靜默 30 秒收斂、額度用盡即斷。
    // 順帶每秒回報伺服器端實收音訊統計:客戶端音量條會動、但這裡 rms≈0,
    // 就代表傳輸把音訊弄壞了(而不是麥克風沒收到)——沒這個數字分不出來。
    let tick = 0;
    const watchdog = setInterval(() => {
      if (audioSeq) send({ type: 'stat', frames: audioSeq, rms: Math.round(rmsSum / Math.max(rmsN, 1)) });
      // 殘句擱太久就切(無標點語言的第二道保險)
      if (pendingSince && Date.now() - pendingSince > PENDING_STALE_MS) flushPending();
      if (Date.now() - t0 > hardCapMs) return void finish('hard-cap');
      if (!ended && Date.now() - lastFrameAt > IDLE_MS) return void finish('idle');
      // 靜音熔斷:框一直在來(所以 idle 不會觸發)但伺服器端量到的一直是靜音。
      // RMS 本來就算好了(下面 pushAudio),原本只拿來顯示,沒有拿來熔斷。
      if (chargeStart && !ended && Date.now() - lastVoicedAt > SILENCE_MS) {
        send({ type: 'error', message: '偵測不到聲音,已自動結束這一場' });
        return void finish('silence');
      }
      // 額度檢查要含**所有**進行中 session 的未結算秒數,不能只看自己這一場
      if (chargeStart && limit > 0 && used + this.liveSeconds() >= limit) {
        send({ type: 'error', message: '今日聽譯額度已用完,台灣時間早上 8 點重置' });
        return void finish('quota');
      }
      // 每 30 秒重讀當日累計:同帳號的另一場結束後會寫回 QuotaCounter,
      // 只靠進門那一次的快照會少算。
      if (++tick % 30 === 0) {
        void this.usedToday(email)
          .then(v => {
            used = v;
          })
          .catch(() => {});
      }
    }, 1000);

    upstream.addEventListener('message', ev => {
      if (typeof ev.data !== 'string') return; // SM 下行皆為 JSON 文字
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      switch (msg.message) {
        case 'RecognitionStarted':
          chargeStart = Date.now(); // 連線即計(PRD §5);Error 情境見 finish
          entry.chargeStart = chargeStart; // 讓其他並行 session 的額度檢查看得到這一場
          send({ type: 'ready', pack: vocabPack ? pack : null, packName: vocabPack?.name ?? null, vocabCount: vocab.length });
          return;
        case 'AddPartialTranscript':
          send({ type: 'partial', t: msg.metadata?.end_time ?? 0, text: pending + (msg.metadata?.transcript ?? '') });
          return;
        case 'AddTranscript':
          onFinal(msg.metadata?.transcript ?? '', msg.metadata?.end_time ?? 0);
          return;
        case 'EndOfTranscript':
          void finish('end-of-transcript');
          return;
        case 'Error':
          // SM 4xx 直接回報不重試(PRD §5);整場沒定稿就不計費
          send({ type: 'error', message: `Speechmatics:${msg.type ?? ''} ${msg.reason ?? ''}`.trim().slice(0, 200) });
          void finish('sm-error', gotFinal);
          return;
      }
    });
    upstream.addEventListener('close', () => void finish('upstream-closed'));
    upstream.addEventListener('error', () => void finish('upstream-error'));

    client.addEventListener('message', ev => {
      if (typeof ev.data === 'string') {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === 'end' && !ended) {
            ended = true;
            upstream.send(JSON.stringify({ message: 'EndOfStream', last_seq_no: audioSeq }));
          } else if (m.type === 'retryZh' && sentences.has(Number(m.seq))) {
            // 每句重試上限:點一次算一次錢,不設限等於把保險絲交給使用者的手指
            const seq = Number(m.seq);
            const n = retries.get(seq) ?? 0;
            if (n >= MAX_RETRY_PER_SEQ) {
              send({ type: 'error', message: `這一句已重試 ${MAX_RETRY_PER_SEQ} 次,請改用其他句子或重開一場` });
            } else {
              retries.set(seq, n + 1);
              translate(seq, sentences.get(seq)!);
            }
          }
        } catch {}
        return;
      }
      // 二進位 = PCM 框。上面已設 binaryType='arraybuffer';Blob 分支是後援
      // (執行環境若未採納設定),用序列化 promise 鏈轉換以免音框亂序。
      let bytes: Uint8Array;
      const d: any = ev.data;
      if (d instanceof ArrayBuffer) bytes = new Uint8Array(d);
      else if (ArrayBuffer.isView(d)) bytes = new Uint8Array(d.buffer, d.byteOffset, d.byteLength);
      else if (typeof d?.arrayBuffer === 'function') {
        blobChain = blobChain.then(async () => {
          const ab = await d.arrayBuffer();
          pushAudio(new Uint8Array(ab));
        });
        return;
      } else return;
      pushAudio(bytes);
    });

    let blobChain: Promise<void> = Promise.resolve();
    function pushAudio(bytes: Uint8Array) {
      if (bytes.length === 0 || bytes.length > 16000) return; // 100ms@16k PCM16 = 3200 bytes,留裕度
      lastFrameAt = Date.now();
      audioSeq++;
      // 以伺服器實收的位元組算 RMS(每 3 框抽一次,成本可忽略)。
      // 這是「送到雲端的到底是不是語音」唯一的伺服器端證據。
      if (audioSeq % 3 === 0) {
        const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        let s = 0;
        const n = bytes.byteLength >> 1;
        for (let i = 0; i < n; i += 4) s += dv.getInt16(i * 2, true) ** 2; // 每 4 個樣本取一個
        const r = Math.sqrt(s / Math.max(Math.ceil(n / 4), 1));
        if (r >= SILENCE_RMS) lastVoicedAt = Date.now();
        rmsSum = rmsN >= 30 ? rmsSum * (29 / 30) + r : rmsSum + r;
        rmsN = Math.min(rmsN + 1, 30);
      }
      try {
        up.send(bytes);
      } catch {}
    }
    client.addEventListener('close', () => {
      if (!ended) void finish('client-closed');
      else void finish('client-closed-after-end');
    });
    client.addEventListener('error', () => void finish('client-error'));

    // 監聽都掛好後才送 StartRecognition(exp1 run_speechmatics_rt.py 同款 config)
    upstream.send(
      JSON.stringify({
        message: 'StartRecognition',
        audio_format: { type: 'raw', encoding: 'pcm_s16le', sample_rate: 16000 },
        transcription_config: {
          language: smLang,
          operating_point: 'enhanced',
          enable_partials: true,
          max_delay: 2.0,
          ...(vocab.length ? { additional_vocab: vocab } : {}),
        },
      }),
    );
  }
}
