/* kikemu Worker:靜態資產(Workers Assets)+ Google OIDC + 白名單/配額 +
   Speechmatics relay(/ws → SessionRelay DO)+ 場景包 API。
   架構仿 sukemu worker/index.ts 與 manemu app/src/index.mjs:
   全 server-side OAuth、HMAC session cookie、R2 名單熱更新、
   DO 每人每日配額(秒)、/admin 管理頁(僅 ADMIN_EMAILS)。
   一次性部署步驟見 app/README.md。 */

import {
  addToWaitlist,
  cookieGet,
  cookieSet,
  randomHex,
  resolveUser,
  sessionFrom,
  sign,
  verify,
  verifyGoogleIdToken,
  type UserInfo,
} from './auth';
import { handleAdmin } from './admin';
import { listPacks } from './vocab';
import { DEFAULT_LANG, LANGS, PACK_LANG } from './langs';
import type { Usage } from './quota';
export { QuotaCounter } from './quota';
export { SessionRelay } from './relay';

export interface Env {
  ASSETS: Fetcher;
  CONFIG: R2Bucket;
  QUOTA: DurableObjectNamespace;
  RELAY: DurableObjectNamespace;
  // 秘密(wrangler secret put,絕不進 repo)
  SPEECHMATICS_API_KEY: string;
  GEMINI_API_KEY: string;
  // OIDC(未設 GOOGLE_CLIENT_ID → 開發用 Email 直登)
  GOOGLE_CLIENT_ID?: string;
  GOOGLE_CLIENT_SECRET?: string;
  SESSION_SECRET?: string;
  TURNSTILE_SITE_KEY?: string;
  TURNSTILE_SECRET?: string;
  // 名單與配額(每日秒數)
  ADMIN_EMAILS?: string;
  QUOTA_TIERS?: string;
  DEFAULT_TIER?: string;
  // 模型與熔斷
  TRANSLATE_MODEL?: string;
  SESSION_HARD_CAP_S?: string;
  // 安全
  CANONICAL_HOST?: string;
}

const json = (data: unknown, init?: ResponseInit) => Response.json(data, init);
const bad = (msg: string, status = 400) => json({ ok: false, error: msg }, { status });

const SEC_HEADERS: Record<string, string> = {
  // 無 unsafe-inline script(所有 JS 都在外部檔);Turnstile 需要 challenges.cloudflare.com;
  // Google Fonts 需要 fonts.googleapis.com(style)與 fonts.gstatic.com(font)
  'content-security-policy':
    "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'none'",
  'x-frame-options': 'DENY',
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'strict-origin-when-cross-origin',
};
const withSec = (res: Response, req?: Request) => {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(SEC_HEADERS)) r.headers.set(k, v);
  // connect-src 的 'self' 對 wss:// 的涵蓋範圍各家瀏覽器不一致(Safari 尤其),
  // 不明寫同源 wss 會讓 /ws 被靜默擋掉。明寫本站的 wss origin。
  if (req) {
    const wss = new URL(req.url).origin.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
    r.headers.set(
      'content-security-policy',
      SEC_HEADERS['content-security-policy'].replace("connect-src 'self'", `connect-src 'self' ${wss}`),
    );
  }
  return r;
};
const sameOrigin = (req: Request) => {
  const o = req.headers.get('origin');
  return !o || o === new URL(req.url).origin;
};

/* Turnstile 必須「site key + secret」兩者齊備才啟用(成對邏輯)。
   只設 secret 會讓前端渲染不出元件、後端卻要求 token —— 每次登入必定 403
   「challenge required」,而且沒有任何自救路徑(sukemu 實測踩過)。
   少了 site key 時挑戰本來就無法運作,關掉不是安全降級,是避免 100% 斷線。 */
const turnstileOn = (env: Env) => !!(env.TURNSTILE_SECRET && env.TURNSTILE_SITE_KEY);

const quotaStub = (env: Env, email: string) => env.QUOTA.get(env.QUOTA.idFromName(email));
const readUsage = async (env: Env, email: string): Promise<Usage> =>
  (await quotaStub(env, email).fetch('https://do/usage')).json<Usage>();

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    try {
      return await route(req, env, ctx);
    } catch (err) {
      // 登入流程的例外要導回登入頁(手機上停在裸 500 等於死路),其餘回 JSON
      console.error('[fetch]', err);
      if (new URL(req.url).pathname.startsWith('/auth/')) {
        return new Response(null, { status: 302, headers: { location: '/?err=auth' } });
      }
      return bad('伺服器錯誤', 500);
    }
  },
} satisfies ExportedHandler<Env>;

async function route(req: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
  const url = new URL(req.url);
  const p = url.pathname;

  // 縱深防禦:設了 CANONICAL_HOST 之後,workers.dev 等非正式 host 一律導回正式網域。
  // 值只認 hostname;誤填成完整 URL(https://host/)時自動剝掉 scheme/路徑/尾斜線——
  // 不剝的話轉址會組出 https://https//host// 這種壞掉的網址(實際發生過)。
  const canonical = (env.CANONICAL_HOST || '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/[/?#].*$/, '')
    .toLowerCase();
  if (canonical && url.hostname.toLowerCase() !== canonical && url.hostname !== 'localhost') {
    if (req.method === 'GET' && !p.startsWith('/api') && p !== '/ws') {
      return Response.redirect(`https://${canonical}${p}${url.search}`, 301);
    }
    return new Response('use canonical host', { status: 403 });
  }

  if (p === '/api/config') {
    if (env.TURNSTILE_SECRET && !env.TURNSTILE_SITE_KEY) {
      console.warn('[turnstile] 設了 TURNSTILE_SECRET 但沒設 TURNSTILE_SITE_KEY,已停用挑戰');
    }
    return json({
      mode: env.GOOGLE_CLIENT_ID ? 'oidc' : 'dev',
      turnstileSiteKey: turnstileOn(env) ? env.TURNSTILE_SITE_KEY : null,
      // 語言清單由伺服器給:加語言只改 worker/langs.ts 一處
      langs: LANGS.map(l => ({ code: l.code, label: l.label })),
      defaultLang: DEFAULT_LANG,
      packLang: PACK_LANG,
    });
  }

  /* ---------- OAuth(仿 sukemu/manemu) ---------- */
  if (p === '/auth/login') {
    if (!env.GOOGLE_CLIENT_ID) return bad('尚未設定 Google OIDC,開發模式請用 Email 登入', 404);
    // Turnstile:site key + secret 都設好才強制驗(POST + token);否則直通。
    // 驗證失敗一律導回登入頁帶 err,讓使用者看得到訊息也能重試——
    // 不要回裸 403 文字頁,手機上等於死路(sukemu 實測回報)。
    if (turnstileOn(env)) {
      if (req.method !== 'POST') return new Response(null, { status: 302, headers: { location: '/' } });
      if (!sameOrigin(req)) return new Response('forbidden', { status: 403 });
      const form = await req.formData().catch(() => null);
      const token = form?.get('cf-turnstile-response');
      if (!token) return new Response(null, { status: 302, headers: { location: '/?err=challenge' } });
      const vr = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          secret: env.TURNSTILE_SECRET!,
          response: String(token),
          remoteip: req.headers.get('cf-connecting-ip') || '',
        }),
      });
      if (!((await vr.json()) as { success: boolean }).success) {
        return new Response(null, { status: 302, headers: { location: '/?err=challenge' } });
      }
    }
    const state = randomHex();
    const nonce = randomHex();
    const stCookie = cookieSet('kk_oauth', await sign({ state, nonce, exp: Date.now() / 1000 + 600 }, env), 600);
    const q = new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID,
      redirect_uri: `${url.origin}/auth/callback`,
      response_type: 'code',
      scope: 'openid email',
      state,
      nonce,
      prompt: 'select_account',
    });
    return new Response(null, {
      status: 302,
      headers: { location: `https://accounts.google.com/o/oauth2/v2/auth?${q}`, 'set-cookie': stCookie },
    });
  }
  if (p === '/auth/callback') {
    const st = await verify(cookieGet(req, 'kk_oauth'), env);
    if (!st || st.state !== url.searchParams.get('state')) return new Response('state mismatch', { status: 403 });
    const tr = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code: url.searchParams.get('code') ?? '',
        client_id: env.GOOGLE_CLIENT_ID ?? '',
        client_secret: env.GOOGLE_CLIENT_SECRET ?? '',
        redirect_uri: `${url.origin}/auth/callback`,
        grant_type: 'authorization_code',
      }),
    });
    const tok = (await tr.json()) as { id_token?: string };
    const claims = tok.id_token && (await verifyGoogleIdToken(tok.id_token, env.GOOGLE_CLIENT_ID!, st.nonce));
    if (!claims) return new Response('token verification failed', { status: 403 });
    if (!(await resolveUser(claims.email, env)).allowed) {
      await addToWaitlist(env, claims.email).catch(() => {}); // admin 可在 /admin 一鍵核准
      return new Response(null, {
        status: 302,
        headers: { location: `/?waitlist=1`, 'set-cookie': cookieSet('kk_oauth', '', 0) },
      });
    }
    const session = await sign({ email: claims.email, exp: Date.now() / 1000 + 7 * 86400 }, env);
    return new Response(null, {
      status: 302,
      headers: { location: '/', 'set-cookie': cookieSet('kk_session', session, 7 * 86400) },
    });
  }
  if (p === '/auth/logout') {
    return new Response(null, { status: 302, headers: { location: '/', 'set-cookie': cookieSet('kk_session', '', 0) } });
  }

  // 開發用 Email 直登:只在未設定 OIDC 時開放
  if (p === '/api/login' && req.method === 'POST') {
    if (env.GOOGLE_CLIENT_ID) return bad('已啟用 Google 登入,請走 /auth/login', 403);
    const { email: raw } = (await req.json().catch(() => ({}))) as { email?: string };
    const email = (raw ?? '').trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return bad('Email 格式不正確');
    if (!(await resolveUser(email, env)).allowed) {
      await addToWaitlist(env, email);
      return json({ ok: false, waitlist: true }, { status: 403 });
    }
    const session = await sign({ email, exp: Date.now() / 1000 + 30 * 86400 }, env);
    return json({ ok: true, email }, { headers: { 'Set-Cookie': cookieSet('kk_session', session, 30 * 86400) } });
  }
  if (p === '/api/logout' && req.method === 'POST') {
    return json({ ok: true }, { headers: { 'Set-Cookie': cookieSet('kk_session', '', 0) } });
  }

  /* ---------- 需要登入的部分(/api/* 與 /ws 一律同源 + session 閘門) ---------- */
  if (p.startsWith('/api/') || p === '/ws') {
    if (!sameOrigin(req)) return new Response('forbidden', { status: 403 });
    const session = await sessionFrom(req, env);
    if (!session) return bad('請先登入', 401);
    // 每次請求重算分級 → R2 白名單改了立刻生效(不用重登入、不用重部署)
    const user = await resolveUser(session.email, env);
    if (!user.allowed) return bad('不在受邀名單內', 403);
    try {
      return await api(req, env, p, session.email, user);
    } catch (err) {
      return bad(err instanceof Error ? err.message : '伺服器錯誤', 500);
    }
  }

  return withSec(await env.ASSETS.fetch(req), req);
}

async function api(req: Request, env: Env, path: string, email: string, user: UserInfo): Promise<Response> {
  if (path.startsWith('/api/admin/')) {
    if (!user.isAdmin) return bad('admin_only', 403);
    return handleAdmin(req, env, path);
  }

  if (path === '/api/me' && req.method === 'GET') {
    const u = await readUsage(env, email).catch(() => null);
    return json({
      ok: true,
      email,
      tier: user.tier,
      isAdmin: user.isAdmin,
      usedSeconds: u?.seconds ?? 0,
      limitSeconds: user.limitSeconds,
    });
  }

  // 場景包清單(頂列選擇器用;內容只在 relay session 載入)
  if (path === '/api/packs' && req.method === 'GET') {
    return json({ ok: true, packs: await listPacks(env) });
  }

  // 聽譯 relay:轉給 per-email 的 SessionRelay DO(額度由 Worker 決定,DO 只執行)
  if (path === '/ws') {
    if (req.headers.get('Upgrade') !== 'websocket') return bad('expected websocket', 426);
    const stub = env.RELAY.get(env.RELAY.idFromName(email));
    const wsUrl = new URL(req.url);
    wsUrl.searchParams.set('limit', String(user.limitSeconds));
    wsUrl.searchParams.set('email', email);
    wsUrl.searchParams.set('lang', new URL(req.url).searchParams.get('lang') || DEFAULT_LANG);
    return stub.fetch(new Request(wsUrl, req));
  }

  return bad('不存在的 API', 404);
}
