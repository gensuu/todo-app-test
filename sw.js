// sw.js (シンプルなプレースホルダー)

// Service Workerをインストールするが、何もしない
self.addEventListener('install', (event) => {
  console.log('Service Worker installed');
});

// Service Workerを有効化するが、何もしない
self.addEventListener('activate', (event) => {
  console.log('Service Worker activated');
});

// fetchイベントをリッスンするが、何もしない（ブラウザの通常の動作に任せる）
self.addEventListener('fetch', (event) => {
  // 何もせず、ネットワークリクエストをそのまま通す
});