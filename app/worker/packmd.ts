/* 場景包 md 匯入:外部 LLM(有搜尋 + 思考)照 public/vocab-prompt.md 產出的 md → 詞條。

   為什麼改成「外面產、這裡匯入」:產品內的關鍵字產包(gemini.ts researchTerms + extractVocab)
   是照寺社觀光寫的——對象清單沒有「商品名・銘柄」,抽詞 prompt 又寫死「一般語は含めない」,
   結果 2026-10-04 的試酒導覽:酒廠・品牌・酒藏都對了,但個別酒款「万穂」與行業術語「日本酒度」
   沒進包,照樣聽錯(results/report.md §2.1g)。外部模型可以查得更深(商品頁、多頁官網),
   prompt 也能照主題調,而這裡只負責解析 + 跑同一條驗證 pipeline(vocab.ts validateEntries)。

   這支是純函式(不碰 Env / R2),驗證 pipeline 之外的事一律不做:
   - 解析寬鬆:模型常把整份包在 ```markdown 圍欄裡、表頭換成英文、多一欄少一欄——都收
   - 但**看不懂的列一定回報**(skipped),不默默吞掉:管理者要知道少了哪幾條 */

export type PackMdMeta = { lang?: string; name?: string; alias?: string };
/** note = 「注意」欄(第 5 欄)。外部模型標「同音:…」的詞,管理頁預覽會特別標出來給人決定要不要收 */
export type PackMdRow = { content: string; sounds_like?: string[]; kind?: string; source?: string; note?: string };
export type PackMdResult = {
  meta: PackMdMeta;
  rows: PackMdRow[];
  /** 文末「出典」與每列出典裡的 http(s) 網址(去重,保留順序) */
  sources: string[];
  /** 看得出是表格列、但解析不出詞條的(附行號與原因) */
  skipped: { line: number; text: string; reason: string }[];
};

/** 讀音分隔:頓號、逗號(全半形)、斜線(全半形)、分號 */
const READING_SPLIT = /[、,,//;;]/;
const URL_RE = /https?:\/\/[^\s|)>\]]+/g;
/** 表頭:第一欄是「表記 / content / 詞 / 語」之類 */
const HEADER_RE = /^(表記|content|term|word|語|詞|詞條|用語)$/i;
/** 分隔列 |---|:--:| */
const SEP_RE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map(c => c.trim());
}

export function parsePackMarkdown(md: string): PackMdResult {
  const text = md.replace(/\r\n?/g, '\n');
  const lines = text.split('\n');
  const meta: PackMdMeta = {};
  const rows: PackMdRow[] = [];
  const skipped: PackMdResult['skipped'] = [];
  const sources: string[] = [];
  const addUrl = (u: string) => {
    const url = u.replace(/[.,。、]+$/, '');
    if (!sources.includes(url)) sources.push(url);
  };

  // front matter:第一個「--- 後面接 key: value」的區塊。模型常在前面多一句
  // 「好的,以下是詞表」或包一層 ``` 圍欄,所以不要求在第 0 行(只找前 20 行)
  let i = 0;
  const fm = lines.slice(0, 20).findIndex((l, k) => l.trim() === '---' && /^\s*[A-Za-z-]+\s*:/.test(lines[k + 1] ?? ''));
  if (fm >= 0) {
    i = fm;
    let j = i + 1;
    for (; j < lines.length && lines[j].trim() !== '---'; j++) {
      const m = lines[j].match(/^\s*([A-Za-z-]+)\s*:\s*(.*?)\s*$/);
      if (!m) continue;
      const k = m[1].toLowerCase();
      const v = m[2].replace(/^["']|["']$/g, '');
      if (k === 'lang') meta.lang = v.toLowerCase();
      else if (k === 'name') meta.name = v;
      else if (k === 'alias') meta.alias = v;
    }
    if (j < lines.length) i = j + 1;
  }

  let inSources = false;
  for (let n = i; n < lines.length; n++) {
    const raw = lines[n];
    const line = raw.trim();
    if (/^#{1,6}\s/.test(line)) {
      inSources = /出典|sources?|参考|參考|來源/i.test(line);
      continue;
    }
    if (inSources) {
      for (const u of line.match(URL_RE) ?? []) addUrl(u);
      continue;
    }
    if (!line.startsWith('|')) continue;
    if (SEP_RE.test(line)) continue;
    const cells = splitRow(line);
    if (HEADER_RE.test(cells[0] ?? '')) continue;
    const content = (cells[0] ?? '').replace(/^`|`$/g, '').trim();
    if (!content) {
      skipped.push({ line: n + 1, text: line.slice(0, 80), reason: '第一欄(表記)是空的' });
      continue;
    }
    if (content.length > 60) {
      skipped.push({ line: n + 1, text: line.slice(0, 80), reason: '表記超過 60 字,不像一個詞' });
      continue;
    }
    const reads = (cells[1] ?? '')
      .split(READING_SPLIT)
      // 讀音中間的空白是模型的分詞習慣(「せいまい ぶあい」),拿掉;不然整條會被字集檢查剔除
      .map(s => s.replace(/[()()]/g, '').replace(/\s+/g, ''))
      .filter(Boolean);
    const row: PackMdRow = { content };
    if (reads.length) row.sounds_like = reads;
    if (cells[2]) row.kind = cells[2].slice(0, 20);
    const src = (cells[3] ?? '').match(URL_RE)?.[0];
    if (src) {
      row.source = src;
      addUrl(src);
    }
    if (cells[4]) row.note = cells[4].slice(0, 60);
    rows.push(row);
  }
  return { meta, rows, sources, skipped };
}

/** 同讀音、不同表記的詞條組(程式自己抓,不靠外部模型標)。
 *  為什麼:Speechmatics 聽到一個讀音只能挑一種寫法,兩條同讀音的詞條就是在跟自己搶——
 *  2026-10-05 新 prompt 的包裡有「真野鶴 / 魔の鶴」(都是まのつる),後者留著,
 *  這場的主角真野鶴就可能被寫成魔の鶴;「KANADEL / かなでる」也是同一型。
 *  有些組合無害(生酛 / 生モト 是同一個東西的兩種寫法),所以只標出來、由人決定,不自動刪。 */
export function findReadingCollisions(entries: { content: string; sounds_like?: string[] }[]): { reading: string; contents: string[] }[] {
  const by = new Map<string, string[]>();
  for (const e of entries) {
    for (const r of new Set(e.sounds_like ?? [])) {
      const list = by.get(r) ?? [];
      if (!list.includes(e.content)) list.push(e.content);
      by.set(r, list);
    }
  }
  return [...by.entries()].filter(([, c]) => c.length > 1).map(([reading, contents]) => ({ reading, contents }));
}
