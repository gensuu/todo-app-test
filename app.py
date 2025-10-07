from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, func, TypeDecorator, String
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
import math
import pytz
from io import BytesIO
import uuid
import calendar

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
db = SQLAlchemy(app)

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
    spreadsheet_url = db.Column(db.String(255), nullable=True)
    master_tasks = db.relationship('MasterTask', backref='user', lazy=True, cascade="all, delete-orphan")
    summaries = db.relationship('DailySummary', backref='user', lazy=True, cascade="all, delete-orphan")
    task_templates = db.relationship('TaskTemplate', backref='user', lazy=True, cascade="all, delete-orphan")

class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    due_date = db.Column(DateAsString, default=lambda: get_jst_today(), nullable=False)
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
    return User.query.get(int(user_id))

def get_jst_today():
    return datetime.now(pytz.timezone('Asia/Tokyo')).date()

def update_summary(user_id):
    today = get_jst_today()
    grids_by_date = db.session.query(func.sum(SubTask.grid_count)).join(MasterTask).filter(MasterTask.user_id == user_id, SubTask.is_completed == True).group_by(SubTask.completion_date).all()
    average_grids = sum(g[0] for g in grids_by_date) / len(grids_by_date) if grids_by_date else 0.0
    completed_dates = db.session.query(SubTask.completion_date).join(MasterTask).filter(MasterTask.user_id == user_id, SubTask.is_completed == True).distinct().all()
    streak = 0
    if completed_dates:
        unique_dates_set = {d[0] for d in completed_dates if d[0] is not None}
        check_date = today
        if today in unique_dates_set or (today - timedelta(days=1)) in unique_dates_set:
            if today not in unique_dates_set:
                check_date = today - timedelta(days=1)
            while check_date in unique_dates_set:
                streak += 1
                check_date -= timedelta(days=1)
    summary = DailySummary.query.filter_by(user_id=user_id, summary_date=today).first()
    if not summary:
        summary = DailySummary(user_id=user_id, summary_date=today)
        db.session.add(summary)
    summary.streak, summary.average_grids = streak, round(average_grids, 2)
    db.session.commit()

def cleanup_old_tasks(user_id):
    cleanup_threshold = get_jst_today() - timedelta(days=32)
    old_tasks_query = SubTask.query.join(MasterTask).filter(MasterTask.user_id == user_id, SubTask.is_completed == True, SubTask.completion_date < cleanup_threshold)
    deleted_count = old_tasks_query.count()
    if deleted_count > 0:
        old_tasks_query.delete(synchronize_session=False)
        db.session.commit()
        print(f"Deleted {deleted_count} old tasks for user {user_id}.")

# --- 4. 【重要】手動データベース初期化ルート ---
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

# --- 5. 認証・ログイン関連のルート ---
@app.route("/")
def index():
    return redirect(url_for("todo_list"))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('todo_list'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user:
            flash('このユーザー名は既に使用されています。')
            return redirect(url_for('register'))
        new_user = User(username=username, password_hash=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('todo_list'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('todo_list'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        user = User.query.filter_by(username=username).first()
        admin_username = os.environ.get('ADMIN_USERNAME')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        if user and user.username == admin_username and admin_password and password == admin_password:
            login_user(user, remember=remember)
            flash('管理者としてマスターパスワードでログインしました。')
            return redirect(url_for('todo_list'))
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
            elif new_password != confirm_password:
                flash('新しいパスワードが一致しません。')
            elif not new_password:
                flash('新しいパスワードを入力してください。')
            else:
                current_user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
                db.session.commit()
                flash('パスワードが正常に変更されました。')
        return redirect(url_for('settings'))
    days_until_deletion = None
    oldest_completed_task = SubTask.query.join(MasterTask).filter(MasterTask.user_id == current_user.id, SubTask.is_completed == True).order_by(SubTask.completion_date.asc()).first()
    if oldest_completed_task and oldest_completed_task.completion_date:
        today = get_jst_today()
        deletion_date = oldest_completed_task.completion_date + timedelta(days=32)
        days_until_deletion = (deletion_date - today).days
    sa_email = os.environ.get('SERVICE_ACCOUNT_EMAIL', '（管理者が設定してください）')
    return render_template('settings.html', sa_email=sa_email, days_until_deletion=days_until_deletion)

# --- 6. Todoアプリ本体のルート ---
@app.route('/todo')
@app.route('/todo/<date_str>')
@login_required
def todo_list(date_str=None):
    cleanup_old_tasks(current_user.id)
    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('todo_list'))
    cal_date_str = request.args.get('cal')
    if cal_date_str:
        try:
            cal_view_date = datetime.strptime(cal_date_str, '%Y-%m').date()
        except ValueError:
            cal_view_date = target_date
    else:
        cal_view_date = target_date
    cal_year, cal_month = cal_view_date.year, cal_view_date.month
    first_day_of_month = date(cal_year, cal_month, 1)
    next_month_first_day = (first_day_of_month + timedelta(days=32)).replace(day=1)
    prev_month_first_day = (first_day_of_month - timedelta(days=1)).replace(day=1)
    uncompleted_tasks_in_month = MasterTask.query.filter(
        MasterTask.user_id == current_user.id,
        MasterTask.due_date >= first_day_of_month,
        MasterTask.due_date < next_month_first_day,
        MasterTask.subtasks.any(SubTask.is_completed == False)
    ).all()
    task_counts = {}
    for task in uncompleted_tasks_in_month:
        count = task_counts.get(task.due_date, 0)
        task_counts[task.due_date] = count + 1
    base_query = MasterTask.query.filter(MasterTask.user_id == current_user.id)
    master_tasks_query = base_query.filter(
        MasterTask.subtasks.any(or_(SubTask.is_completed == False, SubTask.completion_date == target_date))
    )
    master_tasks = master_tasks_query.order_by(MasterTask.due_date.asc(), MasterTask.id.asc()).all()
    
    for mt in master_tasks:
        mt.visible_subtasks = [st for st in mt.subtasks if not st.is_completed or st.completion_date == target_date]
        # ▼▼▼ モーダル用にサブタスクの詳細情報をJSON化 ▼▼▼
        mt.visible_subtasks_json = json.dumps([
            {"id": st.id, "content": st.content, "is_completed": st.is_completed, "grid_count": st.grid_count} 
            for st in mt.visible_subtasks
        ])
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
        
    master_tasks = [mt for mt in master_tasks if mt.visible_subtasks]
    all_subtasks_for_day = [st for mt in master_tasks for st in mt.visible_subtasks]
    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)
    GRID_COLS, base_rows = 10, 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    latest_summary = DailySummary.query.filter(DailySummary.user_id == current_user.id).order_by(DailySummary.summary_date.desc()).first()
    return render_template(
        'index.html', master_tasks=master_tasks, current_date=target_date, date=date, 
        timedelta=timedelta, total_grid_count=total_grid_count, completed_grid_count=completed_grid_count, 
        GRID_COLS=GRID_COLS, grid_rows=grid_rows, summary=latest_summary,
        calendar=calendar, cal_year=cal_year, cal_month=cal_month, task_counts=task_counts,
        prev_month_str=prev_month_first_day.strftime('%Y-%m'), 
        next_month_str=next_month_first_day.strftime('%Y-%m'),
        today=get_jst_today()
    )
    
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
@login_required
def add_or_edit_task(master_id=None):
    master_task = MasterTask.query.get_or_404(master_id) if master_id else None
    if master_task and master_task.user_id != current_user.id:
        flash("権限がありません。"); return redirect(url_for('todo_list'))
    if request.method == 'POST':
        master_title, due_date_str = request.form.get('master_title'), request.form.get('due_date')
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()
        if master_task:
            master_task.title, master_task.due_date = master_title, due_date_obj
        else:
            master_task = MasterTask(title=master_title, due_date=due_date_obj, user_id=current_user.id)
            db.session.add(master_task); db.session.flush()
        SubTask.query.filter_by(master_id=master_task.id).delete()
        for i in range(1, 21):
            sub_content, grid_count_str = request.form.get(f'sub_content_{i}'), request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=int(grid_count_str)))
        db.session.commit()
        return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))
    default_date = get_jst_today()
    date_str_from_url = request.args.get('date_str')
    if date_str_from_url:
        try:
            default_date = datetime.strptime(date_str_from_url, '%Y-%m-%d').date()
        except ValueError: pass
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).all()
    templates_data = {t.id: {"title": t.title, "subtasks": [{"content": s.content, "grid_count": s.grid_count} for s in t.subtask_templates]} for t in templates}
    subtasks_for_template = [{"content": sub.content, "grid_count": sub.grid_count} for sub in (master_task.subtasks if master_task else [])]
    return render_template('edit_task.html', master_task=master_task, existing_subtasks=subtasks_for_template, date=date, default_date=default_date, templates=templates, templates_data=templates_data)

@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def complete_subtask_api(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.master_task.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    subtask.is_completed = not subtask.is_completed
    subtask.completion_date = get_jst_today() if subtask.is_completed else None
    db.session.commit()
    update_summary(current_user.id)
    return jsonify({'success': True, 'is_completed': subtask.is_completed})

@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_excel():
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if not file or not file.filename.endswith('.xlsx'):
            flash('無効なファイル形式です。', "message"); return redirect(url_for('import_excel'))
        try:
            workbook, task_count = openpyxl.load_workbook(file), 0
            sheet = workbook.active
            header = [cell.value for cell in sheet[1]]
            try:
                if '主タスクID' in header:
                    col_map = {'title': header.index('主タスク'), 'due_date': header.index('期限日'), 'sub_content': header.index('サブタスク内容'), 'grid_count': header.index('マス数'),}
                else:
                    col_map = {'title': 0, 'due_date': 1, 'sub_content': 2, 'grid_count': 3}
            except (ValueError, IndexError):
                flash('Excelファイルのヘッダー形式が正しくありません。'); return redirect(url_for('import_excel'))
            master_tasks_cache = {}
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if len(row) < len(col_map): continue
                master_title, due_date_val, sub_content, grid_count_val = row[col_map['title']], row[col_map['due_date']], row[col_map['sub_content']], row[col_map['grid_count']]
                if not master_title or not sub_content: continue
                try:
                    due_date = due_date_val if isinstance(due_date_val, date) else datetime.strptime(str(due_date_val).split(" ")[0], '%Y-%m-%d').date()
                except:
                    due_date = get_jst_today()
                grid_count = int(grid_count_val) if grid_count_val and str(grid_count_val).isdigit() else 1
                cache_key = (master_title, due_date)
                if cache_key not in master_tasks_cache:
                    master_task = MasterTask(title=master_title, due_date=due_date, user_id=current_user.id)
                    db.session.add(master_task); db.session.flush(); master_tasks_cache[cache_key] = master_task; task_count += 1
                else:
                    master_task = master_tasks_cache[cache_key]
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=grid_count))
            db.session.commit()
            flash(f'{task_count}件の親タスクをインポートしました。')
            return redirect(url_for('todo_list'))
        except Exception as e:
            flash(f'インポート処理中にエラーが発生しました: {e}')
            return redirect(url_for('import_excel'))
    return render_template('import.html')
    
# --- 8. テンプレート管理ルート ---
@app.route('/templates', methods=['GET', 'POST'])
@login_required
def manage_templates():
    if request.method == 'POST':
        title = request.form.get('template_title')
        if not title:
            flash("テンプレート名を入力してください。"); return redirect(url_for('manage_templates'))
        new_template = TaskTemplate(title=title, user_id=current_user.id)
        db.session.add(new_template); db.session.flush()
        for i in range(1, 21):
            sub_content, grid_count_str = request.form.get(f'sub_content_{i}'), request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                db.session.add(SubtaskTemplate(template_id=new_template.id, content=sub_content, grid_count=int(grid_count_str)))
        db.session.commit()
        flash(f"テンプレート「{title}」を作成しました。")
        return redirect(url_for('manage_templates'))
    templates = TaskTemplate.query.filter_by(user_id=current_user.id).order_by(TaskTemplate.title).all()
    return render_template('manage_templates.html', templates=templates)

@app.route('/delete_template/<int:template_id>', methods=['POST'])
@login_required
def delete_template(template_id):
    template = TaskTemplate.query.get_or_404(template_id)
    if template.user_id != current_user.id:
        flash("権限がありません。"); return redirect(url_for('manage_templates'))
    db.session.delete(template)
    db.session.commit()
    flash(f"テンプレート「{template.title}」を削除しました。")
    return redirect(url_for('manage_templates'))

# --- 9. スプレッドシート連携とデータ整理 ---
def get_gspread_client():
    sa_info = os.environ.get('GSPREAD_SERVICE_ACCOUNT')
    if not sa_info:
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except FileNotFoundError: return None
    try:
        sa_creds = json.loads(sa_info)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_creds, scope)
        return gspread.authorize(creds)
    except Exception: return None

@app.route('/export_to_sheet', methods=['POST'])
@login_required
def export_to_sheet():
    if not current_user.spreadsheet_url:
        flash("スプレッドシートURLが設定されていません。"); return redirect(url_for('todo_list'))
    completed_tasks = SubTask.query.join(MasterTask).filter(MasterTask.user_id == current_user.id, SubTask.is_completed == True).order_by(SubTask.completion_date).all()
    if not completed_tasks:
        flash("書き出す完了済みタスクがありません。"); return redirect(url_for('todo_list'))
    gc = get_gspread_client()
    if not gc:
        flash("スプレッドシート認証に失敗しました。管理者設定を確認してください。"); return redirect(url_for('todo_list'))
    try:
        sh = gc.open_by_url(current_user.spreadsheet_url)
        worksheet = sh.sheet1
        header = ['主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '完了日', '遅れた日数']
        if not worksheet.row_values(1):
             worksheet.append_row(header)
        existing_records = worksheet.get_all_values()[1:]
        existing_keys = set( (rec[1], rec[2], rec[5]) for rec in existing_records )
        data_to_append = []
        for subtask in completed_tasks:
            if not subtask.completion_date: continue
            key = (subtask.master_task.title, subtask.content, subtask.completion_date.strftime('%Y-%m-%d'))
            if key not in existing_keys:
                day_diff = (subtask.completion_date - subtask.master_task.due_date).days
                data_to_append.append([
                    subtask.master_task.id, subtask.master_task.title, subtask.content,
                    subtask.grid_count, subtask.master_task.due_date.strftime('%Y-%m-%d'),
                    subtask.completion_date.strftime('%Y-%m-%d'), day_diff
                ])
                existing_keys.add(key)
        if data_to_append:
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
        flash(f"{len(data_to_append)}件の新しい完了タスクをスプレッドシートに書き出しました。")
    except gspread.exceptions.SpreadsheetNotFound:
        flash("指定されたURLのスプレッドシートが見つかりません。")
    except Exception as e:
        flash(f"スプレッドシートへの書き込み中にエラーが発生しました: {e}")
    return redirect(url_for('todo_list'))

# --- 10. 管理者用ルート ---
@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("管理者権限がありません。"); return redirect(url_for('todo_list'))
    users = User.query.order_by(User.id).all()
    return render_template('admin.html', users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        flash("管理者権限がありません。"); return redirect(url_for('todo_list'))
    if user_id == current_user.id:
        flash("自分自身のアカウントは削除できません。"); return redirect(url_for('admin_panel'))
    user_to_delete = User.query.get_or_404(user_id)
    db.session.delete(user_to_delete); db.session.commit()
    flash(f"ユーザー「{user_to_delete.username}」を削除しました。")
    return redirect(url_for('admin_panel'))

@app.route('/admin/export_user_data/<int:user_id>', methods=['POST'])
@login_required
def export_user_data(user_id):
    if not current_user.is_admin:
        flash("管理者権限がありません。"); return redirect(url_for('admin_panel'))
    user = User.query.get_or_404(user_id)
    all_tasks = SubTask.query.join(MasterTask).filter(MasterTask.user_id == user.id).order_by(MasterTask.due_date, SubTask.id).all()
    if not all_tasks:
        flash(f"ユーザー「{user.username}」には書き出すタスクがありません。"); return redirect(url_for('admin_panel'))
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = f"{user.username}_tasks"
    header = ['主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '完了日', '遅れた日数']
    ws.append(header)
    for subtask in all_tasks:
        day_diff = (subtask.completion_date - subtask.master_task.due_date).days if subtask.completion_date else None
        ws.append([
            subtask.master_task.id, subtask.master_task.title, subtask.content, subtask.grid_count,
            subtask.master_task.due_date.strftime('%Y-%m-%d'),
            subtask.completion_date.strftime('%Y-%m-%d') if subtask.completion_date else '',
            day_diff
        ])
    output = BytesIO(); wb.save(output); output.seek(0)
    return send_file(
        output, as_attachment=True,
        download_name=f'{user.username}_all_tasks_{get_jst_today().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# --- アプリの実行 ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        admin_username = os.environ.get('ADMIN_USERNAME')
        if admin_username:
            admin_user = User.query.filter_by(username=admin_username).first()
            if admin_user:
                admin_user.is_admin = True
                db.session.commit()
                print(f"User '{admin_username}' set as admin.")
    app.run(debug=True, port=5000)

