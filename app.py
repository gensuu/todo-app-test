from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, func, TypeDecorator, String
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
import math
import pytz
from io import StringIO, BytesIO

# --- Googleスプレッドシート連携用 ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)
app.secret_key = os.urandom(24)

db_url = os.environ.get('DATABASE_URL', 'sqlite:///tasks.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class DateAsString(TypeDecorator):
    impl = String
    def process_bind_param(self, value, dialect):
        return value.isoformat() if value is not None else None
    def process_result_value(self, value, dialect):
        return date.fromisoformat(value) if value is not None else None

# --- 2. データベースモデルの定義 ---
class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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
    summary_date = db.Column(DateAsString, unique=True, nullable=False)
    streak = db.Column(db.Integer, default=0)
    average_grids = db.Column(db.Float, default=0.0)

# (JST取得関数、Gspreadクライアント初期化関数は変更なし)
def get_jst_today():
    utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)
    jst_tz = pytz.timezone('Asia/Tokyo')
    return utc_now.astimezone(jst_tz).date()

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

# --- 4. タスク一覧表示 (Read) ---
# (変更なし)
@app.route('/')
@app.route('/<date_str>')
def todo_list(date_str=None):
    if date_str is None:
        target_date = get_jst_today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('todo_list'))

    master_tasks = MasterTask.query.filter(
        MasterTask.subtasks.any(
            or_(
                SubTask.completion_date == target_date,
                and_(SubTask.is_completed == False, MasterTask.due_date <= target_date)
            )
        )
    ).order_by(MasterTask.due_date, MasterTask.id).all()
    
    all_subtasks_for_day = [
        st for mt in master_tasks for st in mt.subtasks 
        if st.completion_date == target_date or (not st.is_completed and mt.due_date <= target_date)
    ]
    
    total_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day)
    completed_grid_count = sum(sub.grid_count for sub in all_subtasks_for_day if sub.is_completed)
    
    GRID_COLS, base_rows = 10, 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    
    latest_summary = DailySummary.query.order_by(DailySummary.summary_date.desc()).first()

    return render_template(
        'index.html',
        master_tasks=master_tasks,
        current_date=target_date,
        date=date,
        timedelta=timedelta,
        total_grid_count=total_grid_count,
        completed_grid_count=completed_grid_count,
        GRID_COLS=GRID_COLS,
        grid_rows=grid_rows,
        summary=latest_summary
    )

# --- 5. タスク追加・編集機能 ---
# (変更なし)
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
def add_or_edit_task(master_id=None):
    master_task = MasterTask.query.get_or_404(master_id) if master_id else None
    subtasks_for_template = [
        {"content": sub.content, "grid_count": sub.grid_count}
        for sub in (master_task.subtasks if master_task else [])
    ]

    if request.method == 'POST':
        master_title = request.form.get('master_title')
        due_date_str = request.form.get('due_date')
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else get_jst_today()

        if master_task:
            master_task.title, master_task.due_date = master_title, due_date_obj
        else:
            master_task = MasterTask(title=master_title, due_date=due_date_obj)
            db.session.add(master_task)
            db.session.flush()

        SubTask.query.filter_by(master_id=master_task.id).delete()
        for i in range(1, 11):
            sub_content = request.form.get(f'sub_content_{i}')
            grid_count_str = request.form.get(f'grid_count_{i}', '0')
            if sub_content and grid_count_str.isdigit() and int(grid_count_str) > 0:
                db.session.add(SubTask(master_id=master_task.id, content=sub_content, grid_count=int(grid_count_str)))
        
        db.session.commit()
        return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))

    return render_template('edit_task.html', master_task=master_task, existing_subtasks=subtasks_for_template, date=date, get_jst_today=get_jst_today)

# --- 6. サブタスク完了API ---
# (変更なし)
@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
def complete_subtask_api(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    subtask.is_completed = not subtask.is_completed
    subtask.completion_date = get_jst_today() if subtask.is_completed else None
    db.session.commit()
    return jsonify({'success': True, 'is_completed': subtask.is_completed})

# --- 7. Excelインポート機能 ---
# (変更なし)
@app.route('/import', methods=['GET', 'POST'])
def import_excel():
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if not file or not file.filename.endswith('.xlsx'):
            return '無効なファイル形式です', 400
        
        try:
            workbook = openpyxl.load_workbook(file)
            sheet = workbook.active
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row[1]: continue
                master_task = MasterTask(title=row[1], due_date=row[0] if isinstance(row[0], date) else get_jst_today())
                db.session.add(master_task)
                db.session.flush()
                for content in row[2:]:
                    if content and str(content).strip():
                        db.session.add(SubTask(master_id=master_task.id, content=str(content).strip()))
            db.session.commit()
            return redirect(url_for('todo_list'))
        except Exception as e:
            return f'インポート処理中にエラーが発生しました: {e}', 500
    return render_template('import.html')

# --- 8. 【新機能】履歴の集計と古いデータの整理 ---
@app.route('/archive_and_cleanup', methods=['POST'])
def archive_and_cleanup():
    today = get_jst_today()
    cleanup_threshold = today - timedelta(days=14)
    
    old_tasks_query = SubTask.query.filter(SubTask.is_completed == True, SubTask.completion_date < cleanup_threshold)
    deleted_count = old_tasks_query.count()
    if deleted_count > 0:
        old_tasks_query.delete(synchronize_session=False)
        db.session.commit()

    all_completed_tasks = SubTask.query.filter(SubTask.is_completed == True).order_by(SubTask.completion_date).all()
    
    grids_by_date = db.session.query(SubTask.completion_date, func.sum(SubTask.grid_count)).filter(SubTask.is_completed == True).group_by(SubTask.completion_date).all()
    average_grids = sum(g for d, g in grids_by_date) / len(grids_by_date) if grids_by_date else 0.0

    streak = 0
    if all_completed_tasks:
        unique_dates = sorted(list(set(t.completion_date for t in all_completed_tasks)), reverse=True)
        if unique_dates and unique_dates[0] in [today, today - timedelta(days=1)]: #昨日でもストリークが途切れないように
            if unique_dates[0] == today: streak = 1
            else: streak = 0 # 今日タスクを終えてなければ0だが、昨日が連続していれば...

            # ストリーク計算ロジックを修正
            current_streak_date = unique_dates[0]
            if current_streak_date == today:
                for i in range(len(unique_dates) - 1):
                    if unique_dates[i] - timedelta(days=1) == unique_dates[i+1]:
                        streak += 1
                    else:
                        break
    
    summary = DailySummary.query.filter_by(summary_date=today).first()
    if not summary:
        summary = DailySummary(summary_date=today)
        db.session.add(summary)
    summary.streak = streak
    summary.average_grids = round(average_grids, 2)
    db.session.commit()

    # ▼▼▼ スプレッドシートを更新し、追記件数を取得 ▼▼▼
    appended_count = update_spreadsheet(all_completed_tasks)
    
    flash(f"履歴を更新し、{deleted_count}件の古いタスクを削除しました。スプレッドシートに{appended_count}件の新しいタスクを追加しました。")
    return redirect(url_for('todo_list'))


# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
# ★ スプレッドシート更新関数を修正 ★
# ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
def update_spreadsheet(tasks):
    gc = get_gspread_client()
    if not gc:
        print("Google Sheets認証に失敗しました。")
        return 0 # 追記件数0を返す

    SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Todo Grid 完了データ')
    try:
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.sheet1
        
        # ヘッダー行が存在しない場合のみ書き込む
        header = ['完了日', '主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '期限日との差(日)']
        if not worksheet.row_values(1):
             worksheet.append_row(header)

        # 既存のレコードを読み込んで重複をチェック
        existing_records = worksheet.get_all_values()[1:]
        # (完了日, 主タスク名, サブタスク内容) のタプルでユニークキーを作成
        existing_keys = set( (rec[0], rec[2], rec[3]) for rec in existing_records )
        
        data_to_append = []
        for subtask in tasks:
            key = (
                subtask.completion_date.strftime('%Y-%m-%d'),
                subtask.master_task.title,
                subtask.content
            )
            # 重複していないタスクのみを追加リストへ
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
                existing_keys.add(key) # 同じ実行内で重複しないようにキーを追加
        
        # 追記するデータがあれば、まとめて書き込む
        if data_to_append:
            worksheet.append_rows(data_to_append, value_input_option='USER_ENTERED')
        
        return len(data_to_append) # 追記した件数を返す
            
    except Exception as e:
        print(f"スプレッドシートへの書き込み中にエラー: {e}")
        return 0 # エラー時も0を返す

# --- 9. アプリの実行 ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)

