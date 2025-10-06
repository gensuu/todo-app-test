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
    spreadsheet_url = db.Column(db.String(255), nullable=True)
    master_tasks = db.relationship('MasterTask', backref='user', lazy=True, cascade="all, delete-orphan")
    summaries = db.relationship('DailySummary', backref='user', lazy=True, cascade="all, delete-orphan")

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

# --- 3. ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_jst_today():
    return datetime.now(pytz.timezone('Asia/Tokyo')).date()

# --- 4. 【新機能】手動データベース初期化ルート ---
@app.route('/init-db/<secret_key>')
def init_db(secret_key):
    if secret_key == os.environ.get("FLASK_SECRET_KEY"):
        with app.app_context():
            db.create_all()
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
        url = request.form.get('spreadsheet_url')
        current_user.spreadsheet_url = url
        db.session.commit()
        flash('スプレッドシートURLを保存しました。')
        return redirect(url_for('settings'))
    
    # ▼▼▼ 次回削除までの日数を計算 ▼▼▼
    days_until_deletion = None
    oldest_completed_task = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == current_user.id,
        SubTask.is_completed == True
    ).order_by(SubTask.completion_date.asc()).first()

    if oldest_completed_task and oldest_completed_task.completion_date:
        today = get_jst_today()
        deletion_date = oldest_completed_task.completion_date + timedelta(days=32)
        days_until_deletion = (deletion_date - today).days
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    sa_email = os.environ.get('SERVICE_ACCOUNT_EMAIL', '（管理者が設定してください）')
    # ★ テンプレートに days_until_deletion を渡す
    return render_template('settings.html', sa_email=sa_email, days_until_deletion=days_until_deletion)

# --- 6. Todoアプリ本体のルート ---
@app.route('/todo')
@app.route('/todo/<date_str>')
@login_required
def todo_list(date_str=None):
    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('todo_list'))

    base_query = MasterTask.query.filter(MasterTask.user_id == current_user.id)
    master_tasks = base_query.filter(
        MasterTask.subtasks.any(or_(SubTask.completion_date == target_date, and_(SubTask.is_completed == False, MasterTask.due_date <= target_date)))
    ).order_by(MasterTask.due_date, MasterTask.id).all()
    
    all_subtasks_for_day = [st for mt in master_tasks for st in mt.subtasks if st.completion_date == target_date or (not st.is_completed and mt.due_date <= target_date)]
    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)
    
    GRID_COLS, base_rows = 10, 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    
    latest_summary = DailySummary.query.filter(DailySummary.user_id == current_user.id).order_by(DailySummary.summary_date.desc()).first()

    return render_template('index.html', master_tasks=master_tasks, current_date=target_date, date=date, timedelta=timedelta, total_grid_count=total_grid_count, completed_grid_count=completed_grid_count, GRID_COLS=GRID_COLS, grid_rows=grid_rows, summary=latest_summary)
    
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
@login_required
def add_or_edit_task(master_id=None):
    master_task = MasterTask.query.get_or_404(master_id) if master_id else None
    if master_task and master_task.user_id != current_user.id:
        flash("権限がありません。"); return redirect(url_for('todo_list'))

    subtasks_for_template = [{"content": sub.content, "grid_count": sub.grid_count} for sub in (master_task.subtasks if master_task else [])]
    if request.method == 'POST':
        master_title, due_date_str = request.form.get('master_title'), request.form.get('due_date')
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()
        if master_task:
            master_task.title, master_task.due_date = master_title, due_date_obj
        else:
            master_task = MasterTask(title=master_title, due_date=due_date_obj, user_id=current_user.id)
            db.session.add(master_task); db.session.flush()
        SubTask.query.filter_by(master_id=master_task.id).delete()
        for i in range(1, 11):
            sub_content, grid_count_str = request.form.get(f'sub_content_{i}'), request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=int(grid_count_str)))
        db.session.commit()
        return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))
    return render_template('edit_task.html', master_task=master_task, existing_subtasks=subtasks_for_template, date=date, get_jst_today=get_jst_today)

@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def complete_subtask_api(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.master_task.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    subtask.is_completed = not subtask.is_completed
    subtask.completion_date = get_jst_today() if subtask.is_completed else None
    db.session.commit()
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
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row[1]: continue
                due_date_val = row[0] if isinstance(row[0], (date, datetime)) else get_jst_today()
                if isinstance(due_date_val, datetime): due_date_val = due_date_val.date()
                master_task = MasterTask(title=row[1], due_date=due_date_val, user_id=current_user.id)
                db.session.add(master_task); db.session.flush(); task_count += 1
                for content in row[2:]:
                    if content and str(content).strip():
                        db.session.add(SubTask(master_id=master_task.id, content=str(content).strip(), grid_count=1))
            db.session.commit(); flash(f'{task_count}件のタスクをインポートしました。', "message"); return redirect(url_for('todo_list'))
        except Exception as e:
            flash(f'インポート処理中にエラーが発生しました: {e}', "message"); return redirect(url_for('import_excel'))
    return render_template('import.html')

# --- 7. スプレッドシート連携とデータ整理 ---
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
        flash("スプレッドシートURLが設定されていません。設定ページから登録してください。")
        return redirect(url_for('todo_list'))

    completed_tasks = SubTask.query.join(MasterTask).filter(
        MasterTask.user_id == current_user.id,
        SubTask.is_completed == True
    ).order_by(SubTask.completion_date).all()

    if not completed_tasks:
        flash("書き出す完了済みタスクがありません。")
        return redirect(url_for('todo_list'))
    
    gc = get_gspread_client()
    if not gc:
        flash("スプレッドシート認証に失敗しました。管理者設定を確認してください。")
        return redirect(url_for('todo_list'))

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
                    subtask.master_task.id,
                    subtask.master_task.title,
                    subtask.content,
                    subtask.grid_count,
                    subtask.master_task.due_date.strftime('%Y-%m-%d'),
                    subtask.completion_date.strftime('%Y-%m-%d'),
                    day_diff
                ])
                existing_keys.add(key)
        
        if data_to_append:
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
        
        flash(f"{len(data_to_append)}件の新しい完了タスクをスプレッドシートに書き出しました。")

    except gspread.exceptions.SpreadsheetNotFound:
        flash("指定されたURLのスプレッドシートが見つかりません。URLを確認するか、サービスアカウントに共有されているか確認してください。")
    except Exception as e:
        flash(f"スプレッドシートへの書き込み中にエラーが発生しました: {e}")

    return redirect(url_for('todo_list'))

# --- アプリの実行 ---
if __name__ == '__main__':
    app.run(debug=True, port=5000)

