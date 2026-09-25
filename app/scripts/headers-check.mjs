#!/usr/bin/env node
// 安全標頭是不是**每一個回應**都有 —— 對著 worker/index.ts 的 fetch() 實跑。
//
//   node app/scripts/headers-check.mjs
//
// 為什麼要有這支:withSec() 先前只包 env.ASSETS.fetch(),所以 /api/* 的 JSON、
// /auth/* 的 302 與 canonical 的 301 一條標頭都沒有,整站也沒有 HSTS。
//
// 驗證範圍:只驗標頭有沒有送出去,用假的 env。不驗 CSP 的內容對不對,
// 那要真瀏覽器(見 sukemu 與 snapdeck 的做法)。

// worker/ 用的是無副檔名的相對 import(TS 的 bundler 解析),Node 直接載不了。
// 先用 esbuild 打包成一個檔再 import —— 跟 wrangler 部署時做的事一樣。
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { mkdtemp } from 'node:fs/promises';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(await mkdtemp(path.join(tmpdir(), 'kikemu-')), 'worker.mjs');
await build({
  entryPoints: [path.join(here, '../worker/index.ts')],
  bundle: true,
  format: 'esm',
  platform: 'neutral',
  outfile: out,
  logLevel: 'silent',
});
const worker = (await import(out)).default;

const WANT = [
  'content-security-policy',
  'x-frame-options',
  'x-content-type-options',
  'referrer-policy',
  'strict-transport-security',
];

const env = {
  ASSETS: { fetch: async () => new Response('<html>x</html>', { headers: { 'content-type': 'text/html' } }) },
  SESSION_SECRET: 'x'.repeat(64),
  CONFIG: { get: async () => null, put: async () => {} },
};
const ctx = { waitUntil() {}, passThroughOnException() {} };

const CASES = [
  ['靜態頁',            'https://kikemu.ai-apps.work/',            {}],
  ['/api/* 未登入',     'https://kikemu.ai-apps.work/api/config',  {}],
  ['/api/login 非本機', 'https://kikemu.ai-apps.work/api/login',   { method: 'POST' }],
  ['/auth/logout 的 302', 'https://kikemu.ai-apps.work/auth/logout', {}],
  ['不存在的路徑',      'https://kikemu.ai-apps.work/nope',        {}],
];

let bad = 0;
for (const [name, url, init] of CASES) {
  const res = await worker.fetch(new Request(url, init), env, ctx);
  const missing = WANT.filter(h => !res.headers.get(h));
  if (missing.length) bad++;
  console.log(
    `${missing.length ? '✗' : '✓'} ${name.padEnd(22)} ${String(res.status).padEnd(5)} ` +
    (missing.length ? '缺:' + missing.join(', ') : '五個標頭都在'),
  );
}
console.log(bad ? `\n${bad} 個案例失敗` : `\n全部 ${CASES.length} 個案例通過`);
process.exit(bad ? 1 : 0);
