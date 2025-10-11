// キャッシュのバージョンを更新し、すべての機能を統合した最終版
const CACHE_NAME = 'todo-grid-cache-v10';

// アプリの骨格となる静的なファイル (App Shell)
const APP_SHELL_FILES = [
  '/', // ルートもキャッシュに含める
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

      // --- カレンダーページのURLを動的に生成 ---
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
      
      console.log(`Attempting to cache app shell and ${calendarUrls.length} calendar pages.`);

      // まず、重要なApp Shellをキャッシュ
      await cache.addAll(APP_SHELL_FILES);
      
      // 次に、カレンダーのURLを一つずつキャッシュ（一つが失敗しても他は継続）
      const calendarPromises = calendarUrls.map(url => {
        return cache.add(url).catch(err => {
          console.warn(`Failed to cache calendar page ${url}:`, err);
        });
      });
      
      return Promise.all(calendarPromises);
    })
  );
});

// 古いキャッシュを削除する処理
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

// リクエストに応答する処理
self.addEventListener('fetch', event => {
  // GETリクエスト以外はネットワークに任せる
  if (event.request.method !== 'GET') {
    event.respondWith(fetch(event.request));
    return;
  }

  // PWA (standalone表示) 以外からのナビゲーションリクエストは、常にネットワークを優先
  if (event.request.mode === 'navigate' && !self.clients.url.startsWith('https://')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match(event.request);
      })
    );
    return;
  }

  // App Shellに含まれるファイルや、画像などの静的リソースは Cache First 戦略
  if (APP_SHELL_FILES.some(fileUrl => event.request.url.endsWith(fileUrl)) || event.request.destination === 'image' || event.request.destination === 'style' || event.request.destination === 'script') {
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

  // PWA内の動的なページ (タスク一覧など) は Stale-While-Revalidate 戦略
  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        const fetchPromise = fetch(event.request).then(networkResponse => {
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

