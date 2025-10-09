// ▼▼▼ キャッシュのバージョンを v2 に更新 ▼▼▼
const CACHE_NAME = 'todo-grid-cache-v2';
const urlsToCache = [
  '/',
  '/todo',
  '/static/style.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// インストール時にファイルをキャッシュする
self.addEventListener('install', event => {
  self.skipWaiting(); // ★ 新しいSWをすぐに有効化する
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Opened cache and caching basic assets');
      return cache.addAll(urlsToCache);
    })
  );
});

// ▼▼▼ activateイベント：古いキャッシュを削除する ▼▼▼
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME]; // このバージョンのキャッシュだけを保持
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            // ホワイトリストにない古いキャッシュは削除
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim()) // ★ すべてのタブの制御をすぐに取得
  );
});
// ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

// Stale-While-Revalidate戦略 (変更なし)
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        const fetchPromise = fetch(event.request).then(networkResponse => {
          cache.put(event.request, networkResponse.clone());
          return networkResponse;
        });
        return response || fetchPromise;
      });
    })
  );
});

