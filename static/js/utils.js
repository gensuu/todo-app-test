// static/js/utils.js

/**
 * ローディングオーバーレイを指定されたメッセージで表示します。
 * @param {string} [message='処理中...'] - 表示するメッセージ。
 */
function showLoadingOverlay(message = '処理中...') {
    const overlay = document.getElementById('loading-overlay');
    const text = document.getElementById('loading-text');
    if (overlay) {
        if(text) text.textContent = message;
        overlay.style.display = 'flex'; // 表示するために 'flex' を使用
    }
}

/**
 * ローディングオーバーレイを非表示にします。
 */
function hideLoadingOverlay() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.style.display = 'none'; // 非表示にするために 'none' を使用
    }
}

// ページ読み込み/ナビゲーション時にオーバーレイを非表示にするグローバルリスナー
window.addEventListener('pageshow', (event) => {
    // ブラウザキャッシュを使用して戻る/進む操作をした場合に特に役立ちます
    hideLoadingOverlay();
});

