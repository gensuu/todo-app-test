// static/js/noise.js

document.addEventListener('DOMContentLoaded', () => {
    const noiseBtn = document.getElementById('noise-toggle-btn');
    const noiseIcon = noiseBtn?.querySelector('i');
    const volumeSlider = document.getElementById('noise-volume-slider');
    const VOLUME_STORAGE_KEY = 'brownNoiseVolume'; // localStorage 用キー

    let noise = null;
    let isPlaying = false;
    let userInteractionDone = false; // ユーザー操作が行われたかのフラグ

    // --- 音量の復元 ---
    if (volumeSlider) {
        const savedVolume = localStorage.getItem(VOLUME_STORAGE_KEY);
        if (savedVolume !== null) {
            volumeSlider.value = savedVolume; // ストレージからスライダー値を設定
        }
    }

    /** Tone.js AudioContext と Noise generator を初期化 */
    async function initializeAudio() {
        // 既に初期化され実行中の場合
        if (Tone.context.state === 'running' && noise) {
            return true;
        }
        // AudioContext は多くのブラウザで開始にユーザー操作が必要
        if (!userInteractionDone) {
             console.log('AudioContext requires user interaction first.');
             // オプション: ユーザーにプロンプトを表示するか、ボタンクリック自体に依存
             return false;
        }
        try {
            // AudioContext が実行中でなければ開始
            if (Tone.context.state !== 'running') {
                await Tone.start();
                console.log('AudioContext started!');
            }
            // noise generator が存在しなければ作成
            if (!noise) {
                noise = new Tone.Noise("brown").toDestination();
                // スライダーの現在の値（復元済みまたはデフォルト）に基づいて初期音量を設定
                if (volumeSlider) {
                    noise.volume.value = parseFloat(volumeSlider.value);
                }
                console.log('Brown noise generator initialized.');
            }
            return true;
        } catch (error) {
            console.error("Failed to initialize Tone.js:", error);
            alert("音声の初期化に失敗しました。"); // 本番環境ではモーダル/トーストを使用
            return false;
        }
    }

    // --- イベントリスナー ---

    // 最初のユーザー操作を記録 (AudioContext に必要)
    document.body.addEventListener('click', () => { userInteractionDone = true; }, { once: true });

    // トグルボタンクリック
    if (noiseBtn && noiseIcon && volumeSlider) {
        noiseBtn.addEventListener('click', async () => {
            userInteractionDone = true; // 操作フラグが設定されていることを確認
            const initialized = await initializeAudio();
            if (!initialized) return; // 音声を開始できなかった場合は処理を続行しない

            if (isPlaying) { // --- ノイズ停止 ---
                noise.stop();
                noiseIcon.classList.remove('bi-volume-up-fill');
                noiseIcon.classList.add('bi-earbuds');
                noiseBtn.classList.remove('active');
                volumeSlider.style.display = 'none'; // スライダーを非表示
                isPlaying = false;
                console.log("Brown noise stopped.");
            } else { // --- ノイズ開始 ---
                try {
                    // 開始前にコンテキストが実行中であることを確認
                    if (Tone.context.state !== 'running') { await Tone.start(); }
                    // 正しい値で初期化されなかった場合に備えて再度音量を設定
                    if(volumeSlider) noise.volume.value = parseFloat(volumeSlider.value);
                    noise.start();
                    noiseIcon.classList.remove('bi-earbuds');
                    noiseIcon.classList.add('bi-volume-up-fill');
                    noiseBtn.classList.add('active');
                    volumeSlider.style.display = 'block'; // スライダーを表示
                    isPlaying = true;
                    console.log("Brown noise started.");
                } catch (error) {
                     console.error("Failed to start noise:", error);
                     alert("ノイズの再生に失敗しました。"); // モーダル/トーストを使用
                }
            }
        });

        // 音量スライダー入力
        volumeSlider.addEventListener('input', (e) => {
            const volumeDb = parseFloat(e.target.value);
            if (noise) {
                noise.volume.value = volumeDb; // Tone.js の音量を更新
            }
            // 新しい音量を localStorage に保存
            localStorage.setItem(VOLUME_STORAGE_KEY, volumeDb.toString());
            // console.log("Volume set to:", volumeDb, "dB"); // オプションのログ
        });
    } else {
        console.warn("Noise control elements not found.");
    }

    /*
    * バックグラウンド再生に関する注意:
    * Web Audio API のバックグラウンド再生は、ブラウザ/OSの制限により保証されません。
    * 特にモバイル (iOS) では制限が厳しいです。PWAとしてインストールしても、
    * これを完全に解決できない場合があります。
    * 信頼性の高いバックグラウンドオーディオには通常、ネイティブアプリ開発が必要です。
    */
});

