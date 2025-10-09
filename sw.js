// ▼▼▼ キャッシュのバージョンを v3 に更新 ▼▼▼
const CACHE_NAME = 'todo-grid-cache-v3';
// ▼▼▼ アイコンファイルもキャッシュ対象に追加 ▼▼▼
const urlsToCache = [
  '/',
  '/todo',
  '/static/style.css',
  '/static/images/icon-192x192.png',
  '/static/images/icon-512x512.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Opened cache and caching basic assets');
      return cache.addAll(urlsToCache);
    })
  );
});

self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
// ★ キャッシュ戦略を修正 ★
// ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
self.addEventListener('fetch', event => {
  // http/https以外のGETリクエストは無視
  if (event.request.method !== 'GET' || !event.request.url.startsWith('http')) {
    return;
  }

  const url = new URL(event.request.url);

  // APIや認証関連のルートは常にネットワークから取得 (キャッシュしない)
  if (event.request.url.includes('/api/') || url.pathname.startsWith('/login') || url.pathname.startsWith('/register') || url.pathname.startsWith('/logout')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 画像やCSSなどの静的アセットは「Cache First」戦略
  if (url.pathname.startsWith('/static/') || url.origin.includes('cdn.jsdelivr.net')) {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache => {
        return cache.match(event.request).then(response => {
          // キャッシュにあればそれを返す。なければネットワークから取得してキャッシュに保存。
          return response || fetch(event.request).then(networkResponse => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }

  // HTMLページは「Stale-While-Revalidate」戦略
  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        const fetchPromise = fetch(event.request).then(networkResponse => {
          // 正常なレスポンス(200 OK)の場合のみキャッシュに保存
          if (networkResponse && networkResponse.status === 200) {
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        }).catch(err => {
          console.warn('Network request failed, probably offline:', err);
        });
        // キャッシュがあればそれを返しつつ、裏でネットワークに更新を確認
        return response || fetchPromise;
      });
    })
  );
});

