/* Google OIDC + HMAC session cookie + R2 白名單分級(沿 sukemu worker/auth.ts,
   額度單位改為每日「聽譯秒數」)。
   - 白名單 config/allowlist.json 兩種格式,R2 熱更新、改檔即生效:
       ["a@x.com", "b@x.com"]                  → 全部套 DEFAULT_TIER
       {"a@x.com": "admin", "b@x.com": 1200}   → 逐人分級;數字 = 自訂每日秒數
   - 級別對應秒數在 var QUOTA_TIERS(0 = 無上限);ADMIN_EMAILS 一律 admin 級
   - 未設 GOOGLE_CLIENT_ID 時保留開發用 Email 直登(/api/login),部署 OIDC 後自動停用 */

import type { Env } from './index';

const enc = new TextEncoder();

export const b64u = {
  enc: (buf: ArrayBuffer | Uint8Array) =>
    btoa(String.fromCharCode(...new Uint8Array(buf))).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, ''),
  encStr: (s: string) => b64u.enc(enc.encode(s)),
  dec: (s: string) => Uint8Array.from(atob(s.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0)),
  decStr: (s: string) => new TextDecoder().decode(b64u.dec(s)),
};

/* SESSION_SECRET 沒設就退回這把眾所皆知的字串——夠開發用,但正式環境用它
   等於把 session 簽章的鑰匙公開:任何人都能偽造 kk_session(含 admin 帳號
   一路進 /admin)。所以「正式環境=已設 OIDC」時,缺 secret 或還在用這把
   dev 值一律「關門」:sign 直接報錯、verify 一律當未登入。
   寧可全站登入失敗、逼運維補上 secret,也不要靜默放行偽造 session。 */
const DEV_SECRET = 'dev-insecure-secret';
const secret = (env: Env) => env.SESSION_SECRET || DEV_SECRET;
/** 正式環境(已設 GOOGLE_CLIENT_ID)卻缺少獨立 SESSION_SECRET → 危險組態 */
const prodSecretMissing = (env: Env) =>
  !!env.GOOGLE_CLIENT_ID && (!env.SESSION_SECRET || env.SESSION_SECRET === DEV_SECRET);

function hmacKey(s: string) {
  return crypto.subtle.importKey('raw', enc.encode(s), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
export async function sign(payload: object, env: Env): Promise<string> {
  if (prodSecretMissing(env)) {
    throw new Error('SESSION_SECRET 未設定(正式環境必設):npx wrangler secret put SESSION_SECRET');
  }
  const body = b64u.encStr(JSON.stringify(payload));
  const sig = b64u.enc(await crypto.subtle.sign('HMAC', await hmacKey(secret(env)), enc.encode(body)));
  return `${body}.${sig}`;
}
export async function verify(token: string | null, env: Env): Promise<Record<string, any> | null> {
  // 正式環境缺 secret:所有 session / OAuth state 一律視為無效,不給偽造任何機會
  if (prodSecretMissing(env)) return null;
  if (!token || !token.includes('.')) return null;
  const [body, sig] = token.split('.');
  try {
    const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret(env)), b64u.dec(sig), enc.encode(body));
    if (!ok) return null;
    const p = JSON.parse(b64u.decStr(body));
    if (p.exp && Date.now() / 1000 > p.exp) return null;
    return p;
  } catch {
    return null;
  }
}

export function cookieGet(req: Request, name: string): string | null {
  const m = (req.headers.get('cookie') || '').match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : null;
}
export const cookieSet = (name: string, value: string, maxAge: number) =>
  `${name}=${value}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`;

export async function sessionFrom(req: Request, env: Env): Promise<{ email: string } | null> {
  const p = await verify(cookieGet(req, 'kk_session'), env);
  return p?.email ? (p as { email: string }) : null;
}

/* ---- Google id_token 驗證(JWKS,RS256) ---- */
let jwksCache: { keys: any[] | null; at: number } = { keys: null, at: 0 };
async function googleJwks() {
  if (!jwksCache.keys || Date.now() - jwksCache.at > 3600_000) {
    const r = await fetch('https://www.googleapis.com/oauth2/v3/certs');
    jwksCache = { keys: ((await r.json()) as { keys: any[] }).keys, at: Date.now() };
  }
  return jwksCache.keys!;
}
export async function verifyGoogleIdToken(idToken: string, clientId: string, expectedNonce?: string) {
  const [h, p, s] = idToken.split('.');
  if (!s) return null;
  const header = JSON.parse(b64u.decStr(h));
  const payload = JSON.parse(b64u.decStr(p));
  const jwk = (await googleJwks()).find(k => k.kid === header.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  const ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64u.dec(s), enc.encode(`${h}.${p}`));
  if (!ok) return null;
  const now = Date.now() / 1000;
  if (payload.exp < now) return null;
  if (!['https://accounts.google.com', 'accounts.google.com'].includes(payload.iss)) return null;
  if (payload.aud !== clientId) return null;
  if (expectedNonce && payload.nonce !== expectedNonce) return null;
  if (!payload.email || payload.email_verified !== true) return null;
  return payload as { email: string };
}

/* ---- 白名單 + 分級(每日秒數) ---- */
export const CONFIG_KEYS = { allow: 'config/allowlist.json', wait: 'config/waitlist.json' };
const DEFAULT_ALLOWLIST = ['clarence.chien@gmail.com'];

export async function readJson<T>(env: Env, key: string, fallback: T): Promise<T> {
  try {
    const obj = await env.CONFIG.get(key);
    return obj ? await obj.json<T>() : fallback;
  } catch {
    return fallback;
  }
}
export const writeJson = (env: Env, key: string, data: unknown) =>
  env.CONFIG.put(key, JSON.stringify(data, null, 2), { httpMetadata: { contentType: 'application/json' } });

/** 名單統一成物件格式(舊陣列自動升級);檔案不存在時以預設名單建立 */
export async function readAllow(env: Env): Promise<Record<string, string | number>> {
  let data = await readJson<unknown>(env, CONFIG_KEYS.allow, null);
  if (data == null) {
    data = DEFAULT_ALLOWLIST;
    await writeJson(env, CONFIG_KEYS.allow, data).catch(() => {});
  }
  if (Array.isArray(data)) {
    return Object.fromEntries(data.map(e => [String(e).toLowerCase(), env.DEFAULT_TIER || 'beta']));
  }
  return Object.fromEntries(Object.entries(data as object).map(([k, v]) => [k.toLowerCase(), v as string | number]));
}

export type UserInfo = { allowed: boolean; tier: string | null; limitSeconds: number; isAdmin: boolean };

export async function resolveUser(email: string, env: Env): Promise<UserInfo> {
  const lower = String(email).toLowerCase();
  const allow = await readAllow(env);
  let tier: string | null = lower in allow ? String(allow[lower]) : null;

  const isAdmin = (env.ADMIN_EMAILS || '').toLowerCase().split(',').map(s => s.trim()).filter(Boolean).includes(lower);
  if (isAdmin) tier = 'admin'; // admin 一律最高級,不受名單格式影響
  if (!tier) return { allowed: false, tier: null, limitSeconds: 0, isAdmin: false };

  // 名單值可以是級別名(beta/pro/admin…)或直接給每日秒數(1200 / "1200")
  if (/^\d+$/.test(tier)) {
    return { allowed: true, tier: 'custom', limitSeconds: Number(tier), isAdmin };
  }
  let tiers: Record<string, number> = {};
  try {
    tiers = JSON.parse(env.QUOTA_TIERS || '{}');
  } catch {
    /* 格式錯就退回預設 */
  }
  const limitSeconds = tier in tiers ? Number(tiers[tier]) : 900;
  return { allowed: true, tier, limitSeconds, isAdmin };
}

/** 不在名單的登入自動記到等候名單(格式:[{email, at}]) */
export async function addToWaitlist(env: Env, email: string) {
  try {
    const list = await readJson<{ email: string; at: string }[]>(env, CONFIG_KEYS.wait, []);
    if (!list.some(e => e.email === email)) {
      list.push({ email, at: new Date().toISOString() });
      await writeJson(env, CONFIG_KEYS.wait, list);
    }
  } catch {
    /* 等候名單寫失敗不影響回應 */
  }
}

export const randomHex = (n = 16) =>
  [...crypto.getRandomValues(new Uint8Array(n))].map(b => b.toString(16).padStart(2, '0')).join('');
