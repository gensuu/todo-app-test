// ▼▼▼ キャッシュのバージョンを v4 に更新 ▼▼▼
const CACHE_NAME = 'todo-grid-cache-v4';
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
          return response || fetch(event.request).then(networkResponse => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }

  // ★★★ ここからが修正箇所 ★★★
  // HTMLページは「Network First, falling back to Cache」戦略
  event.respondWith(
    // まずネットワークからの取得を試みる
    fetch(event.request)
      .then(networkResponse => {
        // 正常なレスポンス(200 OK)の場合のみキャッシュに保存
        if (networkResponse && networkResponse.status === 200) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseToCache);
          });
        }
        return networkResponse;
      })
      .catch(error => {
        // ネットワークに失敗した場合、キャッシュから取得を試みる
        console.log('Network request failed, trying to serve from cache.', error);
        return caches.match(event.request);
      })
  );
});