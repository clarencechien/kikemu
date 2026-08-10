/* 歷史面板:瀏覽本機 IndexedDB 裡的字幕紀錄,點開回看、可刪除。
   紀錄只存在裝置上(PRD §2:伺服器不留任何內容)。 */

import { sessionStore, type SessionRecord } from '../db';
import { showLines, toast } from './cards';

const $ = (id: string) => document.getElementById(id)!;

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
      item.innerHTML = '<div class="histTx"><div class="t"></div><div class="m"></div></div><button class="histDel">刪除</button>';
      (item.querySelector('.t') as HTMLElement).textContent =
        rec.lines[0]?.zh || rec.lines[0]?.ja || '(空白場次)';
      (item.querySelector('.m') as HTMLElement).textContent =
        `${when} · ${rec.packName ?? '無場景包'} · ${rec.lines.length} 句`;
      item.onclick = e => {
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
