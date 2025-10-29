import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "a-very-secret-key-for-local-development")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///instance/tasks.db')
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Ensure instance folder exists for SQLite relative to the app's root path
    instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
    os.makedirs(instance_path, exist_ok=True)
    if 'sqlite' in db_url:
        # Use absolute path for SQLite to avoid issues, ensure 'instance' folder exists
        db_url = f'sqlite:///{os.path.join(instance_path, "tasks.db")}'

    SQLALCHEMY_DATABASE_URI = db_url

class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    db_url = os.environ.get('DATABASE_URL')
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = db_url
    # Add any other production-specific settings here
    # For example, session cookie settings for security
    # SESSION_COOKIE_SECURE = True
    # SESSION_COOKIE_HTTPONLY = True
    # SESSION_COOKIE_SAMESITE = 'Lax'

# Determine which config to use based on FLASK_ENV
config_name = os.environ.get('FLASK_ENV', 'development')
if config_name == 'production':
    app_config = ProductionConfig()
else:
    app_config = DevelopmentConfig()

# Example usage: from config import app_config

