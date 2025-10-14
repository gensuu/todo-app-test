// static/calendar.js

/**
 * 指定された年月のカレンダーHTMLを生成する
 * @param {Date} dateToDisplay - カレンダーを表示する基準日
 * @param {Date} currentDate - 現在選択されている日付
 * @param {Date} today - 今日の日付
 * @param {Object} taskCounts - 日付ごとの未完了タスク数 {'YYYY-MM-DD': count}
 */
function generateCalendar(dateToDisplay, currentDate, today, taskCounts = {}) {
    const year = dateToDisplay.getFullYear();
    const month = dateToDisplay.getMonth(); // 0-11

    const firstDayOfMonth = new Date(year, month, 1);
    const lastDayOfMonth = new Date(year, month + 1, 0);

    const calendarHeader = `
        <div class="card-header calendar-header">
            <a href="#" id="prev-month-btn" class="btn btn-sm btn-outline-secondary">&lt;</a>
            <span class="fw-bold">${year}年 ${month + 1}月</span>
            <a href="#" id="next-month-btn" class="btn btn-sm btn-outline-secondary">&gt;</a>
        </div>`;

    let calendarBody = '<div class="card-body p-2"><table class="calendar-table"><thead><tr>';
    const weekdays = ['日', '月', '火', '水', '木', '金', '土'];
    weekdays.forEach(day => {
        calendarBody += `<th>${day}</th>`;
    });
    calendarBody += '</tr></thead><tbody>';

    let currentDay = new Date(firstDayOfMonth);
    currentDay.setDate(currentDay.getDate() - currentDay.getDay());

    for (let i = 0; i < 6; i++) {
        calendarBody += '<tr>';
        for (let j = 0; j < 7; j++) {
            const day = currentDay.getDate();
            const loopDateStr = `${currentDay.getFullYear()}-${String(currentDay.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            
            if (currentDay.getMonth() !== month) {
                calendarBody += '<td></td>';
            } else {
                let classes = [];
                if (currentDay.toDateString() === today.toDateString()) classes.push('today');
                if (currentDay.toDateString() === currentDate.toDateString()) classes.push('current-date');
                
                // --- ▼▼▼ 変更点: taskCountsを使ってドットを描画する ---
                let dotsHtml = '';
                if (taskCounts[loopDateStr] > 0) {
                    dotsHtml += '<div class="task-dots">';
                    // 未完了タスクの数だけドットを追加（最大8個）
                    for (let k = 0; k < Math.min(taskCounts[loopDateStr], 8); k++) {
                        dotsHtml += '<span class="task-dot"></span>';
                    }
                    dotsHtml += '</div>';
                }
                
                calendarBody += `
                    <td class="${classes.join(' ')}">
                        <a href="/todo/${loopDateStr}">
                            <span class="calendar-day-num">${day}</span>
                            ${dotsHtml}
                        </a>
                    </td>`;
            }
            currentDay.setDate(currentDay.getDate() + 1);
        }
        calendarBody += '</tr>';
        if (currentDay > lastDayOfMonth && currentDay.getDay() === 0) break;
    }

    calendarBody += '</tbody></table></div>';
    return calendarHeader + calendarBody;
}

/**
 * 日付ナビゲーションのHTMLを生成する
 * @param {Date} currentDate - 現在選択されている日付
 */
function generateDateNav(currentDate) {
    const prevDay = new Date(currentDate);
    prevDay.setDate(currentDate.getDate() - 1);
    const nextDay = new Date(currentDate);
    nextDay.setDate(currentDate.getDate() + 1);

    const prevDayStr = `${prevDay.getFullYear()}-${String(prevDay.getMonth() + 1).padStart(2, '0')}-${String(prevDay.getDate()).padStart(2, '0')}`;
    const nextDayStr = `${nextDay.getFullYear()}-${String(nextDay.getMonth() + 1).padStart(2, '0')}-${String(nextDay.getDate()).padStart(2, '0')}`;

    return `
        <a href="/todo/${prevDayStr}" class="btn btn-outline-secondary">&lt; 前日へ</a>
        <a href="/todo" class="btn btn-outline-secondary">今日に戻る</a>
        <a href="/todo/${nextDayStr}" class="btn btn-outline-secondary">翌日へ &gt;</a>
    `;
}