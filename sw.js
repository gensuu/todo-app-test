// キャッシュのバージョンを更新し、新しい戦略を有効にします
const CACHE_NAME = 'todo-grid-cache-v7';

// アプリの骨格となる静的なファイル (App Shell)
// これらはインストール時に一度だけキャッシュされます
const APP_SHELL_FILES = [
  // PWAの動作に不可欠な静的ページ
  '/login',
  '/register',
  '/scratchpad',
  // CSS, JS, 画像など
  '/static/style.css',
  '/static/images/icon-192x192.png',
  '/static/images/icon-512x512.png',
  // 外部ライブラリ
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// Service Workerのインストール処理
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Cache opened. Caching app shell...');
      return cache.addAll(APP_SHELL_FILES);
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
  // POSTリクエストやAPIへのリクエストは常にネットワークへ
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  const url = new URL(event.request.url);

  // App Shellに含まれるファイルへのリクエストは、まずキャッシュから返す (Cache First)
  // これにより、ログインページや今からTodoなどがオフラインでも瞬時に表示されます
  if (APP_SHELL_FILES.some(fileUrl => url.pathname === new URL(fileUrl, self.location.origin).pathname)) {
    event.respondWith(
      caches.match(event.request).then(cachedResponse => {
        return cachedResponse || fetch(event.request).then(networkResponse => {
          // キャッシュになかった場合はネットワークから取得し、キャッシュに保存する
          return caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }
  
  // ▼▼▼ タスク一覧など、動的なHTMLページに対する戦略 ▼▼▼
  // 「Network Falling Back to Cache」
  event.respondWith(
    // まずネットワークからの取得を試みる
    fetch(event.request)
      .then(networkResponse => {
        // 取得に成功したら、キャッシュを更新してからレスポンスを返す
        return caches.open(CACHE_NAME).then(cache => {
          // 正常なレスポンスのみキャッシュする
          if (networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        });
      })
      .catch(() => {
        // ネットワークに失敗した場合（オフライン）、キャッシュから応答を試みる
        console.log('Network request failed, trying to serve from cache for:', event.request.url);
        return caches.match(event.request);
      })
  );
});

