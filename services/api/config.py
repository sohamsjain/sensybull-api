import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


def _require_env(name: str) -> str:
    """Return an env var's value or raise if missing/empty."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


class Config:
    SECRET_KEY = _require_env('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///sensybull_api.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Engine options: SQLite needs check_same_thread=False; Postgres gets connection pooling
    _db_uri = SQLALCHEMY_DATABASE_URI
    if _db_uri.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'check_same_thread': False}}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_size': 5, 'max_overflow': 10, 'pool_pre_ping': True}

    JWT_SECRET_KEY = _require_env('JWT_SECRET_KEY')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=999)
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    APPLE_CLIENT_ID = os.environ.get('APPLE_CLIENT_ID')
    # Application identity (used in email subjects/templates/links)
    APP_NAME = os.environ.get('APP_NAME') or 'Sensybull'
    FRONTEND_URL = os.environ.get('FRONTEND_URL') or 'http://localhost:3000'
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL') or 'support@example.com'

    # Email (Resend)
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    MAIL_FROM_ADDRESS = os.environ.get('MAIL_FROM_ADDRESS') or 'no-reply@example.com'
    MAIL_FROM_NAME = os.environ.get('MAIL_FROM_NAME') or APP_NAME
    MAIL_REPLY_TO = os.environ.get('MAIL_REPLY_TO') or SUPPORT_EMAIL

    # Token lifetimes
    EMAIL_VERIFY_TOKEN_HOURS = int(os.environ.get('EMAIL_VERIFY_TOKEN_HOURS') or 24)
    PASSWORD_RESET_TOKEN_HOURS = int(os.environ.get('PASSWORD_RESET_TOKEN_HOURS') or 1)

    # CORS: comma-separated origins (defaults to FRONTEND_URL)
    CORS_ALLOWED_ORIGINS = os.environ.get('CORS_ALLOWED_ORIGINS')

    # Rate limiting (use redis://... in production for cross-instance limits)
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://'

    # Alerts
    ALERT_EMAIL_SUBJECT_PREFIX = os.environ.get('ALERT_EMAIL_SUBJECT_PREFIX') or '[Sensybull]'

    # Web Push (generate with: python -c "from py_vapid import Vapid02; v=Vapid02(); v.generate_keys(); ...")
    # or: npx web-push generate-vapid-keys
    VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY')
    VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
    VAPID_SUBJECT = os.environ.get('VAPID_SUBJECT')  # e.g. mailto:support@sensybull.com

    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'

    # Error monitoring (Sentry)
    SENTRY_DSN = os.environ.get('SENTRY_DSN')
    SENTRY_TRACES_SAMPLE_RATE = os.environ.get('SENTRY_TRACES_SAMPLE_RATE') or '0.1'
    SENTRY_ENVIRONMENT = os.environ.get('SENTRY_ENVIRONMENT') or 'production'
