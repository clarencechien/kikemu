/* Admin API(沿 sukemu worker/admin.ts):名單/等候名單/額度管理 + 今日用量,
   加上 kikemu 專屬的場景包管理(pack-generate / pack-delete)。
   僅 ADMIN_EMAILS 內的帳號可用,閘門在 index.ts(session + isAdmin)。
   名單資料就是 R2 的兩個 JSON,想用 wrangler r2 object put 手動改也等價。 */

import { CONFIG_KEYS, readAllow, readJson, writeJson } from './auth';
import { extractVocab } from './gemini';
import { deletePack, listPacks, PACK_ID_RE, savePack, validateEntries } from './vocab';
import type { Usage } from './quota';
import type { Env } from './index';

const bad = (msg: string, status = 400) => Response.json({ ok: false, error: msg }, { status });
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
/** 場景包來源文字上限 64KB(PRD §6 body 上限) */
const MAX_SOURCE_TEXT = 64 * 1024;

export async function handleAdmin(req: Request, env: Env, path: string): Promise<Response> {
  if (path === '/api/admin/data' && req.method === 'GET') {
    const [allow, wait, packs] = await Promise.all([
      readAllow(env),
      readJson<{ email: string; at: string }[]>(env, CONFIG_KEYS.wait, []),
      listPacks(env).catch(() => []),
    ]);
    let tiers: Record<string, number> = {};
    try {
      tiers = JSON.parse(env.QUOTA_TIERS || '{}');
    } catch {}
    // 每人今日用量(秒)——名單通常很短,逐一問 DO 可接受
    const usage: Record<string, Usage> = {};
    await Promise.all(
      Object.keys(allow).map(async email => {
        try {
          const stub = env.QUOTA.get(env.QUOTA.idFromName(email));
          usage[email] = await (await stub.fetch('https://do/usage')).json<Usage>();
        } catch {}
      }),
    );
    return Response.json({
      allowlist: allow,
      waitlist: [...wait].sort((a, b) => (a.at < b.at ? -1 : 1)),
      tiers,
      defaultTier: env.DEFAULT_TIER || 'beta',
      admins: (env.ADMIN_EMAILS || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
      usage,
      packs,
    });
  }

  if (path === '/api/admin/allow' && req.method === 'POST') {
    const { email, tier } = (await req.json()) as { email?: string; tier?: string };
    const lower = String(email || '').trim().toLowerCase();
    if (!EMAIL_RE.test(lower)) return bad('email 格式不對');
    const value = String(tier || env.DEFAULT_TIER || 'beta').trim();
    if (!/^[\w-]+$/.test(value)) return bad('級別只能是英數字或每日秒數');
    const allow = await readAllow(env);
    allow[lower] = /^\d+$/.test(value) ? Number(value) : value;
    await writeJson(env, CONFIG_KEYS.allow, allow);
    // 核准後從等候名單移除
    const wait = await readJson<{ email: string; at: string }[]>(env, CONFIG_KEYS.wait, []);
    const next = wait.filter(e => e.email.toLowerCase() !== lower);
    if (next.length !== wait.length) await writeJson(env, CONFIG_KEYS.wait, next);
    return Response.json({ ok: true, email: lower, tier: allow[lower] });
  }

  if (path === '/api/admin/remove' && req.method === 'POST') {
    const { email } = (await req.json()) as { email?: string };
    const lower = String(email || '').trim().toLowerCase();
    // ADMIN_EMAILS 帳號不可移除(移除了也還是 admin,徒增困惑)
    const admins = (env.ADMIN_EMAILS || '').toLowerCase().split(',').map(s => s.trim());
    if (admins.includes(lower)) return bad('admin 帳號不可移除', 403);
    const allow = await readAllow(env);
    if (!(lower in allow)) return bad('名單裡沒有這個 email', 404);
    delete allow[lower];
    await writeJson(env, CONFIG_KEYS.allow, allow);
    return Response.json({ ok: true });
  }

  if (path === '/api/admin/waitlist-remove' && req.method === 'POST') {
    const { email } = (await req.json()) as { email?: string };
    const lower = String(email || '').trim().toLowerCase();
    const wait = await readJson<{ email: string; at: string }[]>(env, CONFIG_KEYS.wait, []);
    await writeJson(env, CONFIG_KEYS.wait, wait.filter(e => e.email.toLowerCase() !== lower));
    return Response.json({ ok: true });
  }

  /* ---- 場景包管理(kikemu 專屬;scripts/make_dict.py 的產品化) ---- */
  if (path === '/api/admin/pack-generate' && req.method === 'POST') {
    const { id, name, source_text } = (await req.json()) as { id?: string; name?: string; source_text?: string };
    const packId = String(id || '').trim().toLowerCase();
    if (!PACK_ID_RE.test(packId)) return bad('包 id 只能是小寫英數字、- 或 _(32 字內)');
    const packName = String(name || '').trim().slice(0, 60);
    if (!packName) return bad('缺少場景包名稱');
    const src = String(source_text || '').trim();
    if (!src) return bad('缺少來源文字');
    if (src.length > MAX_SOURCE_TEXT) return bad('來源文字過長(上限 64KB)', 413);

    const raw = await extractVocab(env, src); // Gemini JSON mode 抽詞條 + 假名讀音
    const { entries, warnings } = validateEntries(raw); // 全形假名驗證;>6 字警告不阻擋
    if (!entries.length) return bad('沒有抽出任何詞條,請換一段來源文字');
    await savePack(env, packId, { name: packName, entries });
    return Response.json({ ok: true, id: packId, name: packName, count: entries.length, warnings });
  }

  if (path === '/api/admin/pack-delete' && req.method === 'POST') {
    const { id } = (await req.json()) as { id?: string };
    const packId = String(id || '').trim().toLowerCase();
    if (!PACK_ID_RE.test(packId)) return bad('包 id 格式不對');
    await deletePack(env, packId);
    return Response.json({ ok: true });
  }

  return bad('not found', 404);
}
