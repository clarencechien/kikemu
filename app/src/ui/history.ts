/* 歷史面板:瀏覽本機 IndexedDB 裡的字幕紀錄,點開回看、可匯出、可刪除。
   紀錄只存在裝置上(PRD §2:伺服器不留任何內容)——所以「帶得走」很重要:
   使用者想留存就得自己匯出一份,我們這邊沒有雲端副本可以事後補給他。 */

import { sessionStore, type SessionRecord } from '../db';
import { showLines, toast } from './cards';

const $ = (id: string) => document.getElementById(id)!;

const LANG_NAME: Record<string, string> = {
  ja: '日本語',
  ko: '한국어',
  en: 'English',
  cmn_en: '中文・English',
};

const stamp = (iso: string) => {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
};

/** 對照式純文字:一句原文、一句譯文,方便直接貼進筆記 */
function toTxt(rec: SessionRecord): string {
  const src = rec.lang ? LANG_NAME[rec.lang] ?? rec.lang : null;
  const head = [
    'kikemu 聽譯紀錄',
    `時間:${new Date(rec.at).toLocaleString('zh-TW')}`,
    // 舊紀錄沒存語言,就別硬掰一個
    ...(src ? [`語言:${src} → 台灣正體中文`] : []),
    `場景包:${rec.packName ?? '(未使用)'}`,
    `長度:${Math.round(rec.seconds)} 秒・${rec.lines.length} 句`,
    '',
    '────────────────────────────',
    '',
  ];
  const body = rec.lines.map((l, i) => `[${i + 1}]\n原文  ${l.ja}\n譯文  ${l.zh ?? '(未翻出)'}\n`);
  return head.concat(body).join('\n');
}

/* Markdown:給丟進 LLM 用的(叫它摘要、抓重點、整理成遊記)。
   刻意做成「metadata 條列 + 一張對照表」——LLM 對表格的欄位對應解析得最穩,
   而且原文與譯文同列,不會像純文字那樣在長段落裡對錯行。 */
function toMd(rec: SessionRecord): string {
  const src = rec.lang ? LANG_NAME[rec.lang] ?? rec.lang : null;
  // 表格欄位裡的 | 會拆欄、換行會斷表,先中和掉
  const cell = (s: string) => s.replace(/\|/g, '\\|').replace(/\r?\n/g, ' ').trim();
  const when = new Date(rec.at);
  const head = [
    `# kikemu 聽譯紀錄 ${when.toLocaleString('zh-TW')}`,
    '',
    `- 時間:${rec.at}`,
    ...(src ? [`- 來源語言:${src}(\`${rec.lang}\`)`] : []),
    '- 譯文語言:台灣正體中文',
    `- 場景包:${rec.packName ?? '(未使用)'}`,
    `- 長度:${Math.round(rec.seconds)} 秒・${rec.lines.length} 句`,
    '',
    '> 逐句字幕。原文為語音辨識結果(可能有錯字),譯文由 LLM 逐句翻譯;',
    '> `(未翻出)` 表示該句翻譯失敗,不是原文沒有內容。',
    '',
    `| # | 原文${src ? `(${src})` : ''} | 譯文(台灣正體) |`,
    '|---:|---|---|',
  ];
  const rows = rec.lines.map((l, i) => `| ${i + 1} | ${cell(l.ja)} | ${l.zh ? cell(l.zh) : '(未翻出)'} |`);
  return head.concat(rows, ['']).join('\n');
}

/** CSV:給要進試算表校對的人。BOM + CRLF,Excel 才不會把中日文吃成亂碼 */
function toCsv(rec: SessionRecord): string {
  const q = (s: string) => `"${s.replace(/"/g, '""')}"`;
  const rows = [
    ['#', '原文', '譯文'],
    ...rec.lines.map((l, i) => [String(i + 1), l.ja, l.zh ?? '']),
  ];
  return '\ufeff' + rows.map(r => r.map(q).join(',')).join('\r\n') + '\r\n';
}

function download(name: string, mime: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  // 立刻 revoke 在部分瀏覽器會讓下載中斷,等一拍再收
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

type Fmt = 'md' | 'txt' | 'csv';
const FORMATS: Record<Fmt, { mime: string; make: (r: SessionRecord) => string; hint: string }> = {
  md: { mime: 'text/markdown', make: toMd, hint: '匯出 Markdown(對照表格,適合丟給 LLM 摘要)' },
  txt: { mime: 'text/plain', make: toTxt, hint: '匯出對照式純文字(原文 + 譯文)' },
  csv: { mime: 'text/csv', make: toCsv, hint: '匯出 CSV(原文 / 譯文 兩欄,可進試算表)' },
};

function exportRecord(rec: SessionRecord, fmt: Fmt) {
  if (!rec.lines.length) {
    toast('這場沒有字幕可以匯出');
    return;
  }
  const f = FORMATS[fmt];
  download(`kikemu-${stamp(rec.at)}.${fmt}`, f.mime, f.make(rec));
  toast(`已匯出 ${rec.lines.length} 句(原文 + 譯文)`);
}

export function initHistory(guard: { isBusy: () => boolean }) {
  const panel = $('histPanel');
  const list = $('histList');
  const count = $('histCount');

  const close = () => panel.classList.add('hidden');
  ($('histBtn') as HTMLButtonElement).onclick = () => {
    panel.classList.remove('hidden');
    void render();
  };
  ($('histClose') as HTMLButtonElement).onclick = close;
  panel.addEventListener('click', e => {
    if (e.target === panel) close();
  });

  async function render() {
    let recs: SessionRecord[] = [];
    try {
      recs = await sessionStore.list();
    } catch {
      /* 私密模式等情況拿不到 IndexedDB,顯示空清單 */
    }
    count.textContent = recs.length ? `${recs.length} 場・只存在此裝置` : '';
    if (!recs.length) {
      list.innerHTML = '<div class="histEmpty">還沒有聽譯紀錄——按「開始聽」聽一場看看。<br>字幕只存在這台裝置上。</div>';
      return;
    }
    list.innerHTML = '';
    recs.forEach(rec => {
      const when = new Date(rec.at).toLocaleString('zh-TW', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
      const item = document.createElement('div');
      item.className = 'histItem';
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      item.innerHTML =
        '<div class="histTx"><div class="t"></div><div class="m"></div></div>' +
        '<span class="histExpGroup">' +
        (Object.keys(FORMATS) as Fmt[])
          .map(f => `<button class="histExp" data-fmt="${f}" title="${FORMATS[f].hint}">${f.toUpperCase()}</button>`)
          .join('') +
        '</span><button class="histDel">刪除</button>';
      (item.querySelector('.t') as HTMLElement).textContent =
        rec.lines[0]?.zh || rec.lines[0]?.ja || '(空白場次)';
      (item.querySelector('.m') as HTMLElement).textContent =
        `${when} · ${rec.packName ?? '無場景包'} · ${rec.lines.length} 句`;
      item.onclick = e => {
        const exp = (e.target as HTMLElement).closest<HTMLElement>('.histExp');
        if (exp) {
          exportRecord(rec, (exp.dataset.fmt as Fmt) ?? 'md');
          return;
        }
        if ((e.target as HTMLElement).closest('.histDel')) {
          if (confirm('刪除這場紀錄?')) sessionStore.remove(rec.id!).then(render);
          return;
        }
        if (guard.isBusy()) {
          toast('聽譯進行中,先停止再回看紀錄');
          return;
        }
        showLines(rec.lines);
        toast('回放紀錄——按「開始聽」開新的一場');
        close();
      };
      list.appendChild(item);
    });
  }
}
