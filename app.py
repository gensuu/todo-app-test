from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_, TypeDecorator, String, Date # ★ TypeDecoratorとString, Dateを追加
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
import math
from io import StringIO, BytesIO

# --- Googleスプレッドシート連携用 ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)

# PostgreSQL接続（環境変数からDB URLを取得、ローカル用にSQLiteをフォールバック）
db_url = os.environ.get('DATABASE_URL', 'sqlite:///tasks.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★【重要】SQLiteとの互換性のためのカスタムDate型を定義 ★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
class DateAsString(TypeDecorator):
    """日付を'YYYY-MM-DD'形式の文字列としてDBに保存するカスタム型"""
    impl = String

    def process_bind_param(self, value, dialect):
        # Pythonのdateオブジェクトを文字列に変換してDBに保存
        if value is not None:
            return value.isoformat()
        return None

    def process_result_value(self, value, dialect):
        # DBの文字列をPythonのdateオブジェクトに変換してアプリで使う
        if value is not None:
            return date.fromisoformat(value)
        return None

# アップロードフォルダの設定 (Excelインポート用)
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. データベースモデルの定義 ---
class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    due_date = db.Column(DateAsString, default=date.today, nullable=False) # ★ db.Dateから変更
    subtasks = db.relationship('SubTask', backref='master_task', lazy=True, cascade="all, delete-orphan")

class SubTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    master_id = db.Column(db.Integer, db.ForeignKey('master_task.id'), nullable=False)
    content = db.Column(db.String(100), nullable=False)
    grid_count = db.Column(db.Integer, default=1, nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    completion_date = db.Column(DateAsString, nullable=True) # ★ db.Dateから変更

# --- 3. Gspreadクライアント初期化関数 ---
# (変更なし)
def get_gspread_client():
    sa_info = os.environ.get('GSPREAD_SERVICE_ACCOUNT')
    if not sa_info:
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except FileNotFoundError:
            print("WARNING: GSPREAD_SERVICE_ACCOUNT not set and service_account.json not found.")
            return None
    try:
        sa_creds = json.loads(sa_info)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_creds, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error loading service account from environment: {e}")
        return None

# --- 4. タスク一覧表示 (Read) ---
# (変更なし)
@app.route('/')
@app.route('/<date_str>')
def todo_list(date_str=None):
    if date_str is None:
        target_date = date.today()
    else:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('todo_list'))

    master_tasks = MasterTask.query.filter(
        MasterTask.subtasks.any(
            or_(
                SubTask.completion_date == target_date,
                and_(
                    SubTask.is_completed == False,
                    MasterTask.due_date <= target_date
                )
            )
        )
    ).order_by(MasterTask.due_date, MasterTask.id).all()
    
    all_subtasks_for_day = []
    for mt in master_tasks:
        visible_subtasks = [
            st for st in mt.subtasks if 
            st.completion_date == target_date or 
            (not st.is_completed and mt.due_date <= target_date)
        ]
        all_subtasks_for_day.extend(visible_subtasks)
    
    total_grid_count = sum(subtask.grid_count for subtask in all_subtasks_for_day)
    completed_grid_count = sum(
        subtask.grid_count for subtask in all_subtasks_for_day if subtask.is_completed
    )
    
    GRID_COLS = 10
    base_rows = 2
    required_rows = math.ceil(total_grid_count / GRID_COLS) if total_grid_count > 0 else 1
    grid_rows = max(base_rows, required_rows)
    
    return render_template(
        'index.html',
        master_tasks=master_tasks,
        current_date=target_date,
        date=date,
        timedelta=timedelta,
        total_grid_count=total_grid_count,
        completed_grid_count=completed_grid_count,
        GRID_COLS=GRID_COLS,
        grid_rows=grid_rows
    )

# --- 5. タスク追加・編集機能 (Create/Update) ---
# (変更なし)
@app.route('/add_or_edit_task', methods=['GET', 'POST'])
@app.route('/add_or_edit_task/<int:master_id>', methods=['GET', 'POST'])
def add_or_edit_task(master_id=None):
    master_task = None
    subtasks_for_template = [] 

    if master_id:
        master_task = MasterTask.query.get_or_404(master_id)
        existing_subtasks_objects = SubTask.query.filter_by(master_id=master_id).order_by(SubTask.id).all()
        subtasks_for_template = [
            {"content": sub.content, "grid_count": sub.grid_count}
            for sub in existing_subtasks_objects
        ]

    if request.method == 'POST':
        master_title = request.form.get('master_title')
        due_date_str = request.form.get('due_date')
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else date.today()

        if master_task:
            master_task.title = master_title
            master_task.due_date = due_date_obj
        else:
            master_task = MasterTask(title=master_title, due_date=due_date_obj)
            db.session.add(master_task)
            db.session.flush()

        SubTask.query.filter_by(master_id=master_task.id).delete()
        
        for i in range(1, 11):
            sub_content = request.form.get(f'sub_content_{i}')
            try:
                grid_count = int(request.form.get(f'grid_count_{i}', 0))
            except (ValueError, TypeError):
                grid_count = 0

            if sub_content and grid_count > 0:
                new_subtask = SubTask(
                    master_id=master_task.id,
                    content=sub_content,
                    grid_count=grid_count
                )
                db.session.add(new_subtask)
        
        db.session.commit()
        return redirect(url_for('todo_list', date_str=master_task.due_date.strftime('%Y-%m-%d')))

    return render_template('edit_task.html', master_task=master_task, existing_subtasks=subtasks_for_template, date=date)

# --- 6. サブタスク完了API ---
# (変更なし)
@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
def complete_subtask_api(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    subtask.is_completed = not subtask.is_completed
    
    if subtask.is_completed:
        subtask.completion_date = date.today()
    else:
        subtask.completion_date = None
        
    db.session.commit()
    
    return jsonify({
        'success': True,
        'is_completed': subtask.is_completed,
    })

# --- 7. Excelインポート機能 ---
# (変更なし)
@app.route('/import', methods=['GET', 'POST'])
def import_excel():
    if request.method == 'POST':
        if 'excel_file' not in request.files or request.files['excel_file'].filename == '':
            return 'ファイルが選択されていません', 400
        
        file = request.files['excel_file']
        
        if file and file.filename.endswith('.xlsx'):
            try:
                workbook = openpyxl.load_workbook(file)
                sheet = workbook.active
                
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    if not row[1]: continue
                    
                    master_task = MasterTask(title=row[1], due_date=row[0] if isinstance(row[0], date) else date.today())
                    db.session.add(master_task)
                    db.session.flush()

                    for sub_task_content in row[2:]:
                        if sub_task_content and str(sub_task_content).strip():
                            new_subtask = SubTask(
                                master_id=master_task.id,
                                content=str(sub_task_content).strip(),
                                grid_count=1
                            )
                            db.session.add(new_subtask)
                
                db.session.commit()
                return redirect(url_for('todo_list'))

            except Exception as e:
                return f'インポート処理中にエラーが発生しました: {e}', 500
        
        return '無効なファイル形式です', 400

    return render_template('import.html')

# --- 8. スプレッドシート書き出し機能 ---
# (変更なし)
@app.route('/export_to_sheet/<date_str>')
def export_to_sheet(date_str):
    try:
        export_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return '無効な日付形式です', 400

    completed_subtasks = SubTask.query.join(MasterTask).filter(
        SubTask.completion_date == export_date,
        SubTask.is_completed == True
    ).all()

    if not completed_subtasks:
        return "完了タスクがありません。", 200

    gc = get_gspread_client()
    if not gc:
        return "Google Sheets認証に失敗しました。", 500

    SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Todo Grid 完了データ')
    try:
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.sheet1
    except gspread.SpreadsheetNotFound:
        return f"スプレッドシート '{SPREADSHEET_NAME}' が見つかりません。", 500

    if not worksheet.row_values(1):
        worksheet.append_row(['完了日', '主タスクID', '主タスク', 'サブタスク内容', 'マス数', '期限日', '期限日との差(日)'])

    data_to_append = []
    for subtask in completed_subtasks:
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

    if data_to_append:
        worksheet.append_rows(data_to_append)
    
    return f"完了タスク {len(data_to_append)} 件をGoogleスプレッドシート '{SPREADSHEET_NAME}' に書き出しました。", 200

# --- 9. アプリの実行 ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)

