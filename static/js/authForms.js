// static/js/authForms.js

/**
 * 指定されたパスワード入力とボタンに対してパスワード表示切り替えを設定します。
 * @param {string} inputId パスワード入力フィールドの ID。
 * @param {string} buttonId トグルボタンの ID。
 */
function setupPasswordToggle(inputId, buttonId) {
    const toggleButton = document.getElementById(buttonId);
    const passwordInput = document.getElementById(inputId);
    if (!toggleButton || !passwordInput) return; // 要素が見つからない場合は終了

    toggleButton.addEventListener('click', function () {
        // type 属性を切り替え
        const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
        passwordInput.setAttribute('type', type);

        // アイコンを切り替え
        const icon = this.querySelector('i');
        if (icon) {
            icon.classList.toggle('bi-eye-fill');
            icon.classList.toggle('bi-eye-slash-fill');
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    // これらの ID が存在するページ (ログインと登録) でトグルを設定
    setupPasswordToggle('password', 'togglePassword'); // 両方で共通の ID を想定

    // フォーム送信時にローディングオーバーレイを追加
    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');

    // showLoadingOverlay が定義されているか確認 (utils.js からのはず)
    if (typeof showLoadingOverlay !== 'function') {
        console.error("showLoadingOverlay function is not defined. Ensure utils.js is loaded.");
        return; // 関数がない場合はリスナーを追加しない
    }


    if (loginForm) {
        loginForm.addEventListener('submit', () => {
            showLoadingOverlay('ログイン中...');
        });
    }
    if (registerForm) {
        registerForm.addEventListener('submit', () => {
            showLoadingOverlay('登録中...');
        });
    }
});
