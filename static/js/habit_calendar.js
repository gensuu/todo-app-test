// static/js/habit_calendar.js
// Assumes utils.js (show/hideLoadingOverlay) is loaded before this script.

document.addEventListener('DOMContentLoaded', () => {
    // --- Get Elements ---
    const calendarGridDays = document.getElementById('calendar-grid')?.querySelector('.days');
    const calendarTitle = document.getElementById('calendar-title');
    const prevMonthBtn = document.getElementById('prev-month');
    const nextMonthBtn = document.getElementById('next-month');
    const stickerSheet = document.getElementById('sticker-sheet');
    const stickerTemplate = document.getElementById('sticker-template');
    const noHabitsMessage = document.getElementById('no-habits-message');

    // Get API base URL from data attribute
    const dataContainer = document.getElementById('habit-calendar-data-container'); // Needs adding to habit_calendar.html
     if (!dataContainer) {
         console.error("Data container #habit-calendar-data-container not found!");
         return;
     }
    const API_URL_BASE = dataContainer.dataset.apiUrlBase; // Pass url_for('main.habit_calendar_data', year=0, month=0)[:-3]

    // Validate elements and base URL
    if (!calendarGridDays || !calendarTitle || !prevMonthBtn || !nextMonthBtn || !stickerSheet || !stickerTemplate || !noHabitsMessage || !API_URL_BASE) {
        console.error("One or more required elements for habit calendar not found!");
        return; // Stop execution if essential elements are missing
    }


    let currentDate = new Date();
    currentDate.setDate(1); // Ensure it starts at the beginning of the month

    // --- Drag & Drop State ---
    let draggedStickerElement = null; // The temporary clone being dragged
    let sourceStickerElement = null;  // The original sticker element on the sheet
    let initialOffsetX, initialOffsetY;

    // --- localStorage Sticker Persistence ---
    const STORAGE_KEY_PREFIX = 'habit_stickers_';
    function getStorageKey(year, month) { // month is 0-11
        return `${STORAGE_KEY_PREFIX}${year}-${String(month + 1).padStart(2, '0')}`;
    }

    /** Saves the placed stickers map for a given month to localStorage. */
    function savePlacedStickers(year, month, placedStickersMap) {
        try {
            localStorage.setItem(getStorageKey(year, month), JSON.stringify(placedStickersMap));
        } catch (e) {
            console.error("Error saving stickers to localStorage:", e);
            alert("シールの状態を保存できませんでした。"); // Use modal/toast
        }
    }

    /** Loads the placed stickers map for a given month from localStorage. */
    function loadPlacedStickers(year, month) {
        try {
            const data = localStorage.getItem(getStorageKey(year, month));
            // Ensure we return an object even if data is null or invalid
            const parsed = data ? JSON.parse(data) : {};
            return (typeof parsed === 'object' && parsed !== null) ? parsed : {};
        } catch (e) {
            console.error("Error loading stickers from localStorage:", e);
            return {}; // Return empty object on error
        }
    }

    // --- API Data Fetching ---
    /** Fetches completed habit data for a specific month from the backend API. */
    async function fetchHabitData(year, month) { // month is 0-11
        // Check if show/hideLoadingOverlay are defined
        const showLoader = typeof showLoadingOverlay === 'function';
        const hideLoader = typeof hideLoadingOverlay === 'function';

        if (showLoader) showLoadingOverlay('カレンダーデータを読込中...');
        try {
            // Construct API URL: base + year + / + month+1
            const apiUrl = `${API_URL_BASE}/${year}/${month + 1}`;
            const response = await fetch(apiUrl);
            if (!response.ok) {
                throw new Error(`API Error: ${response.status} ${response.statusText}`);
            }
            const data = await response.json();
            if (hideLoader) hideLoadingOverlay();
            return data || {}; // Ensure an object is returned
        } catch (error) {
            console.error("Error fetching habit data:", error);
            alert('カレンダーデータの読み込みに失敗しました。'); // Use modal/toast
            if (hideLoader) hideLoadingOverlay();
            return {}; // Return empty object on error
        }
    }

    // --- Calendar Rendering ---
    /** Renders the calendar grid and sticker sheet for the given month. */
    async function renderCalendar(date) {
        calendarGridDays.innerHTML = ''; // Clear previous days
        stickerSheet.innerHTML = '';   // Clear previous stickers
        noHabitsMessage.style.display = 'block'; // Show "no habits" initially

        const year = date.getFullYear();
        const month = date.getMonth(); // 0-11 (January is 0)
        calendarTitle.textContent = `${year}年 ${month + 1}月`;

        const firstDayOfMonth = new Date(year, month, 1).getDay(); // 0=Sunday, 1=Monday...
        const daysInMonth = new Date(year, month + 1, 0).getDate(); // Get last day of month

        // Calculate offset needed for the first day (assuming Monday start)
        // If Sunday (0), offset is 6. If Monday (1), offset is 0.
        const dayOffset = (firstDayOfMonth === 0) ? 6 : firstDayOfMonth - 1;

        // Load persisted sticker placements and fetch current month's API data
        const placedStickersMap = loadPlacedStickers(year, month);
        const habitApiData = await fetchHabitData(year, month); // { 'YYYY-MM-DD': [{initial, color, title}, ...] }

        // Map to store unique habits encountered this month for the sticker sheet
        const habitsInMonth = new Map(); // { title: { initial, color, element } }

        // --- Generate Sticker Sheet ---
        // Iterate through API data to find unique habits completed this month
        Object.values(habitApiData).flat().forEach(habit => {
            if (habit && habit.title && !habitsInMonth.has(habit.title)) {
                const stickerElement = createStickerElement(habit);
                setupStickerDragEvents(stickerElement); // Attach drag listeners
                stickerSheet.appendChild(stickerElement);
                habitsInMonth.set(habit.title, { ...habit, element: stickerElement });
                noHabitsMessage.style.display = 'none'; // Hide message if stickers exist
            }
        });

        // --- Generate Calendar Grid Days ---
        // Add empty cells for the offset
        for (let i = 0; i < dayOffset; i++) {
            calendarGridDays.appendChild(createDayElement('', ['empty']));
        }

        // Add cells for each day of the month
        const today = new Date();
        const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

        for (let day = 1; day <= daysInMonth; day++) {
            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            const dayElement = createDayElement(day);
            const dateObj = new Date(year, month, day);
            const weekday = dateObj.getDay(); // 0=Sunday, 6=Saturday

            // Add classes for styling (today, weekend)
            if (dateStr === todayStr) dayElement.classList.add('today');
            if (weekday === 6) dayElement.classList.add('saturday'); // Saturday
            else if (weekday === 0) dayElement.classList.add('sunday'); // Sunday

            // Create container for stickers within the day cell
            const stickersContainer = document.createElement('div');
            stickersContainer.className = 'stickers-container';
            dayElement.appendChild(stickersContainer);
            dayElement.dataset.date = dateStr; // Store date string for drop handling

            // --- Place Saved Stickers ---
            if (placedStickersMap[dateStr]) {
                placedStickersMap[dateStr].forEach(habitTitle => {
                    const habitInfo = habitsInMonth.get(habitTitle);
                    if (habitInfo) {
                        const placedSticker = createStickerElement(habitInfo);
                        stickersContainer.appendChild(placedSticker);
                        // Mark the corresponding sticker on the sheet as used
                        if (habitInfo.element) habitInfo.element.classList.add('used');
                    } else {
                        // Handle case where sticker data exists but habit wasn't completed this month (rare)
                        console.warn(`Habit "${habitTitle}" found in storage for ${dateStr} but not in current month's API data.`);
                    }
                });
            }

            // --- Add Placeholders for Droppable Stickers ---
            if (habitApiData[dateStr]) {
                 habitApiData[dateStr].forEach(habit => {
                     // Only add placeholder if the sticker hasn't been placed yet
                     const alreadyPlaced = placedStickersMap[dateStr]?.includes(habit.title);
                     if (!alreadyPlaced) {
                         const placeholder = createStickerElement(habit, ['placeholder']);
                         stickersContainer.appendChild(placeholder);
                     }
                 });
            }

            // --- Setup Drop Target ---
            setupDayDropTargetEvents(dayElement, year, month, placedStickersMap, habitsInMonth);
            calendarGridDays.appendChild(dayElement);
        }
    }

    // --- Element Creation Helpers ---
    /** Creates a div element representing a day in the calendar. */
    function createDayElement(dayNumber, classes = []) {
        const dayDiv = document.createElement('div');
        dayDiv.className = 'day';
        dayDiv.classList.add(...classes);
        // Only add day number if it's not an empty cell
        if (dayNumber) {
             dayDiv.innerHTML = `<span class="day-number">${dayNumber}</span>`;
        }
        return dayDiv;
    }

    /** Creates a sticker div element based on habit info. */
    function createStickerElement(habitInfo, classes = []) {
        const stickerDiv = stickerTemplate.content.firstElementChild.cloneNode(true);
        stickerDiv.classList.add(...classes);
        stickerDiv.style.backgroundColor = habitInfo.color || '#cccccc'; // Default color
        stickerDiv.querySelector('.sticker-initial').textContent = habitInfo.initial || '?';
        stickerDiv.dataset.title = habitInfo.title || 'Unknown'; // Store title in data attribute
        stickerDiv.title = habitInfo.title || 'Unknown'; // Tooltip
        return stickerDiv;
    }

    // --- Drag and Drop Event Setup ---

    /** Sets up drag event listeners for a sticker element on the sheet. */
    function setupStickerDragEvents(stickerElement) {
        stickerElement.addEventListener('dragstart', (e) => {
            // Prevent dragging used stickers
            if (stickerElement.classList.contains('used')) {
                e.preventDefault();
                return;
            }

            sourceStickerElement = stickerElement; // Remember the original sticker

            // Create a temporary clone for visual feedback during drag
            draggedStickerElement = stickerElement.cloneNode(true);
            draggedStickerElement.style.position = 'absolute'; // Position relative to viewport
            draggedStickerElement.style.zIndex = '1000';
            draggedStickerElement.style.opacity = '0.7';
            draggedStickerElement.classList.add('dragging');

            // Calculate offset from cursor to top-left of sticker
            const rect = stickerElement.getBoundingClientRect();
            initialOffsetX = e.clientX - rect.left;
            initialOffsetY = e.clientY - rect.top;

            // Position the clone initially at the cursor
            draggedStickerElement.style.left = `${e.clientX - initialOffsetX}px`;
            draggedStickerElement.style.top = `${e.clientY - initialOffsetY}px`;
            document.body.appendChild(draggedStickerElement);

            // Set data transfer data (habit title) and effect
            e.dataTransfer.setData('text/plain', stickerElement.dataset.title);
            e.dataTransfer.effectAllowed = 'copy'; // Indicate a copy operation visually

            // Make the original sticker semi-transparent
            stickerElement.style.opacity = '0.5';
        });

        // Update clone position during drag (smoother than relying solely on dragover)
        // Using document listener for broader coverage
        // Added a flag to only update when dragging is active
        let isDragging = false;
        stickerElement.addEventListener('drag', () => { isDragging = true; }); // Set flag on drag
        document.addEventListener('dragover', (e) => {
             if (draggedStickerElement && isDragging) {
                 e.preventDefault(); // Necessary to allow drop
                 draggedStickerElement.style.left = `${e.clientX - initialOffsetX}px`;
                 draggedStickerElement.style.top = `${e.clientY - initialOffsetY}px`;
             }
        }, false);

        stickerElement.addEventListener('dragend', (e) => {
            isDragging = false; // Reset flag
            // Remove the temporary clone from the body
            if (draggedStickerElement && draggedStickerElement.parentNode === document.body) {
                 document.body.removeChild(draggedStickerElement);
            }
            draggedStickerElement = null;

            // Restore original sticker opacity
            if (sourceStickerElement) {
                sourceStickerElement.style.opacity = '1';
                sourceStickerElement = null;
            }

            // Clean up any lingering 'no-drop' classes on day elements
            document.querySelectorAll('.penco-calendar .day.no-drop').forEach(el => el.classList.remove('no-drop'));
             // Clean up any lingering 'drag-over' classes (should be handled by drop/leave, but just in case)
             document.querySelectorAll('.penco-calendar .day.drag-over').forEach(el => el.classList.remove('drag-over'));
        });
    }

    /** Sets up drop zone event listeners for a calendar day element. */
    function setupDayDropTargetEvents(dayElement, year, month, placedStickersMap, habitsInMonth) {
         dayElement.addEventListener('dragenter', (e) => {
             e.preventDefault(); // Allow drop
             if (sourceStickerElement) { // Check if dragging started from a valid source
                 const targetDateStr = dayElement.dataset.date;
                 const habitTitle = sourceStickerElement.dataset.title;
                 const stickersContainer = dayElement.querySelector('.stickers-container');
                 const hasPlaceholder = stickersContainer?.querySelector(`.sticker.placeholder[data-title="${habitTitle}"]`);
                 const alreadyPlaced = placedStickersMap[targetDateStr]?.includes(habitTitle);

                 // Add visual feedback: highlight if droppable, mark if not
                 if (hasPlaceholder && !alreadyPlaced) {
                     dayElement.classList.add('drag-over');
                     dayElement.classList.remove('no-drop');
                 } else {
                     dayElement.classList.add('no-drop');
                     dayElement.classList.remove('drag-over');
                 }
             }
         });

        dayElement.addEventListener('dragover', (e) => {
            e.preventDefault(); // Necessary to allow drop operation
            // Visual feedback handled by dragenter/dragleave
        });

        dayElement.addEventListener('dragleave', (e) => {
            // Remove highlighting if dragging leaves the element boundaries
            // Check relatedTarget to prevent flickering when moving over child elements
             if (!dayElement.contains(e.relatedTarget)) {
                dayElement.classList.remove('drag-over', 'no-drop');
            }
        });

        dayElement.addEventListener('drop', (e) => {
            e.preventDefault(); // Prevent default browser drop behavior
            dayElement.classList.remove('drag-over', 'no-drop'); // Clean up visual feedback

            if (sourceStickerElement) { // Ensure a valid sticker was being dragged
                const targetDateStr = dayElement.dataset.date;
                const habitTitle = sourceStickerElement.dataset.title;
                const stickersContainer = dayElement.querySelector('.stickers-container');
                const placeholder = stickersContainer?.querySelector(`.sticker.placeholder[data-title="${habitTitle}"]`);
                const alreadyPlaced = placedStickersMap[targetDateStr]?.includes(habitTitle);

                // --- Perform drop logic only if valid ---
                if (placeholder && !alreadyPlaced && stickersContainer) {
                    const habitInfo = habitsInMonth.get(habitTitle);
                    if (habitInfo) {
                        // Create a new sticker instance for the calendar day
                        const newSticker = createStickerElement(habitInfo);
                        stickersContainer.appendChild(newSticker);
                        placeholder.remove(); // Remove the placeholder

                        // --- Update and Save State ---
                        if (!placedStickersMap[targetDateStr]) {
                            placedStickersMap[targetDateStr] = [];
                        }
                        placedStickersMap[targetDateStr].push(habitTitle);
                        savePlacedStickers(year, month, placedStickersMap);

                        // Mark the original sticker on the sheet as used
                        sourceStickerElement.classList.add('used');
                    } else {
                         console.error(`Habit info not found in habitsInMonth map for title: ${habitTitle}`);
                    }
                } else {
                    console.log(`Drop prevented for "${habitTitle}" on ${targetDateStr}. Placeholder missing or sticker already placed.`);
                }
            } else {
                console.warn("Drop event occurred but no sourceStickerElement was set.");
            }
            // Clone removal is handled in dragend
        });
    }


    // --- Month Navigation ---
    prevMonthBtn.addEventListener('click', () => {
        currentDate.setMonth(currentDate.getMonth() - 1);
        renderCalendar(currentDate); // Re-render with previous month's data
    });

    nextMonthBtn.addEventListener('click', () => {
        currentDate.setMonth(currentDate.getMonth() + 1);
        renderCalendar(currentDate); // Re-render with next month's data
    });

    // --- Initial Render ---
    renderCalendar(currentDate);

});
