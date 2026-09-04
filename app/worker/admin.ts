/* Admin API(沿 sukemu worker/admin.ts):名單/等候名單/額度管理 + 今日用量,
   加上 kikemu 專屬的場景包管理(pack-search / pack-save / pack-revalidate / pack-delete)。
   僅 ADMIN_EMAILS 內的帳號可用,閘門在 index.ts(session + isAdmin)。
   名單資料就是 R2 的兩個 JSON,想用 wrangler r2 object put 手動改也等價。 */

import { CONFIG_KEYS, readAllow, readJson, writeJson } from './auth';
import { extractVocab, researchTerms } from './gemini';
import { deletePack, listPacks, PACK_ID_RE, readRawPack, savePack, validateEntries } from './vocab';
import { PACK_LANGS } from './langs';
import type { Usage } from './quota';
import type { Env } from './index';

/** grounding 來源:只留 http(s) 的,最多 10 筆。uri 是模型控制的資料。 */
function safeSources(v: unknown): { title: string; uri: string }[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((s): s is { uri: string; title?: unknown } =>
      !!s && typeof s === 'object' && typeof (s as { uri?: unknown }).uri === 'string' &&
      /^https?:\/\//i.test((s as { uri: string }).uri.trim()))
    .map(s => ({
      uri: s.uri.trim(),
      title: typeof s.title === 'string' ? s.title.slice(0, 300) : '',
    }))
    .slice(0, 10);
}

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
    const body0 = (await req.json()) as { id?: string; name?: string; source_text?: string; alias?: string; lang?: string };
    const { id, name, source_text } = body0;
    const packId = String(id || '').trim().toLowerCase();
    if (!PACK_ID_RE.test(packId)) return bad('包 id 只能是小寫英數字、- 或 _(32 字內)');
    const packName = String(name || '').trim().slice(0, 60);
    if (!packName) return bad('缺少場景包名稱');
    const src = String(source_text || '').trim();
    if (!src) return bad('缺少來源文字');
    if (src.length > MAX_SOURCE_TEXT) return bad('來源文字過長(上限 64KB)', 413);

    const packLang = PACK_LANGS.includes((body0 as any).lang) ? String((body0 as any).lang) : 'ja';
    const alias = String((body0 as any).alias || '').trim().slice(0, 40) || packName;
    const raw = await extractVocab(env, src, packLang); // Gemini JSON mode 抽詞條 + 讀音
    const { entries, warnings, issues, stats } = validateEntries(raw, packLang); // 驗證 pipeline
    if (!entries.length) return bad('沒有抽出任何詞條,請換一段來源文字');
    await savePack(env, packId, { name: packName, alias, lang: packLang, entries });
    return Response.json({ ok: true, id: packId, alias, name: packName, lang: packLang, count: entries.length, warnings, issues, stats });
  }

  /* 關鍵字產包:輸入「大阪城」就出一包。兩趟——
       pass A 搜尋接地蒐集固有名詞與讀音(來源筆數一併回報)
       pass B 把 A 的文字結構化成 JSON 並做假名驗證
     preview=true 時只回結果不落地:讀音錯的詞表會反過來傷辨識,
     所以預設讓管理者先看過再存(admin.js 走兩段流程)。 */
  /* 預覽:搜尋 + 抽詞,只回不存(約 60~100 秒) */
  if (path === '/api/admin/pack-search' && req.method === 'POST') {
    const { keyword, lang } = (await req.json()) as { keyword?: string; lang?: string };
    const kw = String(keyword || '').trim().slice(0, 100);
    if (!kw) return bad('缺少關鍵字');
    const packLang = PACK_LANGS.includes(lang as any) ? String(lang) : 'ja';

    const research = await researchTerms(env, kw, packLang);
    const raw = await extractVocab(env, research.text, packLang);
    // 保險:主題本身是導覽全程重複最多次的詞,模型漏掉就程式補(實測漏過「枚岡神社」)
    if (!raw.some(e => e?.content === kw)) raw.unshift({ content: kw });
    const { entries, warnings, issues, stats } = validateEntries(raw, packLang);
    if (!entries.length) return bad('搜尋結果裡抽不出詞條,請換個關鍵字或改用貼上來源文字');

    return Response.json({
      ok: true, keyword: kw, lang: packLang, count: entries.length,
      entries, warnings, issues, stats, sources: research.sources.slice(0, 10), queries: research.queries,
    });
  }

  /* 存檔:直接收預覽時看到的詞條,不重跑搜尋——
     既省 100 秒等待,也保證「存下去的就是剛才過目的那份」。 */
  if (path === '/api/admin/pack-save' && req.method === 'POST') {
    const body = (await req.json()) as {
      id?: string; alias?: string; name?: string; lang?: string; keyword?: string;
      entries?: { content?: string; sounds_like?: string[] }[];
      sources?: { title: string; uri: string }[]; queries?: string[];
    };
    const packId = String(body.id || '').trim().toLowerCase();
    if (!PACK_ID_RE.test(packId)) return bad('包 id 只能是小寫英數字、- 或 _(32 字內)');
    const alias = String(body.alias || '').trim().slice(0, 40);
    if (!alias) return bad('缺少中文別名(使用者介面顯示用)');
    const packLang = PACK_LANGS.includes(body.lang as any) ? String(body.lang) : 'ja';
    const packName = String(body.name || '').trim().slice(0, 60) || String(body.keyword || alias);
    // 存檔前再跑一次 pipeline:預覽送回來的詞條可能被前端改過,不能信
    const { entries, warnings, issues, stats } = validateEntries((body.entries ?? []) as any, packLang);
    if (!entries.length) return bad('沒有可存的詞條');

    await savePack(env, packId, {
      name: packName, alias, lang: packLang, entries,
      // sources 的 uri 來自模型的 grounding metadata。在**存檔時**就過濾,
      // 不要只擋在顯示層 —— 存進去的東西之後會被別的地方讀,而那些地方不會
      // 記得要再過濾一次。
      source: { kind: 'search', keyword: String(body.keyword || ''), queries: body.queries ?? [], sources: safeSources(body.sources), at: new Date().toISOString() },
    });
    return Response.json({ ok: true, id: packId, alias, name: packName, lang: packLang, count: entries.length, warnings, issues, stats });
  }

  /* 重驗:把既有的包重跑一次驗證 pipeline 再存回去。
     為什麼需要——pipeline 只在生成當下跑,規則後來補強了(content 掃描、
     混寫假名正規化)不會回頭套用到已經存在 R2 的包。正式站第一包「大阪城」
     就帶著 `黄金 of 茶室` 與 `トらいし` 上線,靠這支修掉,不必重跑 100 秒搜尋。
     只有詞條會被改寫,alias / lang / source 生成軌跡原封不動。 */
  if (path === '/api/admin/pack-revalidate' && req.method === 'POST') {
    const { id } = (await req.json()) as { id?: string };
    const packId = String(id || '').trim().toLowerCase();
    if (!PACK_ID_RE.test(packId)) return bad('包 id 格式不對');
    const pack = await readRawPack(env, packId);
    if (!pack) return bad('找不到這個場景包', 404);
    const before = pack.entries.length;
    const { entries, warnings, issues, stats } = validateEntries(pack.entries, pack.lang || 'ja');
    if (!entries.length) return bad('重驗後沒有可存的詞條,已中止(原包保持不變)');
    if (!stats.fix && !stats.drop) {
      return Response.json({ ok: true, id: packId, unchanged: true, count: before, warnings, issues, stats });
    }
    await savePack(env, packId, { ...pack, entries });
    return Response.json({ ok: true, id: packId, count: entries.length, before, warnings, issues, stats });
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
