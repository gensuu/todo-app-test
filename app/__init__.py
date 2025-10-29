import os
import logging
from flask import Flask, redirect, url_for, flash, request
from config import app_config # Import from root config.py
from .extensions import db, login_manager
from .models import User # Import User model for context setup

def create_app(config_object=app_config):
    """Application factory function."""
    app = Flask(__name__, instance_relative_config=False,
                template_folder='templates', # Explicitly set template folder relative to app package
                static_folder='../static') # Explicitly set static folder relative to project root

    app.config.from_object(config_object)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login" # Set login view endpoint (blueprint.view_func)
    login_manager.login_message = "このページにアクセスするにはログインが必要です。" # Optional: Set message
    login_manager.login_message_category = "info" # Optional: Set message category

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.info(f"Using database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

    with app.app_context():
        # Import models here to ensure they are known to SQLAlchemy before create_all
        from . import models

        # Import Blueprints
        from .main import main_bp
        from .auth import auth_bp
        from .admin import admin_bp

        # Register Blueprints
        app.register_blueprint(main_bp)
        app.register_blueprint(auth_bp) # No url_prefix needed for auth typically
        app.register_blueprint(admin_bp, url_prefix='/admin') # Admin routes under /admin/

        # Create database tables if they don't exist
        # Note: For production, Flask-Migrate is recommended for schema changes
        try:
            db.create_all()
            app.logger.info("Database tables checked/created.")
            # Set initial admin flag if specified in environment variables
            admin_username = os.environ.get('ADMIN_USERNAME')
            if admin_username:
                admin_user = User.query.filter_by(username=admin_username).first()
                if admin_user and not admin_user.is_admin:
                    admin_user.is_admin = True
                    db.session.commit()
                    app.logger.info(f"User '{admin_username}' set as admin.")
        except Exception as e:
            app.logger.error(f"Error during initial DB setup/admin check: {e}", exc_info=True)
            # Depending on the error, you might want to handle it more gracefully

        # --- Request Hooks ---
        @app.before_request
        def require_password_change():
            """Redirects users who need to reset their password."""
            from flask_login import current_user # Import here to avoid circular dependency
            # Ensure user is authenticated before checking password_reset_required
            if current_user.is_authenticated and current_user.password_reset_required:
                # Allow access to necessary endpoints even if password change is required
                allowed_endpoints = ['auth.settings', 'auth.logout', 'static']
                # Check if the request endpoint is defined and not in the allowed list
                if request.endpoint and request.endpoint not in allowed_endpoints:
                    # Allow admins access to admin endpoints
                    is_admin_endpoint = request.endpoint.startswith('admin.')
                    if not (current_user.is_admin and is_admin_endpoint):
                        flash('セキュリティのため、新しいパスワードを設定してください。', 'warning')
                        return redirect(url_for('auth.settings', force_change='true')) # Redirect to settings

        return app

