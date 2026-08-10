/* 字幕流渲染:每句一張卡(ja 原文 ink;zh 譯文 --brand 琥珀,定稿後浮現)。
   listen.ts(真連線)與 demo.ts(登入前預覽)共用這層 DOM——
   demo 只碰得到這裡,結構上永遠碰不到 WS 與麥克風。 */

import type { Line } from '../types';

const stream = () => document.getElementById('stream')!;

let partialEl: HTMLElement | null = null;

const scrollDown = () => {
  const s = stream();
  s.scrollTop = s.scrollHeight;
};

export function clearStream() {
  stream().innerHTML = '';
  partialEl = null;
}

/** partial 暫定字:灰 ghost + 底線游標,以 opacity 過渡改寫不閃爍(實測改寫率 16%) */
export function setPartial(text: string) {
  if (!text) {
    partialEl?.remove();
    partialEl = null;
    return;
  }
  if (!partialEl) {
    partialEl = document.createElement('div');
    partialEl.className = 'card partial';
    partialEl.innerHTML = '<p class="ja ghost"><span class="ptext"></span><span class="caret" aria-hidden="true"></span></p>';
  }
  stream().appendChild(partialEl); // 永遠停在最下面
  const span = partialEl.querySelector('.ptext') as HTMLElement;
  if (span.textContent !== text) {
    span.textContent = text;
    span.classList.remove('flash');
    void span.offsetWidth; // 重觸發 opacity 過渡
    span.classList.add('flash');
  }
  scrollDown();
}

/** 一句定稿:插在 partial 卡之前;zh 欄先掛「翻譯中…」佔位 */
export function addFinal(seq: number, ja: string) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.seq = String(seq);
  card.innerHTML = '<p class="ja"></p><p class="zh pending">…</p>';
  (card.querySelector('.ja') as HTMLElement).textContent = ja;
  if (partialEl && partialEl.parentElement) stream().insertBefore(card, partialEl);
  else stream().appendChild(card);
  scrollDown();
}

const cardOf = (seq: number) =>
  stream().querySelector<HTMLElement>(`.card[data-seq="${seq}"] .zh`);

export function setZh(seq: number, text: string) {
  const el = cardOf(seq);
  if (!el) return;
  el.classList.remove('pending', 'zh-err');
  el.textContent = text;
  el.onclick = null;
  scrollDown();
}

/** 翻譯失敗:明講「譯文暫缺・點擊重試」(不假裝成功);onRetry 為 null 時只顯示暫缺 */
export function setZhError(seq: number, onRetry: ((seq: number) => void) | null) {
  const el = cardOf(seq);
  if (!el) return;
  el.classList.remove('pending');
  el.classList.add('zh-err');
  el.textContent = onRetry ? '譯文暫缺・點擊重試' : '譯文暫缺';
  el.onclick = onRetry ? () => onRetry(seq) : null;
}

/** 系統卡:斷線/額度/提示都在卡片內明講原因(不假裝成功原則) */
export function addNote(kind: 'info' | 'error', text: string) {
  const el = document.createElement('div');
  el.className = `card note ${kind}`;
  el.textContent = text;
  if (partialEl && partialEl.parentElement) stream().insertBefore(el, partialEl);
  else stream().appendChild(el);
  scrollDown();
}

/** 收整場字幕(存 IndexedDB 用);只收有 seq 的定稿卡 */
export function collectLines(): Line[] {
  return [...stream().querySelectorAll<HTMLElement>('.card[data-seq]')].map(card => {
    const zhEl = card.querySelector('.zh')!;
    const failed = zhEl.classList.contains('pending') || zhEl.classList.contains('zh-err');
    return {
      ja: card.querySelector('.ja')!.textContent ?? '',
      zh: failed ? null : (zhEl.textContent ?? ''),
    };
  });
}

/** 歷史回放:把一場舊字幕載回流裡(唯讀) */
export function showLines(lines: Line[]) {
  clearStream();
  lines.forEach((l, i) => {
    addFinal(i + 1, l.ja);
    if (l.zh) setZh(i + 1, l.zh);
    else setZhError(i + 1, null);
  });
}

/* 輕量 toast(拾音指引等一次性提示) */
let toastTimer: ReturnType<typeof setTimeout> | undefined;
export function toast(msg: string) {
  const t = document.getElementById('toast')!;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 5000);
}
