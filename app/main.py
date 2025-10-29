from flask import (
    Blueprint, render_template, request, redirect, url_for,
    jsonify, flash, session, send_file, current_app
)
from flask_login import current_user, login_required
from sqlalchemy import or_, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, date
import os
import openpyxl
import json
import math
import pytz
from io import BytesIO
import calendar
import secrets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from .extensions import db
from .models import (
    User, MasterTask, SubTask, DailySummary, TaskTemplate,
    SubtaskTemplate, get_jst_today, DateAsString, RecurrenceType
)

main_bp = Blueprint('main', __name__)

# --- Helper Functions (specific to main blueprint) ---

def reset_recurring_tasks_if_needed(user_id):
    """Resets the completion status of recurring tasks based on their schedule."""
    today = get_jst_today()
    # Find recurring tasks that haven't been reset today (or ever)
    tasks_to_reset = MasterTask.query.filter(
        MasterTask.user_id == user_id,
        MasterTask.recurrence_type != 'none',
        or_(MasterTask.last_reset_date == None, MasterTask.last_reset_date < today)
    ).all()

    reset_count = 0
    for task in tasks_to_reset:
        should_reset_today = False
        # Don't reset if the start date (due_date) is in the future
        if task.due_date > today:
            continue

        if task.recurrence_type == 'daily':
            should_reset_today = True
        elif task.recurrence_type == 'weekly' and task.recurrence_days:
            today_weekday = str(today.weekday()) # Monday is 0, Sunday is 6
            if today_weekday in task.recurrence_days:
                should_reset_today = True

        if should_reset_today:
            # Update subtasks: set is_completed=False, completion_date=None
            subtasks_updated = SubTask.query.filter(
                SubTask.master_id == task.id,
                SubTask.is_completed == True
            ).update({
                SubTask.is_completed: False,
                SubTask.completion_date: None
            }, synchronize_session=False) # Important for bulk updates

            if subtasks_updated > 0:
                reset_count += subtasks_updated
            # Update the last reset date for the master task
            task.last_reset_date = today

    if reset_count > 0:
        db.session.commit()
        current_app.logger.info(f"User {user_id}: Reset {reset_count} subtasks for {today}.")
    elif tasks_to_reset: # Commit even if no subtasks were reset (to update last_reset_date)
        db.session.commit()

def update_summary(user_id):
    """Calculates and updates the daily summary (streak, average grids) for the user."""
    today = get_jst_today()

    # Calculate average grids completed in the last 30 days
    thirty_days_ago = today - timedelta(days=30)
    grids_last_30_days = db.session.query(
        func.sum(SubTask.grid_count)
    ).join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date >= thirty_days_ago,
        SubTask.completion_date <= today # Include today
    ).scalar() or 0 # Default to 0 if no tasks completed

    # Count distinct days with completions in the last 30 days for average calculation
    distinct_completion_days_count = db.session.query(
        func.count(func.distinct(SubTask.completion_date))
    ).join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date >= thirty_days_ago,
        SubTask.completion_date <= today
    ).scalar() or 0

    average_grids = (grids_last_30_days / distinct_completion_days_count) if distinct_completion_days_count > 0 else 0.0

    # Calculate current streak
    completed_dates = db.session.query(
        SubTask.completion_date
    ).join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date != None
    ).distinct().all()

    streak = 0
    if completed_dates:
        unique_dates_set = {d[0] for d in completed_dates}
        check_date = today
        # Streak continues if completed today OR yesterday
        if today in unique_dates_set or (today - timedelta(days=1)) in unique_dates_set:
            if today not in unique_dates_set: # If not completed today, start checking from yesterday
                check_date = today - timedelta(days=1)
            # Go back day by day as long as there was a completion
            while check_date in unique_dates_set:
                streak += 1
                check_date -= timedelta(days=1)

    # Find or create today's summary record
    summary = DailySummary.query.filter_by(user_id=user_id, summary_date=today).first()
    if not summary:
        summary = DailySummary(user_id=user_id, summary_date=today)
        db.session.add(summary)

    # Update and commit
    summary.streak = streak
    summary.average_grids = round(average_grids, 2)
    db.session.commit()

# Note: cleanup_old_tasks is not called automatically. Consider scheduling or manual trigger.
def cleanup_old_tasks(user_id):
    """Deletes old, completed, non-recurring tasks."""
    cleanup_threshold = get_jst_today() - timedelta(days=32)
    old_subtasks_query = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == user_id,
        MasterTask.recurrence_type == 'none',
        SubTask.is_completed == True,
        SubTask.completion_date < cleanup_threshold
    )
    # Get master_ids before deleting subtasks
    master_ids_to_check = [st.master_id for st in old_subtasks_query.all()]

    deleted_subtask_count = old_subtasks_query.delete(synchronize_session=False)

    deleted_master_count = 0
    if master_ids_to_check:
        # Find master tasks whose *only* remaining subtasks were the ones deleted
        masters_to_delete = MasterTask.query.filter(
            MasterTask.id.in_(master_ids_to_check),
            ~MasterTask.subtasks.any(or_(SubTask.is_completed == False, SubTask.completion_date >= cleanup_threshold))
        )
        deleted_master_count = masters_to_delete.delete(synchronize_session=False)

    if deleted_subtask_count > 0 or deleted_master_count > 0:
        db.session.commit()
        current_app.logger.info(f"Cleanup: User {user_id} deleted {deleted_master_count} masters, {deleted_subtask_count} subs.")


# --- Main Routes ---

@main_bp.route("/")
def index_redirect():
    """Redirects root URL based on authentication status."""
    if current_user.is_authenticated:
        return redirect(url_for("main.todo_list")) # Redirect to user's todo list
    else:
        return redirect(url_for("auth.login")) # Redirect to login page

@main_bp.route("/healthz")
def health_check():
    """Basic health check endpoint."""
    # Could add DB check here if needed
    return "OK", 200

# --- PWA Static Files ---
@main_bp.route('/sw.js')
def serve_sw():
    """Serves the Service Worker file from the project root."""
    # Use send_from_directory for better security/path handling
    return send_file('../sw.js', mimetype='application/javascript')

@main_bp.route('/manifest.json')
def serve_manifest():
    """Serves the Web App Manifest file from the static folder."""
    return send_file('../static/manifest.json', mimetype='application/manifest+json')

# Note: offline.js and calendar.js are served automatically by Flask's static handler

# --- Todo List View ---
@main_bp.route('/todo')
@main_bp.route('/todo/<date_str>')
@login_required
def todo_list(date_str=None):
    """Displays the todo list for a specific date."""
    reset_recurring_tasks_if_needed(current_user.id) # Reset tasks before displaying

    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            current_app.logger.warning(f"Invalid date format: {date_str}. Redirecting to today.")
            return redirect(url_for('main.todo_list'))

    # --- Prepare data for the calendar view (task counts) ---
    first_day_of_month = target_date.replace(day=1)
    # Calculate the first day of the next month safely
    next_month_first_day = (first_day_of_month + timedelta(days=32)).replace(day=1)

    uncompleted_tasks_count = db.session.query(
        MasterTask.due_date, func.count(MasterTask.id)
    ).outerjoin(SubTask).filter(
        MasterTask.user_id == current_user.id,
        MasterTask.due_date >= first_day_of_month,
        MasterTask.due_date < next_month_first_day,
        MasterTask.recurrence_type == 'none', # Only count non-recurring tasks for calendar dots
        MasterTask.subtasks.any(SubTask.is_completed == False)
    ).group_by(MasterTask.due_date).all()
    # Convert to dictionary for easy JS access { 'YYYY-MM-DD': count }
    task_counts_for_js = {d.isoformat(): c for d, c in uncompleted_tasks_count}

    # --- Fetch and filter tasks to display for the target_date ---
    today_weekday = str(target_date.weekday())

    # Eager load subtasks to avoid N+1 queries in the loop
    all_master_tasks = MasterTask.query.options(
        selectinload(MasterTask.subtasks)
    ).filter(
        MasterTask.user_id == current_user.id,
        MasterTask.subtasks.any() # Optimization: Only fetch master tasks with subtasks
    ).order_by(MasterTask.is_urgent.desc(), MasterTask.due_date.asc(), MasterTask.id.asc()).all()

    daily_tasks_for_template = []       # Tasks due today (non-recurring)
    recurring_tasks_for_template = []   # Recurring tasks active today

    for mt in all_master_tasks:
        is_visible_today = False
        # Check if recurring task should be shown today
        if mt.recurrence_type != 'none' and mt.due_date <= target_date: # Started on or before today
            if mt.recurrence_type == 'daily':
                is_visible_today = True
            elif mt.recurrence_type == 'weekly' and mt.recurrence_days and today_weekday in mt.recurrence_days:
                is_visible_today = True
        # Check if non-recurring task is due today
        elif mt.recurrence_type == 'none' and mt.due_date == target_date:
            is_visible_today = True

        if not is_visible_today: continue # Skip if not visible today

        # Determine which subtasks to show within the master task card
        if mt.recurrence_type != 'none':
            visible_subtasks = mt.subtasks # Show all for recurring
        else:
            # Show uncompleted or completed *today* for non-recurring
            visible_subtasks = [st for st in mt.subtasks if not st.is_completed or st.completion_date == target_date]

        # Only include master task if it has visible subtasks for the day
        if not visible_subtasks: continue

        # Prepare data for the template
        mt.visible_subtasks = sorted(visible_subtasks, key=lambda x: x.id) # Sort by ID for consistent order
        subtasks_as_dicts = [{"id": st.id, "content": st.content, "is_completed": st.is_completed, "grid_count": st.grid_count} for st in mt.visible_subtasks]
        mt.visible_subtasks_json = json.dumps(subtasks_as_dicts) # For focus modal
        mt.all_completed_today = all(st.is_completed for st in mt.visible_subtasks) if mt.visible_subtasks else False

        # Calculate last completion date among all subtasks (for header display)
        all_completed_ever = all(st.is_completed for st in mt.subtasks)
        if all_completed_ever:
            completion_dates = [st.completion_date for st in mt.subtasks if st.completion_date]
            mt.last_completion_date = max(completion_dates) if completion_dates else None
        else:
            mt.last_completion_date = None

        # Add to appropriate list for rendering
        if mt.recurrence_type != 'none':
            recurring_tasks_for_template.append(mt)
        else:
            daily_tasks_for_template.append(mt)

    # --- Calculate Grid Data ---
    all_subtasks_for_day_grid = []
    # Include visible subtasks from both lists
    for mt in daily_tasks_for_template: all_subtasks_for_day_grid.extend(mt.visible_subtasks)
    for mt in recurring_tasks_for_template: all_subtasks_for_day_grid.extend(mt.visible_subtasks)

    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day_grid)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day_grid if sub.is_completed)

    # Determine grid dimensions
    GRID_COLS, base_rows = 10, 2 # Constants for grid layout
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)

    # --- Update and Fetch Summary ---
    update_summary(current_user.id) # Ensure summary is up-to-date
    latest_summary = DailySummary.query.filter(DailySummary.user_id == current_user.id).order_by(DailySummary.summary_date.desc()).first()

    return render_template(
        'index.html',
        daily_tasks=daily_tasks_for_template,
        recurring_tasks=recurring_tasks_for_template,
        current_date=target_date,
        today=get_jst_today(),
        total_grid_count=total_grid_count,
        completed_grid_count=completed_grid_count,
        GRID_COLS=GRID_COLS,
        grid_rows=grid_rows,
        summary=latest_summary,
        task_counts=task_counts_for_js # Pass counts for the calendar JS
    )


# --- Add/Edit Task View ---
# Handles both creating new tasks and editing existing ones
@main_bp.route('/add_or_edit_task', methods=['GET', 'POST'])
@main_bp.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
@login_required
def add_or_edit_task(master_id=None):
    master_task = db.session.get(MasterTask, master_id) if master_id else None
    # Authorization check
    if master_task and master_task.user_id != current_user.id:
        flash("アクセス権限がありません。", "danger")
        return redirect(url_for('main.todo_list'))

    # Determine the URL for the current page (used for back links)
    date_str_param = request.args.get('date_str', get_jst_today().strftime('%Y-%m-%d'))
    if master_id:
        from_url = url_for('main.add_or_edit_task', master_id=master_id, date_str=date_str_param)
    else:
        from_url = url_for('main.add_or_edit_task', date_str=date_str_param)

    if request.method == 'POST':
        try:
            # --- Handle "Save as Template" action ---
            if request.form.get('save_as_template') == 'true':
                template_title = request.form.get('master_title', '').strip()
                if not template_title:
                    flash("テンプレート名を指定してください。", "warning")
                    return redirect(request.args.get('back_url') or from_url) # Redirect back

                # Check if template exists, update or create
                existing_template = TaskTemplate.query.filter_by(user_id=current_user.id, title=template_title).first()
                if existing_template:
                    template = existing_template
                    # Delete existing subtask templates before adding new ones
                    SubtaskTemplate.query.filter_by(template_id=template.id).delete()
                    current_app.logger.info(f"Updating template '{template_title}' by user {current_user.id}.")
                else:
                    template = TaskTemplate(title=template_title, user_id=current_user.id)
                    db.session.add(template)
                    db.session.flush() # Get template.id before adding subtasks
                    current_app.logger.info(f"Creating new template '{template_title}' by user {current_user.id}.")

                # Add subtask templates from form data
                subtask_count = 0
                for i in range(1, 21): # Assuming max 20 subtask fields in form
                    sub_content = request.form.get(f'sub_content_{i}', '').strip()
                    grid_count_str = request.form.get(f'grid_count_{i}', '0').strip()
                    if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                        grid_count = int(grid_count_str)
                        db.session.add(SubtaskTemplate(template_id=template.id, content=sub_content, grid_count=grid_count))
                        subtask_count += 1

                if subtask_count == 0:
                    flash("有効なサブタスクがないため、テンプレートは保存されませんでした。", "warning")
                    db.session.rollback() # Roll back template creation if no subtasks
                    # No need to pop session data anymore
                    return redirect(request.args.get('back_url') or from_url)

                db.session.commit()
                flash(f"テンプレート「{template_title}」を保存しました。", "success")
                # Redirect back using the 'back_url' parameter passed in the action URL
                return redirect(request.args.get('back_url') or from_url)

            # --- Handle "Save Task" action (Create or Update) ---
            master_title = request.form.get('master_title', '').strip()
            due_date_str = request.form.get('due_date')
            is_urgent = 'is_urgent' in request.form
            is_habit = 'is_habit' in request.form
            recurrence_type = request.form.get('recurrence_type', 'none')
            recurrence_days = "".join(sorted(request.form.getlist('recurrence_days'))) if recurrence_type == 'weekly' else None

            # Basic validation
            if not master_title:
                flash("タスクタイトルは必須です。", "warning")
                return redirect(from_url)
            try:
                due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()
            except ValueError:
                flash("日付の形式が正しくありません (YYYY-MM-DD)。", "warning")
                return redirect(from_url)

            if master_task: # --- Update Existing Task ---
                master_task.title = master_title
                master_task.due_date = due_date_obj # Update start/due date
                master_task.is_urgent = is_urgent
                master_task.is_habit = is_habit
                master_task.recurrence_type = recurrence_type
                master_task.recurrence_days = recurrence_days if recurrence_type == 'weekly' else None
                # Reset last_reset_date if recurrence is modified
                if recurrence_type != 'none': master_task.last_reset_date = None

                current_app.logger.info(f"Updating task ID {master_task.id} for user {current_user.id}.")
                # Delete existing subtasks before adding new ones
                SubTask.query.filter_by(master_id=master_task.id).delete()
            else: # --- Create New Task ---
                master_task = MasterTask(
                    title=master_title,
                    due_date=due_date_obj,
                    user_id=current_user.id,
                    is_urgent=is_urgent,
                    is_habit=is_habit,
                    recurrence_type=recurrence_type,
                    recurrence_days=recurrence_days if recurrence_type == 'weekly' else None,
                    last_reset_date=None # New tasks don't have a reset date
                )
                db.session.add(master_task)
                db.session.flush() # Need master_task.id for subtasks
                current_app.logger.info(f"Creating new task '{master_title}' for user {current_user.id}.")

            # --- Add/Update Subtasks ---
            subtask_added = False
            for i in range(1, 21): # Assuming max 20 subtask fields
                sub_content = request.form.get(f'sub_content_{i}', '').strip()
                grid_count_str = request.form.get(f'grid_count_{i}', '0').strip()
                if sub_content and grid_count_str.isdigit():
                    grid_count = int(grid_count_str)
                    if grid_count > 0:
                        db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=grid_count))
                        subtask_added = True

            if not subtask_added:
                flash("有効なサブタスクを少なくとも1つ入力してください。", "warning")
                db.session.rollback() # Roll back master task creation/update if no subtasks
                return redirect(from_url)

            db.session.commit()
            # Session data for temporary storage is no longer needed
            # session.pop('temp_task_data', None)
            flash("タスクを保存しました。", "success")

            # Redirect to the appropriate date view
            redirect_date_str = get_jst_today().strftime('%Y-%m-%d') if recurrence_type != 'none' else master_task.due_date.strftime('%Y-%m-%d')
            return redirect(url_for('main.todo_list', date_str=redirect_date_str))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error saving task for user {current_user.id}: {e}", exc_info=True)
            flash(f"タスクの保存中にエラーが発生しました。", "danger") # Use generic error
            return redirect(from_url)

    # --- GET Request Logic ---
    # Session data is no longer used for repopulating form
    session_data = None # Keep variable for template compatibility, but always None

    default_date = get_jst_today()
    if date_str_param:
        try:
            default_date = datetime.strptime(date_str_param, '%Y-%m-%d').date()
        except ValueError:
            pass # Use today's date if param is invalid

    # Fetch templates for the dropdown
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).order_by(TaskTemplate.title).all()
    # Prepare template data for JavaScript
    templates_data = {
        t.id: {
            "title": t.title,
            "subtasks": [{"content": s.content, "grid_count": s.grid_count} for s in t.subtask_templates]
        } for t in templates
    }

    # Prepare existing subtasks if editing
    subtasks_for_template = []
    if master_task:
        # Load subtasks eagerly if not already loaded (though selectinload should handle this)
        subtasks_for_template = [{"content": sub.content, "grid_count": sub.grid_count} for sub in master_task.subtasks]

    return render_template(
        'edit_task.html',
        master_task=master_task,
        existing_subtasks=subtasks_for_template,
        default_date=default_date,
        templates=templates,
        templates_data=templates_data,
        session_data=session_data, # Pass None
        from_url=from_url # Pass the current URL for back links
    )

# --- API: Complete Subtask ---
@main_bp.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def complete_subtask_api(subtask_id):
    """API endpoint to toggle the completion status of a subtask."""
    subtask = db.session.get(SubTask, subtask_id)
    if not subtask:
        return jsonify({'success': False, 'error': 'Subtask not found'}), 404
    if subtask.master_task.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    master_task = subtask.master_task
    today = get_jst_today()
    # Get the date context from the request (important for UI updates)
    target_date_str = request.json.get('current_date') if request.is_json else None
    try:
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date() if target_date_str else today
    except ValueError:
        target_date = today # Default to today if date is invalid

    # Toggle completion status
    subtask.is_completed = not subtask.is_completed

    # Update completion date
    if subtask.is_completed:
        subtask.completion_date = today # Mark completion with today's date
    elif master_task.recurrence_type == 'none':
         subtask.completion_date = None # Clear date only for non-recurring when marked incomplete

    try:
        db.session.commit()
        current_app.logger.info(f"Subtask {subtask_id} completion toggled to {subtask.is_completed} for user {current_user.id}.")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating subtask {subtask_id} completion: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Database error'}), 500

    # --- Recalculate and return data needed for UI update ---
    update_summary(current_user.id) # Update overall summary stats

    # Re-fetch master task with its subtasks to get the latest state
    # Use options(selectinload(...)) to ensure subtasks are loaded efficiently
    master_task = MasterTask.query.options(selectinload(MasterTask.subtasks)).get(master_task.id)

    # Determine visible subtasks and completion status *for the target_date*
    today_weekday = str(target_date.weekday())
    is_recurring_today = False
    if master_task.recurrence_type != 'none' and master_task.due_date <= target_date:
        if master_task.recurrence_type == 'daily': is_recurring_today = True
        elif master_task.recurrence_type == 'weekly' and master_task.recurrence_days and today_weekday in master_task.recurrence_days: is_recurring_today = True

    if is_recurring_today:
        visible_subtasks = master_task.subtasks # Show all subtasks
    else: # Normal task or recurring but not for today
        visible_subtasks = [st for st in master_task.subtasks if not st.is_completed or st.completion_date == target_date]

    visible_subtasks.sort(key=lambda x: x.id) # Ensure consistent order

    # Prepare data needed specifically for the master task header update
    subtasks_as_dicts = [{"id": st.id, "content": st.content, "is_completed": st.is_completed, "grid_count": st.grid_count} for st in visible_subtasks]
    master_task.visible_subtasks_json = json.dumps(subtasks_as_dicts) # For focus modal data attribute
    master_task.all_completed_today = all(st.is_completed for st in visible_subtasks) if visible_subtasks else False
    all_completed_ever = all(st.is_completed for st in master_task.subtasks)
    if all_completed_ever:
        completion_dates = [st.completion_date for st in master_task.subtasks if st.completion_date]
        master_task.last_completion_date = max(completion_dates) if completion_dates else None
    else:
        master_task.last_completion_date = None

    # Render only the header part using the updated master_task data
    updated_header_html = render_template('_master_task_header.html', master_task=master_task, current_date=target_date)

    # --- Recalculate grid and summary data based on the *current* state ---
    # (Similar logic to the main todo_list view, focused on the target_date)
    all_master_tasks = MasterTask.query.options(selectinload(MasterTask.subtasks)).filter(
        MasterTask.user_id == current_user.id, MasterTask.subtasks.any()
    ).all()

    daily_tasks_active = []; recurring_tasks_active = []
    for mt in all_master_tasks:
        _is_recurring_today = False
        if mt.recurrence_type != 'none' and mt.due_date <= target_date:
            if mt.recurrence_type == 'daily': _is_recurring_today = True
            elif mt.recurrence_type == 'weekly' and mt.recurrence_days and today_weekday in mt.recurrence_days: _is_recurring_today = True

        if _is_recurring_today:
            mt.visible_subtasks = mt.subtasks # Use all subtasks for recurring today
            recurring_tasks_active.append(mt)
        elif mt.recurrence_type == 'none' and mt.due_date == target_date:
             mt.visible_subtasks = [st for st in mt.subtasks if not st.is_completed or st.completion_date == target_date]
             if mt.visible_subtasks: # Only include if there are visible subtasks
                 daily_tasks_active.append(mt)

    all_subtasks_for_day_grid = []
    for mt in daily_tasks_active: all_subtasks_for_day_grid.extend(mt.visible_subtasks)
    for mt in recurring_tasks_active: all_subtasks_for_day_grid.extend(mt.visible_subtasks) # Use visible (all) for recurring

    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day_grid)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day_grid if sub.is_completed)

    # Fetch the latest summary data (already updated by update_summary call)
    latest_summary = DailySummary.query.filter(DailySummary.user_id == current_user.id).order_by(DailySummary.summary_date.desc()).first()
    summary_data = {
        'streak': latest_summary.streak if latest_summary else 0,
        'average_grids': latest_summary.average_grids if latest_summary else 0.0
    }

    # Return all necessary data for the frontend JS to update the UI
    return jsonify({
        'success': True,
        'is_completed': subtask.is_completed, # New status of the toggled task
        'total_grid_count': total_grid_count, # Updated total grids for the day
        'completed_grid_count': completed_grid_count, # Updated completed grids for the day
        'summary': summary_data, # Updated streak and average
        'updated_header_html': updated_header_html, # HTML for the specific master task header
        'master_task_id': subtask.master_id # ID of the affected master task
    })

# --- Habit Calendar ---
@main_bp.route('/habit_calendar')
@login_required
def habit_calendar():
    """Renders the habit calendar page."""
    return render_template('habit_calendar.html')

@main_bp.route('/api/habit_calendar/<int:year>/<int:month>')
@login_required
def habit_calendar_data(year, month):
    """API endpoint to fetch completed habit data for a given month."""
    try:
        # Validate month and year if necessary
        start_date = date(year, month, 1)
        _, last_day = calendar.monthrange(year, month) # Get the number of days in the month
        end_date = date(year, month, last_day)

        current_app.logger.debug(f"Fetching habit data for User {current_user.id} {year}-{month}")

        # Query distinct completion date and title for completed habits in the month
        completed_habits = db.session.query(
            SubTask.completion_date,
            MasterTask.title
        ).join(MasterTask).filter(
            MasterTask.user_id == current_user.id,
            MasterTask.is_habit == True,
            SubTask.is_completed == True,
            SubTask.completion_date >= start_date,
            SubTask.completion_date <= end_date
        ).distinct(SubTask.completion_date, MasterTask.title).order_by(SubTask.completion_date).all()

        # Process data into a dictionary grouped by date { 'YYYY-MM-DD': [ {initial, color, title}, ... ] }
        habits_by_date = {}
        colors = ['#EF4444', '#FCD34D', '#10B981', '#3B82F6', '#A855F7', '#EC4899'] # Predefined colors
        habit_colors = {} # Cache colors assigned to each habit title
        color_index = 0

        for completion_date, title in completed_habits:
            if completion_date is None: continue # Skip if somehow completion_date is null

            date_str = completion_date.isoformat()
            initial = title[0].upper() if title else '?'

            # Assign a color consistently to each habit title
            if title not in habit_colors:
                habit_colors[title] = colors[color_index % len(colors)]
                color_index += 1
            color = habit_colors[title]

            # Append habit info to the list for that date
            if date_str not in habits_by_date:
                habits_by_date[date_str] = []
            habits_by_date[date_str].append({'initial': initial, 'color': color, 'title': title})

        current_app.logger.debug(f"Found {len(habits_by_date)} dates with habits for {year}-{month}.")
        return jsonify(habits_by_date)

    except ValueError: # Handle invalid year/month
        current_app.logger.error(f"Invalid date requested: {year}-{month}")
        return jsonify({"error": "Invalid date format"}), 400
    except Exception as e:
        current_app.logger.error(f"Error fetching habit calendar data: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch habit data"}), 500


# --- Excel Import ---
@main_bp.route('/import', methods=['GET', 'POST'])
@login_required
def import_excel():
    """Handles Excel file upload and task import."""
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if not file or not file.filename.endswith('.xlsx'):
            flash('無効なファイル形式です (.xlsxのみ)。', "warning")
            return redirect(url_for('main.import_excel'))
        try:
            current_app.logger.info(f"Starting Excel import for user {current_user.username}...")
            workbook = openpyxl.load_workbook(file)
            sheet = workbook.active
            header = [str(cell.value or '').strip() for cell in sheet[1]] # Read header row

            # --- Column Mapping Logic ---
            col_map = {}
            expected_headers = { # Define expected header names
                'title': ['主タスク', '親タスクのタイトル'],
                'due_date': ['期限日'],
                'sub_content': ['サブタスク内容'],
                'grid_count': ['マス数'],
            }
            found_all_headers = True
            for key, possible_names in expected_headers.items():
                found = False
                for name in possible_names:
                    if name in header:
                        col_map[key] = header.index(name)
                        found = True
                        break
                if not found:
                    found_all_headers = False
                    break

            # Fallback to column indices if headers are unreliable
            if not found_all_headers and len(header) >= 4:
                col_map = {'title': 0, 'due_date': 1, 'sub_content': 2, 'grid_count': 3}
                flash('ヘッダーが期待通りでないため、列位置 (A, B, C, D) でインポートを試みます。', 'info')
            elif not found_all_headers:
                flash('必要なヘッダー (タイトル, 期限日, 内容, マス数) が見つからないか、列数が不足しています。', 'danger')
                return redirect(url_for('main.import_excel'))

            # --- Process Rows ---
            master_tasks_cache = {} # Cache master tasks to avoid duplicates { (title, due_date): MasterTask }
            master_task_count = 0; sub_task_count = 0; skipped_rows = 0

            for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                # Basic row validation
                if len(row) <= max(col_map.values()): # Check if row has enough columns
                    skipped_rows += 1; current_app.logger.warning(f"Skipping row {row_idx}: Not enough columns."); continue

                # Extract data based on col_map
                master_title = str(row[col_map['title']]).strip() if row[col_map['title']] else None
                due_date_val = row[col_map['due_date']]
                sub_content = str(row[col_map['sub_content']]).strip() if row[col_map['sub_content']] else None
                grid_count_val = row[col_map['grid_count']]

                # Skip row if essential data is missing
                if not master_title or not sub_content:
                    skipped_rows += 1; current_app.logger.warning(f"Skipping row {row_idx}: Missing master title or subtask content."); continue

                # --- Parse Due Date ---
                due_date = get_jst_today() # Default to today
                if isinstance(due_date_val, datetime): due_date = due_date_val.date()
                elif isinstance(due_date_val, date): due_date = due_date_val
                elif isinstance(due_date_val, (str, int, float)):
                    try: # Attempt to parse Excel date number or string
                        if isinstance(due_date_val, (int, float)): # Excel date number (requires epoch adjustment)
                            delta = timedelta(days=due_date_val - 25569) # Adjust for Excel epoch
                            due_date = date(1970, 1, 1) + delta
                        else: # String format
                            str_date = str(due_date_val).split(" ")[0] # Handle 'YYYY-MM-DD HH:MM:SS'
                            due_date = datetime.strptime(str_date, '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        current_app.logger.warning(f"Row {row_idx}: Could not parse date '{due_date_val}'. Using default: {due_date}.")

                # --- Parse Grid Count ---
                grid_count = 1 # Default to 1
                if grid_count_val:
                    try:
                        parsed_count = int(float(str(grid_count_val))) # Allow float/text numbers
                        if parsed_count > 0: grid_count = parsed_count
                    except (ValueError, TypeError):
                        current_app.logger.warning(f"Row {row_idx}: Could not parse grid count '{grid_count_val}'. Using default: {grid_count}.")

                # --- Find or Create Master Task ---
                cache_key = (master_title, due_date)
                if cache_key not in master_tasks_cache:
                    master_task = MasterTask(title=master_title, due_date=due_date, user_id=current_user.id, recurrence_type='none')
                    db.session.add(master_task); db.session.flush(); # Get ID before adding subtask
                    master_tasks_cache[cache_key] = master_task; master_task_count += 1
                else:
                    master_task = master_tasks_cache[cache_key]

                # --- Add Sub Task ---
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=grid_count))
                sub_task_count += 1

            db.session.commit() # Commit all changes at the end
            current_app.logger.info(f"Import success: {master_task_count} masters, {sub_task_count} subs. Skipped {skipped_rows}.")
            flash(f'{master_task_count}件の親タスク ({sub_task_count}件のサブタスク) をインポート。{skipped_rows}行スキップ。', 'success')
            return redirect(url_for('main.todo_list'))

        except Exception as e:
            db.session.rollback() # Rollback on any error during processing
            current_app.logger.error(f'Excel import failed: {e}', exc_info=True)
            flash(f'インポートエラーが発生しました。ファイル形式を確認してください。', 'danger') # Generic error
            return redirect(url_for('main.import_excel'))

    # GET request: render the upload form
    return render_template('import.html')


# --- Template Management ---
@main_bp.route('/templates', methods=['GET', 'POST'])
@login_required
def manage_templates():
    """Handles viewing, creating, and triggering deletion of templates."""
    back_url = request.args.get('back_url') # Preserve back URL for navigation

    if request.method == 'POST':
        # --- Create/Update Template ---
        try:
            template_title = request.form.get('template_title', '').strip()
            if not template_title:
                flash("テンプレート名は必須です。", "warning")
                return redirect(url_for('main.manage_templates', back_url=back_url))

            # Check if template exists by title for the current user
            existing_template = TaskTemplate.query.filter_by(user_id=current_user.id, title=template_title).first()
            if existing_template:
                template = existing_template
                # Delete existing subtasks before replacing them (update scenario)
                SubtaskTemplate.query.filter_by(template_id=template.id).delete()
                current_app.logger.info(f"Updating template '{template_title}' from manage page.")
            else:
                template = TaskTemplate(title=template_title, user_id=current_user.id)
                db.session.add(template)
                db.session.flush() # Need the ID for subtasks
                current_app.logger.info(f"Creating template '{template_title}' from manage page.")

            # Add subtasks from the form
            subtask_count = 0
            for i in range(1, 21): # Assume max 20 fields
                sub_content = request.form.get(f'sub_content_{i}', '').strip()
                grid_count_str = request.form.get(f'grid_count_{i}', '0').strip()
                if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                    grid_count = int(grid_count_str)
                    db.session.add(SubtaskTemplate(template_id=template.id, content=sub_content, grid_count=grid_count))
                    subtask_count += 1

            if subtask_count == 0:
                flash("有効なサブタスクがないため、保存されませんでした。", "warning")
                db.session.rollback() # Roll back template creation/update
            else:
                db.session.commit()
                flash(f"テンプレート「{template_title}」を保存しました。", "success")

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error saving template from manage page: {e}", exc_info=True)
            flash(f"テンプレート保存エラー。", "danger") # Generic error

        return redirect(url_for('main.manage_templates', back_url=back_url)) # Redirect back to manage page

    # --- GET Request: Display templates ---
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).order_by(TaskTemplate.title).all()
    return render_template('manage_templates.html', templates=templates, back_url=back_url)


@main_bp.route('/delete_template/<int:template_id>', methods=['POST'])
@login_required
def delete_template(template_id):
    """Handles template deletion."""
    template = db.session.get(TaskTemplate, template_id)
    # Check if template exists and belongs to the user
    if not template:
        flash("テンプレートが見つかりません。", "warning")
    elif template.user_id != current_user.id:
        flash("アクセス権限がありません。", "danger")
    else:
        try:
            title = template.title
            db.session.delete(template) # Cascade delete handles subtasks
            db.session.commit()
            flash(f"テンプレート「{title}」を削除しました。", "success")
            current_app.logger.info(f"Deleted template '{title}' (ID: {template_id}).")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting template {template_id}: {e}", exc_info=True)
            flash(f"テンプレート削除エラー。", "danger") # Generic error

    # Redirect back, preserving the back_url if possible
    back_url = request.args.get('back_url') or request.referrer or url_for('main.manage_templates')
    return redirect(back_url)


# --- Scratchpad ---
@main_bp.route('/scratchpad')
@login_required
def scratchpad():
    """Renders the scratchpad page."""
    # This template is standalone, doesn't extend layout.html
    return render_template('scratchpad.html')

@main_bp.route('/export_scratchpad', methods=['POST'])
@login_required
def export_scratchpad():
    """API endpoint to export scratchpad items to today's quick task."""
    if not request.is_json:
        return jsonify({'success': False, 'message': '無効なリクエスト形式です。'}), 400
    tasks_to_add = request.json.get('tasks')
    if not tasks_to_add or not isinstance(tasks_to_add, list):
        return jsonify({'success': False, 'message': '有効なタスクがありません。'}), 400

    today = get_jst_today()
    master_title = f"{today.strftime('%Y-%m-%d')}のクイックタスク" # Standard title for quick tasks

    try:
        # Find or create the master task for today's quick tasks
        master_task = MasterTask.query.filter_by(user_id=current_user.id, title=master_title, due_date=today, recurrence_type='none').first()
        if not master_task:
            master_task = MasterTask(title=master_title, due_date=today, user_id=current_user.id, recurrence_type='none')
            db.session.add(master_task)
            db.session.flush() # Need the ID
            current_app.logger.info(f"Created quick task master '{master_title}'.")

        # Add each valid scratchpad item as a subtask
        added_count = 0
        for task_content in tasks_to_add:
            if isinstance(task_content, str) and task_content.strip():
                db.session.add(SubTask(master_id=master_task.id, content=task_content.strip(), grid_count=1)) # Default grid count 1
                added_count += 1

        if added_count > 0:
            db.session.commit()
            current_app.logger.info(f"Exported {added_count} scratchpad tasks.")
            return jsonify({'success': True, 'message': f'{added_count}件のタスクを追加しました。'})
        else:
            current_app.logger.info("No valid tasks to export from scratchpad.")
            return jsonify({'success': False, 'message': '追加する有効なタスクがありませんでした。'})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error exporting scratchpad: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'タスク追加中にサーバーエラーが発生しました。'}), 500


# --- Spreadsheet Export ---
def get_gspread_client():
    """Helper function to authenticate and get gspread client."""
    # Prioritize environment variable, fallback to file
    sa_info = os.environ.get('GSPREAD_SERVICE_ACCOUNT')
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        if sa_info:
            sa_creds = json.loads(sa_info)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_creds, scope)
            current_app.logger.info("GSpread authenticated using environment variable.")
        else:
            # Assumes 'service_account.json' is in the root directory
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            current_app.logger.info("GSpread authenticated using service_account.json.")
        return gspread.authorize(creds)
    except FileNotFoundError:
        current_app.logger.error("GSpread auth failed: service_account.json not found and GSPREAD_SERVICE_ACCOUNT env var not set.")
        return None
    except json.JSONDecodeError:
         current_app.logger.error("GSpread auth failed: Could not decode JSON from GSPREAD_SERVICE_ACCOUNT env var.")
         return None
    except Exception as e:
        current_app.logger.error(f"GSpread authentication failed: {e}", exc_info=True)
        return None

@main_bp.route('/export_to_sheet', methods=['POST'])
@login_required
def export_to_sheet():
    """Exports completed non-recurring tasks to the user's Google Sheet."""
    if not current_user.spreadsheet_url:
        flash("スプレッドシートURLが設定されていません。", "warning")
        return redirect(url_for('auth.settings')) # Redirect to settings in auth blueprint

    # Fetch completed, non-recurring tasks with completion dates
    completed_tasks = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == current_user.id,
        MasterTask.recurrence_type == 'none',
        SubTask.is_completed == True,
        SubTask.completion_date != None
    ).order_by(SubTask.completion_date).all()

    if not completed_tasks:
        flash("書き出す完了済みタスクがありません。", "info")
        return redirect(url_for('main.todo_list'))

    gc = get_gspread_client()
    if not gc:
        flash("スプレッドシート認証に失敗しました。設定を確認してください。", "danger")
        return redirect(url_for('auth.settings')) # Redirect to settings

    try:
        current_app.logger.info(f"Opening spreadsheet: {current_user.spreadsheet_url}")
        sh = gc.open_by_url(current_user.spreadsheet_url)
        worksheet = sh.sheet1 # Use the first sheet

        # --- Check/Write Header ---
        header = ['主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '完了日', '遅れた日数']
        try:
            existing_header = worksheet.row_values(1)
        except gspread.exceptions.APIError as api_err:
             # Handle case where sheet might be completely empty or inaccessible briefly
            if "exceeds grid limits" in str(api_err): # Heuristic for empty sheet
                existing_header = []
            else:
                 raise # Re-raise other API errors
        if not existing_header:
            worksheet.append_row(header)
            current_app.logger.info("Appended header to empty sheet.")
        elif existing_header != header:
            current_app.logger.warning("Spreadsheet header mismatch. Appending data anyway.")

        # --- Fetch Existing Data to Avoid Duplicates ---
        current_app.logger.info("Fetching existing records...")
        try:
            records = worksheet.get_all_values()
            existing_records = records[1:] if len(records) > 1 else []
            # Create a set of unique keys (Master Title, Subtask Content, Completion Date)
            existing_keys = set( (rec[1], rec[2], rec[5]) for rec in existing_records if len(rec) >= 6) # Index 1, 2, 5
            current_app.logger.info(f"Found {len(existing_keys)} existing unique keys.")
        except gspread.exceptions.APIError as api_err:
            current_app.logger.error(f"GSpread API error fetching records: {api_err}")
            flash(f"シートからのデータ取得エラー: {api_err}", "danger")
            return redirect(url_for('main.todo_list'))

        # --- Prepare Data to Append ---
        data_to_append = []
        current_app.logger.info(f"Processing {len(completed_tasks)} tasks for export...")
        for subtask in completed_tasks:
            if not subtask.completion_date: continue # Should not happen due to query filter

            completion_date_str = subtask.completion_date.strftime('%Y-%m-%d')
            due_date_str = subtask.master_task.due_date.strftime('%Y-%m-%d')
            # Create unique key for duplicate check
            key = (subtask.master_task.title, subtask.content, completion_date_str)

            if key not in existing_keys:
                day_diff = (subtask.completion_date - subtask.master_task.due_date).days
                data_to_append.append([
                    subtask.master_task.id, subtask.master_task.title, subtask.content,
                    subtask.grid_count, due_date_str,
                    completion_date_str, day_diff
                ])
                existing_keys.add(key) # Add to set to prevent duplicates within this batch

        # --- Append New Data ---
        if data_to_append:
            current_app.logger.info(f"Appending {len(data_to_append)} new rows...")
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
            flash(f"{len(data_to_append)}件の新しい完了タスクを書き出しました。", "success")
        else:
            flash("スプレッドシートに書き出す新しい完了タスクはありませんでした。", "info")

    except gspread.exceptions.SpreadsheetNotFound:
        current_app.logger.error(f"Spreadsheet not found: {current_user.spreadsheet_url}")
        flash("指定URLのシートが見つかりません。URLと共有設定を確認してください。", "danger")
        return redirect(url_for('auth.settings'))
    except gspread.exceptions.APIError as api_err:
        current_app.logger.error(f"GSpread API error during export: {api_err}")
        flash(f"スプレッドシート書き込みAPIエラー: {api_err}", "danger")
        return redirect(url_for('auth.settings'))
    except Exception as e:
        current_app.logger.error(f"Unexpected export error: {e}", exc_info=True)
        flash(f"予期せぬ書き込みエラーが発生しました。", "danger") # Generic error
        return redirect(url_for('main.todo_list'))

    return redirect(url_for('main.todo_list'))


# --- PWA Sync API ---
@main_bp.route('/api/sync', methods=['POST'])
@login_required
def sync_api():
    """API endpoint for synchronizing offline data stored in IndexedDB."""
    if not request.is_json:
        return jsonify({"success": False, "error": "Invalid JSON request"}), 400

    data = request.json
    user_id = current_user.id
    today = get_jst_today()
    current_app.logger.info(f"Sync request received for user {user_id}.")

    try:
        # --- Process New Tasks ---
        new_tasks = data.get('new_tasks', [])
        current_app.logger.info(f"Sync: Processing {len(new_tasks)} new tasks.")
        for task_data in new_tasks:
            # Basic validation
            title = task_data.get('title')
            due_date_str = task_data.get('due_date')
            subtasks = task_data.get('subtasks')
            if not title or not due_date_str or not isinstance(subtasks, list):
                current_app.logger.warning(f"Skipping incomplete new task data: {task_data}")
                continue
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                current_app.logger.warning(f"Skipping task with invalid date: {due_date_str}")
                continue

            master_task = MasterTask(
                user_id=user_id, title=title, due_date=due_date,
                is_urgent=task_data.get('is_urgent', False),
                is_habit=task_data.get('is_habit', False),
                recurrence_type=task_data.get('recurrence_type', 'none'),
                recurrence_days=task_data.get('recurrence_days')
            )
            db.session.add(master_task)
            db.session.flush() # Get ID for subtasks

            for sub_data in subtasks:
                if sub_data.get('content') and isinstance(sub_data.get('grid_count'), int) and sub_data['grid_count'] > 0:
                    db.session.add(SubTask(master_id=master_task.id, content=sub_data['content'], grid_count=sub_data['grid_count']))

        # --- Process Scratchpad Tasks ---
        scratchpad_tasks = data.get('scratchpad_tasks', []) # Should be a flat list of strings
        current_app.logger.info(f"Sync: Processing {len(scratchpad_tasks)} scratchpad tasks.")
        if scratchpad_tasks:
            master_title = f"{today.strftime('%Y-%m-%d')}のクイックタスク"
            master_task = MasterTask.query.filter_by(user_id=user_id, title=master_title, due_date=today, recurrence_type='none').first()
            if not master_task:
                master_task = MasterTask(title=master_title, due_date=today, user_id=user_id, recurrence_type='none')
                db.session.add(master_task)
                db.session.flush()
            for task_content in scratchpad_tasks:
                if isinstance(task_content, str) and task_content.strip():
                    db.session.add(SubTask(master_id=master_task.id, content=task_content.strip(), grid_count=1))

        # --- Process New Templates ---
        new_templates = data.get('new_templates', [])
        current_app.logger.info(f"Sync: Processing {len(new_templates)} new templates.")
        for template_data in new_templates:
            title = template_data.get('title')
            subtasks = template_data.get('subtasks')
            if not title or not isinstance(subtasks, list):
                current_app.logger.warning(f"Skipping incomplete template data: {template_data}")
                continue
            # Upsert logic: Update if exists, create if not
            template = TaskTemplate.query.filter_by(user_id=user_id, title=title).first()
            if template:
                SubtaskTemplate.query.filter_by(template_id=template.id).delete() # Clear old subtasks
            else:
                template = TaskTemplate(title=title, user_id=user_id)
                db.session.add(template)
                db.session.flush() # Get ID
            for sub_data in subtasks:
                if sub_data.get('content') and isinstance(sub_data.get('grid_count'), int) and sub_data['grid_count'] > 0:
                    db.session.add(SubtaskTemplate(template_id=template.id, content=sub_data['content'], grid_count=sub_data['grid_count']))

        # --- Process Completed Tasks ---
        completed_tasks = data.get('completed_tasks', []) # List of { subtaskId, isCompleted }
        current_app.logger.info(f"Sync: Processing {len(completed_tasks)} completed task updates.")
        for comp_data in completed_tasks:
            subtask_id = comp_data.get('subtaskId')
            is_completed = comp_data.get('isCompleted') # Should be boolean
            if subtask_id is None or not isinstance(is_completed, bool):
                 current_app.logger.warning(f"Skipping invalid completion data: {comp_data}")
                 continue

            subtask = db.session.get(SubTask, subtask_id)
            # Important: Check ownership before modifying
            if subtask and subtask.master_task.user_id == user_id:
                subtask.is_completed = is_completed
                # Set completion date based on server time during sync
                if is_completed:
                    subtask.completion_date = today
                # Clear completion date only for non-recurring when marked incomplete
                elif subtask.master_task.recurrence_type == 'none':
                     subtask.completion_date = None
                # (Recurring task completion dates are managed by reset logic)
            else:
                 current_app.logger.warning(f"Sync: Subtask {subtask_id} not found or permission denied for user {user_id}.")


        # --- Commit All Changes ---
        db.session.commit()
        current_app.logger.info(f"Successfully synced offline data for user {user_id}")
        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback() # Rollback everything if any part fails
        current_app.logger.error(f"Error during offline sync for user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Sync failed on server"}), 500

