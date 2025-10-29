// static/js/index.js

// このスクリプトより前に layout.html で calendar.js がロードされていることを確認
// また、utils.js (show/hideLoadingOverlay) もこのスクリプトより前にロードされていると仮定

document.addEventListener('DOMContentLoaded', () => {
    // --- Jinja から渡された変数を取得 ---
    const containerElement = document.getElementById('index-data-container'); // index.html に追加する必要がある
    if (!containerElement) {
        console.error("Data container #index-data-container not found!");
        return; // データコンテナがない場合は実行停止
    }
    const CURRENT_DATE_STR = containerElement.dataset.currentDate;
    const TODAY_STR = containerElement.dataset.today;
    const taskCountsJson = containerElement.dataset.taskCounts || '{}';
    // 完了APIのベースURL (末尾のスラッシュなし) を渡す: url_for('main.complete_subtask_api', subtask_id=0)[:-2] のようなイメージ
    const API_COMPLETE_URL_BASE = containerElement.dataset.apiCompleteUrlBase;

    // 不可欠なデータの検証
    if (!CURRENT_DATE_STR || !TODAY_STR || !API_COMPLETE_URL_BASE) {
         console.error("Essential data (dates or API URL) missing from data container attributes.");
         return;
    }


    const taskFocusModal = document.getElementById('taskFocusModal');

    // 日付文字列を Date オブジェクトに変換
    const currentDate = new Date(CURRENT_DATE_STR + "T00:00:00"); // 一貫性のために時刻部分を追加
    const today = new Date(TODAY_STR + "T00:00:00");
    let taskCounts = {};
     try {
         taskCounts = JSON.parse(taskCountsJson);
     } catch(e) { console.error("Error parsing task counts JSON:", e); }


    // --- 関数 ---
    /** 動的UI要素 (グリッド、サマリー、プログレスサークル) を更新 */
    function updateDynamicUI(data) {
        const completedCountDisplay = document.getElementById('completed-count-display');
        const totalCountDisplay = document.getElementById('total-count-display');
        const gridCells = document.querySelectorAll('.new-grid-container .grid-cell');
        const summaryStreak = document.getElementById('summary-streak');
        const summaryAverageGrids = document.getElementById('summary-average-grids');
        const progressCircle = document.querySelector('.progress-circle');
        const progressText = document.querySelector('.progress-text');

        if (completedCountDisplay) completedCountDisplay.textContent = data.completed_grid_count;
        if (totalCountDisplay) totalCountDisplay.textContent = data.total_grid_count;

        gridCells.forEach((cell, index) => {
            cell.classList.toggle('completed', index < data.completed_grid_count);
            cell.classList.toggle('task-area', index < data.total_grid_count);
        });

        if (summaryStreak) summaryStreak.textContent = data.summary.streak;
        if (summaryAverageGrids) summaryAverageGrids.textContent = data.summary.average_grids;

        if (progressCircle && progressText) {
            const percentage = data.total_grid_count > 0 ? (data.completed_grid_count / data.total_grid_count * 100) : 0;
            progressCircle.style.setProperty('--progress', `${percentage}%`);
            progressText.textContent = `${Math.round(percentage)}%`;
        }
    }

    /** サブタスク完了状態のトグルを処理 */
    async function handleTaskCompletion(subtaskId) {
        const listItem = document.querySelector(`.sub-task[data-subtask-id='${subtaskId}']`);
        const icon = listItem?.querySelector('.complete-toggle');
        const wasCompleted = listItem?.classList.contains('completed');

        // ---- 楽観的UI更新 ----
        // サーバー応答前にUIをすぐに更新して、ユーザー体験を向上させる
        if (listItem && icon) {
            listItem.classList.toggle('completed', !wasCompleted);
            icon.classList.toggle('bi-check-square-fill', !wasCompleted);
            icon.classList.toggle('bi-square', wasCompleted);
            icon.style.pointerEvents = 'none'; // 連続クリック防止
        }
        // ----------------------

        try {
            const response = await fetch(`${API_COMPLETE_URL_BASE}/${subtaskId}`, { // 特定のURLを構築
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json' // サーバーがJSONを返すことを期待
                 },
                body: JSON.stringify({ current_date: CURRENT_DATE_STR }) // コンテキストとして現在の日付を送信
            });

            // エラー応答の処理
            if (!response.ok) {
                // UIを元に戻す
                if (listItem && icon) {
                    listItem.classList.toggle('completed', wasCompleted); // 元の状態に戻す
                    icon.classList.toggle('bi-check-square-fill', wasCompleted);
                    icon.classList.toggle('bi-square', !wasCompleted);
                    icon.style.pointerEvents = 'auto';
                }
                throw new Error(`Server status: ${response.status}`);
            }

            const data = await response.json();
            if (!data.success) {
                 // UIを元に戻す
                if (listItem && icon) {
                    listItem.classList.toggle('completed', wasCompleted); // 元の状態に戻す
                    icon.classList.toggle('bi-check-square-fill', wasCompleted);
                    icon.classList.toggle('bi-square', !wasCompleted);
                    icon.style.pointerEvents = 'auto';
                }
                throw new Error(data.error || "API Error");
            }

            // --- サーバー応答に基づいてUIを確定・更新 ---
            // 複数の場所に同じサブタスクが表示されている可能性を考慮 (モーダルなど)
             document.querySelectorAll(`.sub-task[data-subtask-id='${subtaskId}']`).forEach(li => {
                const currentIcon = li.querySelector('.complete-toggle');
                if(currentIcon) {
                    // サーバーからの最終的な状態を適用
                    li.classList.toggle('completed', data.is_completed);
                    currentIcon.classList.toggle('bi-check-square-fill', data.is_completed);
                    currentIcon.classList.toggle('bi-square', !data.is_completed);
                    currentIcon.style.pointerEvents = 'auto'; // クリック可能に戻す
                }
            });

            // マスタータスクカードの状態とヘッダーを更新
            const masterCard = document.querySelector(`.master-task-card[data-master-id='${data.master_task_id}']`);
            if (masterCard) {
                const header = masterCard.querySelector('.card-header');
                // サーバーから返されたHTMLでヘッダーを更新
                if (header && data.updated_header_html) {
                     header.innerHTML = data.updated_header_html;
                     // ヘッダー内のクリックイベントリスナーを再設定する必要があるかもしれないが、
                     // 今回はモーダル起動用属性がHTMLに含まれるため不要
                }
                // カード全体の完了状態クラスを更新
                const visibleSubtasksInCard = masterCard.querySelectorAll('.list-group-item.sub-task');
                const completedVisibleSubtasks = masterCard.querySelectorAll('.list-group-item.sub-task.completed');
                masterCard.classList.toggle('all-completed',
                    visibleSubtasksInCard.length > 0 && visibleSubtasksInCard.length === completedVisibleSubtasks.length
                );
            }

            // グリッド、サマリーなどの動的UIを更新
            updateDynamicUI(data);

        } catch (error) {
            console.error('Error handling task completion:', error);
            alert('タスク状態の更新に失敗しました。ページをリロードして最新の状態を確認してください。');
             // UIを元に戻す (既に試みているが、ここでも再度確認)
             if (listItem && icon) {
                 listItem.classList.toggle('completed', wasCompleted);
                 icon.classList.toggle('bi-check-square-fill', wasCompleted);
                 icon.classList.toggle('bi-square', !wasCompleted);
                 icon.style.pointerEvents = 'auto';
             }
        }
    }

    /** 指定された親要素内のタスク完了トグルにイベントリスナーを設定 */
    function setupEventListeners(parentElement) {
        parentElement.addEventListener('click', (e) => {
            const toggle = e.target.closest('.complete-toggle');
            if (toggle) {
                e.preventDefault(); // デフォルト動作（もしあれば）を防止
                e.stopPropagation(); // 親要素へのイベント伝播を停止
                const subtaskId = toggle.closest('li.sub-task')?.dataset.subtaskId;
                if (subtaskId) {
                    handleTaskCompletion(subtaskId);
                }
                return; // 処理したので早期リターン
            }

            // 必要であれば、他のクリックイベント（例：マスタータスクタイトルクリック）もここで処理
        });
    }

    // --- 初期化 ---

    // メインのタスクリストコンテナにイベントリスナーを設定
    const taskListContainer = document.getElementById('task-list-container');
    if(taskListContainer) {
        setupEventListeners(taskListContainer);
    }

    // フォーカスモーダルの設定
    if (taskFocusModal) {
        taskFocusModal.addEventListener('show.bs.modal', event => {
            const triggerElement = event.relatedTarget; // モーダルをトリガーした要素
            if (!triggerElement) return;

            // data-* 属性からデータを取得
            const taskTitle = triggerElement.dataset.taskTitle || "タスク詳細";
            const subtasksJson = triggerElement.dataset.taskSubtasks; // JSON文字列

            const modalTitle = taskFocusModal.querySelector('.modal-title');
            const modalBodyList = taskFocusModal.querySelector('#focus-subtask-list');

            if (modalTitle) modalTitle.textContent = taskTitle;
            if (!modalBodyList) return; // リスト要素がなければ終了

            modalBodyList.innerHTML = ''; // 既存の内容をクリア

            try {
                const subtasks = JSON.parse(subtasksJson || '[]'); // JSONをパース
                if (subtasks && subtasks.length > 0) {
                    subtasks.forEach(subtask => {
                        // 各サブタスクのリスト項目を作成
                        const li = document.createElement('li');
                        li.className = `list-group-item sub-task d-flex align-items-center ${subtask.is_completed ? 'completed' : ''}`;
                        li.dataset.subtaskId = subtask.id; // IDを設定

                        const icon = document.createElement('i');
                        icon.className = `bi ${subtask.is_completed ? 'bi-check-square-fill' : 'bi-square'} complete-toggle`;
                        icon.style.cursor = 'pointer';
                        icon.style.marginRight = '10px';

                        const span = document.createElement('span');
                        span.className = 'flex-grow-1';
                        span.textContent = `${subtask.content} (${subtask.grid_count}マス)`;

                        li.appendChild(icon);
                        li.appendChild(span);
                        modalBodyList.appendChild(li);
                    });
                    // モーダル内のリストにもイベントリスナーを設定
                    setupEventListeners(modalBodyList);
                } else {
                    modalBodyList.innerHTML = '<li class="list-group-item text-muted">サブタスクはありません。</li>';
                }
            } catch(e) {
                console.error("Error parsing subtasks JSON for modal:", e);
                modalBodyList.innerHTML = '<li class="list-group-item text-danger">サブタスクデータの読み込みに失敗しました。</li>';
            }
        });
    }

    // プログレスサークルの初期化
    const progressCircle = document.querySelector('.progress-circle');
    const progressText = document.querySelector('.progress-text');
    if (progressCircle && progressText) {
        const initialPercentage = parseFloat(progressCircle.dataset.progress || 0);
        progressText.textContent = `${Math.round(initialPercentage)}%`;
        // CSS変数を使って初期進捗を設定
        progressCircle.style.setProperty('--progress', `${initialPercentage}%`);
    }

    // カレンダーの初期化とレンダリング (calendar.js が必要)
    const calendarContainer = document.getElementById('calendar-container');
    const dateNavContainer = document.getElementById('date-nav-container');
    let displayedMonthDate = new Date(currentDate); // カレンダー表示用の月を管理

    function renderCalendarAndNav() {
        // calendar.js の関数が存在するか確認
        if (typeof generateCalendar === 'function' && calendarContainer) {
            calendarContainer.innerHTML = generateCalendar(displayedMonthDate, currentDate, today, taskCounts);
            // 月移動ボタンにイベントリスナーを設定
            const prevBtn = document.getElementById('prev-month-btn');
            const nextBtn = document.getElementById('next-month-btn');
            if(prevBtn) {
                prevBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    displayedMonthDate.setMonth(displayedMonthDate.getMonth() - 1);
                    renderCalendarAndNav(); // カレンダーを再描画
                });
            }
            if(nextBtn) {
                nextBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    displayedMonthDate.setMonth(displayedMonthDate.getMonth() + 1);
                    renderCalendarAndNav(); // カレンダーを再描画
                });
            }
        } else {
            console.error("generateCalendar function not found or calendar container missing.");
        }

        // 日付ナビゲーションのレンダリング
        if (typeof generateDateNav === 'function' && dateNavContainer) {
            dateNavContainer.innerHTML = generateDateNav(currentDate);
        } else {
             console.error("generateDateNav function not found or date navigation container missing.");
        }
    }

    renderCalendarAndNav(); // 初期描画を実行

    // フォーム送信時のローディング表示 (例: スプレッドシート書き出し)
    const exportSheetForm = document.getElementById('export-sheet-form');
    if (exportSheetForm && typeof showLoadingOverlay === 'function') {
        exportSheetForm.addEventListener('submit', () => showLoadingOverlay('書き出し中...'));
    }

    // リンククリック時のローディング表示 (例: インポートページへ移動)
    const importLink = document.querySelector('a[href*="import_excel"]'); // 部分一致で検索
     if (importLink && typeof showLoadingOverlay === 'function') {
        importLink.addEventListener('click', (e) => {
             // 実際のページ遷移を妨げないように、非同期処理は行わない
             showLoadingOverlay('インポートページへ移動中...');
             // デフォルトのリンク動作は継続される
        });
    }

});

