// キャッシュのバージョンを更新
const CACHE_NAME = 'todo-grid-cache-v12';

// 新しいオフライン用JSをインポート
self.importScripts('/static/offline.js');

// アプリの骨格となる静的なファイル (App Shell)
const APP_SHELL_FILES = [
  '/', 
  '/login',
  '/register',
  '/scratchpad',
  '/static/style.css',
  '/static/offline.js',
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
      console.log('Cache opened. Caching app shell...');
      await cache.addAll(APP_SHELL_FILES);
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

// バックグラウンド同期のイベントリスナー
self.addEventListener('sync', event => {
  if (event.tag === 'background-sync') {
      console.log('Sync event triggered!');
      event.waitUntil(sendQueueToServer());
  }
});

// リクエストに応答する処理
self.addEventListener('fetch', event => {
  // GETリクエスト以外はネットワークに任せる
  if (event.request.method !== 'GET') {
    return;
  }

  const url = new URL(event.request.url);

  // ▼▼▼ 【修正点】scratchpadへのリクエストを特別に処理 ▼▼▼
  // URLにクエリパラメータ(?new=trueなど)が付いていても、キャッシュから /scratchpad を探して返す
  if (url.pathname === '/scratchpad') {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache => {
        return cache.match('/scratchpad').then(cachedResponse => {
          const fetchPromise = fetch(event.request).then(networkResponse => {
            // オンライン時は最新のものをキャッシュに保存
            if (networkResponse && networkResponse.status === 200) {
              cache.put('/scratchpad', networkResponse.clone());
            }
            return networkResponse;
          });
          // オフラインならキャッシュを返し、オンラインならネットワークの結果を優先
          return cachedResponse || fetchPromise;
        });
      })
    );
    return; // このリクエストの処理はここまで
  }
  // ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

  // その他のリクエストは Stale-While-Revalidate 戦略
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