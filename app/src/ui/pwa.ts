/* PWA:SW 註冊、安裝入口、iOS 加入主畫面提示(沿 sukemu src/ui/pwa.ts)。
   安裝後以 standalone 開啟,網址列隱藏。 */

export function initPwa() {
  if (import.meta.env.PROD && 'serviceWorker' in navigator) {
    addEventListener('load', () => navigator.serviceWorker.register('/sw.js'));
  }

  const btn = document.getElementById('installBtn') as HTMLButtonElement;
  let deferred: (Event & { prompt(): Promise<void> }) | null = null;

  addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferred = e as typeof deferred & Event;
    btn.classList.remove('hidden');
  });
  btn.onclick = async () => {
    await deferred?.prompt();
    deferred = null;
    btn.classList.add('hidden');
  };
  addEventListener('appinstalled', () => btn.classList.add('hidden'));

  // iOS Safari 沒有 beforeinstallprompt,給文字指引
  const standalone =
    matchMedia('(display-mode: standalone)').matches ||
    ('standalone' in navigator && (navigator as { standalone?: boolean }).standalone);
  if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone) {
    document.getElementById('iosHint')!.classList.remove('hidden');
  }
}
