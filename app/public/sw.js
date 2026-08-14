/* 離線殼(沿 sukemu public/sw.js):
   - 檔名帶 hash 的資產(/assets/、/icons/)才走快取優先——改內容一定換檔名,快取不會過期
   - **其餘同源檔案一律網路優先**(拿得到新版就用新版,離線才退回快取)
   - /api/ 與 /ws 一律不進 SW

   為什麼不能全部快取優先(實測踩過):/admin.js、/pcm-worklet.js 的檔名不帶 hash,
   一旦進了快取就凍結,重新部署也換不掉——症狀是「HTML 是新的、行為是舊的」,
   例如 admin 的語言下拉在新版 HTML 裡存在,卻被舊版 JS 漏掉而永遠空白。
   改版本號會讓 activate 清掉舊快取,已安裝的客戶端也能自己痊癒。 */
const CACHE = 'kikemu-shell-v2';
const SHELL = ['/', '/manifest.json', '/icons/icon-192.png', '/icons/icon-512.png'];
/** 只有這些路徑的檔名帶 hash 或永不改內容,才可以快取優先 */
const IMMUTABLE = /^\/(assets|icons)\//;

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

/** 網路優先:成功就順手更新快取,失敗(離線)才退回快取 */
function networkFirst(req, cacheKey = req) {
  return fetch(req)
    .then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(cacheKey, copy));
      }
      return res;
    })
    .catch(() => caches.match(cacheKey).then(hit => hit || caches.match('/')));
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (
    e.request.method !== 'GET' ||
    url.origin !== location.origin ||
    url.pathname.startsWith('/api/') ||
    url.pathname === '/ws'
  ) return;

  if (e.request.mode === 'navigate') {
    // 只有首頁能當離線殼:先前不分頁面都寫進 '/',逛過 /admin 之後離線開 app 會看到管理頁
    e.respondWith(networkFirst(e.request, url.pathname === '/' ? '/' : e.request));
    return;
  }

  if (IMMUTABLE.test(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then(
        hit =>
          hit ||
          fetch(e.request).then(res => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then(c => c.put(e.request, copy));
            }
            return res;
          }),
      ),
    );
    return;
  }

  e.respondWith(networkFirst(e.request));
});
