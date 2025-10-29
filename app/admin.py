from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_file
from flask_login import current_user, login_required
from functools import wraps
import secrets
import openpyxl
from io import BytesIO
from sqlalchemy.orm import selectinload

from .extensions import db # Relative import
from .models import User, SubTask, MasterTask, get_jst_today # Relative import

admin_bp = Blueprint('admin', __name__)

# --- Decorator for Admin Access ---
def admin_required(f):
    """Decorator to ensure the logged-in user is an admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("管理者権限が必要です。", "danger")
            return redirect(url_for('main.todo_list')) # Redirect non-admins
        return f(*args, **kwargs)
    return decorated_function

# --- Admin Routes (all require @admin_required) ---

@admin_bp.route('/') # Base route for admin is /admin/
@login_required
@admin_required
def admin_panel():
    """Displays the main admin panel with a list of users."""
    try:
        # Fetch all users ordered by ID
        users = User.query.order_by(User.id).all()
        return render_template('admin.html', users=users)
    except Exception as e:
        current_app.logger.error(f"Error loading admin panel: {e}", exc_info=True)
        flash("ユーザーリストの読み込み中にエラーが発生しました。", "danger")
        return redirect(url_for('main.todo_list')) # Redirect to main app on error

@admin_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Deletes a user and their associated data."""
    # Prevent admin from deleting themselves
    if user_id == current_user.id:
        flash("自分自身のアカウントは削除できません。", "warning")
        return redirect(url_for('admin.admin_panel'))

    user_to_delete = db.session.get(User, user_id)
    if not user_to_delete:
        flash("指定されたユーザーが見つかりません。", "warning")
        return redirect(url_for('admin.admin_panel'))

    try:
        username = user_to_delete.username
        # Delete the user; cascade rule in User model handles related data
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f"ユーザー「{username}」とその関連データを削除しました。", "success")
        current_app.logger.info(f"Admin {current_user.username} deleted user {username} (ID: {user_id}).")
    except Exception as e:
        db.session.rollback() # Roll back on error
        current_app.logger.error(f"Error deleting user {user_id} by admin {current_user.username}: {e}", exc_info=True)
        flash(f"ユーザーの削除中にエラーが発生しました: {e}", "danger")

    return redirect(url_for('admin.admin_panel'))


@admin_bp.route('/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_password(user_id):
    """Resets a non-admin user's password and forces them to change it."""
    user_to_reset = db.session.get(User, user_id)
    if not user_to_reset:
        flash("指定されたユーザーが見つかりません。", "warning")
        return redirect(url_for('admin.admin_panel'))
    # Prevent resetting admin passwords this way
    if user_to_reset.is_admin:
        flash("管理者ユーザーのパスワードはこの方法ではリセットできません。", "warning")
        return redirect(url_for('admin.admin_panel'))

    try:
        # Generate a secure random temporary password
        new_password = secrets.token_hex(8) # 16 characters long
        user_to_reset.set_password(new_password) # Use the model method to hash
        user_to_reset.password_reset_required = True # Force change on next login
        db.session.commit()

        # Flash the temporary password *only* to the admin performing the action
        flash(f"ユーザー「{user_to_reset.username}」の新一時パスワード：「{new_password}」。コピーしてユーザーに伝えてください。次回ログイン時に変更要求。", 'success')
        current_app.logger.info(f"Admin {current_user.username} reset password for user {user_to_reset.username} (ID: {user_id}).")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting password for user {user_id} by admin {current_user.username}: {e}", exc_info=True)
        flash(f"パスワードのリセット中にエラーが発生しました: {e}", "danger")

    return redirect(url_for('admin.admin_panel'))


@admin_bp.route('/export_user_data/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def export_user_data(user_id):
    """Exports all task data for a specific user to an Excel file."""
    user = db.session.get(User, user_id)
    if not user:
        flash("指定されたユーザーが見つかりません。", "warning")
        return redirect(url_for('admin.admin_panel'))

    try:
        # Fetch all subtasks, eagerly loading the related master task
        all_subtasks = SubTask.query.join(MasterTask).filter(
            MasterTask.user_id == user.id
        ).options(
            selectinload(SubTask.master_task)
        ).order_by(
            MasterTask.due_date, MasterTask.id, SubTask.id # Logical sorting
        ).all()

        if not all_subtasks:
            flash(f"ユーザー「{user.username}」には書き出すタスクデータがありません。", "info")
            return redirect(url_for('admin.admin_panel'))

        current_app.logger.info(f"Starting data export for user {user.username} (ID: {user_id}). Found {len(all_subtasks)} subtasks.")

        # Create Excel workbook in memory
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{user.username}_tasks"

        # Define and write header row
        header = [
            '親タスクID', '親タスクタイトル', '期限日/開始日', '緊急', '習慣',
            '繰り返し種別', '繰り返し曜日',
            'サブタスクID', 'サブタスク内容', 'マス数', '完了状態', '完了日', '遅れた日数'
        ]
        ws.append(header)

        # Write data rows
        for subtask in all_subtasks:
            master = subtask.master_task
            completion_date_str = subtask.completion_date.strftime('%Y-%m-%d') if subtask.completion_date else ''
            # Calculate delay only for completed non-recurring tasks
            day_diff = ''
            if subtask.is_completed and subtask.completion_date and master.recurrence_type == 'none':
                day_diff = (subtask.completion_date - master.due_date).days

            ws.append([
                master.id, master.title, master.due_date.strftime('%Y-%m-%d'),
                'Yes' if master.is_urgent else 'No',
                'Yes' if master.is_habit else 'No',
                master.recurrence_type,
                master.recurrence_days or '', # Handle None for recurrence_days
                subtask.id, subtask.content, subtask.grid_count,
                '完了' if subtask.is_completed else '未完了',
                completion_date_str,
                day_diff # Calculated delay
            ])

        # Save workbook to a BytesIO buffer
        output = BytesIO()
        wb.save(output)
        output.seek(0) # Rewind the buffer

        # Prepare filename and send the file
        filename = f'{user.username}_all_tasks_{get_jst_today().strftime("%Y%m%d")}.xlsx'
        current_app.logger.info(f"Successfully generated export file: {filename}")
        return send_file(
            output,
            as_attachment=True, # Trigger download
            download_name=filename, # Set filename for download
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' # Correct MIME type
        )

    except Exception as e:
        current_app.logger.error(f"Error exporting data for user {user_id} by admin {current_user.username}: {e}", exc_info=True)
        flash(f"ユーザーデータの書き出し中にエラーが発生しました: {e}", "danger")
        return redirect(url_for('admin.admin_panel'))

