from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify # jsonifyを追加
from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timedelta
import os
import csv
from io import StringIO
import openpyxl

# --- 1. アプリの初期化と設定 ---
app = Flask(__name__)

# SQLiteデータベースの設定
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
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
    title = db.Column(db.String(100), nullable=False)
    due_date = db.Column(db.Date, default=date.today, nullable=False)

    subtasks = db.relationship('SubTask', backref='master_task', lazy=True, cascade="all, delete-orphan") 

    def __repr__(self):
        return f'<MasterTask {self.id}: {self.title}>'

class SubTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    master_id = db.Column(db.Integer, db.ForeignKey('master_task.id'), nullable=False)
    
    content = db.Column(db.String(100), nullable=False)
    grid_count = db.Column(db.Integer, default=1, nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<SubTask {self.id}: {self.content} (Parent: {self.master_id})>'

# --- 3. データベースの初期化（初回のみ実行） ---
with app.app_context():
    db.create_all()

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
    # ... (ロジックは前回コードと同じため省略) ...
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


# --- 6. サブタスク完了機能 (非同期API化 - Stage 4で変更) ---
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
    # ... (ロジックは前回コードと同じため省略) ...
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

# --- 8. 完了タスクのCSVエクスポート機能 ---
@app.route('/export_completed_tasks/<date_str>')
def export_completed_tasks(date_str):
    try:
        export_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return '無効な日付形式です', 400

    completed_subtasks = SubTask.query.join(MasterTask).filter(
        (MasterTask.due_date <= export_date) & 
        (SubTask.is_completed == True)
    ).all()

    # CSVデータをまず文字列としてメモリ上で作成 (StringIO)
    csv_buffer = StringIO()
    # Windows/Excelで文字化けしないよう、UTF-8 with BOM (utf-8-sig) に対応させるため、
    # 一旦StringIOに書き込みます。
    writer = csv.writer(csv_buffer)
    
    # ヘッダー行
    writer.writerow(['完了日', '主タスク', 'サブタスク内容', 'マス数'])
    
    # データ行
    for subtask in completed_subtasks:
        writer.writerow([
            export_date.strftime('%Y-%m-%d'), 
            subtask.master_task.title,
            subtask.content,
            subtask.grid_count
        ])

    # 2. StringIOの内容をバイナリデータ（BytesIO）に変換
    from io import BytesIO # app.pyの冒頭でインポート済みの前提
    binary_output = BytesIO()
    
    # CSV文字列をUTF-8 BOM (utf-8-sig) でエンコードしてBytesIOに書き込む
    csv_data = csv_buffer.getvalue()
    binary_output.write(csv_data.encode('utf-8-sig'))
    binary_output.seek(0)
    
    # ファイルとしてユーザーに送信 (send_fileはBytesIOを受け付けます)
    return send_file(
        binary_output,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'completed_tasks_{export_date.strftime("%Y%m%d")}.csv'
    )
# --- 9. アプリの実行 ---
if __name__ == '__main__':
    # デバッグモードはローカル環境でのみ有効
    app.run(debug=True)