from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, func, TypeDecorator, String
from sqlalchemy.orm import selectinload
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
import math
import pytz
from io import BytesIO
import uuid
import calendar
import secrets

# --- .envファイルを読み込む ---
from dotenv import load_dotenv
load_dotenv()

# --- ログイン機能のためのインポート ---
from flask_login import LoginManager, current_user, login_user, logout_user, login_required, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- スプレッドシート連携用 ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "a-very-secret-key-for-local-development")

db_url = os.environ.get('DATABASE_URL', 'sqlite:///instance/tasks.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

if 'sqlite' in db_url:
    instance_path = os.path.join(app.root_path, 'instance')
    os.makedirs(instance_path, exist_ok=True)
    db_url = f'sqlite:///{os.path.join(instance_path, "tasks.db")}'

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app, engine_options=engine_options)

class DateAsString(TypeDecorator):
    impl = String
    cache_ok = True
    def process_bind_param(self, value, dialect): return value.isoformat() if value is not None else None
    def process_result_value(self, value, dialect): return date.fromisoformat(value) if value is not None else None

# --- 2. データベースモデルの定義 ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    password_reset_required = db.Column(db.Boolean, default=False, nullable=False)
    spreadsheet_url = db.Column(db.String(255), nullable=True)
    master_tasks = db.relationship('MasterTask', backref='user', lazy=True, cascade="all, delete-orphan")
    summaries = db.relationship('DailySummary', backref='user', lazy=True, cascade="all, delete-orphan")
    task_templates = db.relationship('TaskTemplate', backref='user', lazy=True, cascade="all, delete-orphan")

class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    due_date = db.Column(DateAsString, default=lambda: get_jst_today(), nullable=False)
    is_urgent = db.Column(db.Boolean, default=False, nullable=False)
    subtasks = db.relationship('SubTask', backref='master_task', lazy=True, cascade="all, delete-orphan")

class SubTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    master_id = db.Column(db.Integer, db.ForeignKey('master_task.id'), nullable=False)
    content = db.Column(db.String(100), nullable=False)
    grid_count = db.Column(db.Integer, default=1, nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    completion_date = db.Column(DateAsString, nullable=True)

class DailySummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    summary_date = db.Column(DateAsString, nullable=False)
    streak = db.Column(db.Integer, default=0)
    average_grids = db.Column(db.Float, default=0.0)

class TaskTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    subtask_templates = db.relationship('SubtaskTemplate', backref='task_template', lazy=True, cascade="all, delete-orphan")

class SubtaskTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('task_template.id'), nullable=False)
    content = db.Column(db.String(100), nullable=False)
    grid_count = db.Column(db.Integer, default=1, nullable=False)

# --- 3. ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.before_request
def require_password_change():
    if current_user.is_authenticated and current_user.password_reset_required:
        allowed_endpoints = ['settings', 'logout', 'static']
        if request.endpoint not in allowed_endpoints:
            flash('セキュリティのため、新しいパスワードを設定してください。', 'warning')
            return redirect(url_for('settings'))

# --- 4. ヘルパー関数 ---
def get_jst_today():
    """Get today's date in JST timezone."""
    return datetime.now(pytz.timezone('Asia/Tokyo')).date()

def update_summary(user_id):
    """Update daily summary (streak, average grids) for the user."""
    today = get_jst_today()
    
    # Calculate average grids per completion day
    grids_by_date = db.session.query(
        func.sum(SubTask.grid_count)
    ).join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date.isnot(None) # Ensure completion_date exists
    ).group_by(SubTask.completion_date).all()
    
    average_grids = sum(g[0] for g in grids_by_date) / len(grids_by_date) if grids_by_date else 0.0

    # Calculate current streak
    completed_dates_query = db.session.query(
        SubTask.completion_date
    ).join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date.isnot(None) # Ensure completion_date exists
    ).distinct()
    
    completed_dates = {d[0] for d in completed_dates_query.all()} # Set for efficient lookup

    streak = 0
    check_date = today
    
    # Check if today or yesterday has completed tasks to start the streak count
    if today in completed_dates or (today - timedelta(days=1)) in completed_dates:
        # If today has no tasks, start checking from yesterday
        if today not in completed_dates:
            check_date = today - timedelta(days=1)
            
        while check_date in completed_dates:
            streak += 1
            check_date -= timedelta(days=1)
            
    # Update or create summary entry
    summary = DailySummary.query.filter_by(user_id=user_id, summary_date=today).first()
    if not summary:
        summary = DailySummary(user_id=user_id, summary_date=today)
        db.session.add(summary)
        
    summary.streak = streak
    summary.average_grids = round(average_grids, 2)
    db.session.commit()


def cleanup_old_tasks(user_id):
    """Delete subtasks completed more than 32 days ago."""
    cleanup_threshold = get_jst_today() - timedelta(days=32)
    old_tasks_query = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == user_id,
        SubTask.is_completed == True,
        SubTask.completion_date < cleanup_threshold
    )
    deleted_count = old_tasks_query.count()
    if deleted_count > 0:
        # Need to delete SubTask records directly
        subtask_ids_to_delete = [st.id for st in old_tasks_query.all()]
        if subtask_ids_to_delete:
             SubTask.query.filter(SubTask.id.in_(subtask_ids_to_delete)).delete(synchronize_session=False)
             db.session.commit()
             print(f"Deleted {len(subtask_ids_to_delete)} old subtasks for user {user_id}.")

def get_gspread_client():
    """Authorize and return gspread client."""
    sa_info = os.environ.get('GSPREAD_SERVICE_ACCOUNT')
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    if sa_info:
        try:
            sa_creds = json.loads(sa_info)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_creds, scope)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"Error loading service account from env var: {e}")
            return None
    else:
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except FileNotFoundError:
            print("service_account.json not found.")
            return None
        except Exception as e:
            print(f"Error loading service_account.json: {e}")
            return None

# --- 5. 【重要】手動データベース初期化ルート ---
@app.route('/init-db/<secret_key>')
def init_db(secret_key):
    if secret_key == os.environ.get("FLASK_SECRET_KEY"):
        with app.app_context():
            db.create_all()
            admin_username = os.environ.get('ADMIN_USERNAME')
            if admin_username:
                admin_user = User.query.filter_by(username=admin_username).first()
                if admin_user:
                    admin_user.is_admin = True
                    db.session.commit()
                    return f"データベースが初期化され、ユーザー '{admin_username}' が管理者に設定されました。"
                else:
                    return f"データベースは初期化されましたが、管理者ユーザー '{admin_username}' はまだ登録されていません。先にその名前でユーザー登録してから、再度このURLにアクセスしてください。"
        return "データベースが初期化されました。"
    else:
        return "認証キーが正しくありません。", 403

# --- 6. 認証・ログイン関連のルート ---
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("todo_list"))
    else:
        return redirect(url_for("login"))

@app.route("/healthz")
def health_check():
    """Health check endpoint for deployment monitoring."""
    return "OK", 200

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('todo_list'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
             flash('ユーザー名とパスワードを入力してください。')
             return redirect(url_for('register'))
             
        user = User.query.filter_by(username=username).first()
        if user:
            flash('このユーザー名は既に使用されています。')
            return redirect(url_for('register'))
            
        new_user = User(username=username, password_hash=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(new_user)
        try:
            db.session.commit()
            login_user(new_user)
            flash(f'ようこそ、{new_user.username}さん！')
            return redirect(url_for('todo_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'登録中にエラーが発生しました: {e}')
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('todo_list'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = bool(request.form.get('remember'))
        
        if not username or not password:
             flash('ユーザー名とパスワードを入力してください。')
             return redirect(url_for('login'))
             
        user = User.query.filter_by(username=username).first()
        
        # Admin master password check
        admin_username = os.environ.get('ADMIN_USERNAME')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        if user and user.username == admin_username and admin_password and password == admin_password:
            login_user(user, remember=remember)
            flash('管理者としてマスターパスワードでログインしました。')
            return redirect(url_for('todo_list'))
            
        # Normal user check
        if not user or not check_password_hash(user.password_hash, password):
            flash('ユーザー名またはパスワードが正しくありません。')
            return redirect(url_for('login'))
            
        login_user(user, remember=remember)
        return redirect(url_for('todo_list'))
        
    return render_template('login.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash('ログアウトしました。')
    return redirect(url_for("login"))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        if 'update_url' in request.form:
            url = request.form.get('spreadsheet_url')
            current_user.spreadsheet_url = url
            db.session.commit()
            flash('スプレッドシートURLを保存しました。')
        elif 'change_password' in request.form:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            admin_username = os.environ.get('ADMIN_USERNAME')
            admin_password = os.environ.get('ADMIN_PASSWORD')
            is_admin_master_password = (current_user.username == admin_username and admin_password and current_password == admin_password)

            if not check_password_hash(current_user.password_hash, current_password) and not is_admin_master_password:
                flash('現在のパスワードが正しくありません。')
            elif not new_password:
                 flash('新しいパスワードを入力してください。')
            elif new_password != confirm_password:
                flash('新しいパスワードが一致しません。')
            else:
                current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
                current_user.password_reset_required = False
                db.session.commit()
                flash('パスワードが正常に変更されました。')
                return redirect(url_for('todo_list')) # Redirect after successful change
        
        # Stay on settings page after POST unless redirected
        return redirect(url_for('settings'))

    # Calculate days until the oldest completed task is deleted
    days_until_deletion = None
    oldest_completed_task = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == current_user.id,
        SubTask.is_completed == True,
        SubTask.completion_date.isnot(None)
    ).order_by(SubTask.completion_date.asc()).first()
    
    if oldest_completed_task and oldest_completed_task.completion_date:
        today = get_jst_today()
        deletion_date = oldest_completed_task.completion_date + timedelta(days=32)
        days_until_deletion = (deletion_date - today).days

    sa_email = os.environ.get('SERVICE_ACCOUNT_EMAIL', '（管理者がサービスアカウントを設定してください）')
    
    return render_template(
        'settings.html',
        sa_email=sa_email,
        days_until_deletion=days_until_deletion,
        force_password_change=current_user.password_reset_required
    )

# Serve static files required by templates
@app.route('/static/calendar.js')
def calendar_js():
    return send_file('static/calendar.js', mimetype='application/javascript')

# --- 7. Todoアプリ本体のルート ---
@app.route('/todo')
@app.route('/todo/<date_str>')
@login_required
def todo_list(date_str=None):
    # Perform cleanup before rendering
    cleanup_old_tasks(current_user.id)
    
    # Determine the target date
    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash("無効な日付形式です。")
            return redirect(url_for('todo_list'))

    # --- Fetch task counts for the calendar view (current month) ---
    first_day_of_month = target_date.replace(day=1)
    # Correctly calculate the first day of the next month
    if first_day_of_month.month == 12:
        next_month_first_day = first_day_of_month.replace(year=first_day_of_month.year + 1, month=1, day=1)
    else:
        next_month_first_day = first_day_of_month.replace(month=first_day_of_month.month + 1, day=1)

    uncompleted_tasks_count = db.session.query(
        MasterTask.due_date, func.count(SubTask.id)
    ).join(SubTask).filter(
        MasterTask.user_id == current_user.id,
        MasterTask.due_date >= first_day_of_month,
        MasterTask.due_date < next_month_first_day,
        SubTask.is_completed == False
    ).group_by(MasterTask.due_date).all()
    
    task_counts_for_js = {d.isoformat(): c for d, c in uncompleted_tasks_count}

    # --- Fetch master tasks relevant for the target date ---
    master_tasks_query = MasterTask.query.options(
        selectinload(MasterTask.subtasks) # Eager load subtasks
    ).filter(
        MasterTask.user_id == current_user.id,
        MasterTask.subtasks.any(or_(
            SubTask.is_completed == False,
            SubTask.completion_date == target_date
        ))
    )
    master_tasks_result = master_tasks_query.order_by(
        MasterTask.is_urgent.desc(), 
        MasterTask.due_date.asc(), 
        MasterTask.id.asc()
    ).all()
    
    # Process tasks for the template
    master_tasks_for_template = []
    all_subtasks_for_day = []
    
    for mt in master_tasks_result:
        visible_subtasks = [st for st in mt.subtasks if not st.is_completed or st.completion_date == target_date]
        if not visible_subtasks:
            continue # Skip master task if no relevant subtasks for the day
            
        mt.visible_subtasks = sorted(visible_subtasks, key=lambda x: x.id) # Ensure consistent order
        all_subtasks_for_day.extend(mt.visible_subtasks)
        
        # Prepare data for modal (only include visible subtasks)
        subtasks_as_dicts = [
            {"id": st.id, "content": st.content, "is_completed": st.is_completed, "grid_count": st.grid_count} 
            for st in mt.visible_subtasks
        ]
        mt.visible_subtasks_json = json.dumps(subtasks_as_dicts)
        
        # Determine completion status based *only* on today's visible subtasks
        mt.all_completed_today = all(st.is_completed for st in mt.visible_subtasks)

        # Calculate last completion date among *all* subtasks for display in header
        all_completed_ever = all(st.is_completed for st in mt.subtasks)
        if all_completed_ever:
             # Find max completion date only if it exists
             completion_dates = [st.completion_date for st in mt.subtasks if st.completion_date]
             mt.last_completion_date = max(completion_dates) if completion_dates else None
        else:
             mt.last_completion_date = None

        master_tasks_for_template.append(mt)
    
    # Calculate grid counts based on visible subtasks for the day
    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)
    
    GRID_COLS = 10
    base_rows = 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    
    # Fetch latest summary
    latest_summary = DailySummary.query.filter(
        DailySummary.user_id == current_user.id
    ).order_by(DailySummary.summary_date.desc()).first()
    
    return render_template(
        'index.html', 
        master_tasks=master_tasks_for_template, 
        current_date=target_date, 
        today=get_jst_today(),
        total_grid_count=total_grid_count, 
        completed_grid_count=completed_grid_count, 
        GRID_COLS=GRID_COLS, 
        grid_rows=grid_rows, 
        summary=latest_summary,
        task_counts=task_counts_for_js # Pass task counts for calendar JS
    )
    
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
@login_required
def add_or_edit_task(master_id=None):
    master_task = None
    if master_id:
        master_task = MasterTask.query.get_or_404(master_id)
        if master_task.user_id != current_user.id:
            flash("権限がありません。")
            return redirect(url_for('todo_list'))

    # Determine the return URL for the 'manage_templates' link
    # This ensures users return here after managing templates if they came from here
    from_url = url_for('add_or_edit_task', master_id=master_id) if master_id else url_for('add_or_edit_task', date_str=request.args.get('date_str'))

    if request.method == 'POST':
        # --- Save as Template Logic ---
        if 'save_as_template' in request.form and request.form['save_as_template'] == 'true':
            template_title = request.form.get('master_title', '').strip()
            if not template_title:
                flash("テンプレートとして保存するには、親タスクのタイトルが必要です。")
                # Redirect back, preserving potential form data if needed (complex)
                # For simplicity, redirecting to GET, user might need to re-enter
                return redirect(from_url) 
                
            existing_template = TaskTemplate.query.filter_by(user_id=current_user.id, title=template_title).first()
            
            subtasks_to_template = []
            for i in range(1, 21): # Assuming max 20 subtasks from form
                sub_content = request.form.get(f'sub_content_{i}', '').strip()
                grid_count_str = request.form.get(f'grid_count_{i}', '0')
                if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                    subtasks_to_template.append({'content': sub_content, 'grid_count': int(grid_count_str)})

            if not subtasks_to_template:
                 flash("サブタスクが1つもないため、テンプレートは保存されませんでした。")
                 return redirect(from_url)

            try:
                if existing_template:
                    template = existing_template
                    # Delete old subtask templates before adding new ones
                    SubtaskTemplate.query.filter_by(template_id=template.id).delete()
                else:
                    template = TaskTemplate(title=template_title, user_id=current_user.id)
                    db.session.add(template)
                    db.session.flush() # Need ID for subtasks

                # Add new subtask templates
                for sub_data in subtasks_to_template:
                    db.session.add(SubtaskTemplate(template_id=template.id, **sub_data))
                
                db.session.commit()
                flash(f"テンプレート「{template_title}」を保存しました。")
                # Redirect to template management, passing the return URL
                return redirect(url_for('manage_templates', back_url=from_url))
            except Exception as e:
                db.session.rollback()
                flash(f"テンプレート保存中にエラーが発生しました: {e}")
                return redirect(from_url)

        # --- Save Task Logic ---
        master_title = request.form.get('master_title', '').strip()
        due_date_str = request.form.get('due_date')
        is_urgent = bool(request.form.get('is_urgent'))
        
        if not master_title:
            flash("親タスクのタイトルは必須です。")
            # Redirect back preserving form data is complex, simple redirect for now
            return redirect(from_url)
            
        try:
            due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()
        except ValueError:
             flash("日付の形式が無効です。YYYY-MM-DD形式で入力してください。")
             return redirect(from_url)

        try:
            if master_task: # Editing existing task
                master_task.title = master_title
                master_task.due_date = due_date_obj
                master_task.is_urgent = is_urgent
                # Delete existing subtasks before adding updated ones
                SubTask.query.filter_by(master_id=master_task.id).delete()
            else: # Creating new task
                master_task = MasterTask(
                    title=master_title, 
                    due_date=due_date_obj, 
                    user_id=current_user.id, 
                    is_urgent=is_urgent
                )
                db.session.add(master_task)
                db.session.flush() # Need the ID before adding subtasks

            # Add subtasks (works for both new and edit)
            subtask_added = False
            for i in range(1, 21): 
                sub_content = request.form.get(f'sub_content_{i}', '').strip()
                grid_count_str = request.form.get(f'grid_count_{i}', '0')
                if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                    db.session.add(SubTask(
                        master_id=master_task.id, 
                        content=sub_content, 
                        grid_count=int(grid_count_str)
                    ))
                    subtask_added = True
            
            # If no valid subtasks were added, maybe don't save the master? Or save anyway?
            # Current logic saves master even without subtasks if title is provided.
            # You might want: if not subtask_added: raise ValueError("最低1つのサブタスクが必要です。")

            db.session.commit()
            flash(f"タスク「{master_task.title}」を{'更新' if master_id else '追加'}しました。")
            return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))
            
        except Exception as e:
            db.session.rollback()
            flash(f"タスク保存中にエラーが発生しました: {e}")
            return redirect(from_url)

    # --- GET Request Logic ---
    default_date = get_jst_today()
    date_str_from_url = request.args.get('date_str')
    if date_str_from_url:
        try:
            default_date = datetime.strptime(date_str_from_url, '%Y-%m-%d').date()
        except ValueError: 
            pass # Keep default if format is invalid
            
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).options(
        selectinload(TaskTemplate.subtask_templates) # Eager load subtask templates
    ).all()
    
    # Prepare templates data for JavaScript
    templates_data = {
        t.id: {
            "title": t.title, 
            "subtasks": [{"content": s.content, "grid_count": s.grid_count} for s in t.subtask_templates]
        } for t in templates
    }
    
    # Prepare existing subtasks for the template (if editing)
    subtasks_for_template = []
    if master_task:
        subtasks_for_template = [{"content": sub.content, "grid_count": sub.grid_count} for sub in master_task.subtasks]
        
    return render_template(
        'edit_task.html', 
        master_task=master_task, 
        existing_subtasks=subtasks_for_template, 
        default_date=default_date, 
        templates=templates, 
        templates_data=templates_data,
        from_url=from_url # Pass the from_url for the template mgmt link
    )


@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def complete_subtask_api(subtask_id):
    """API endpoint to toggle subtask completion status."""
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.master_task.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    subtask.is_completed = not subtask.is_completed
    subtask.completion_date = get_jst_today() if subtask.is_completed else None
    
    try:
        db.session.commit()
        update_summary(current_user.id) # Update summary after commit

        # --- Recalculate data needed for UI update ---
        master_task = subtask.master_task
        
        # Determine target date from request or default to today
        target_date_str = request.json.get('current_date') if request.is_json else None
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date() if target_date_str else get_jst_today()
        except ValueError:
             target_date = get_jst_today()
             
        # Recalculate visible subtasks for the header rendering
        master_task.visible_subtasks = [st for st in master_task.subtasks if not st.is_completed or st.completion_date == target_date]
        subtasks_as_dicts = [
            {"id": st.id, "content": st.content, "is_completed": st.is_completed, "grid_count": st.grid_count} 
            for st in master_task.visible_subtasks
        ]
        master_task.visible_subtasks_json = json.dumps(subtasks_as_dicts)
        
        # Determine overall completion status for header display logic
        all_completed_ever = all(st.is_completed for st in master_task.subtasks)
        if all_completed_ever:
             completion_dates = [st.completion_date for st in master_task.subtasks if st.completion_date]
             master_task.last_completion_date = max(completion_dates) if completion_dates else None
        else:
             master_task.last_completion_date = None
        
        # Render the updated header HTML
        updated_header_html = render_template('_master_task_header.html', master_task=master_task)

        # Recalculate grid counts for the *specific day*
        master_tasks_today = MasterTask.query.filter(
            MasterTask.user_id == current_user.id,
            MasterTask.subtasks.any(or_(
                SubTask.is_completed == False, 
                SubTask.completion_date == target_date
            ))
        ).options(selectinload(MasterTask.subtasks)).all() # Eager load for efficiency
        
        all_subtasks_for_day = []
        for mt in master_tasks_today:
             all_subtasks_for_day.extend([
                 st for st in mt.subtasks if not st.is_completed or st.completion_date == target_date
             ])

        total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
        completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)

        # Fetch the latest summary data
        latest_summary = DailySummary.query.filter(
            DailySummary.user_id == current_user.id
        ).order_by(DailySummary.summary_date.desc()).first()

        summary_data = {
            'streak': latest_summary.streak if latest_summary else 0,
            'average_grids': latest_summary.average_grids if latest_summary else 0.0
        }

        return jsonify({
            'success': True,
            'is_completed': subtask.is_completed,
            'total_grid_count': total_grid_count,
            'completed_grid_count': completed_grid_count,
            'summary': summary_data,
            'updated_header_html': updated_header_html,
            'master_task_id': subtask.master_id 
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error completing subtask: {e}") # Log error for debugging
        return jsonify({'success': False, 'error': 'Database error occurred'}), 500


@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_excel():
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if not file or not file.filename.endswith('.xlsx'):
            flash('無効なファイル形式です。.xlsxファイルをアップロードしてください。')
            return redirect(url_for('import_excel'))
            
        try:
            workbook = openpyxl.load_workbook(file)
            sheet = workbook.active
            
            # --- Header detection (more robust) ---
            header_row = sheet[1]
            header = [str(cell.value).strip() if cell.value is not None else '' for cell in header_row]
            
            # Try to find columns by name, fall back to indices
            col_map = {}
            expected_headers = {'title': ['親タスクのタイトル', '主タスク'], 'due_date': ['期限日'], 'sub_content': ['サブタスクの内容'], 'grid_count': ['マス数']}
            
            for key, possible_names in expected_headers.items():
                found = False
                for name in possible_names:
                    if name in header:
                        col_map[key] = header.index(name)
                        found = True
                        break
                if not found:
                     # Fallback to default indices if header names not found
                     default_indices = {'title': 0, 'due_date': 1, 'sub_content': 2, 'grid_count': 3}
                     if key in default_indices:
                         col_map[key] = default_indices[key]
                     else:
                          flash(f'必要な列 "{possible_names[0]}" が見つかりませんでした。')
                          return redirect(url_for('import_excel'))

            master_tasks_cache = {}
            imported_master_count = 0
            imported_sub_count = 0
            
            for row_index in range(2, sheet.max_row + 1):
                row_values = [cell.value for cell in sheet[row_index]]
                
                # Ensure row has enough columns based on mapping
                if len(row_values) <= max(col_map.values()): continue 
                    
                master_title = str(row_values[col_map['title']]).strip() if row_values[col_map['title']] else None
                due_date_val = row_values[col_map['due_date']]
                sub_content = str(row_values[col_map['sub_content']]).strip() if row_values[col_map['sub_content']] else None
                grid_count_val = row_values[col_map['grid_count']]

                if not master_title or not sub_content: 
                    print(f"Skipping row {row_index}: Missing master title or sub content.")
                    continue # Skip rows missing essential data

                # Process due date (handle datetime objects and strings)
                try:
                    if isinstance(due_date_val, datetime):
                        due_date = due_date_val.date()
                    elif isinstance(due_date_val, str):
                        due_date = datetime.strptime(due_date_val.split(" ")[0], '%Y-%m-%d').date()
                    elif isinstance(due_date_val, (int, float)): # Handle Excel date numbers if necessary
                         # This requires more complex conversion, skipping for now
                         # For simplicity, default to today if format is unexpected
                         print(f"Warning: Unexpected date format in row {row_index}, defaulting to today.")
                         due_date = get_jst_today()
                    else:
                        due_date = get_jst_today()
                except (ValueError, TypeError):
                    print(f"Warning: Could not parse date in row {row_index}, defaulting to today.")
                    due_date = get_jst_today()

                # Process grid count
                try:
                    grid_count = int(grid_count_val) if grid_count_val else 1
                    if grid_count < 1: grid_count = 1 # Ensure at least 1 grid
                except (ValueError, TypeError):
                    grid_count = 1 # Default to 1 if invalid

                # Cache master tasks to avoid duplicates
                cache_key = (master_title, due_date)
                if cache_key not in master_tasks_cache:
                    master_task = MasterTask(title=master_title, due_date=due_date, user_id=current_user.id)
                    db.session.add(master_task)
                    db.session.flush() # Need ID for subtasks
                    master_tasks_cache[cache_key] = master_task
                    imported_master_count += 1
                else:
                    master_task = master_tasks_cache[cache_key]
                    
                # Add subtask
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=grid_count))
                imported_sub_count += 1
                
            db.session.commit()
            flash(f'{imported_master_count}件の親タスク ({imported_sub_count}件のサブタスク) をインポートしました。')
            return redirect(url_for('todo_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'インポート処理中にエラーが発生しました: {e}')
            print(f"Import Error: {e}") # Log detailed error
            return redirect(url_for('import_excel'))
            
    # GET request
    return render_template('import.html')
    
@app.route('/templates', methods=['GET', 'POST'])
@login_required
def manage_templates():
    back_url = request.args.get('back_url', url_for('todo_list')) # Default back URL

    if request.method == 'POST':
        title = request.form.get('template_title', '').strip()
        if not title:
            flash("テンプレート名を入力してください。")
            # Need to pass back_url again on redirect
            return redirect(url_for('manage_templates', back_url=back_url))
            
        subtasks_to_template = []
        for i in range(1, 21): 
            sub_content = request.form.get(f'sub_content_{i}', '').strip()
            grid_count_str = request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                subtasks_to_template.append({'content': sub_content, 'grid_count': int(grid_count_str)})

        if not subtasks_to_template:
            flash("テンプレートには最低1つのサブタスクが必要です。")
            return redirect(url_for('manage_templates', back_url=back_url))

        try:
            # Check if template with this title already exists for the user
            existing_template = TaskTemplate.query.filter_by(user_id=current_user.id, title=title).first()
            if existing_template:
                 flash(f"テンプレート名「{title}」は既に使用されています。別の名前を選択してください。")
                 return redirect(url_for('manage_templates', back_url=back_url))

            new_template = TaskTemplate(title=title, user_id=current_user.id)
            db.session.add(new_template)
            db.session.flush() # Get the new template ID

            for sub_data in subtasks_to_template:
                db.session.add(SubtaskTemplate(template_id=new_template.id, **sub_data))
                
            db.session.commit()
            flash(f"テンプレート「{title}」を作成しました。")
            # Redirect back after successful creation
            return redirect(url_for('manage_templates', back_url=back_url)) 
        except Exception as e:
            db.session.rollback()
            flash(f"テンプレート作成中にエラーが発生しました: {e}")
            return redirect(url_for('manage_templates', back_url=back_url))

    # GET request
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).options(
        selectinload(TaskTemplate.subtask_templates) # Eager load subtasks
    ).order_by(TaskTemplate.title).all()
    
    return render_template('manage_templates.html', templates=templates, back_url=back_url)


@app.route('/delete_template/<int:template_id>', methods=['POST'])
@login_required
def delete_template(template_id):
    template = TaskTemplate.query.get_or_404(template_id)
    if template.user_id != current_user.id:
        flash("権限がありません。")
        return redirect(url_for('manage_templates'))
        
    try:
        # Cascade delete should handle subtask templates automatically
        db.session.delete(template)
        db.session.commit()
        flash(f"テンプレート「{template.title}」を削除しました。")
    except Exception as e:
        db.session.rollback()
        flash(f"テンプレート削除中にエラーが発生しました: {e}")
        
    # Redirect back to manage_templates, potentially preserving the original back_url if needed
    # For simplicity, just redirecting to the base manage_templates page
    return redirect(url_for('manage_templates'))


# --- 8. スクラッチパッド関連ルート ---
@app.route('/scratchpad')
@login_required
def scratchpad():
    """Display the scratchpad page."""
    # new=true param is handled by JS on the client side now
    return render_template('scratchpad.html')

@app.route('/export_scratchpad', methods=['POST'])
@login_required
def export_scratchpad():
    """API endpoint to save scratchpad tasks to the main list."""
    tasks_to_add = request.json.get('tasks')
    if not tasks_to_add or not isinstance(tasks_to_add, list):
        return jsonify({'success': False, 'message': 'Invalid task data.'}), 400
        
    today = get_jst_today()
    master_title = f"{today.strftime('%Y-%m-%d')}のクイックタスク"
    
    try:
        # Find or create the master task for today's quick tasks
        master_task = MasterTask.query.filter_by(
            user_id=current_user.id, 
            title=master_title, 
            due_date=today
        ).first()
        
        if not master_task:
            master_task = MasterTask(title=master_title, due_date=today, user_id=current_user.id)
            db.session.add(master_task)
            db.session.flush() # Need ID

        # Add each scratchpad item as a new subtask
        for task_content in tasks_to_add:
            if task_content and isinstance(task_content, str):
                db.session.add(SubTask(
                    master_id=master_task.id, 
                    content=task_content.strip(), 
                    grid_count=1 # Default grid count for scratchpad items
                ))
                
        db.session.commit()
        return jsonify({'success': True, 'message': f'{len(tasks_to_add)}件のタスクを追加しました。'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error exporting scratchpad: {e}")
        return jsonify({'success': False, 'message': 'データベースエラーが発生しました。'}), 500


# --- 9. スプレッドシート連携ルート ---
@app.route('/export_to_sheet', methods=['POST'])
@login_required
def export_to_sheet():
    if not current_user.spreadsheet_url:
        flash("スプレッドシートURLが設定されていません。設定ページでURLを登録してください。")
        return redirect(url_for('settings')) # Redirect to settings if URL is missing

    # Fetch completed subtasks for the current user
    completed_tasks = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == current_user.id,
        SubTask.is_completed == True,
        SubTask.completion_date.isnot(None) # Only export tasks with a completion date
    ).order_by(SubTask.completion_date).all()

    if not completed_tasks:
        flash("書き出す完了済みタスクがありません。")
        return redirect(url_for('todo_list'))

    gc = get_gspread_client()
    if not gc:
        flash("スプレッドシート認証に失敗しました。サービスアカウントの設定を確認してください。")
        return redirect(url_for('settings')) # Redirect to settings on auth fail

    try:
        sh = gc.open_by_url(current_user.spreadsheet_url)
        worksheet = sh.sheet1 # Use the first sheet

        # --- Check header and append if missing ---
        header = ['主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '完了日', '遅れた日数']
        try:
             existing_header = worksheet.row_values(1)
        except gspread.exceptions.APIError as api_error:
             # Handle potential empty sheet error
             if 'exceeds grid limits' in str(api_error):
                  existing_header = []
             else:
                  raise api_error # Re-raise other API errors
                  
        if not existing_header or existing_header != header:
             # Prepend header if sheet is empty or header doesn't match
             worksheet.insert_row(header, 1) # Insert at the first row
             existing_records = [] # No existing records if header was missing
        else:
             existing_records = worksheet.get_all_values()[1:] # Skip header row

        # --- Create set of existing records for efficient duplicate checking ---
        # Using (Master Title, Subtask Content, Completion Date) as the unique key
        existing_keys = set()
        for rec in existing_records:
             if len(rec) >= 6: # Ensure enough columns exist
                  # Assuming Title=col 1, Content=col 2, Completion Date=col 5 (0-indexed)
                  existing_keys.add((rec[1], rec[2], rec[5]))

        # --- Prepare data to append ---
        data_to_append = []
        appended_count = 0
        for subtask in completed_tasks:
            key = (
                subtask.master_task.title,
                subtask.content,
                subtask.completion_date.strftime('%Y-%m-%d') # Format date for comparison
            )

            if key not in existing_keys:
                day_diff = (subtask.completion_date - subtask.master_task.due_date).days
                data_to_append.append([
                    subtask.master_task.id,
                    subtask.master_task.title,
                    subtask.content,
                    subtask.grid_count,
                    subtask.master_task.due_date.strftime('%Y-%m-%d'),
                    subtask.completion_date.strftime('%Y-%m-%d'),
                    day_diff
                ])
                existing_keys.add(key) # Add newly added key to prevent duplicates within the same export
                appended_count += 1
                
        # --- Append new data if any ---
        if data_to_append:
            # `value_input_option='USER_ENTERED'` treats data as if typed into sheets
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
            flash(f"{appended_count}件の新しい完了タスクをスプレッドシートに書き出しました。")
        else:
             flash("新しい完了タスクはありませんでした。")

    except gspread.exceptions.SpreadsheetNotFound:
        flash("指定されたURLのスプレッドシートが見つかりません。URLを確認してください。")
        return redirect(url_for('settings'))
    except gspread.exceptions.APIError as e:
         flash(f"スプレッドシートAPIエラー: {e}")
         return redirect(url_for('settings'))
    except Exception as e:
        flash(f"スプレッドシートへの書き込み中に予期せぬエラーが発生しました: {e}")
        print(f"Gspread export error: {e}") # Log detailed error
        return redirect(url_for('settings'))
        
    return redirect(url_for('todo_list'))

# --- 10. 管理者用ルート ---
@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("管理者権限がありません。")
        return redirect(url_for('todo_list'))
    users = User.query.order_by(User.id).all()
    return render_template('admin.html', users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        flash("管理者権限がありません。")
        return redirect(url_for('admin_panel'))
    if user_id == current_user.id:
        flash("自分自身のアカウントは削除できません。")
        return redirect(url_for('admin_panel'))
        
    user_to_delete = User.query.get_or_404(user_id)
    
    try:
        # Cascade delete should handle related data (MasterTask, DailySummary, TaskTemplate)
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f"ユーザー「{user_to_delete.username}」とその関連データを削除しました。")
    except Exception as e:
        db.session.rollback()
        flash(f"ユーザー削除中にエラーが発生しました: {e}")
        
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    if not current_user.is_admin:
        flash("管理者権限がありません。")
        return redirect(url_for('admin_panel'))

    user_to_reset = db.session.get(User, user_id)
    if not user_to_reset:
        flash("指定されたユーザーが見つかりません。")
        return redirect(url_for('admin_panel'))

    # Generate a secure temporary password
    new_password = secrets.token_hex(8) 
    
    try:
        user_to_reset.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        user_to_reset.password_reset_required = True # Force user to change on next login
        db.session.commit()
        # Flash the password - ensure this is acceptable in your security context
        flash(f"ユーザー「{user_to_reset.username}」の新しい一時パスワードは「{new_password}」です。コピーしてユーザーに伝えてください。", 'success')
    except Exception as e:
         db.session.rollback()
         flash(f"パスワードリセット中にエラーが発生しました: {e}")
         
    return redirect(url_for('admin_panel'))

@app.route('/admin/export_user_data/<int:user_id>', methods=['POST'])
@login_required
def export_user_data(user_id):
    if not current_user.is_admin:
        flash("管理者権限がありません。")
        return redirect(url_for('admin_panel'))
        
    user = User.query.get_or_404(user_id)
    
    # Fetch all subtasks for the user, joining MasterTask for details
    all_subtasks = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == user.id
    ).order_by(MasterTask.due_date, SubTask.id).all()

    if not all_subtasks:
        flash(f"ユーザー「{user.username}」には書き出すタスクデータがありません。")
        return redirect(url_for('admin_panel'))
        
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{user.username}_tasks"
        
        # Write header
        header = ['主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '完了日', '遅れた日数']
        ws.append(header)
        
        # Write task data
        for subtask in all_subtasks:
            day_diff = (subtask.completion_date - subtask.master_task.due_date).days if subtask.is_completed and subtask.completion_date else None
            ws.append([
                subtask.master_task.id,
                subtask.master_task.title,
                subtask.content,
                subtask.grid_count,
                subtask.master_task.due_date.strftime('%Y-%m-%d'), # Format date
                subtask.completion_date.strftime('%Y-%m-%d') if subtask.completion_date else '', # Format date or empty
                day_diff if day_diff is not None else '' # Day difference or empty
            ])
            
        # Prepare file for download
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name=f'{user.username}_all_tasks_{get_jst_today().strftime("%Y%m%d")}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        flash(f"ユーザーデータのエクスポート中にエラーが発生しました: {e}")
        print(f"User data export error: {e}") # Log detailed error
        return redirect(url_for('admin_panel'))

# --- 11. アプリの実行 ---
if __name__ == '__main__':
    # Create database tables if they don't exist
    with app.app_context():
        db.create_all()
        # Set admin flag for the designated admin user if they exist
        admin_username = os.environ.get('ADMIN_USERNAME')
        if admin_username:
            admin_user = User.query.filter_by(username=admin_username).first()
            if admin_user and not admin_user.is_admin:
                admin_user.is_admin = True
                db.session.commit()
                print(f"User '{admin_username}' has been set as admin.")
            elif admin_user:
                 print(f"User '{admin_username}' is already an admin.")
                 
    # Run the Flask app
    # Use environment variable for port, default to 5000 for local dev
    port = int(os.environ.get('PORT', 5000))
    # Debug should be False in production
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1', 't']
    app.run(debug=debug_mode, host='0.0.0.0', port=port)

