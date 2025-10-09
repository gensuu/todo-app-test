const CACHE_NAME = 'todo-grid-cache-v2';
const urlsToCache = [
  '/',
  '/todo',
  '/static/style.css',
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
  // http/https以外のGETリクエストは無視する
  if (event.request.method !== 'GET' || !event.request.url.startsWith('http')) {
    return;
  }

  // APIや認証関連のルートは常にネットワークから取得する
  if (event.request.url.includes('/api/') || event.request.url.includes('/login') || event.request.url.includes('/register') || event.request.url.includes('/logout')) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        const fetchPromise = fetch(event.request).then(networkResponse => {
          // ★ 正常なレスポンス(200 OK)の場合のみキャッシュに保存する
          if (networkResponse && networkResponse.status === 200) {
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        }).catch(err => {
          console.warn('Network request failed, probably offline:', err);
        });

        return response || fetchPromise;
      });
    })
  );
});

