// static/js/manage_templates.js
// Assumes utils.js (show/hideLoadingOverlay) is loaded.
// Assumes offline.js defines openDB, NEW_TEMPLATE_STORE, saveTemplateToOutbox if offline is needed.

/** Loads and displays templates saved offline in IndexedDB. */
async function loadAndDisplayOfflineTemplates() {
    const container = document.getElementById('offline-templates-container');
    const noTemplatesMessage = document.getElementById('no-templates-message'); // To hide if offline exist

    // Check if offline functions/constants are available
    if (typeof openDB !== 'function' || typeof NEW_TEMPLATE_STORE === 'undefined') {
        console.warn("Offline DB functions/constants not available.");
        return;
    }

    try {
        const db = await openDB();
        const templates = await new Promise((resolve, reject) => {
            const transaction = db.transaction(NEW_TEMPLATE_STORE, 'readonly');
            const request = transaction.objectStore(NEW_TEMPLATE_STORE).getAll();
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
            // transaction.oncomplete is implicitly handled by promise resolution/rejection
        });

        if (templates && templates.length > 0) {
            if (noTemplatesMessage) noTemplatesMessage.style.display = 'none'; // Hide "no templates" message

            // Clear previous offline display before adding new ones
            if (container) container.innerHTML = '';

            templates.forEach(template => {
                // Generate HTML for each offline template card
                const subtasksHtml = (template.subtasks || [])
                    .map(sub => `<li class="list-group-item">${sub.content || '?'} (${sub.grid_count || '?'})</li>`)
                    .join('');

                const cardHtml = `
                    <div class="card mb-3 opacity-50 border-warning"> {# Add border for emphasis #}
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <span>${template.title || '無題'}</span>
                            <small class="text-warning">(オフライン保存 - 同期待ち)</small>
                        </div>
                        ${subtasksHtml ? `<ul class="list-group list-group-flush">${subtasksHtml}</ul>` : '<div class="card-body text-muted small">サブタスクなし</div>'}
                    </div>
                `;
                if (container) container.insertAdjacentHTML('beforeend', cardHtml);
            });
        }
    } catch (error) {
        console.error("Failed to load offline templates:", error);
        // Optionally show an error message to the user in the UI
        // if (container) container.innerHTML = '<p class="text-danger">オフラインテンプレートの読込失敗</p>';
    }
}


document.addEventListener('DOMContentLoaded', () => {
    // Load offline templates on page load
    loadAndDisplayOfflineTemplates();

    const subtasksContainer = document.getElementById('subtasks-container');
    const addSubtaskBtn = document.getElementById('add-subtask-btn');
    const saveTemplateBtn = document.getElementById('save-template-btn');
    const templateForm = document.getElementById('template-form'); // The form for creating new templates
    let subtaskCounter = 0; // Counter for naming new subtask inputs

    /** Creates and appends a new subtask row to the form. */
    const createSubtaskRow = (task = { content: '', grid_count: 1 }) => {
        if (!subtasksContainer) return;
        subtaskCounter++;
        const row = document.createElement('div');
        row.className = 'row g-2 mb-2 align-items-center subtask-row';
        row.innerHTML = `
            <div class="col">
                <input type="text" class="form-control form-control-sm" name="sub_content_${subtaskCounter}" placeholder="サブタスクの内容" value="${task.content || ''}" required>
            </div>
            <div class="col-4 col-md-3">
                <div class="input-group input-group-sm"> {# Add input-group-sm for consistency #}
                    <input type="number" class="form-control form-control-sm" name="grid_count_${subtaskCounter}" min="1" value="${task.grid_count || 1}" required> {# Add form-control-sm #}
                    <span class="input-group-text">マス</span>
                </div>
            </div>
            <div class="col-auto">
                <button type="button" class="btn btn-sm btn-danger remove-subtask-btn" title="削除"><i class="bi bi-trash"></i></button>
            </div>
        `;
        subtasksContainer.appendChild(row);

         // Focus the new input field
        const newInput = row.querySelector('input[name^="sub_content_"]');
        if (newInput) newInput.focus();
    };

    // --- Initial setup ---
    createSubtaskRow(); // Add the first empty subtask row

    // --- Event Listeners ---
    // Add Subtask Button
    if (addSubtaskBtn) {
        addSubtaskBtn.addEventListener('click', () => createSubtaskRow());
    }

    // Remove Subtask Buttons (using event delegation)
    if (subtasksContainer) {
        subtasksContainer.addEventListener('click', (e) => {
            const removeButton = e.target.closest('.remove-subtask-btn');
            if (removeButton) {
                const rowToRemove = removeButton.closest('.subtask-row');
                if (rowToRemove) {
                    rowToRemove.remove();
                    // Optional: If all rows are removed, add a new empty one back
                    // if (subtasksContainer.children.length === 0) createSubtaskRow();
                }
            }
        });
    }

    // Save Template Button
    if (saveTemplateBtn && templateForm) {
        saveTemplateBtn.addEventListener('click', async () => {
            // Standard HTML5 validation first
            if (!templateForm.reportValidity()) {
                console.log("Template form validation failed.");
                return;
            }

            // Check if showLoadingOverlay exists (from utils.js)
             if (typeof showLoadingOverlay !== 'function') {
                 console.error("showLoadingOverlay is not defined.");
                 // Fallback or just proceed without overlay
                 // alert("保存中..."); // Simple fallback
             } else {
                 showLoadingOverlay('テンプレートを保存中...');
             }


            // Gather template data from form
            const templateData = {
                title: templateForm.querySelector('#template_title')?.value.trim() || '',
                subtasks: Array.from(subtasksContainer?.querySelectorAll('.subtask-row') || []).map(row => {
                    const contentInput = row.querySelector('input[name^="sub_content_"]');
                    const gridInput = row.querySelector('input[name^="grid_count_"]');
                    return {
                        content: contentInput ? contentInput.value.trim() : '',
                        grid_count: gridInput ? parseInt(gridInput.value, 10) || 0 : 0
                    };
                }).filter(s => s.content && s.grid_count > 0) // Filter out invalid subtasks
            };

            // Validate minimum required data
            if (!templateData.title) {
                 alert("テンプレート名は必須です。"); // Use modal/toast
                 if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
                 return;
            }
            if (templateData.subtasks.length === 0) {
                alert("少なくとも1つの有効なサブタスクが必要です。"); // Use modal/toast
                if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
                return;
            }

            // --- Offline / Online Submission Logic ---
            const isOffline = localStorage.getItem('offlineMode') === 'true' || !navigator.onLine;

            if (isOffline && typeof saveTemplateToOutbox === 'function') {
                try {
                    await saveTemplateToOutbox(templateData);
                    if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
                    alert("テンプレートをオフラインで保存しました。オンライン時に同期されます。"); // Use modal/toast
                    // Refresh offline display and reset form
                    loadAndDisplayOfflineTemplates();
                    templateForm.reset(); // Reset form fields
                    if (subtasksContainer) subtasksContainer.innerHTML = ''; // Clear subtasks
                    createSubtaskRow(); // Add back one empty row
                } catch(e) {
                    if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
                    alert('オフラインでの保存に失敗しました。'); // Use modal/toast
                    console.error("Offline template save error:", e);
                }
            } else if (isOffline && typeof saveTemplateToOutbox !== 'function') {
                 alert("オフラインモードですが、保存機能が利用できません。");
                 if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
            }
             else {
                // Online: Submit the form via standard POST
                // The form action should already be set correctly in the HTML template
                templateForm.submit();
                // Loading overlay will hide automatically on page navigation (via pageshow event in utils.js)
            }
        });
    }

    // Add confirmation to delete forms
    const deleteForms = document.querySelectorAll('form[action*="delete_template"]');
    deleteForms.forEach(form => {
        form.addEventListener('submit', (e) => {
            if (!confirm('本当にこのテンプレートを削除しますか？')) {
                e.preventDefault(); // Prevent submission if user cancels
            } else {
                 // If proceeding, show loading indicator
                 if (typeof showLoadingOverlay === 'function') {
                    showLoadingOverlay('テンプレート削除中...');
                }
            }
        });
    });

});
