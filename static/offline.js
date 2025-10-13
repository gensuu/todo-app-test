// --- IndexedDBの初期設定 ---
const DB_NAME = 'todo-offline-db';
// ▼▼▼ 修正点: データベースのバージョンを更新 ▼▼▼
const DB_VERSION = 4;
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
            console.log("Upgrading database schema...");
            const db = event.target.result;
            if (!db.objectStoreNames.contains(NEW_TASK_STORE)) {
                console.log(`Creating object store: ${NEW_TASK_STORE}`);
                db.createObjectStore(NEW_TASK_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains(SCRATCHPAD_STORE)) {
                console.log(`Creating object store: ${SCRATCHPAD_STORE}`);
                db.createObjectStore(SCRATCHPAD_STORE, { autoIncrement: true, keyPath: 'id' });
            }
        };
    });
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

// --- ページ側から呼ばれるオフライン保存用の関数 ---
async function saveNewTaskToOutbox(taskData) {
    console.log("Saving new task to outbox:", taskData);
    const db = await openDB();
    const transaction = db.transaction(NEW_TASK_STORE, 'readwrite');
    const store = transaction.objectStore(NEW_TASK_STORE);
    store.add(taskData);
    await registerBackgroundSync();
    return transaction.complete;
}

async function saveScratchpadToOutbox(tasks) {
    console.log("Saving scratchpad tasks to outbox:", tasks);
    const db = await openDB();
    const transaction = db.transaction(SCRATCHPAD_STORE, 'readwrite');
    const store = transaction.objectStore(SCRATCHPAD_STORE);
    store.add({ tasks: tasks, timestamp: new Date().toISOString() });
    await registerBackgroundSync();
    return transaction.complete;
}

// --- サーバーへデータを送信するメインの関数 (Service Workerから呼ばれる) ---
async function sendQueueToServer() {
    console.log('Attempting to sync with server...');
    const db = await openDB();
    
    const newTasksTx = db.transaction(NEW_TASK_STORE, 'readonly');
    const newTasks = await new Promise(resolve => newTasksTx.objectStore(NEW_TASK_STORE).getAll().onsuccess = e => resolve(e.target.result));
    
    const scratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readonly');
    const scratchpadItems = await new Promise(resolve => scratchpadTx.objectStore(SCRATCHPAD_STORE).getAll().onsuccess = e => resolve(e.target.result));
    
    if (newTasks.length === 0 && scratchpadItems.length === 0) {
        console.log('No tasks to sync.');
        return;
    }

    const scratchpadTasks = scratchpadItems.flatMap(item => item.tasks);

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
            throw new Error(`Server response was not ok: ${response.status} ${response.statusText}`);
        }

        console.log('Sync successful!');
        
        const clearNewTasksTx = db.transaction(NEW_TASK_STORE, 'readwrite');
        await clearNewTasksTx.objectStore(NEW_TASK_STORE).clear();
        
        const clearScratchpadTx = db.transaction(SCRATCHPAD_STORE, 'readwrite');
        await clearScratchpadTx.objectStore(SCRATCHPAD_STORE).clear();

        if (self.clients) {
             self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
                clients.forEach(client => {
                    client.postMessage({ type: 'SYNC_COMPLETED' });
                });
            });
        }

    } catch (error) {
        console.error('Sync failed:', error);
        throw error;
    }
}