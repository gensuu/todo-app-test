// static/js/pwa.js

// --- Service Worker 登録 ---
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        // Flask の url_for('main.serve_sw') によって生成された URL を使用
        // この URL は layout.html の script ブロックでグローバル変数 SW_URL として渡す必要がある
        if (typeof SW_URL !== 'undefined') {
            navigator.serviceWorker.register(SW_URL)
                .then(registration => {
                    console.log('Service Worker registered successfully. Scope:', registration.scope);
                    // オプション: 更新などのロジックを追加
                })
                .catch(err => {
                    console.error('Service Worker registration failed:', err);
                });
        } else {
            console.error("SW_URL is not defined. Cannot register Service Worker.");
        }
    });

     // オプション: Service Worker からのメッセージをリッスン (例: 同期完了)
     navigator.serviceWorker.addEventListener('message', event => {
        if (event.data && event.data.type === 'SYNC_COMPLETED') {
            console.log('Received SYNC_COMPLETED message from SW.');
            // オプション: ユーザーに成功メッセージを表示するか、データを更新
            // 自動リロードは邪魔になる可能性があるので注意
            // リフレッシュボタンを有効にするか、控えめな通知を表示する方が良いかも
            // 例:
            // const syncStatus = document.getElementById('sync-status');
            // if (syncStatus) syncStatus.textContent = '同期完了';
            // setTimeout(() => { if (syncStatus) syncStatus.textContent = ''; }, 3000);
        }
     });

} else {
    console.log('Service Worker not supported in this browser.');
}


// --- PWA インストールボタンのロジック (通常は設定ページなどで必要) ---
document.addEventListener('DOMContentLoaded', () => {
    let deferredPrompt;
    const installButton = document.getElementById('install-app-button'); // settings.html のボタンID

    if (installButton) {
        window.addEventListener('beforeinstallprompt', (e) => {
            // モバイルでのミニインフォバーの表示を防止
            e.preventDefault();
            // 後でトリガーできるようにイベントを保持
            deferredPrompt = e;
            // PWA をインストールできることをユーザーに通知する UI を更新
            installButton.style.display = 'block'; // ボタンを表示
            console.log('beforeinstallprompt event fired.');
        });

        installButton.addEventListener('click', async () => {
            if (!deferredPrompt) {
                console.log('Install prompt not available.');
                return; // プロンプトが利用不可
            }
            // インストールプロンプトを表示
            deferredPrompt.prompt();
            // ユーザーの応答を待つ
            const { outcome } = await deferredPrompt.userChoice;
            console.log(`User response to the install prompt: ${outcome}`);
            // プロンプトは使用済みのため、再度使用できない。クリアする。
            deferredPrompt = null;
            // インストールボタンを非表示
            installButton.style.display = 'none';
        });

        window.addEventListener('appinstalled', () => {
            // インストールをアナリティクスまたはコンソールに記録
            console.log('PWA was installed');
            // ボタンが何らかの理由でまだ表示されている場合は非表示にする
            installButton.style.display = 'none';
            deferredPrompt = null; // プロンプト参照をクリア
        });
    }
});

