// sw.js

// offline.jsの同期関数をインポート
self.importScripts('/static/offline.js');

const CACHE_NAME = 'todo-grid-cache-v23'; // 新しいキャッシュバージョン

const APP_SHELL_URLS = [
  '/',
  '/todo',
  '/add_or_edit_task',
  '/manage_templates',
  '/import',
  '/settings',
  '/scratchpad',
  '/login',
  '/register',
  '/static/style.css',
  '/static/calendar.js',
  '/static/offline.js',
  '/static/images/icon-192x192.png',
  '/static/images/icon-512x512.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.mode === 'navigate') {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache => {
        return fetch(event.request).then(networkResponse => {
          cache.put(event.request, networkResponse.clone());
          return networkResponse;
        }).catch(() => {
          return cache.match(event.request);
        });
      })
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      const fetchPromise = fetch(event.request).then(networkResponse => {
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, networkResponse.clone());
        });
        return networkResponse;
      });
      return cachedResponse || fetchPromise;
    })
  );
});

// バックグラウンド同期イベント（接続回復時など）
self.addEventListener('sync', event => {
    if (event.tag === 'background-sync') {
        event.waitUntil(sendQueueToServer());
    }
});

// --- ▼▼▼ 修正点: ページからの即時同期命令を受け取るリスナー ▼▼▼ ---
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'SYNC_NOW') {
        console.log('Service Worker: Received SYNC_NOW command from client.');
        // sendQueueToServerを実行し、完了するまでService Workerを止めない
        event.waitUntil(sendQueueToServer());
    }
});
