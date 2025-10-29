from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, current_app
)
from flask_login import current_user, login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
import os

from .extensions import db, login_manager # Relative imports
from .models import User, SubTask, MasterTask, get_jst_today # Import models needed here
from datetime import timedelta

auth_bp = Blueprint('auth', __name__)

# --- Authentication Routes ---

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Handles user registration."""
    if current_user.is_authenticated:
        return redirect(url_for('main.todo_list')) # Redirect if already logged in

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Basic validation
        if not username or not password:
            flash('ユーザー名とパスワードは必須です。', 'warning')
            return redirect(url_for('auth.register'))
        if len(password) < 4: # Example minimum length
             flash('パスワードは4文字以上で設定してください。', 'warning')
             return redirect(url_for('auth.register'))


        # Check if username already exists
        user = User.query.filter_by(username=username).first()
        if user:
            flash('このユーザー名は既に使用されています。', 'warning')
            return redirect(url_for('auth.register'))

        try:
            # Create new user
            new_user = User(username=username)
            new_user.set_password(password) # Use the model's method to hash password
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user) # Log in the new user immediately
            current_app.logger.info(f"New user registered and logged in: {username}")
            flash('登録が完了しました。', 'success')
            return redirect(url_for('main.todo_list')) # Redirect to main app page
        except Exception as e:
            db.session.rollback() # Roll back in case of error
            current_app.logger.error(f"Error during registration for {username}: {e}", exc_info=True)
            flash('登録中にエラーが発生しました。しばらくしてから再度お試しください。', 'danger')
            return redirect(url_for('auth.register'))

    # GET request: render the registration form
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if current_user.is_authenticated:
        return redirect(url_for('main.todo_list')) # Redirect if already logged in

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = bool(request.form.get('remember')) # Checkbox for 'Remember Me'

        if not username or not password:
            flash('ユーザー名とパスワードを入力してください。', 'warning')
            return redirect(url_for('auth.login'))

        user = User.query.filter_by(username=username).first()

        # --- Admin Master Password Check ---
        # Allows admin to log in with a master password defined in environment variables
        admin_username = os.environ.get('ADMIN_USERNAME')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        if user and user.username == admin_username and admin_password and password == admin_password:
            login_user(user, remember=remember)
            flash('管理者としてマスターパスワードでログインしました。', 'info')
            current_app.logger.info(f"Admin user {username} logged in with master password.")
            # Check if password reset is required even for admin master login? Decide policy.
            # if user.password_reset_required: return redirect(url_for('auth.settings', force_change='true'))
            return redirect(url_for('main.todo_list'))

        # --- Standard Password Check ---
        # Use the model's check_password method
        if not user or not user.check_password(password):
            flash('ユーザー名またはパスワードが正しくありません。', 'danger')
            current_app.logger.warning(f"Failed login attempt for username: {username}")
            return redirect(url_for('auth.login'))

        # Login successful
        login_user(user, remember=remember)
        current_app.logger.info(f"User {username} logged in successfully.")

        # Redirect to the page they were trying to access, or the main list
        next_page = request.args.get('next')
        # Basic security check for next_page to prevent open redirect
        if next_page and not next_page.startswith('/'): next_page = None
        return redirect(next_page or url_for('main.todo_list'))

    # GET request: render the login form
    return render_template('login.html')


@auth_bp.route("/logout")
@login_required # Ensure user is logged in to log out
def logout():
    """Logs the current user out."""
    current_app.logger.info(f"User {current_user.username} logging out.")
    logout_user()
    flash('ログアウトしました。', 'success')
    return redirect(url_for("auth.login")) # Redirect to login page after logout


@auth_bp.route('/settings', methods=['GET', 'POST'])
@login_required # Settings page requires login
def settings():
    """Handles user settings: spreadsheet URL and password change."""
    if request.method == 'POST':
        try:
            # --- Update Spreadsheet URL ---
            if 'update_url' in request.form:
                url = request.form.get('spreadsheet_url', '').strip()
                # Basic validation for Google Sheets URL format
                if url and url.startswith('https://docs.google.com/spreadsheets/'):
                    current_user.spreadsheet_url = url
                    db.session.commit()
                    flash('スプレッドシートURLを保存しました。', 'success')
                    current_app.logger.info(f"User {current_user.username} updated spreadsheet URL.")
                else:
                    flash('有効なGoogleスプレッドシートURLを入力してください。', 'warning')

            # --- Change Password ---
            elif 'change_password' in request.form:
                current_password = request.form.get('current_password')
                new_password = request.form.get('new_password')
                confirm_password = request.form.get('confirm_password')

                # Check admin master password override
                admin_username = os.environ.get('ADMIN_USERNAME')
                admin_password = os.environ.get('ADMIN_PASSWORD')
                is_admin_master_password = (current_user.username == admin_username and admin_password and current_password == admin_password)

                # Validation
                if not current_password or not new_password or not confirm_password:
                    flash('すべてのパスワード欄を入力してください。', 'warning')
                # Check current password unless using admin master password
                elif not current_user.check_password(current_password) and not is_admin_master_password:
                    flash('現在のパスワードが正しくありません。', 'danger')
                    current_app.logger.warning(f"User {current_user.username} failed password change (incorrect current password).")
                elif new_password != confirm_password:
                    flash('新しいパスワードが一致しません。', 'warning')
                elif len(new_password) < 4: # Enforce minimum length
                    flash('パスワードは4文字以上で設定してください。', 'warning')
                else:
                    # Update password and clear reset flag
                    current_user.set_password(new_password)
                    current_user.password_reset_required = False
                    db.session.commit()
                    flash('パスワードが正常に変更されました。', 'success')
                    current_app.logger.info(f"User {current_user.username} successfully changed password.")
                    # If password change was forced, redirect to main app page
                    if request.args.get('force_change'):
                        return redirect(url_for('main.todo_list'))
                    # Otherwise, just reload the settings page
                    return redirect(url_for('auth.settings'))

        except Exception as e:
            db.session.rollback() # Roll back on error
            current_app.logger.error(f"Error processing settings form for user {current_user.username}: {e}", exc_info=True)
            flash('設定の保存中にエラーが発生しました。', 'danger')

        # Redirect back to settings page after POST, even if there was an error
        return redirect(url_for('auth.settings'))

    # --- GET Request Logic ---
    # Calculate days until oldest completed task might be deleted
    days_until_deletion = None
    try:
        cleanup_threshold_days = 32
        # Find the oldest completed non-recurring subtask for the user
        oldest_completed_subtask = SubTask.query.join(MasterTask).filter(
            MasterTask.user_id == current_user.id,
            MasterTask.recurrence_type == 'none',
            SubTask.is_completed == True,
            SubTask.completion_date != None
        ).order_by(SubTask.completion_date.asc()).first()

        if oldest_completed_subtask:
            today = get_jst_today()
            oldest_completion_date = oldest_completed_subtask.completion_date
            deletion_date = oldest_completion_date + timedelta(days=cleanup_threshold_days)
            days_until_deletion = (deletion_date - today).days # Can be negative if past due

    except Exception as e:
        current_app.logger.error(f"Error calculating days_until_deletion for user {current_user.id}: {e}", exc_info=True)
        days_until_deletion = None # Set to None on error

    # Get service account email for display
    sa_email = os.environ.get('SERVICE_ACCOUNT_EMAIL', '（SERVICE_ACCOUNT_EMAIL 環境変数が設定されていません）')
    # Check if password change is currently required
    force_password_change = current_user.password_reset_required

    return render_template('settings.html',
                           sa_email=sa_email,
                           days_until_deletion=days_until_deletion,
                           force_password_change=force_password_change)

