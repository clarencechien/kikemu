#!/usr/bin/env node
/* 端到端探針:把「已知能被辨識」的音檔灌進 /ws,看 relay 回什麼。
 *
 * 為什麼要有這支:當畫面上「有收到音、卻一個字都不出來」時,可能是
 *   (a) 瀏覽器送出的音訊壞掉(重取樣、格式、音量)
 *   (b) relay → Speechmatics 這段壞掉(config、轉發、金鑰)
 *   (c) 講的內容本身 SM 認不出來(語言不對、太吵)
 * 這支繞過瀏覽器,直接用 exp1 實測拿到 0.836 專名召回率的原始音檔餵進去。
 *   出得來字 → (a) 或 (c),relay 是好的
 *   出不來字 → (b),問題在伺服器這側
 *
 * 用法:
 *   node scripts/probe-ws.mjs --host https://kikemu.ai-apps.work \
 *        --email you@example.com --wav ../corpus/conditions/hig01_A1__N0.wav [--pack higashiosaka]
 *
 * Google OIDC 已啟用時 /api/login 會 403,改用瀏覽器 DevTools 複製 kk_session:
 *   node scripts/probe-ws.mjs --host … --cookie "kk_session=…" --wav …
 */
import { readFileSync } from 'node:fs';
import WebSocket from 'ws'; // 需要自訂 header 帶 cookie,Node 全域 WebSocket 不支援

const arg = (k, d) => {
  const i = process.argv.indexOf(`--${k}`);
  return i > 0 ? process.argv[i + 1] : d;
};

const host = (arg('host') || 'http://localhost:8787').replace(/\/$/, '');
const wavPath = arg('wav');
const pack = arg('pack', '');
const email = arg('email');
let cookie = arg('cookie', '');

if (!wavPath) {
  console.error('缺 --wav。建議用 exp1 的已知良品:../corpus/conditions/hig01_A1__N0.wav');
  process.exit(2);
}

/** 讀 16-bit PCM WAV,回傳 {pcm: Buffer, rate, channels}。只支援 PCM fmt(我們的語料就是)。 */
function readWav(path) {
  const b = readFileSync(path);
  if (b.toString('ascii', 0, 4) !== 'RIFF' || b.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('不是 RIFF/WAVE 檔');
  }
  let pos = 12, rate = 0, channels = 1, bits = 16, pcm = null;
  while (pos + 8 <= b.length) {
    const id = b.toString('ascii', pos, pos + 4);
    const size = b.readUInt32LE(pos + 4);
    const body = pos + 8;
    if (id === 'fmt ') {
      channels = b.readUInt16LE(body + 2);
      rate = b.readUInt32LE(body + 4);
      bits = b.readUInt16LE(body + 14);
    } else if (id === 'data') {
      pcm = b.subarray(body, body + size);
    }
    pos = body + size + (size % 2);
  }
  if (!pcm) throw new Error('找不到 data chunk');
  if (bits !== 16) throw new Error(`只支援 16-bit,這檔是 ${bits}-bit`);
  return { pcm, rate, channels };
}

const rmsOf = pcm => {
  let s = 0;
  const n = pcm.length >> 1;
  for (let i = 0; i < n; i++) s += pcm.readInt16LE(i * 2) ** 2;
  return Math.sqrt(s / Math.max(n, 1));
};

const { pcm, rate, channels } = readWav(wavPath);
if (rate !== 16000 || channels !== 1) {
  console.error(`⚠ 這支探針只送 16kHz 單聲道原始 PCM(此檔 ${rate}Hz/${channels}ch)。`);
  console.error('  轉檔:ffmpeg -i in.wav -ac 1 -ar 16000 -sample_fmt s16 out.wav');
  process.exit(2);
}
const durS = pcm.length / 2 / 16000;
console.log(`音檔 ${wavPath}\n  ${durS.toFixed(1)}s / 16kHz mono / 全檔 RMS ${rmsOf(pcm).toFixed(0)}(講話約 500~3000)`);

if (!cookie && email) {
  const r = await fetch(`${host}/api/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  const setC = r.headers.get('set-cookie') || '';
  const m = /kk_session=[^;]+/.exec(setC);
  if (!m) {
    console.error(`登入失敗(${r.status}):${await r.text()}`);
    console.error('若已啟用 Google 登入,請改用 --cookie "kk_session=…"');
    process.exit(1);
  }
  cookie = m[0];
  console.log(`已登入 ${email}`);
}
if (!cookie) {
  console.error('缺認證:給 --email(開發登入)或 --cookie');
  process.exit(2);
}

const wsUrl = host.replace(/^http/, 'ws') + '/ws' + (pack ? `?pack=${encodeURIComponent(pack)}` : '');
console.log(`連線 ${wsUrl}\n`);
const ws = new WebSocket(wsUrl, { headers: { cookie, origin: host } });
ws.binaryType = 'arraybuffer';

const t0 = Date.now();
const ts = () => `[${((Date.now() - t0) / 1000).toFixed(1)}s]`;
let partials = 0, finals = 0, zhs = 0;
let ready = false;

ws.addEventListener('open', () => console.log(`${ts()} WS 已開`));

ws.addEventListener('message', async ev => {
  let m;
  try {
    m = JSON.parse(String(ev.data));
  } catch {
    return;
  }
  switch (m.type) {
    case 'ready':
      ready = true;
      console.log(`${ts()} ready(場景包:${m.packName ?? '無'} / ${m.vocabCount ?? 0} 詞)— 開始送音訊`);
      void feed();
      return;
    case 'partial':
      partials++;
      process.stdout.write(`\r${ts()} partial: ${m.text.slice(-60)}`.padEnd(90));
      return;
    case 'final':
      finals++;
      console.log(`\n${ts()} FINAL #${m.seq} (t=${m.t}) ${m.text}`);
      return;
    case 'zh':
      zhs++;
      console.log(`${ts()}   譯文 #${m.forSeq} ${m.text}`);
      return;
    case 'zhError':
      console.log(`${ts()}   ✗ 譯文失敗 #${m.forSeq}`);
      return;
    case 'error':
      console.log(`\n${ts()} ✗ ERROR: ${m.message}`);
      return;
    case 'done':
      console.log(`\n${ts()} done(${m.reason})・計費 ${m.seconds}s`);
      report();
      ws.close();
      return;
    default:
      console.log(`${ts()} ${JSON.stringify(m).slice(0, 160)}`);
  }
});

ws.addEventListener('close', e => {
  if (!ready) console.log(`${ts()} WS 關閉(code ${e.code})——沒等到 ready,多半是認證或額度問題`);
  process.exit(0);
});
ws.addEventListener('error', e => console.error(`${ts()} WS 錯誤`, e.message ?? e));

/** 以真實速度(1×)送 100ms 框,與 app 完全相同的節奏 */
async function feed() {
  const FRAME = 3200; // 100ms @ 16kHz PCM16
  let off = 0;
  let next = Date.now();
  while (off < pcm.length) {
    if (ws.readyState !== 1) return;
    ws.send(pcm.subarray(off, Math.min(off + FRAME, pcm.length)));
    off += FRAME;
    next += 100;
    await new Promise(r => setTimeout(r, Math.max(0, next - Date.now())));
  }
  console.log(`\n${ts()} 音訊送完(${(off / 2 / 16000).toFixed(1)}s),送出 end,等收尾…`);
  ws.send(JSON.stringify({ type: 'end' }));
  setTimeout(() => {
    if (ws.readyState === 1) {
      console.log(`${ts()} 收尾逾時`);
      report();
      ws.close();
    }
  }, 15000);
}

function report() {
  console.log('\n──────── 判讀 ────────');
  if (finals > 0) {
    console.log(`✓ relay → Speechmatics 正常(${finals} 句定稿 / ${partials} 次 partial / ${zhs} 句譯文)`);
    console.log('  → 問題在瀏覽器送出的音訊,或現場講的內容不是日文/太吵。');
    console.log('  → 下一步:用 app 錄同一段話,比較狀態列的音量與這裡的 RMS。');
  } else if (partials > 0) {
    console.log(`△ 有 partial 但沒有定稿(${partials} 次)——音訊有進去,句子沒收斂。`);
  } else {
    console.log('✗ 已知良品音檔也認不出來 → 問題在 relay/Speechmatics 這側,不是瀏覽器。');
    console.log('  檢查:SPEECHMATICS_API_KEY 是否有效、額度是否用盡、上方有無 ERROR 訊息。');
  }
  if (zhs === 0 && finals > 0) console.log('  另注意:有定稿但沒有譯文 → GEMINI_API_KEY 或翻譯 hop 有問題。');
}
