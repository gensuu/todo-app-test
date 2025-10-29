from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import TypeDecorator, String, Enum as SQLAlchemyEnum
from datetime import date, datetime
import pytz
from werkzeug.security import generate_password_hash, check_password_hash

# extensions.py から db をインポートするように変更 (循環インポート回避のため)
from .extensions import db

# --- Helper Functions ---
def get_jst_today():
    """JSTタイムゾーンでの今日の日付を取得"""
    return datetime.now(pytz.timezone('Asia/Tokyo')).date()

class DateAsString(TypeDecorator):
    """SQLite で Date 型を文字列として安全に保存するためのカスタム型"""
    impl = String
    cache_ok = True
    def process_bind_param(self, value, dialect):
        # Python の date オブジェクトを ISO 形式文字列 (YYYY-MM-DD) に変換して保存
        return value.isoformat() if value is not None else None
    def process_result_value(self, value, dialect):
        # DB から読み込んだ ISO 形式文字列を Python の date オブジェクトに変換
        return date.fromisoformat(value) if value is not None else None

# --- ▼▼▼ RecurrenceType クラス定義を追加 ▼▼▼ ---
class RecurrenceType(SQLAlchemyEnum):
    """繰り返しタイプのための Enum"""
    NONE = 'none'
    DAILY = 'daily'
    WEEKLY = 'weekly'
# --- ▲▲▲ 追加ここまで ▲▲▲ ---

# --- データベースモデル ---
class User(db.Model): # UserMixin は __init__.py で適用するためここでは不要
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    password_reset_required = db.Column(db.Boolean, default=False, nullable=False)
    spreadsheet_url = db.Column(db.String(255), nullable=True)

    # リレーションシップ定義
    master_tasks = db.relationship('MasterTask', backref='user', lazy=True, cascade="all, delete-orphan")
    summaries = db.relationship('DailySummary', backref='user', lazy=True, cascade="all, delete-orphan")
    task_templates = db.relationship('TaskTemplate', backref='user', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        """パスワードをハッシュ化して保存"""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        """提供されたパスワードがハッシュと一致するか確認"""
        return check_password_hash(self.password_hash, password)

    # Flask-Login に必要なプロパティとメソッド (UserMixin が提供するが、明示しても良い)
    @property
    def is_active(self): return True
    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)


class MasterTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    due_date = db.Column(DateAsString, default=get_jst_today, nullable=False) # デフォルト値は関数呼び出し
    is_urgent = db.Column(db.Boolean, default=False, nullable=False)
    is_habit = db.Column(db.Boolean, default=False, nullable=False) # 習慣フラグ
    # ▼▼▼ Enum 型を使用するように修正 ▼▼▼
    recurrence_type = db.Column(db.Enum(RecurrenceType, name='recurrence_type_enum'), default=RecurrenceType.NONE, nullable=False)
    # ▲▲▲ 修正ここまで ▲▲▲
    recurrence_days = db.Column(db.String(7), nullable=True) # 繰り返し曜日 (例: '01234') 月曜=0
    last_reset_date = db.Column(DateAsString, nullable=True) # 最後に完了状態がリセットされた日

    # リレーションシップ定義
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

    # リレーションシップ定義
    subtask_templates = db.relationship('SubtaskTemplate', backref='task_template', lazy=True, cascade="all, delete-orphan")

class SubtaskTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('task_template.id'), nullable=False)
    content = db.Column(db.String(100), nullable=False)
    grid_count = db.Column(db.Integer, default=1, nullable=False)

