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
import type { Usage } from './quota';

const SM_URL = 'https://eu2.rt.speechmatics.com/v2';
/** WS 靜默 30 秒自動收斂(PRD §5 熔斷) */
const IDLE_MS = 30_000;
/** 句尾標點:定稿句切分依據(逐句觸發翻譯,非整場重譯) */
const SENT_END = /(?<=[。!?!?])/;

export class SessionRelay {
  // DO 介面要求 (state, env);本 DO 無持久狀態(配額在 QuotaCounter),state 不用
  constructor(_state: DurableObjectState, private env: Env) {}

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

    // 配額保險絲:進門先擋(0 = 無上限);session 中另有 watchdog 逐秒檢查
    const used = await this.usedToday(email);
    if (limit > 0 && used >= limit) {
      return Response.json(
        { error: 'quota_exceeded', usedSeconds: used, limitSeconds: limit },
        { status: 429 },
      );
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    this.pipe(server, { email, limit, used, pack }).catch(e => {
      try {
        server.send(JSON.stringify({ type: 'error', message: String(e?.message ?? e).slice(0, 200) }));
        server.close();
      } catch {}
    });
    return new Response(null, { status: 101, webSocket: client });
  }

  private async pipe(
    client: WebSocket,
    { email, limit, used, pack }: { email: string; limit: number; used: number; pack: string },
  ) {
    // fail-closed:金鑰缺就明講,不連上游、不計費
    if (!this.env.SPEECHMATICS_API_KEY) throw new Error('SPEECHMATICS_API_KEY 未設定(wrangler secret put)');
    if (!this.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY 未設定(wrangler secret put)');

    // 場景包隨 session config 送出(Speechmatics 限制:中途不可換,換包 = 重連)
    const vocabPack = pack ? await readPack(this.env, pack) : null;
    const vocab = (vocabPack?.entries ?? []).slice(0, 1000);

    // 上游:Speechmatics RT。需要 Authorization header → 走 fetch-upgrade
    //(Workers 原生 new WebSocket() 不能帶自訂 header)。
    const resp = await fetch(SM_URL, {
      headers: { Upgrade: 'websocket', Authorization: `Bearer ${this.env.SPEECHMATICS_API_KEY}` },
    });
    const upstream = resp.webSocket;
    if (!upstream) throw new Error(`Speechmatics 連線失敗(HTTP ${resp.status})`);
    upstream.accept();

    const hardCapMs = Number(this.env.SESSION_HARD_CAP_S || 3600) * 1000;
    const t0 = Date.now();
    let chargeStart = 0; // RecognitionStarted 起錶;0 = 還沒開始計費
    let closed = false;
    let gotFinal = false;
    let ended = false; // 收到 client {type:"end"} 之後只等 SM 收尾
    let lastFrameAt = Date.now();
    let audioSeq = 0;
    let sentSeq = 0;
    let pending = ''; // 已定稿但還沒斷句的文字
    let pendingT = 0;
    const sentences = new Map<number, string>(); // seq → 原文(retryZh 用)
    let inflight = 0; // 未完成的翻譯呼叫數(收尾要等它們落地)

    const send = (msg: unknown) => {
      try {
        client.send(JSON.stringify(msg));
      } catch {}
    };

    const translate = (seq: number, text: string) => {
      inflight++;
      translateSentence(this.env, text)
        .then(zh => send({ type: 'zh', forSeq: seq, text: zh }))
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
      // 殘句仍以 partial 樣式顯示,不留白
      send({ type: 'partial', t, text: pending });
    };

    const flushPending = () => {
      const sentence = pending.trim();
      pending = '';
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
      flushPending();
      // 給未落地的譯文最多 5 秒收尾(逐句 hop 通常 1–2 秒)
      const grace = Date.now() + 5000;
      while (inflight > 0 && Date.now() < grace) await new Promise(r => setTimeout(r, 100));
      // 計費:SM 連線中的秒數;整場連一句定稿都沒有的失敗 session 不扣額度
      const seconds = chargeStart ? (Date.now() - chargeStart) / 1000 : 0;
      const charged = charge && chargeStart > 0 && gotFinal;
      if (charged) {
        await this.quotaStub(email)
          .fetch('https://do/add', { method: 'POST', body: JSON.stringify({ seconds }) })
          .catch(() => {});
      }
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

    // 熔斷 watchdog:hard cap 60 分鐘、WS 靜默 30 秒收斂、額度用盡即斷
    const watchdog = setInterval(() => {
      if (Date.now() - t0 > hardCapMs) return void finish('hard-cap');
      if (!ended && Date.now() - lastFrameAt > IDLE_MS) return void finish('idle');
      if (chargeStart && limit > 0 && used + (Date.now() - chargeStart) / 1000 >= limit) {
        send({ type: 'error', message: '今日聽譯額度已用完,台灣時間早上 8 點重置' });
        return void finish('quota');
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
            translate(Number(m.seq), sentences.get(Number(m.seq))!);
          }
        } catch {}
        return;
      }
      // 二進位 = PCM 框。非 ArrayBuffer 型別顯式轉換(manemu 踩過 length 0 的坑)
      let bytes: Uint8Array;
      const d: any = ev.data;
      if (d instanceof ArrayBuffer) bytes = new Uint8Array(d);
      else if (ArrayBuffer.isView(d)) bytes = new Uint8Array(d.buffer, d.byteOffset, d.byteLength);
      else return;
      if (bytes.length === 0 || bytes.length > 16000) return; // 100ms@16k PCM16 = 3200 bytes,留裕度
      lastFrameAt = Date.now();
      audioSeq++;
      try {
        upstream.send(bytes);
      } catch {}
    });
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
          language: 'ja',
          operating_point: 'enhanced',
          enable_partials: true,
          max_delay: 2.0,
          ...(vocab.length ? { additional_vocab: vocab } : {}),
        },
      }),
    );
  }
}
