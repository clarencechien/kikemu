/* 聽譯中保持螢幕常亮(Screen Wake Lock API)。

   為什麼需要:kikemu 一場導覽是連續收音,不是按一下講一句。螢幕一鎖,
   瀏覽器就把頁面丟到背景——AudioContext 被暫停、WebSocket 可能被收掉,
   字幕整段斷掉(PRD §8 已知風險 1)。保持螢幕常亮是最直接的防線。

   瀏覽器的規則(也是這支要處理的事):
   - 頁面一被隱藏(切 App、鎖屏),wake lock 會被**系統自動釋放**——
     回到前景時要自己重新要一次,不會自己回來
   - 要求可能被拒(省電模式、頁面不在前景),拒絕不能當成錯誤卡住聽譯,
     只能明講「螢幕可能會自己鎖」
   - 不支援的瀏覽器:同樣明講,請使用者把「自動鎖定」調長

   ⚠️ iOS 的實際行為(尤其「加入主畫面」的 PWA 模式)**沒有真機驗證過**,
   跟 PRD §8 的 iOS 連續收音是同一批待驗項目。 */

type Notify = (text: string) => void;

let sentinel: WakeLockSentinel | null = null;
let wanted = false; // 目前是否「應該」常亮(聽譯中)
let warned = false; // 不支援 / 被拒的提示一場只講一次

const supported = () => typeof navigator !== 'undefined' && 'wakeLock' in navigator;

async function acquire(notify: Notify) {
  if (!wanted || sentinel || document.visibilityState !== 'visible') return;
  if (!supported()) {
    if (!warned) {
      warned = true;
      notify('這台裝置不支援保持螢幕常亮——請把系統的「自動鎖定」調長,螢幕一鎖聽譯就可能中斷');
    }
    return;
  }
  try {
    sentinel = await navigator.wakeLock.request('screen');
    // 系統自己收回(切到背景、省電)時把狀態清掉,回前景才會重新要
    sentinel.addEventListener('release', () => {
      sentinel = null;
    });
  } catch (e) {
    sentinel = null;
    if (!warned) {
      warned = true;
      const name = (e as DOMException)?.name ?? '';
      notify(
        `沒辦法保持螢幕常亮${name ? `(${name})` : ''}——可能是省電模式。` +
          '螢幕一鎖聽譯就可能中斷,請關閉省電模式或把「自動鎖定」調長',
      );
    }
  }
}

/** 開始聽譯時呼叫(最好在使用者按下開始的那一刻,某些瀏覽器要求在前景) */
export function keepScreenOn(notify: Notify) {
  // 重入保護:前一次沒 release 就又呼叫時,先拆掉舊的監聽,不然會疊兩個 visibilitychange
  // (冒煙測試抓到的:同一頁連呼叫兩次,監聽器與提示都變兩份)
  if (onVisible) document.removeEventListener('visibilitychange', onVisible);
  const first = !wanted;
  wanted = true;
  if (first) warned = false;
  void acquire(notify);
  // 回到前景時重新要:系統在頁面隱藏時會自動釋放
  onVisible = () => {
    if (document.visibilityState === 'visible') void acquire(notify);
  };
  document.addEventListener('visibilitychange', onVisible);
}

let onVisible: (() => void) | null = null;

/** 停止聽譯時呼叫:放掉常亮,讓螢幕照系統設定自己鎖 */
export function releaseScreen() {
  wanted = false;
  if (onVisible) document.removeEventListener('visibilitychange', onVisible);
  onVisible = null;
  const s = sentinel;
  sentinel = null;
  s?.release().catch(() => {});
}

/** 目前是否真的拿到常亮(狀態列顯示用) */
export const screenKeptOn = () => !!sentinel;
