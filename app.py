from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, func, TypeDecorator, String, JSON
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
import math
import pytz
from io import StringIO, BytesIO
import uuid

# --- .envファイルを読み込む ---
from dotenv import load_dotenv
load_dotenv()

# --- ログイン機能のためのインポート ---
from flask_login import LoginManager, current_user, login_user, logout_user, login_required, UserMixin
from flask_dance.contrib.google import make_google_blueprint, google

import google.oauth2.credentials
import google_auth_oauthlib.flow

# --- スプレッドシート連携用 ---
import gspread

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-for-local-dev")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

db_url = os.environ.get('DATABASE_URL', 'sqlite:///tasks.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

app.config["GOOGLE_OAUTH_CLIENT_ID"] = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")

google_bp = make_google_blueprint(
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/spreadsheets",
    ],
    offline=True,
    reprompt_consent=True
)
app.register_blueprint(google_bp, url_prefix="/login")

class DateAsString(TypeDecorator):
    impl = String
    cache_ok = True
    def process_bind_param(self, value, dialect): return value.isoformat() if value is not None else None
    def process_result_value(self, value, dialect): return date.fromisoformat(value) if value is not None else None

# --- 2. データベースモデルの定義 ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True)
    spreadsheet_name = db.Column(db.String(255), nullable=True)
    oauth_token = db.Column(JSON)
    master_tasks = db.relationship('MasterTask', backref='user', lazy=True, cascade="all, delete-orphan")
    summaries = db.relationship('DailySummary', backref='user', lazy=True, cascade="all, delete-orphan")

class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    session_id = db.Column(db.String(255), nullable=True)
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
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    session_id = db.Column(db.String(255), nullable=True)
    summary_date = db.Column(DateAsString, nullable=False)
    streak = db.Column(db.Integer, default=0)
    average_grids = db.Column(db.Float, default=0.0)

# --- 3. ログイン管理 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "index"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_jst_today():
    return datetime.now(pytz.timezone('Asia/Tokyo')).date()

# --- 4. 認証・ログイン関連のルート ---
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("todo_list"))
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session.permanent = True
    return redirect(url_for("todo_list"))

@app.route("/login/google/authorized")
def google_authorized():
    if not google.authorized:
        flash("Googleログインに失敗しました。", "message")
        return redirect(url_for("index"))

    resp = google.get("/oauth2/v2/userinfo")
    assert resp.ok, resp.text
    user_info = resp.json()
    google_id = user_info["id"]
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User(google_id=google_id, name=user_info["name"], email=user_info["email"])
        db.session.add(user)
    
    token = google.token
    if isinstance(token.get('scope'), str):
        token['scope'] = token['scope'].split(' ')
    user.oauth_token = token
    db.session.commit()

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # ★ 処理の順番を最終修正 ★
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    
    # 1. まず、セッションから大きなトークンを削除する
    session.pop('google_oauth_token', None)

    # 2. 次に、ログイン処理を実行する
    login_user(user, remember=True)

    # 3. 匿名セッションのデータを移行し、不要になったIDを削除する
    if 'session_id' in session:
        MasterTask.query.filter_by(session_id=session['session_id']).update({'user_id': user.id, 'session_id': None})
        DailySummary.query.filter_by(session_id=session['session_id']).update({'user_id': user.id, 'session_id': None})
        db.session.commit()
        session.pop('session_id', None)
    
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    
    flash(f"{user.name}さん、ようこそ！", "message")
    return redirect(url_for("todo_list"))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("ログアウトしました。", "message")
    return redirect(url_for("index"))

# --- 5. Todoアプリ本体のルート ---
@app.route('/todo')
@app.route('/todo/<date_str>')
def todo_list(date_str=None):
    if not current_user.is_authenticated and 'session_id' not in session:
         return redirect(url_for('index'))
    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('todo_list'))
    base_query = MasterTask.query
    if current_user.is_authenticated:
        base_query = base_query.filter(MasterTask.user_id == current_user.id)
    else:
        base_query = base_query.filter(MasterTask.session_id == session.get('session_id'))
    master_tasks = base_query.filter(
        MasterTask.subtasks.any(or_(SubTask.completion_date == target_date, and_(SubTask.is_completed == False, MasterTask.due_date <= target_date)))
    ).order_by(MasterTask.due_date, MasterTask.id).all()
    all_subtasks_for_day = [st for mt in master_tasks for st in mt.subtasks if st.completion_date == target_date or (not st.is_completed and mt.due_date <= target_date)]
    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)
    GRID_COLS, base_rows = 10, 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    summary_query = DailySummary.query
    if current_user.is_authenticated:
        summary_query = summary_query.filter(DailySummary.user_id == current_user.id)
    else:
        summary_query = summary_query.filter(DailySummary.session_id == session.get('session_id'))
    latest_summary = summary_query.order_by(DailySummary.summary_date.desc()).first()

    return render_template('index.html', master_tasks=master_tasks, current_date=target_date, date=date, timedelta=timedelta, total_grid_count=total_grid_count, completed_grid_count=completed_grid_count, GRID_COLS=GRID_COLS, grid_rows=grid_rows, summary=latest_summary)
    
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
def add_or_edit_task(master_id=None):
    if not current_user.is_authenticated and 'session_id' not in session: return redirect(url_for('index'))
    master_task = MasterTask.query.get_or_404(master_id) if master_id else None
    if master_task and ((current_user.is_authenticated and master_task.user_id != current_user.id) or (not current_user.is_authenticated and master_task.session_id != session.get('session_id'))):
        flash("権限がありません。", "message")
        return redirect(url_for('todo_list'))
    subtasks_for_template = [{"content": sub.content, "grid_count": sub.grid_count} for sub in (master_task.subtasks if master_task else [])]
    if request.method == 'POST':
        master_title, due_date_str = request.form.get('master_title'), request.form.get('due_date')
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()
        if master_task:
            master_task.title, master_task.due_date = master_title, due_date_obj
        else:
            master_task = MasterTask(title=master_title, due_date=due_date_obj)
            if current_user.is_authenticated: master_task.user_id = current_user.id
            else: master_task.session_id = session.get('session_id')
            db.session.add(master_task)
            db.session.flush()
        SubTask.query.filter_by(master_id=master_task.id).delete()
        for i in range(1, 11):
            sub_content, grid_count_str = request.form.get(f'sub_content_{i}'), request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=int(grid_count_str)))
        db.session.commit()
        return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))
    return render_template('edit_task.html', master_task=master_task, existing_subtasks=subtasks_for_template, date=date, get_jst_today=get_jst_today)

@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
def complete_subtask_api(subtask_id):
    if not current_user.is_authenticated and 'session_id' not in session: return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    subtask = SubTask.query.get_or_404(subtask_id)
    master_task = subtask.master_task
    if (current_user.is_authenticated and master_task.user_id != current_user.id) or (not current_user.is_authenticated and master_task.session_id != session.get('session_id')):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    subtask.is_completed = not subtask.is_completed
    subtask.completion_date = get_jst_today() if subtask.is_completed else None
    db.session.commit()
    return jsonify({'success': True, 'is_completed': subtask.is_completed})

@app.route('/import', methods=['GET', 'POST'])
def import_excel():
    if not current_user.is_authenticated and 'session_id' not in session: return redirect(url_for('index'))
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
                master_task = MasterTask(title=row[1], due_date=due_date_val)
                if current_user.is_authenticated: master_task.user_id = current_user.id
                else: master_task.session_id = session.get('session_id')
                db.session.add(master_task); db.session.flush(); task_count += 1
                for content in row[2:]:
                    if content and str(content).strip():
                        db.session.add(SubTask(master_id=master_task.id, content=str(content).strip(), grid_count=1))
            db.session.commit(); flash(f'{task_count}件のタスクをインポートしました。', "message"); return redirect(url_for('todo_list'))
        except Exception as e:
            flash(f'インポート処理中にエラーが発生しました: {e}', "message"); return redirect(url_for('import_excel'))
    return render_template('import.html')

@app.route('/archive_and_cleanup', methods=['POST'])
def archive_and_cleanup():
    if not current_user.is_authenticated and 'session_id' not in session: return redirect(url_for('index'))
    today, cleanup_threshold = get_jst_today(), get_jst_today() - timedelta(days=14)
    base_subtask_query = SubTask.query.join(MasterTask)
    base_summary_query = DailySummary.query
    if current_user.is_authenticated:
        base_subtask_query = base_subtask_query.filter(MasterTask.user_id == current_user.id)
        base_summary_query = base_summary_query.filter(DailySummary.user_id == current_user.id)
    else:
        base_subtask_query = base_subtask_query.filter(MasterTask.session_id == session.get('session_id'))
        base_summary_query = base_summary_query.filter(DailySummary.session_id == session.get('session_id'))
    old_tasks_query = base_subtask_query.filter(SubTask.is_completed == True, SubTask.completion_date < cleanup_threshold)
    deleted_count = old_tasks_query.count()
    if deleted_count > 0:
        old_tasks_query.delete(synchronize_session=False)
    all_completed_tasks = base_subtask_query.filter(SubTask.is_completed == True).order_by(SubTask.completion_date).all()
    grids_by_date_query = db.session.query(SubTask.completion_date, func.sum(SubTask.grid_count)).join(MasterTask)
    if current_user.is_authenticated:
        grids_by_date_query = grids_by_date_query.filter(MasterTask.user_id == current_user.id)
    else:
        grids_by_date_query = grids_by_date_query.filter(MasterTask.session_id == session.get('session_id'))
    grids_by_date = grids_by_date_query.filter(SubTask.is_completed == True).group_by(SubTask.completion_date).all()
    average_grids = sum(g for d, g in grids_by_date) / len(grids_by_date) if grids_by_date else 0.0
    streak = 0
    if all_completed_tasks:
        unique_dates = sorted(list(set(t.completion_date for t in all_completed_tasks)), reverse=True)
        if unique_dates and unique_dates[0] in [today, today - timedelta(days=1)]:
            streak = 1
            for i in range(len(unique_dates) - 1):
                if unique_dates[i] - timedelta(days=1) == unique_dates[i+1]: streak += 1
                else: break
    summary = base_summary_query.filter_by(summary_date=today).first()
    if not summary:
        summary = DailySummary(summary_date=today)
        if current_user.is_authenticated: summary.user_id = current_user.id
        else: summary.session_id = session.get('session_id')
        db.session.add(summary)
    summary.streak, summary.average_grids = streak, round(average_grids, 2); db.session.commit()
    appended_count = 0
    if current_user.is_authenticated:
        appended_count = update_spreadsheet(all_completed_tasks)
    message = f"履歴を更新し、{deleted_count}件の古いタスクを削除しました。"
    if current_user.is_authenticated:
        message += f" スプレッドシートに{appended_count}件の新しいタスクを追加しました。"
    else:
        message += " スプレッドシートへの書き出しはGoogleログインが必要です。"
    flash(message, "message")
    return redirect(url_for('todo_list'))

def get_gspread_client_for_user():
    if not current_user.is_authenticated or not current_user.oauth_token:
        flash("Googleアカウントに接続されていません。", "message")
        return None
    try:
        creds = google.oauth2.credentials.Credentials.from_authorized_user_info(current_user.oauth_token)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            current_user.oauth_token = json.loads(creds.to_json())
            db.session.commit()
        return gspread.authorize(creds)
    except Exception as e:
        print(f"gspread認証エラー: {e}")
        flash(f"Google Sheets認証に失敗しました: {e}", "message")
        return None

def update_spreadsheet(tasks):
    gc = get_gspread_client_for_user()
    if not gc: return 0
    if not current_user.spreadsheet_name:
        try:
            spreadsheet = gc.create(f"Todo Grid - {current_user.name}")
            current_user.spreadsheet_name = spreadsheet.title
            db.session.commit()
            spreadsheet.share(current_user.email, perm_type='user', role='writer')
        except Exception as e:
            print(f"スプレッドシートの作成に失敗: {e}")
            return 0
    try:
        sh = gc.open(current_user.spreadsheet_name)
        worksheet = sh.sheet1
        header = ['完了日', '主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '期限日との差(日)']
        if not worksheet.row_values(1):
             worksheet.append_row(header)
        existing_records = worksheet.get_all_values()[1:]
        existing_keys = set( (rec[0], rec[2], rec[3]) for rec in existing_records )
        data_to_append = []
        for subtask in tasks:
            key = (subtask.completion_date.strftime('%Y-%m-%d'), subtask.master_task.title, subtask.content)
            if key not in existing_keys:
                day_diff = (subtask.completion_date - subtask.master_task.due_date).days
                data_to_append.append([
                    subtask.completion_date.strftime('%Y-%m-%d'),
                    subtask.master_id,
                    subtask.master_task.title,
                    subtask.content,
                    subtask.grid_count,
                    subtask.master_task.due_date.strftime('%Y-%m-%d'),
                    day_diff
                ])
                existing_keys.add(key)
        if data_to_append:
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
        return len(data_to_append)
    except Exception as e:
        print(f"スプレッドシートへの書き込み中にエラー: {e}")
        return 0

# --- アプリの実行 ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(debug=True, port=5000)

