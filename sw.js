// キャッシュのバージョンを更新し、新しい戦略を有効にします
const CACHE_NAME = 'todo-grid-cache-v6'; 

// キャッシュするファイルのリスト（HTMLも再度含めます）
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

// Service Workerのインストール処理
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('Opened cache and caching basic assets');
      // 一つのファイルのキャッシュに失敗しても、全体が失敗しないようにPromiseでラップ
      const promises = urlsToCache.map(url => {
        return cache.add(url).catch(err => {
          console.warn(`Failed to cache ${url}:`, err);
        });
      });
      return Promise.all(promises);
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
  // GETリクエスト以外は無視
  if (event.request.method !== 'GET') {
    return;
  }

  const url = new URL(event.request.url);

  // APIや認証関連のルートは常にネットワークから取得 (変更なし)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/login') || url.pathname.startsWith('/register') || url.pathname.startsWith('/logout')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 画像やCSSなどの静的アセットは「Cache First」戦略 (変更なし)
  if (url.pathname.startsWith('/static/') || url.origin.includes('cdn.jsdelivr.net')) {
    event.respondWith(
      caches.match(event.request).then(response => {
        return response || fetch(event.request).then(networkResponse => {
          return caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }

  // ▼▼▼ HTMLページに対する新しい戦略「Stale-While-Revalidate」 ▼▼▼
  event.respondWith(
    caches.open(CACHE_NAME).then(cache => {
      return cache.match(event.request).then(response => {
        // まずキャッシュを返しつつ、裏側でネットワークに更新を確認しにいく
        const fetchPromise = fetch(event.request).then(networkResponse => {
          // 正常なレスポンスの場合のみキャッシュを更新
          if (networkResponse && networkResponse.status === 200) {
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        }).catch(err => {
            console.warn('Network request failed, probably offline:', err);
            // オフラインの場合はキャッシュが返されているので、ここでは何もしない
        });

        // キャッシュがあればそれを即座に返す。なければネットワークの結果を待つ。
        return response || fetchPromise;
      });
    })
  );
});