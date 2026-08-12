/* 場景包(vocab pack):R2 CONFIG bucket 的 vocab/{id}.json,
   格式 {"name": "顯示名", "entries": [{"content": "...", "sounds_like": ["..."]}]}。
   - session 載入:relay.ts 讀出 entries 當 Speechmatics additional_vocab
     (exp1:C+−C 全條件 +0.12~0.19;詞表中途不換,換地點 = 重連)
   - 生成/驗證:scripts/make_dict.py 的規則搬過來,再擴成四段 pipeline
     (trim → content → reading → dedupe),每段回報 fix/warn/drop;
     >6 字標警告但不阻擋(exp1 記載的不確定性)
   - 種子包:app/seed/higashiosaka.json,上傳指令見 package.json "seed:pack" */

import type { Env } from './index';
import { SOUNDS_LIKE_RE } from './langs';

export type VocabEntry = { content: string; sounds_like?: string[] };
export type VocabPack = {
  /** 中文別名:使用者介面顯示用(原文名對台灣使用者不好認) */
  alias?: string;
  /** 來源語言(ja / ko);舊包沒有這欄,一律視為 ja */
  lang?: string; name: string; entries: VocabEntry[]
  /** 生成軌跡(關鍵字產包時記錄搜尋詞與來源,便於追溯與重生成) */
  source?: { kind: string; keyword?: string; queries?: string[]; sources?: { title: string; uri: string }[]; at?: string };
};
export type PackMeta = { id: string; name: string; alias?: string; lang: string; count: number; updated: string | null };

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
    // alias / lang 也要帶出來——只回 name+entries 會讓別名與語言在讀取時消失,
    // 清單顯示成「日文」、使用者端也過濾不掉(實測踩過)
    return {
      name: String(data.name || id),
      alias: data.alias ? String(data.alias) : undefined,
      lang: String(data.lang || 'ja'),
      entries: data.entries,
    };
  } catch {
    return null;
  }
}

/** 原封不動讀出整包(含 source 生成軌跡)——重驗要寫回去,不能像 readPack 那樣挑欄位 */
export async function readRawPack(env: Env, id: string): Promise<VocabPack | null> {
  if (!PACK_ID_RE.test(id)) return null;
  try {
    const obj = await env.CONFIG.get(packKey(id));
    if (!obj) return null;
    const data = await obj.json<VocabPack>();
    return Array.isArray(data?.entries) ? data : null;
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
      out.push({ id, name: pack.name, alias: pack.alias, lang: pack.lang || 'ja', count: pack.entries.length, updated: obj.uploaded?.toISOString?.() ?? null });
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

/* ─────────────── 詞條驗證 pipeline ───────────────
   為什麼要分段:正式站第一包「大阪城」(149 詞)實測撞到兩種壞法,
   兩個都**通得過**舊版那種「單看讀音字集」的檢查:
     - `黄金 of 茶室` —— content 裡的「の」被模型翻成 of(舊版根本沒驗 content)
     - `虎石` → `トらいし` —— 片假名混進平假名(KANA_RE 兩種都收,混寫也合法)
   所以改成具名分段,每段各自回報 fix / warn / drop,管理者在預覽時
   看得到「這包被動了什麼手腳」,而不是只拿到一串沒有出處的警告字串。 */

export type IssueLevel = 'fix' | 'warn' | 'drop';
/** 一條驗證發現:哪一段、什麼等級、哪個詞、發生什麼事 */
export type Issue = { stage: string; level: IssueLevel; content: string; message: string };
export type Validated = {
  entries: VocabEntry[];
  /** 相容舊介面的扁平訊息(admin.js warnSummary 仍在用) */
  warnings: string[];
  issues: Issue[];
  stats: { in: number; out: number; fix: number; warn: number; drop: number };
};

/** CJK 字集(含諺文):用來判斷「拉丁字母是不是夾在漢字/假名中間」 */
const CJK = '\\p{Script=Han}\\p{Script=Hiragana}\\p{Script=Katakana}\\p{Script=Hangul}';
/* 小寫拉丁字被 CJK 夾住 = 模型把助詞翻成英文的特徵。
   只抓小寫是刻意的:同一包裡 `JO-TERRACE OSAKA`、`RUNNING BASE大阪城`、
   `もりのみやキューズモールBASE` 都是真的店名,全大寫,不能誤殺。 */
const LATIN_IN_CJK = new RegExp(`[${CJK}]\\s*\\b([a-z]{1,4})\\b\\s*[${CJK}]`, 'u');
/** 只自動還原證據最明確的屬格助詞(of / no ← の / 의),其餘只警告不動手。
    韓文帶一個空白:諺文是分詞書寫的,`경복궁의 근정전` 才是正常詞形。 */
const GENITIVE: Record<string, string> = { ja: 'の', ko: '의 ' };
const GENITIVE_RE = new RegExp(`([${CJK}])\\s*\\b(of|no)\\b\\s*([${CJK}])`, 'gu');

/** 片假名 → 平假名(只用在「混寫」的讀音上;整條片假名是合法寫法,不動) */
const toHiragana = (s: string) => s.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60));
const MIXED_KANA = (s: string) => /[ぁ-ゖ]/.test(s) && /[ァ-ヺ]/.test(s);

type Ctx = { lang: string; re: RegExp; setName: string; push: (i: Issue) => void };
type Stage = { name: string; run: (items: VocabEntry[], ctx: Ctx) => VocabEntry[] };

/** ① 清洗:content 去空白、讀音去空白 + NFKC(半形假名 ｱｲｳ → 全形),空的丟掉 */
const stageTrim: Stage = {
  name: 'trim',
  run: (items, ctx) =>
    items.flatMap(it => {
      const content = String(it?.content ?? '').trim();
      if (!content) return [];
      const reads = (Array.isArray(it?.sounds_like) ? it.sounds_like : [])
        .map(s => String(s).normalize('NFKC').trim())
        .filter(Boolean);
      void ctx;
      return [{ content, ...(reads.length ? { sounds_like: reads } : {}) }];
    }),
};

/** ② content 掃描:抓「の 被翻成 of」這類翻譯痕跡(舊版完全沒驗這一欄) */
const stageContent: Stage = {
  name: 'content',
  run: (items, ctx) =>
    items.map(it => {
      if (!LATIN_IN_CJK.test(it.content)) return it;
      const particle = GENITIVE[ctx.lang] ?? 'の';
      const fixed = it.content.replace(GENITIVE_RE, (_m, a, _w, b) => `${a}${particle}${b}`);
      if (fixed !== it.content) {
        ctx.push({ stage: 'content', level: 'fix', content: it.content, message: `助詞被翻成英文,已還原為「${fixed}」` });
        return { ...it, content: fixed };
      }
      ctx.push({ stage: 'content', level: 'warn', content: it.content, message: '詞形夾雜小寫英文,可能是模型翻譯的痕跡,請確認' });
      return it;
    }),
};

/** ③ 讀音:混寫假名自動轉平假名 → 字集不合剔除 → 去重 → 過長警告 */
const stageReading: Stage = {
  name: 'reading',
  run: (items, ctx) =>
    items.map(it => {
      const raw = it.sounds_like ?? [];
      if (!raw.length) return it;
      const norm = raw.map(s => {
        if (ctx.lang !== 'ja' || !MIXED_KANA(s)) return s;
        const h = toHiragana(s);
        ctx.push({ stage: 'reading', level: 'fix', content: it.content, message: `讀音混片假名「${s}」,已正規化為「${h}」` });
        return h;
      });
      const bad = norm.filter(s => !ctx.re.test(s));
      if (bad.length) {
        ctx.push({ stage: 'reading', level: 'drop', content: it.content, message: `剔除非${ctx.setName}讀音:${bad.join('、')}` });
      }
      const ok = [...new Set(norm.filter(s => ctx.re.test(s)))];
      const long = ok.filter(s => s.length > 6);
      if (long.length) {
        ctx.push({ stage: 'reading', level: 'warn', content: it.content, message: `讀音超過 6 字(不阻擋,實測不確定性):${long.join('、')}` });
      }
      const out: VocabEntry = { content: it.content };
      if (ok.length) out.sounds_like = ok;
      return out;
    }),
};

/** ④ 去重:同一個表記只留第一條(讀音合併,避免後面那條的讀音白白丟掉) */
const stageDedupe: Stage = {
  name: 'dedupe',
  run: (items, ctx) => {
    const byContent = new Map<string, VocabEntry>();
    for (const it of items) {
      const prev = byContent.get(it.content);
      if (!prev) {
        byContent.set(it.content, it);
        continue;
      }
      const merged = [...new Set([...(prev.sounds_like ?? []), ...(it.sounds_like ?? [])])];
      if (merged.length) prev.sounds_like = merged;
      ctx.push({ stage: 'dedupe', level: 'drop', content: it.content, message: '重複表記,讀音併入前一條' });
    }
    return [...byContent.values()];
  },
};

const PIPELINE: Stage[] = [stageTrim, stageContent, stageReading, stageDedupe];

/** 格式驗證:依語言的讀音字集(日文全形假名、韓文諺文,皆經探針驗證 SM 會接受)
    跑過上面四段 pipeline。回傳詞條 + 每段的發現。 */
export function validateEntries(items: VocabEntry[], lang = 'ja'): Validated {
  const issues: Issue[] = [];
  const ctx: Ctx = {
    lang,
    re: SOUNDS_LIKE_RE[lang] ?? SOUNDS_LIKE_RE.ja,
    setName: lang === 'ko' ? '諺文' : '全形假名',
    push: i => issues.push(i),
  };
  let cur = Array.isArray(items) ? items : [];
  for (const stage of PIPELINE) cur = stage.run(cur, ctx);
  const count = (l: IssueLevel) => issues.filter(i => i.level === l).length;
  return {
    entries: cur,
    warnings: issues.map(i => `「${i.content}」${i.message}`),
    issues,
    stats: { in: items?.length ?? 0, out: cur.length, fix: count('fix'), warn: count('warn'), drop: count('drop') },
  };
}

export async function savePack(env: Env, id: string, pack: VocabPack): Promise<void> {
  await env.CONFIG.put(packKey(id), JSON.stringify(pack, null, 1), {
    httpMetadata: { contentType: 'application/json' },
  });
}

export async function deletePack(env: Env, id: string): Promise<void> {
  await env.CONFIG.delete(packKey(id));
}
