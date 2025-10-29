from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# Initialize extensions without app object
db = SQLAlchemy()
login_manager = LoginManager()

