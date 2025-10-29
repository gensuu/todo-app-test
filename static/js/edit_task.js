// static/js/edit_task.js
// Assumes utils.js (show/hideLoadingOverlay) is loaded before this script.
// Assumes offline.js (saveNewTaskToOutbox) is loaded if offline support is needed.

document.addEventListener('DOMContentLoaded', () => {
    // --- Get Elements ---
    const container = document.getElementById('subtasks-container');
    const addBtn = document.getElementById('add-subtask-btn');
    const templateSelector = document.getElementById('template_selector');
    const recurrenceTypeSelect = document.getElementById('recurrence_type');
    const recurrenceDaysGroup = document.getElementById('recurrence-days-group');
    const saveAsTemplateButton = document.getElementById('save-as-template-btn');
    const saveTaskButton = document.getElementById('save-task-btn'); // Renamed for clarity
    const form = document.getElementById('task-form');

    // --- Get Data from Jinja (via data attributes) ---
    const dataContainer = document.getElementById('edit-task-data-container'); // Needs adding to edit_task.html
    if (!dataContainer) {
        console.error("Data container #edit-task-data-container not found!");
        return;
    }
    const defaultDateValue = dataContainer.dataset.defaultDate;
    const templatesDataJson = dataContainer.dataset.templatesData || '{}';
    const existingSubtasksJson = dataContainer.dataset.existingSubtasks || '[]';
    const isEditMode = dataContainer.dataset.isEditMode === 'true'; // Check if editing
    const masterTaskId = dataContainer.dataset.masterTaskId; // Needed for edit action URL
    const saveActionUrl = dataContainer.dataset.saveActionUrl; // Base URL from url_for
    const addActionUrl = dataContainer.dataset.addActionUrl; // Base URL for adding

     // Validate essential data
     if (!defaultDateValue || !saveActionUrl || !addActionUrl) {
          console.error("Essential data missing from data container attributes (defaultDate, action URLs).");
          return;
     }

    // --- Toast Elements ---
    const templateSaveToastEl = document.getElementById('templateSaveToast');
    const templateSaveToast = templateSaveToastEl ? new bootstrap.Toast(templateSaveToastEl, { delay: 3000 }) : null;
    const templateErrorToastEl = document.getElementById('templateErrorToast');
    const templateErrorToast = templateErrorToastEl ? new bootstrap.Toast(templateErrorToastEl, { delay: 5000 }) : null;
    const templateErrorTextEl = document.getElementById('templateErrorText');

    // --- Constants and State ---
    const maxSubtasks = 20;
    let templatesData = {};
    let existingSubtasks = [];
     try {
         templatesData = JSON.parse(templatesDataJson);
         existingSubtasks = JSON.parse(existingSubtasksJson);
     } catch (e) { console.error("Error parsing JSON data:", e); }

    let subtaskCounter = 0; // Tracks the index for new subtasks

    // --- Functions ---
    /** Creates and appends a subtask input row. */
    const createSubtaskRow = (task = { content: '', grid_count: 1 }) => {
        if (!container || subtaskCounter >= maxSubtasks) return;
        const newIndex = ++subtaskCounter; // Increment first
        const row = document.createElement('div');
        row.className = 'row g-2 mb-2 align-items-center subtask-row';
        row.innerHTML = `
            <div class="col">
                <input type="text" class="form-control form-control-sm" name="sub_content_${newIndex}" placeholder="サブタスクの内容" value="${task.content || ''}" required>
            </div>
            <div class="col-4 col-md-3">
                <div class="input-group input-group-sm">
                    <input type="number" class="form-control" name="grid_count_${newIndex}" min="1" value="${task.grid_count || 1}" required>
                    <span class="input-group-text">マス</span>
                </div>
            </div>
            <div class="col-auto">
                <button type="button" class="btn btn-sm btn-danger remove-subtask-btn" title="サブタスク削除"><i class="bi bi-trash"></i></button>
            </div>
        `;
        container.appendChild(row);

        // Focus the new input field
        const newInput = row.querySelector('input[name^="sub_content_"]');
        if (newInput) newInput.focus();

    };

    /** Populates the subtask container with existing or default tasks. */
    const populateSubtasks = (tasks) => {
        if (!container) return;
        container.innerHTML = ''; // Clear existing rows
        subtaskCounter = 0; // Reset counter
        if (tasks && tasks.length > 0) {
            tasks.forEach(createSubtaskRow);
        } else {
            createSubtaskRow(); // Add one empty row if no tasks exist
        }
    };

    /** Shows or hides the recurrence days selection based on recurrence type. */
    const toggleRecurrenceDays = () => {
        if (recurrenceDaysGroup && recurrenceTypeSelect) {
            recurrenceDaysGroup.style.display = (recurrenceTypeSelect.value === 'weekly') ? 'block' : 'none';
        }
    };

    /** Submits the form to save the current content as a template. */
    const saveTemplate = () => {
        if (!form || typeof showLoadingOverlay !== 'function') return;
        // Basic form validation first
        if (!form.reportValidity()) {
             // Modern browsers show default validation messages.
             // You could add custom highlighting or messages here if needed.
             console.log("Form validation failed for template save.");
             return;
        }

         // Add a hidden input to signify the 'save as template' action
         let hiddenInput = form.querySelector('input[name="save_as_template"]');
         if (!hiddenInput) {
            hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.name = 'save_as_template';
            form.appendChild(hiddenInput);
         }
         hiddenInput.value = 'true'; // Set value to true

         showLoadingOverlay('テンプレート保存中...');

         // Determine the correct action URL (edit or add)
         const currentUrl = new URL(window.location.href);
         const dateStr = currentUrl.searchParams.get('date_str') || defaultDateValue;
         // The action URL should point back to the same view (add_or_edit_task)
         // The back_url query parameter tells where manage_templates should link back to
         const backUrlForManageTemplates = isEditMode
            ? `${saveActionUrl}?date_str=${dateStr}` // Point back to edit page
            : `${addActionUrl}?date_str=${dateStr}`; // Point back to add page
         // The form action itself points to the current page (edit or add)
         form.action = `${isEditMode ? saveActionUrl : addActionUrl}?date_str=${dateStr}&back_url=${encodeURIComponent(backUrlForManageTemplates)}`;

         form.submit();
     };

     /** Submits the form to save the task (new or edited). */
     const saveTask = () => {
         if (!form || typeof showLoadingOverlay !== 'function') return;

         // Ensure 'save_as_template' hidden input (if exists) is removed or value cleared
         let templateInput = form.querySelector('input[name="save_as_template"]');
         if (templateInput) templateInput.value = 'false'; // Or remove it: templateInput.remove();

         // Validate recurrence days if weekly is selected
         if (recurrenceTypeSelect && recurrenceTypeSelect.value === 'weekly') {
              const checkedDays = form.querySelectorAll('input[name="recurrence_days"]:checked').length;
              if (checkedDays === 0) {
                  alert("毎週繰り返す場合は曜日を少なくとも1つ選択してください。"); // Use modal/toast
                  recurrenceDaysGroup?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  // Optionally highlight the group
                  recurrenceDaysGroup?.classList.add('border', 'border-danger');
                  setTimeout(() => recurrenceDaysGroup?.classList.remove('border', 'border-danger'), 2000);
                  return; // Prevent submission
              }
         }

         // Standard HTML5 form validation
         if (!form.reportValidity()) {
             console.log("Form validation failed for task save.");
             return;
         }

         showLoadingOverlay('タスク保存中...');

         // --- Offline Handling for NEW tasks ---
         // Editing requires online connection in this implementation
         if (!isEditMode) {
             const isOffline = localStorage.getItem('offlineMode') === 'true' || !navigator.onLine;

             if (isOffline && typeof saveNewTaskToOutbox === 'function') {
                 // Gather data for offline storage
                 const recurrenceType = recurrenceTypeSelect ? recurrenceTypeSelect.value : 'none';
                 let recurrenceDays = null;
                 if (recurrenceType === 'weekly') {
                     recurrenceDays = Array.from(form.querySelectorAll('input[name="recurrence_days"]:checked')).map(cb => cb.value).join('');
                 }
                 const taskData = {
                     title: form.querySelector('#master_title')?.value.trim() || '',
                     due_date: form.querySelector('#due_date')?.value || defaultDateValue,
                     is_urgent: form.querySelector('#is_urgent')?.checked || false,
                     is_habit: form.querySelector('#is_habit')?.checked || false,
                     recurrence_type: recurrenceType,
                     recurrence_days: recurrenceDays,
                     subtasks: Array.from(container?.querySelectorAll('.subtask-row') || []).map(row => {
                         const contentInput = row.querySelector('input[name^="sub_content_"]');
                         const gridInput = row.querySelector('input[name^="grid_count_"]');
                         return {
                             content: contentInput ? contentInput.value.trim() : '',
                             grid_count: gridInput ? parseInt(gridInput.value, 10) || 0 : 0
                         };
                     }).filter(s => s.content && s.grid_count > 0) // Filter invalid subtasks
                 };

                 // Basic validation before offline save
                 if (!taskData.title) { alert("タイトルは必須です。"); hideLoadingOverlay(); return; }
                 if (taskData.subtasks.length === 0) { alert("有効なサブタスクが最低1つ必要です。"); hideLoadingOverlay(); return; }

                 saveNewTaskToOutbox(taskData)
                     .then(() => {
                         // Determine redirect date (today for recurring, due_date otherwise)
                         const todayJS = new Date();
                         const yyyy = todayJS.getFullYear();
                         const mm = String(todayJS.getMonth() + 1).padStart(2, '0');
                         const dd = String(todayJS.getDate()).padStart(2, '0');
                         const todayStr = `${yyyy}-${mm}-${dd}`;
                         const redirectDate = (taskData.recurrence_type !== 'none') ? todayStr : taskData.due_date;
                         // Redirect after successful offline save
                         window.location.href = `/todo/${redirectDate}`; // Assumes Flask route structure
                     })
                     .catch(error => {
                         console.error("Offline save error:", error);
                         alert("オフライン保存に失敗しました。"); // Use modal/toast
                         hideLoadingOverlay();
                     });
                 return; // Prevent online submission if offline succeeded (or started)
             } else if (isOffline && typeof saveNewTaskToOutbox !== 'function') {
                 alert("オフラインモードですが、保存機能が利用できません。");
                 hideLoadingOverlay();
                 return;
             }
         } else if (isEditMode && !navigator.onLine) {
              alert("タスクの編集はオンラインである必要があります。"); // Use modal/toast
              hideLoadingOverlay();
              return; // Prevent submission
         }

         // --- Online Submission ---
         // Set the correct action URL for add or edit
         form.action = isEditMode ? saveActionUrl : addActionUrl;
         // Append date_str for context if needed by backend (might not be necessary if using POST data only)
         // form.action += `?date_str=${defaultDateValue}`;
         form.submit();
     };


    // --- Initialization ---
    populateSubtasks(existingSubtasks); // Load initial subtasks
    toggleRecurrenceDays(); // Set initial visibility of recurrence days

    // --- Event Listeners ---
    // Template Selector
    if (templateSelector) {
        templateSelector.addEventListener('change', (e) => {
            const templateId = e.target.value;
            const masterTitleInput = document.getElementById('master_title');
            if (templateId && templatesData[templateId]) {
                const t = templatesData[templateId];
                if (masterTitleInput) masterTitleInput.value = t.title || '';
                populateSubtasks(t.subtasks || []); // Load subtasks from selected template
                // Optionally update other fields like recurrence, urgent, habit based on template
            } else if (!templateId && masterTitleInput) {
                 // If "Select..." is chosen, maybe clear the form? Or do nothing.
                 // masterTitleInput.value = '';
                 // populateSubtasks([]);
            }
        });
    }

    // Add Subtask Button
    if (addBtn) {
        addBtn.addEventListener('click', () => createSubtaskRow());
    }

    // Remove Subtask Buttons (using event delegation on container)
    if (container) {
        container.addEventListener('click', (e) => {
            const removeButton = e.target.closest('.remove-subtask-btn');
            if (removeButton) {
                const rowToRemove = removeButton.closest('.subtask-row');
                if (rowToRemove) {
                    rowToRemove.remove();
                    // Ensure at least one row remains if all are deleted?
                    // if (container.children.length === 0) createSubtaskRow();
                }
            }
        });
    }

    // Recurrence Type Change
    if (recurrenceTypeSelect) {
        recurrenceTypeSelect.addEventListener('change', toggleRecurrenceDays);
    }

    // Save as Template Button
    if (saveAsTemplateButton) {
        saveAsTemplateButton.addEventListener('click', saveTemplate);
    }

    // Save Task Button
    if (saveTaskButton) {
        saveTaskButton.addEventListener('click', saveTask);
    }

});

