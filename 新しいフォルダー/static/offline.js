// --- IndexedDBの初期設定 ---
const DB_NAME = 'todo-offline-db';
const DB_VERSION = 1;
const NEW_TASK_STORE = 'new_tasks_outbox';
const SCRATCHPAD_STORE = 'scratchpad_outbox';
const SYNC_TAG = 'background-sync';

let db;

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
                db.createObjectStore(NEW_TASK_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains(SCRATCHPAD_STORE)) {
                db.createObjectStore(SCRATCHPAD_STORE, { autoIncrement: true, keyPath: 'id' });
            }
        };
    });
}

// --- データ保存用の関数 ---

async function saveTaskToOutbox(taskData) {
    const db = await openDB();
    const transaction = db.transaction(NEW_TASK_STORE, 'readwrite');
    const store = transaction.objectStore(NEW_TASK_STORE);
    store.add(taskData);
    return transaction.complete;
}

async function saveScratchpadToOutbox(tasks) {
    const db = await openDB();
    const transaction = db.transaction(SCRATCHPAD_STORE, 'readwrite');
    const store = transaction.objectStore(SCRATCHPAD_STORE);
    store.add({ tasks: tasks });
    return transaction.complete;
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

// --- サーバーへデータを送信するメインの関数 ---
async function sendQueueToServer() {
    console.log('Attempting to sync with server...');
    const db = await openDB();
    
    // 両方のストアからデータを取得
    const newTasksTx = db.transaction(NEW_TASK_STORE, 'readonly');
    const newTasksStore = newTasksTx.objectStore(NEW_TASK_STORE);
    const newTasks = await new Promise(resolve => newTasksStore.getAll().onsuccess = e => resolve(e.target.result));
    
    const scratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readonly');
    const scratchpadStore = scratchpadTx.objectStore(SCRATCHPAD_STORE);
    const scratchpadItems = await new Promise(resolve => scratchpadStore.getAll().onsuccess = e => resolve(e.target.result));
    const scratchpadTasks = scratchpadItems.flatMap(item => item.tasks);

    if (newTasks.length === 0 && scratchpadTasks.length === 0) {
        console.log('No tasks to sync.');
        return;
    }

    try {
        const response = await fetch('/api/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                new_tasks: newTasks,
                scratchpad_tasks: scratchpadTasks
            })
        });

        if (!response.ok) {
            throw new Error('Server response was not ok.');
        }

        console.log('Sync successful!');
        
        // 成功したらストアをクリア
        const clearNewTasksTx = db.transaction(NEW_TASK_STORE, 'readwrite');
        await clearNewTasksTx.objectStore(NEW_TASK_STORE).clear();
        
        const clearScratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readwrite');
        await clearScratchpadTx.objectStore(SCRATCHPAD_STORE).clear();

    } catch (error) {
        console.error('Sync failed:', error);
        // 失敗した場合は何もしない（データはDBに残り、次回の同期で再試行される）
    }
}