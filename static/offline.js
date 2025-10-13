// --- IndexedDBの初期設定 ---
const DB_NAME = 'todo-offline-db';
const DB_VERSION = 1;
const NEW_TASK_STORE = 'new_tasks_outbox';
const SCRATCHPAD_STORE = 'scratchpad_outbox';
const SYNC_TAG = 'background-sync';

let db;

// IndexedDBを開く関数
function openDB() {
    return new Promise((resolve, reject) => {
        if (db) {
            return resolve(db);
        }
        const request = indexedDB.open(DB_NAME, DB_VERSION);

        request.onerror = (event) => {
            console.error("Database error:", event.target.errorCode);
            reject("Database error: " + event.target.errorCode);
        };

        request.onsuccess = (event) => {
            db = event.target.result;
            resolve(db);
        };

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(NEW_TASK_STORE)) {
                // 新しいタスク保存用ストア
                db.createObjectStore(NEW_TASK_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains(SCRATCHPAD_STORE)) {
                // スクラッチパッドからのタスク保存用ストア
                db.createObjectStore(SCRATCHPAD_STORE, { autoIncrement: true, keyPath: 'id' });
            }
        };
    });
}

// --- データ保存用のヘルパー関数 ---

// 新しいタスクをアウトボックスに保存
async function saveTaskToOutbox(taskData) {
    const db = await openDB();
    const transaction = db.transaction(NEW_TASK_STORE, 'readwrite');
    const store = transaction.objectStore(NEW_TASK_STORE);
    store.add(taskData); // タスクデータをストアに追加
    return transaction.complete; // トランザクション完了を待つ
}

// スクラッチパッドからのタスクをアウトボックスに保存
async function saveScratchpadToOutbox(tasks) {
    const db = await openDB();
    const transaction = db.transaction(SCRATCHPAD_STORE, 'readwrite');
    const store = transaction.objectStore(SCRATCHPAD_STORE);
    store.add({ tasks: tasks, timestamp: new Date().toISOString() }); // タスクとタイムスタンプをストアに追加
    return transaction.complete;
}

// --- ページ側から呼ばれるオフライン処理関数 ---

// スクラッチパッドのタスクを同期キューに追加する（オフライン時）
async function addToSyncQueue(tasks) {
    // navigator.onLine をチェックすることで、オフライン時の挙動をシミュレートしたり、
    // 実際にオフラインであるかを確認できます。
    if (!navigator.onLine) {
        console.log(`Offline: Adding scratchpad tasks to sync queue.`);
        await saveScratchpadToOutbox(tasks);
        registerBackgroundSync(); // オフラインなのでバックグラウンド同期を登録
        return true; // オフラインで保存成功
    } else {
        console.log("Online: Not adding to sync queue, attempting immediate server call (Placeholder).");
        // オンラインの場合は、直接サーバーに送信するロジックをここに実装
        // return await sendTasksImmediatelyToServer(tasks); // 例
        return false; // キューには追加せず、オンライン処理を促す
    }
}

// 新しいタスクをローカルに追加する（オフライン時）
async function addLocalTask(taskData) {
    if (!navigator.onLine) {
        console.log("Offline: Adding new task to local outbox.");
        await saveTaskToOutbox(taskData);
        registerBackgroundSync(); // オフラインなのでバックグラウンド同期を登録
        return true; // オフラインで保存成功
    } else {
        console.log("Online: Not adding to local outbox, attempting immediate server call (Placeholder).");
        // オンラインの場合は、直接サーバーに送信するロジックをここに実装
        // return await sendTaskImmediatelyToServer(taskData); // 例
        return false; // ローカルには追加せず、オンライン処理を促す
    }
}


// --- Service Workerにバックグラウンド同期を要求 ---
async function registerBackgroundSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.sync.register(SYNC_TAG);
            console.log('Background sync registered');
        } catch (err) {
            console.error('Background sync registration failed:', err);
        }
    }
}

// --- サーバーへデータを送信するメインの関数 (Service Workerから呼ばれることを想定) ---
async function sendQueueToServer() {
    console.log('Attempting to sync with server...');
    const db = await openDB();
    
    // NEW_TASK_STORE からデータを取得
    const newTasksTx = db.transaction(NEW_TASK_STORE, 'readonly');
    const newTasksStore = newTasksTx.objectStore(NEW_TASK_STORE);
    const newTasks = await new Promise(resolve => newTasksStore.getAll().onsuccess = e => resolve(e.target.result));
    
    // SCRATCHPAD_STORE からデータを取得
    const scratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readonly');
    const scratchpadStore = scratchpadTx.objectStore(SCRATCHPAD_STORE);
    const scratchpadItems = await new Promise(resolve => scratchpadStore.getAll().onsuccess = e => resolve(e.target.result));
    const scratchpadTasks = scratchpadItems.flatMap(item => item.tasks); // scratchpadItemsはオブジェクトの配列なので、中のtasks配列をフラット化

    if (newTasks.length === 0 && scratchpadTasks.length === 0) {
        console.log('No tasks to sync.');
        return;
    }

    try {
        const response = await fetch('/api/sync', { // バックエンドの同期エンドポイント
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                new_tasks: newTasks,
                scratchpad_tasks: scratchpadTasks
            })
        });

        if (!response.ok) {
            throw new Error(`Server response was not ok: ${response.status} ${response.statusText}`);
        }

        console.log('Sync successful!');
        
        // 成功したら両方のストアをクリア
        const clearNewTasksTx = db.transaction(NEW_TASK_STORE, 'readwrite');
        await clearNewTasksTx.objectStore(NEW_TASK_STORE).clear();
        
        const clearScratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readwrite');
        await clearScratchpadTx.objectStore(SCRATCHPAD_STORE).clear();

        // 同期成功後、ページをリロードしてUIを更新
        // Service Workerのスコープでは直接window.location.reload()は呼べないため、
        // クライアントにメッセージを送るか、あるいは Service Worker 以外から呼ぶ
        // ここでは、Service Workerから呼ばれた場合、それが終わった後にブラウザがページをリロードするのを待つ
        // または、以下のようにクライアントに指示を送ることも可能（より高度な実装）
        self.clients.matchAll().then(clients => {
            clients.forEach(client => {
                client.postMessage({ type: 'SYNC_COMPLETED' });
            });
        });

    } catch (error) {
        console.error('Sync failed:', error);
        // 失敗した場合は何もしない（データはDBに残り、次回の同期で再試行される）
    }
}