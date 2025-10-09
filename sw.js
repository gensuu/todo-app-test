const CACHE_NAME = 'todo-grid-cache-v2';
const urlsToCache = [
  '/',
  '/todo',
  '/static/style.css'
];

// インストール時に基本的なファイルをキャッシュする
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Opened cache and caching basic assets');
      return cache.addAll(urlsToCache);
    })
  );
});

// Stale-While-Revalidate戦略
self.addEventListener('fetch', event => {
  // POSTリクエストやAPIリクエストはキャッシュしない
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        // 1. まずキャッシュから返す (Stale)
        const fetchPromise = fetch(event.request).then(networkResponse => {
          // 2. 裏でネットワークから取得し、キャッシュを更新 (Revalidate)
          cache.put(event.request, networkResponse.clone());
          return networkResponse;
        });

        return response || fetchPromise;
      });
    })
  );
});

// 古いキャッシュを削除
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

