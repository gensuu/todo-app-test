// キャッシュのバージョンを更新
// バージョン番号を上げることで、新しいサービスワーカーがインストールされやすくなります
const CACHE_NAME = 'todo-grid-cache-v15-full-with-calendar'; // v15 に更新

// オフライン処理用のスクリプトをインポート
self.importScripts('/static/offline.js');

// アプリの全機能をキャッシュ対象に追加
const APP_SHELL_FILES = [
  // '/' を削除し、'/todo' をメインの開始点として維持
  '/todo', 
  '/add_or_edit_task',
  '/manage_templates',
  '/import',
  '/settings',
  '/scratchpad',
  '/login',
  '/register',
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
      console.log('Cache opened. Caching app shell and calendar pages...');

      // --- ▼▼▼ カレンダーページのURLを動的に生成 ▼▼▼ ---
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

      // まず、重要なApp Shellを一つずつキャッシュ（一つが失敗しても他は継続）
      // cache.addAll() を個別の cache.add().catch() で置き換えることで、
      // いずれかのファイルのキャッシュに失敗しても、他のファイルのキャッシュは試行されます。
      const appShellPromises = APP_SHELL_FILES.map(url => {
        return cache.add(url).catch(err => {
          console.error(`Failed to cache app shell file ${url}:`, err);
        });
      });
      await Promise.all(appShellPromises); // 全てのapp shellファイルのキャッシュ試行が完了するのを待つ

      // 次に、カレンダーのURLを一つずつキャッシュ（一つが失敗しても他は継続）
      const calendarPromises = calendarUrls.map(url => {
        return cache.add(url).catch(err => {
          // ログインしていない状態では /todo/YYYY-MM-DD へのアクセスが /login にリダイレクトされ、
          // cache.add() が失敗することがある。これは想定内の動作なので警告のみ表示。
          console.warn(`Failed to cache calendar page ${url}. This might be due to a redirect to the login page.`, err);
        });
      });
      
      // 全てのキャッシュ試行が完了するのを待つ
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

// バックグラウンド同期のイベントリスナー (この部分は変更なし)
self.addEventListener('sync', event => {
  if (event.tag === 'background-sync') {
      console.log('Sync event triggered!');
      event.waitUntil(sendQueueToServer()); // sendQueueToServer() は外部で定義されていると仮定
  }
});

// リクエストに応答する処理 (Cache First戦略) (この部分は変更なし)
self.addEventListener('fetch', event => {
  // GETリクエスト以外はネットワークに任せる
  if (event.request.method !== 'GET') {
    return;
  }
  
  // キャッシュを優先して応答
  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      // キャッシュがあればそれを返す
      if (cachedResponse) {
        return cachedResponse;
      }
      
      // キャッシュがなければネットワークにリクエスト
      return fetch(event.request).then(networkResponse => {
          // 正常に取得できたら、動的にキャッシュに追加
          if(networkResponse && networkResponse.ok) {
              const responseToCache = networkResponse.clone();
              caches.open(CACHE_NAME).then(cache => {
                  cache.put(event.request, responseToCache);
              });
          }
          return networkResponse;
      });
    })
  );
});