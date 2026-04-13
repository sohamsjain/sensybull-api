import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///sensybull_api.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'jwt-secret-string'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=999)
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    EDGAR_IDENTITY = os.environ.get('EDGAR_IDENTITY')  # e.g. 'Your Name your@email.com'

    # Application identity (used in email subjects/templates/links)
    APP_NAME = os.environ.get('APP_NAME') or 'Sensybull'
    FRONTEND_URL = os.environ.get('FRONTEND_URL') or 'http://localhost:3000'
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL') or 'support@example.com'

    # Email provider selection: 'resend' | 'smtp' | 'console' (default for dev)
    MAIL_PROVIDER = os.environ.get('MAIL_PROVIDER') or 'console'
    MAIL_FROM_ADDRESS = os.environ.get('MAIL_FROM_ADDRESS') or 'no-reply@example.com'
    MAIL_FROM_NAME = os.environ.get('MAIL_FROM_NAME') or APP_NAME
    MAIL_REPLY_TO = os.environ.get('MAIL_REPLY_TO') or SUPPORT_EMAIL

    # Resend backend
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')

    # SMTP backend
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT') or 587)
    SMTP_USER = os.environ.get('SMTP_USER')
    SMTP_PASS = os.environ.get('SMTP_PASS')
    SMTP_USE_TLS = (os.environ.get('SMTP_USE_TLS') or 'true').lower() == 'true'

    # Token lifetimes
    EMAIL_VERIFY_TOKEN_HOURS = int(os.environ.get('EMAIL_VERIFY_TOKEN_HOURS') or 24)
    PASSWORD_RESET_TOKEN_HOURS = int(os.environ.get('PASSWORD_RESET_TOKEN_HOURS') or 1)

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://'
