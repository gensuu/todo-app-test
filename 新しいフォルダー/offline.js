// --- IndexedDBの初期設定 ---
const DB_NAME = 'todo-offline-db';
const DB_VERSION = 7; // バージョンを更新
const NEW_TASK_STORE = 'new_tasks_outbox';
const SCRATCHPAD_STORE = 'scratchpad_outbox';
const NEW_TEMPLATE_STORE = 'new_templates_outbox';
const COMPLETED_TASK_STORE = 'completed_tasks_outbox'; // 完了タスク用ストアを追加
const SYNC_TAG = 'background-sync';

let db;

function openDB() {
    return new Promise((resolve, reject) => {
        if (db) return resolve(db);
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onerror = (e) => reject(e.target.error);
        request.onsuccess = (e) => {
            db = e.target.result;
            resolve(db);
        };
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(NEW_TASK_STORE)) {
                db.createObjectStore(NEW_TASK_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains(SCRATCHPAD_STORE)) {
                db.createObjectStore(SCRATCHPAD_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            if (!db.objectStoreNames.contains(NEW_TEMPLATE_STORE)) {
                db.createObjectStore(NEW_TEMPLATE_STORE, { autoIncrement: true, keyPath: 'id' });
            }
            // ▼▼▼ 修正点: 完了タスクストアを作成 ▼▼▼
            if (!db.objectStoreNames.contains(COMPLETED_TASK_STORE)) {
                db.createObjectStore(COMPLETED_TASK_STORE, { autoIncrement: true, keyPath: 'id' });
            }
        };
    });
}

async function registerBackgroundSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
        try {
            const registration = await navigator.serviceWorker.ready;
            await registration.sync.register(SYNC_TAG);
        } catch (err) { console.error('Background sync registration failed:', err); }
    }
}

async function saveNewTaskToOutbox(taskData) {
    const db = await openDB();
    const transaction = db.transaction(NEW_TASK_STORE, 'readwrite');
    transaction.objectStore(NEW_TASK_STORE).add(taskData);
    registerBackgroundSync();
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
    });
}

async function saveScratchpadToOutbox(tasks) {
    const db = await openDB();
    const transaction = db.transaction(SCRATCHPAD_STORE, 'readwrite');
    transaction.objectStore(SCRATCHPAD_STORE).add({ tasks: tasks, timestamp: new Date().toISOString() });
    registerBackgroundSync();
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
    });
}

async function saveTemplateToOutbox(templateData) {
    const db = await openDB();
    const transaction = db.transaction(NEW_TEMPLATE_STORE, 'readwrite');
    transaction.objectStore(NEW_TEMPLATE_STORE).add(templateData);
    registerBackgroundSync();
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
    });
}

// ▼▼▼ 修正点: タスク完了状態をオフライン保存する関数を新設 ▼▼▼
async function saveTaskCompletionToOutbox(subtaskId, isCompleted) {
    const db = await openDB();
    const transaction = db.transaction(COMPLETED_TASK_STORE, 'readwrite');
    transaction.objectStore(COMPLETED_TASK_STORE).add({ subtaskId, isCompleted, timestamp: new Date().toISOString() });
    registerBackgroundSync();
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
    });
}


async function triggerManualSync() {
    try {
        const db = await openDB();
        const getAll = (storeName) => new Promise((resolve, reject) => {
            const request = db.transaction(storeName).objectStore(storeName).getAll();
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

        const newTasks = await getAll(NEW_TASK_STORE);
        const scratchpadItems = await getAll(SCRATCHPAD_STORE);
        const newTemplates = await getAll(NEW_TEMPLATE_STORE);
        const completedTasks = await getAll(COMPLETED_TASK_STORE); // 完了タスクを取得

        if (newTasks.length === 0 && scratchpadItems.length === 0 && newTemplates.length === 0 && completedTasks.length === 0) {
            return true;
        }

        const scratchpadTasks = scratchpadItems.flatMap(item => item.tasks);

        const response = await fetch('/api/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                new_tasks: newTasks,
                scratchpad_tasks: scratchpadTasks,
                new_templates: newTemplates,
                completed_tasks: completedTasks // 完了タスクをペイロードに追加
            })
        });

        if (!response.ok) throw new Error('Server sync failed');
        
        const clearStore = (storeName) => new Promise((resolve, reject) => {
             const transaction = db.transaction(storeName, 'readwrite').objectStore(storeName).clear();
             transaction.onsuccess = () => resolve();
             transaction.onerror = () => reject(transaction.error);
        });

        await clearStore(NEW_TASK_STORE);
        await clearStore(SCRATCHPAD_STORE);
        await clearStore(NEW_TEMPLATE_STORE);
        await clearStore(COMPLETED_TASK_STORE); // 完了タスクストアをクリア

        return true;
    } catch (error) {
        console.error('Manual Sync failed:', error);
        return false;
    }
}

async function sendQueueToServer() {
    try {
        await triggerManualSync();
        if (self.clients) {
             self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
                clients.forEach(client => client.postMessage({ type: 'SYNC_COMPLETED' }));
            });
        }
    } catch (error) {
        throw error;
    }
}

