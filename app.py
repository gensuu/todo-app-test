from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timedelta
import os
import openpyxl
import json
from io import StringIO, BytesIO # CSVエクスポート修正のためBytesIOを再利用

# --- Googleスプレッドシート連携用 ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)

# PostgreSQL接続（環境変数からDB URLを取得、ローカル用にSQLiteをフォールバック）
db_url = os.environ.get('DATABASE_URL', 'sqlite:///tasks.db') 

app.config['SQLALCHEMY_DATABASE_URI'] = db_url 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False 
db = SQLAlchemy(app)

# アップロードフォルダの設定 (Excelインポート用)
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 2. データベースモデルの定義（MasterTaskとSubTaskの階層構造） ---
class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True) 
    title = db.Column(db.String(100), nullable=False) # 主タスク名
    due_date = db.Column(db.Date, default=date.today, nullable=False) # 期限日

    subtasks = db.relationship('SubTask', backref='master_task', lazy=True, cascade="all, delete-orphan") 

    def __repr__(self):
        return f'<MasterTask {self.id}: {self.title}>'

class SubTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    master_id = db.Column(db.Integer, db.ForeignKey('master_task.id'), nullable=False)
    
    content = db.Column(db.String(100), nullable=False) # サブタスクの内容
    grid_count = db.Column(db.Integer, default=1, nullable=False) # 塗りつぶすマス数
    is_completed = db.Column(db.Boolean, default=False) # 完了フラグ
    
    def __repr__(self):
        return f'<SubTask {self.id}: {self.content} (Parent: {self.master_id})>'

# --- 3. Gspreadクライアント初期化関数 ---
def get_gspread_client():
    # Render環境変数からサービスアカウント情報を取得
    sa_info = os.environ.get('GSPREAD_SERVICE_ACCOUNT')
    if not sa_info:
        # ローカル実行時（認証ファイルを使用）
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            # ローカルの 'service_account.json' ファイルを参照
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            return gspread.authorize(creds)
        except FileNotFoundError:
            # 認証ファイルが見つからなかった場合の警告
            print("WARNING: GSPREAD_SERVICE_ACCOUNT not set and service_account.json not found.")
            return None
    
    # Render環境での実行時（JSON文字列から認証）
    try:
        sa_creds = json.loads(sa_info)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_creds, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error loading service account from environment: {e}")
        return None

# --- 4. タスク一覧表示と繰り越しロジック (Read) ---
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

    # MasterTaskの抽出: 期限日がターゲット日以前の全ての主タスクを取得
    master_tasks = MasterTask.query.filter(
        (MasterTask.due_date == target_date) | 
        (MasterTask.due_date < target_date)
    ).order_by(MasterTask.id).all()
    
    # --- 視覚化のためのデータ計算 ---
    
    # MasterTaskの期限日がターゲット日以前のSubTaskをすべて取得
    all_subtasks = SubTask.query.join(MasterTask).filter(
        (MasterTask.due_date <= target_date)
    ).all()

    # 1. 全タスク総マス数の計算
    total_grid_count = sum(subtask.grid_count for subtask in all_subtasks)

    # 2. 完了タスク総マス数の計算
    completed_grid_count = sum(
        subtask.grid_count for subtask in all_subtasks if subtask.is_completed
    )

    # テンプレートに渡す
    return render_template(
        'index.html', 
        master_tasks=master_tasks, 
        current_date=target_date,
        date=date,
        timedelta=timedelta,
        total_grid_count=total_grid_count, 
        completed_grid_count=completed_grid_count
    )

# --- 5. タスク追加・編集機能 (Create/Update) ---
@app.route('/edit_master/<int:master_id>', methods=['GET', 'POST'])
@app.route('/add_master', methods=['GET', 'POST'])
def add_or_edit_task(master_id=None):
    master_task = None
    existing_subtasks = []
    
    if master_id:
        master_task = MasterTask.query.get_or_404(master_id)
        existing_subtasks = SubTask.query.filter_by(master_id=master_id).all()

    if request.method == 'POST':
        master_title = request.form.get('master_title')
        if master_task:
            master_task.title = master_title
        else:
            master_task = MasterTask(title=master_title)
            db.session.add(master_task)
        db.session.commit()

        # 既存のサブタスクを削除し、新しいフォームの内容で上書き
        SubTask.query.filter_by(master_id=master_task.id).delete()
        
        for i in range(1, 11): 
            sub_content = request.form.get(f'sub_content_{i}')
            try:
                grid_count = int(request.form.get(f'grid_count_{i}', 0))
            except ValueError:
                grid_count = 0 

            if sub_content and grid_count > 0:
                new_subtask = SubTask(
                    master_id=master_task.id,
                    content=sub_content,
                    grid_count=grid_count
                )
                db.session.add(new_subtask)
            
        db.session.commit()
        return redirect(url_for('todo_list'))

    return render_template('edit_task.html', master_task=master_task, existing_subtasks=existing_subtasks)


# --- 6. サブタスク完了機能 (非同期API) ---
@app.route('/api/complete_subtask/<int:subtask_id>', methods=['POST'])
def complete_subtask_api(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    subtask.is_completed = not subtask.is_completed
    db.session.commit()
    
    # 完了フラグ更新後、方眼グリッドの総完了数を再計算
    all_subtasks = SubTask.query.join(MasterTask).all() 
    completed_grid_count = sum(subtask.grid_count for subtask in all_subtasks if subtask.is_completed)
    
    # JSONデータとしてクライアント（JavaScript）に返す
    return jsonify({
        'success': True,
        'is_completed': subtask.is_completed,
        'completed_grid_count': completed_grid_count
    })

# --- 7. Excelインポート機能 (Create: 一括登録) ---
@app.route('/import', methods=['GET', 'POST'])
def import_excel():
    if request.method == 'POST':
        if 'excel_file' not in request.files or request.files['excel_file'].filename == '':
            return 'ファイルが選択されていません', 400
        
        file = request.files['excel_file']
        
        if file and file.filename.endswith('.xlsx'):
            filename = file.filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            try:
                workbook = openpyxl.load_workbook(filepath)
                sheet = workbook.active
                default_date = date.today()
                
                for row_index, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                    master_task_title = row[1] 
                    
                    if not master_task_title:
                        continue 
                    
                    master_task = MasterTask(title=master_task_title, due_date=default_date)
                    db.session.add(master_task)
                    db.session.commit()

                    for sub_task_content in row[2:]:
                        if sub_task_content is not None and str(sub_task_content).strip() != '':
                            new_subtask = SubTask(
                                master_id=master_task.id,
                                content=str(sub_task_content).strip(),
                                grid_count=1 
                            )
                            db.session.add(new_subtask)
                            
                db.session.commit()
                os.remove(filepath)
                return redirect(url_for('todo_list'))

            except Exception as e:
                return f'インポート処理中にエラーが発生しました: {e}', 500
        
        return '無効なファイル形式です', 400

    return render_template('import.html')

# --- 8. 完了タスクのGoogleスプレッドシートへの書き出し機能 ---
@app.route('/export_to_sheet/<date_str>')
def export_to_sheet(date_str):
    try:
        export_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return '無効な日付形式です', 400

    completed_subtasks = SubTask.query.join(MasterTask).filter(
        (MasterTask.due_date <= export_date) & 
        (SubTask.is_completed == True)
    ).all()

    if not completed_subtasks:
        return "完了タスクがありません。", 200

    gc = get_gspread_client()
    if not gc:
        return "Google Sheets認証に失敗しました。環境変数またはJSONファイルを確認してください。", 500

    # ⚠️ Renderの環境変数からスプレッドシート名を取得
    SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME', 'Todo Grid 完了データ')
    try:
        sh = gc.open(SPREADSHEET_NAME) 
        worksheet = sh.sheet1 
    except gspread.SpreadsheetNotFound:
        return f"スプレッドシート '{SPREADSHEET_NAME}' が見つかりません。名前と共有設定を確認してください。", 500

    # ヘッダー行 (データが空でなければ書き込みません)
    if not worksheet.row_values(1):
        worksheet.append_row(['完了日', '主タスクID', '主タスク', 'サブタスク内容', 'マス数'])

    data_to_append = []
    for subtask in completed_subtasks:
        data_to_append.append([
            export_date.strftime('%Y-%m-%d'), 
            subtask.master_id,
            subtask.master_task.title,
            subtask.content,
            subtask.grid_count
        ])

    try:
        worksheet.append_rows(data_to_append)
        return f"完了タスク {len(data_to_append)} 件をGoogleスプレッドシート '{SPREADSHEET_NAME}' に書き出しました。", 200
    except Exception as e:
        return f"スプレッドシートへの書き込み中にエラーが発生しました: {e}", 500


# --- 9. アプリの実行 ---
# データベースの初期化はapp.run()の前に実行
with app.app_context():
    db.create_all()
    
if __name__ == '__main__':
    app.run(debug=True)
