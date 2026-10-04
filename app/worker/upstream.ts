/* 聽寫上游的轉接層:relay 只認得這個介面,不管對面是 Speechmatics 還是 Gemini。

   為什麼要拆:模式選單(worker/modes.ts)要在同一條 relay 管線上換耳朵,
   而導覽模式必須跟 2026-10 之前**行為完全相同**——exp1 的數字是在那條路徑上量的。
   所以 SM 轉接器是把 relay.ts 原本的程式碼原樣搬過來,只多一個 diarize 分支;
   guide 模式送出的 StartRecognition 與過去逐欄相同。 */

import type { Env } from './index';
import type { VocabEntry } from './vocab';
import { b64, openGeminiLive, type LiveHandlers } from './gemini';

export type UpEvents = {
  /** 上游開始收音(SM 的 RecognitionStarted;Gemini 是 setupComplete 之後)——計費起點 */
  ready: () => void;
  /** 暫定:整句目前的假設(不是增量) */
  partial: (text: string, t: number) => void;
  /** 定稿片段。speaker 只在對話模式有(SM 的 S1/S2…) */
  final: (text: string, t: number, speaker?: string) => void;
  /** 收尾完成(client 送 end 之後,上游吐完最後的定稿) */
  ended: () => void;
  /** 上游回報的致命錯誤,訊息已整理成可給使用者看的字串 */
  error: (message: string) => void;
  closed: (why: string) => void;
};

export type Upstream = {
  name: 'sm' | 'gemini';
  /** 監聽都掛好之後才呼叫(SM:送 StartRecognition) */
  start: () => void;
  push: (bytes: Uint8Array) => void;
  end: (lastSeq: number) => void;
  close: () => void;
};

/* ── Speechmatics ─────────────────────────────────────────────── */

const SM_URL = 'https://eu2.rt.speechmatics.com/v2';

/** 對話模式:一則 AddTranscript 裡若有兩位以上講者,依 results 的 speaker 切段。
 *  只有一位講者時直接用 SM 自己排好的 metadata.transcript(與導覽模式同一個字串)。
 *  'UU' 是 SM 的「認不出是誰」,不當成換人。 */
function splitBySpeaker(msg: any, noSpace: boolean): { text: string; t: number; speaker?: string }[] {
  const res: any[] = msg.results ?? [];
  const named = (r: any): string | undefined => {
    const s = r?.alternatives?.[0]?.speaker;
    return s && s !== 'UU' ? s : undefined;
  };
  const speakers = new Set(res.map(named).filter(Boolean));
  if (speakers.size <= 1) {
    return [{ text: msg.metadata?.transcript ?? '', t: msg.metadata?.end_time ?? 0, speaker: [...speakers][0] as string | undefined }];
  }
  type Seg = { text: string; t: number; speaker?: string };
  const out: Seg[] = [];
  for (const r of res) {
    const a = r.alternatives?.[0];
    if (!a) continue;
    const last: Seg | undefined = out[out.length - 1];
    const sp: string | undefined = named(r) ?? last?.speaker;
    const seg: Seg = !last || (sp && sp !== last.speaker) ? { text: '', t: 0, speaker: sp } : last;
    if (seg !== last) out.push(seg);
    const sep = !seg.text || noSpace || r.type === 'punctuation' ? '' : ' ';
    seg.text += sep + a.content;
    seg.t = r.end_time ?? seg.t;
  }
  return out;
}

export async function openSpeechmatics(
  env: Env,
  o: { lang: string; vocab: VocabEntry[]; diarize: boolean; noSpace: boolean },
  ev: UpEvents,
): Promise<Upstream> {
  // 需要 Authorization header → 走 fetch-upgrade(Workers 原生 new WebSocket() 不能帶自訂 header)
  const resp = await fetch(SM_URL, {
    headers: { Upgrade: 'websocket', Authorization: `Bearer ${env.SPEECHMATICS_API_KEY}` },
  });
  const ws = resp.webSocket;
  if (!ws) throw new Error(`Speechmatics 連線失敗(HTTP ${resp.status})`);
  ws.accept();

  ws.addEventListener('message', e => {
    if (typeof e.data !== 'string') return; // SM 下行皆為 JSON 文字
    let msg: any;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    switch (msg.message) {
      case 'RecognitionStarted':
        return ev.ready();
      case 'AddPartialTranscript':
        return ev.partial(msg.metadata?.transcript ?? '', msg.metadata?.end_time ?? 0);
      case 'AddTranscript':
        if (!o.diarize) return ev.final(msg.metadata?.transcript ?? '', msg.metadata?.end_time ?? 0);
        for (const seg of splitBySpeaker(msg, o.noSpace)) ev.final(seg.text, seg.t, seg.speaker);
        return;
      case 'EndOfTranscript':
        return ev.ended();
      case 'Error':
        // SM 4xx 直接回報不重試(PRD §5)
        return ev.error(`Speechmatics:${msg.type ?? ''} ${msg.reason ?? ''}`.trim().slice(0, 200));
    }
  });
  ws.addEventListener('close', () => ev.closed('upstream-closed'));
  ws.addEventListener('error', () => ev.closed('upstream-error'));

  return {
    name: 'sm',
    start: () =>
      // exp1 run_speechmatics_rt.py 同款 config。diarize=false 時與 2026-10 之前逐欄相同。
      // 對話模式:prefer_current_speaker 降低「同一人被誤判成換人」——誤判換人會多切出半句,
      // 那正是街訪逐字稿裡最傷譯文的錯。max_speakers 不設(官方預設),準確度未量測。
      ws.send(
        JSON.stringify({
          message: 'StartRecognition',
          audio_format: { type: 'raw', encoding: 'pcm_s16le', sample_rate: 16000 },
          transcription_config: {
            language: o.lang,
            operating_point: 'enhanced',
            enable_partials: true,
            max_delay: 2.0,
            ...(o.vocab.length ? { additional_vocab: o.vocab } : {}),
            ...(o.diarize ? { diarization: 'speaker', speaker_diarization_config: { prefer_current_speaker: true } } : {}),
          },
        }),
      ),
    push: bytes => {
      try {
        ws.send(bytes);
      } catch {}
    },
    end: lastSeq => {
      try {
        ws.send(JSON.stringify({ message: 'EndOfStream', last_seq_no: lastSeq }));
      } catch {}
    },
    close: () => {
      try {
        ws.close();
      } catch {}
    },
  };
}

/* ── Gemini 3.5 Transcribe Live ──────────────────────────────────
   官方文件(live-api/live-transcribe,2026-10-04 讀)與 handoff-v12 探針的事實:
   · 只收 responseModalities TEXT
   · 兩層轉寫:interimInputTranscription(暫定,整句假設)+ inputTranscription(定稿,停頓時)
   · customVocabulary ≤ 1000(官方建議 ≤ 100),**沒有讀音欄位**
   · 單場**最長 10 分鐘** → 9 分鐘主動換線;收到 goAway 也換
   · 即時**不支援語者分離**、**沒有詞級時間戳**(所以 t 一律給 0,前端不算延遲)
   · 不回 usageMetadata → 花費以音訊秒數記(relay 收尾的 log) */

const TRANSCRIBE_MODEL = 'models/gemini-3.5-transcribe-live';
const ROTATE_MS = 9 * 60 * 1000;
const END_QUIET_MS = 4000;
const END_CAP_MS = 12000;
const MAX_RECONNECTS = 5;

export async function openGeminiTranscribe(
  env: Env,
  o: { codes: string[]; vocab: string[]; noSpace: boolean },
  ev: UpEvents,
): Promise<Upstream> {
  const setup = {
    setup: {
      model: TRANSCRIBE_MODEL,
      generationConfig: { responseModalities: ['TEXT'] },
      inputAudioTranscription: {
        languageCodes: o.codes,
        ...(o.vocab.length ? { customVocabulary: o.vocab.slice(0, 1000) } : {}),
      },
    },
  };
  // 暫定與定稿都會在日文字之間夾空白(「幻の 堺幕府」),日文把 CJK 之間的空白拿掉
  const tidy = (s: string) => (o.noSpace ? s.replace(/(?<=[^\x00-\x7F])\s+(?=[^\x00-\x7F])/g, '') : s);

  let gen = 0; // 目前這條 session 的代號;舊 session 的定稿照收,暫定與關閉事件不理
  let ending = false;
  let stopped = false;
  let rotating = false;
  let reconnects = 0;
  let lastMsgAt = Date.now();
  let rotateTimer: number | null = null;
  let endTimer: number | null = null;
  let endedFired = false;

  const handlers = (myGen: number): LiveHandlers => ({
    onMessage: m => {
      lastMsgAt = Date.now();
      const sc = m.serverContent ?? {};
      if (sc.interimInputTranscription?.text && myGen === gen) ev.partial(tidy(sc.interimInputTranscription.text), 0);
      if (sc.inputTranscription?.text) ev.final(tidy(sc.inputTranscription.text), 0);
      if (m.goAway && myGen === gen) void rotate('goAway');
    },
    onClose: (code, reason) => {
      if (myGen !== gen || stopped || ending) return;
      // 非預期關閉:先試著接回去,接不回去才報錯(rotate 失敗會走 ev.error)
      if (reconnects++ < MAX_RECONNECTS) void rotate(`closed ${code} ${String(reason).slice(0, 60)}`);
      else ev.error(`Gemini 連線一直被關閉(${code} ${String(reason).slice(0, 100)})`);
    },
  });

  let cur = await openGeminiLive(env, setup, handlers(0));

  async function rotate(why: string) {
    if (rotating || ending || stopped) return;
    rotating = true;
    try {
      const myGen = gen + 1;
      const next = await openGeminiLive(env, setup, handlers(myGen));
      const old = cur;
      gen = myGen;
      cur = next;
      try {
        old.send(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
      } catch {}
      // 舊 session 的最後一句定稿還可能在路上:留 15 秒再關
      setTimeout(() => {
        try {
          old.close(1000, 'rotated');
        } catch {}
      }, 15000);
      console.log(`[gemini-live] 換線(${why})`);
    } catch (e) {
      ev.error(`Gemini 換線失敗:${String((e as Error)?.message ?? e).slice(0, 120)}`);
    } finally {
      rotating = false;
    }
  }

  return {
    name: 'gemini',
    start: () => {
      ev.ready();
      rotateTimer = setInterval(() => void rotate('10 分鐘上限'), ROTATE_MS) as unknown as number;
    },
    push: bytes => {
      if (ending || stopped) return;
      try {
        cur.send(JSON.stringify({ realtimeInput: { audio: { data: b64(bytes), mimeType: 'audio/pcm;rate=16000' } } }));
      } catch {}
    },
    end: () => {
      if (ending || stopped) return;
      ending = true;
      clearInterval(rotateTimer);
      try {
        cur.send(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
      } catch {}
      const t0 = Date.now();
      // Gemini 沒有 EndOfTranscript:安靜 4 秒(或最多 12 秒)就當收尾完成
      endTimer = setInterval(() => {
        if (endedFired) return;
        if (Date.now() - lastMsgAt > END_QUIET_MS || Date.now() - t0 > END_CAP_MS) {
          endedFired = true;
          clearInterval(endTimer);
          ev.ended();
        }
      }, 500) as unknown as number;
    },
    close: () => {
      stopped = true;
      clearInterval(rotateTimer);
      clearInterval(endTimer);
      try {
        cur.close(1000, 'done');
      } catch {}
    },
  };
}
