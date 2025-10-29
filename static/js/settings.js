// static/js/settings.js

// setupPasswordToggle をインポートまたは定義
// utils.js が showLoadingOverlay をグローバルに定義していると仮定
// pwa.js がこのページに必要な場合にインストールボタンロジックをグローバルに処理すると仮定

/**
 * パスワード表示切り替えを設定 (独立性のために authForms.js からコピー)。
 * @param {string} inputId パスワード入力の ID。
 * @param {string} buttonId トグルボタンの ID。
 */
function setupPasswordToggleSettings(inputId, buttonId) {
    const toggleButton = document.getElementById(buttonId);
    const passwordInput = document.getElementById(inputId);
    if (!toggleButton || !passwordInput) return;

    toggleButton.addEventListener('click', function () {
        const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
        passwordInput.setAttribute('type', type);
        const icon = this.querySelector('i');
        if (icon) {
            icon.classList.toggle('bi-eye-fill');
            icon.classList.toggle('bi-eye-slash-fill');
        }
    });
}


document.addEventListener('DOMContentLoaded', () => {
    // パスワード変更フォームの表示切り替えを設定
    setupPasswordToggleSettings('current_password', 'toggleCurrentPassword');
    setupPasswordToggleSettings('new_password', 'toggleNewPassword');
    setupPasswordToggleSettings('confirm_password', 'toggleConfirmPassword');

    // フォーム送信時にローディングオーバーレイを追加
    const urlForm = document.getElementById('settings-url-form');
    const passwordForm = document.getElementById('settings-password-form');

    // showLoadingOverlay が存在するか確認
    if (typeof showLoadingOverlay !== 'function') {
        console.error("showLoadingOverlay is not defined. Ensure utils.js is loaded.");
        // これをどう処理するか決定 - サブミットボタンを無効にするか、エラーをログに記録するだけか
    } else {
        if (urlForm) {
            urlForm.addEventListener('submit', () => {
                showLoadingOverlay('URLを保存中...');
            });
        }
        if (passwordForm) {
            passwordForm.addEventListener('submit', () => {
                showLoadingOverlay('パスワードを変更中...');
            });
        }
    }

    // PWA インストールボタンのロジックは、このページにボタンが存在する場合、
    // layout.html 経由でロードされる pwa.js によって処理される想定。
});
