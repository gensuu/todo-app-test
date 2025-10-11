// キャッシュのバージョンを更新し、新しい戦略を有効にします
const CACHE_NAME = 'todo-grid-cache-v8';

// アプリの骨格となる静的なファイル (App Shell)
const APP_SHELL_FILES = [
  '/login',
  '/register',
  '/scratchpad',
  '/static/style.css',
  '/static/images/icon-192x192.png',
  '/static/images/icon-512x512.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// Service Workerのインストール処理
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      console.log('Cache opened. Caching app shell and calendar pages...');

      // ▼▼▼ カレンダーページのURLを動的に生成 ▼▼▼
      const calendarUrls = [];
      const today = new Date();
      const daysToCache = 180; // 約半年

      // 過去の日付を生成
      for (let i = daysToCache; i > 0; i--) {
        const targetDate = new Date(today);
        targetDate.setDate(today.getDate() - i);
        const year = targetDate.getFullYear();
        const month = String(targetDate.getMonth() + 1).padStart(2, '0');
        const day = String(targetDate.getDate()).padStart(2, '0');
        calendarUrls.push(`/todo/${year}-${month}-${day}`);
      }
      
      // 未来の日付を生成 (今日を含む)
      for (let i = 0; i <= daysToCache; i++) {
        const targetDate = new Date(today);
        targetDate.setDate(today.getDate() + i);
        const year = targetDate.getFullYear();
        const month = String(targetDate.getMonth() + 1).padStart(2, '0');
        const day = String(targetDate.getDate()).padStart(2, '0');
        calendarUrls.push(`/todo/${year}-${month}-${day}`);
      }
      // ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

      const allUrlsToCache = [...APP_SHELL_FILES, ...calendarUrls];
      console.log(`Attempting to cache ${allUrlsToCache.length} URLs.`);

      // cache.addAllは一つでも失敗すると全体が失敗するため、
      // 重要なApp Shellだけを先にキャッシュし、カレンダーは個別に追加します。
      await cache.addAll(APP_SHELL_FILES);
      
      // カレンダーのURLは一つずつキャッシュを試みる
      const calendarPromises = calendarUrls.map(url => {
        return cache.add(url).catch(err => {
          console.warn(`Failed to cache calendar page ${url}:`, err);
        });
      });
      
      return Promise.all(calendarPromises);
    })
  );
});

// 古いキャッシュを削除する処理 (変更なし)
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

// リクエストに応答する処理 (変更なし)
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  const url = new URL(event.request.url);

  if (APP_SHELL_FILES.some(fileUrl => url.pathname === new URL(fileUrl, self.location.origin).pathname)) {
    event.respondWith(
      caches.match(event.request).then(cachedResponse => {
        return cachedResponse || fetch(event.request).then(networkResponse => {
          return caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }
  
  event.respondWith(
    fetch(event.request)
      .then(networkResponse => {
        return caches.open(CACHE_NAME).then(cache => {
          if (networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        });
      })
      .catch(() => {
        console.log('Network request failed, trying to serve from cache for:', event.request.url);
        return caches.match(event.request);
      })
  );
});

