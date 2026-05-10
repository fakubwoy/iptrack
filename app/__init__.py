import os
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()
migrate = Migrate()
scheduler = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    # ── Config ──────────────────────────────────────────────────────────────
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    database_url = os.environ.get("DATABASE_URL", "sqlite:///iptrack.db")
    # Railway sometimes returns postgres:// which SQLAlchemy 1.4+ rejects
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 5)),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 2)),
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["CHECK_INTERVAL_HOURS"] = int(os.environ.get("CHECK_INTERVAL_HOURS", 12))
    app.config["ANTICAPTCHA_KEY"] = os.environ.get("ANTICAPTCHA_KEY", "")
    app.config["TWOCAPTCHA_KEY"] = os.environ.get("TWOCAPTCHA_KEY", "")

    # ── Extensions ───────────────────────────────────────────────────────────
    db.init_app(app)
    bcrypt.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    # ── Blueprints ───────────────────────────────────────────────────────────
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    # ── Scheduler ────────────────────────────────────────────────────────────
    global scheduler
    if scheduler is None or not scheduler.running:
        scheduler = BackgroundScheduler(timezone=pytz.utc)
        from app.tasks import schedule_status_checks
        schedule_status_checks(app, scheduler)
        try:
            scheduler.start()
            logger.info("Scheduler started.")
        except Exception as e:
            logger.warning(f"Scheduler start error: {e}")

    with app.app_context():
        db.create_all()

    return app
