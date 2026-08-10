/* 場景包(vocab pack):R2 CONFIG bucket 的 vocab/{id}.json,
   格式 {"name": "顯示名", "entries": [{"content": "...", "sounds_like": ["..."]}]}。
   - session 載入:relay.ts 讀出 entries 當 Speechmatics additional_vocab
     (exp1:C+−C 全條件 +0.12~0.19;詞表中途不換,換地點 = 重連)
   - 生成/驗證:scripts/make_dict.py 的規則搬過來——sounds_like 必須全形假名,
     不合格的讀音直接剔除;>6 字標警告但不阻擋(exp1 記載的不確定性)
   - 種子包:app/seed/higashiosaka.json,上傳指令見 package.json "seed:pack" */

import type { Env } from './index';

export type VocabEntry = { content: string; sounds_like?: string[] };
export type VocabPack = { name: string; entries: VocabEntry[] };
export type PackMeta = { id: string; name: string; count: number; updated: string | null };

export const PACK_ID_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
/** 全形假名(ひらがな+カタカナ+長音),同 scripts/make_dict.py KANA_RE */
export const KANA_RE = /^[ぁ-ゖァ-ヺー]+$/;

const packKey = (id: string) => `vocab/${id}.json`;

export async function readPack(env: Env, id: string): Promise<VocabPack | null> {
  if (!PACK_ID_RE.test(id)) return null;
  try {
    const obj = await env.CONFIG.get(packKey(id));
    if (!obj) return null;
    const data = await obj.json<VocabPack>();
    if (!Array.isArray(data.entries)) return null;
    return { name: String(data.name || id), entries: data.entries };
  } catch {
    return null;
  }
}

/** GET /api/packs:列出所有場景包(名稱、詞條數、更新時間) */
export async function listPacks(env: Env): Promise<PackMeta[]> {
  const out: PackMeta[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.CONFIG.list({ prefix: 'vocab/', limit: 100, cursor });
    for (const obj of page.objects) {
      const id = obj.key.slice('vocab/'.length).replace(/\.json$/, '');
      if (!PACK_ID_RE.test(id)) continue;
      const pack = await readPack(env, id);
      if (!pack) continue;
      out.push({ id, name: pack.name, count: pack.entries.length, updated: obj.uploaded?.toISOString?.() ?? null });
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

/** 格式驗證:sounds_like 全形假名(不合格剔除)、>6 字警告不阻擋、去重 */
export function validateEntries(items: VocabEntry[]): { entries: VocabEntry[]; warnings: string[] } {
  const entries: VocabEntry[] = [];
  const warnings: string[] = [];
  const seen = new Set<string>();
  for (const it of items) {
    const content = String(it?.content ?? '').trim();
    if (!content || seen.has(content)) continue;
    seen.add(content);
    const raw = Array.isArray(it.sounds_like) ? it.sounds_like.map(s => String(s).trim()).filter(Boolean) : [];
    const bad = raw.filter(s => !KANA_RE.test(s));
    if (bad.length) warnings.push(`「${content}」剔除非全形假名讀音:${bad.join('、')}`);
    const ok = raw.filter(s => KANA_RE.test(s));
    const long = ok.filter(s => s.length > 6);
    if (long.length) warnings.push(`「${content}」讀音超過 6 字(不阻擋,實測不確定性):${long.join('、')}`);
    const entry: VocabEntry = { content };
    if (ok.length) entry.sounds_like = ok;
    entries.push(entry);
  }
  return { entries, warnings };
}

export async function savePack(env: Env, id: string, pack: VocabPack): Promise<void> {
  await env.CONFIG.put(packKey(id), JSON.stringify(pack, null, 1), {
    httpMetadata: { contentType: 'application/json' },
  });
}

export async function deletePack(env: Env, id: string): Promise<void> {
  await env.CONFIG.delete(packKey(id));
}
