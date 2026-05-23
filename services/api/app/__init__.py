from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config
from sqlalchemy import event
from app.services.realtime.socketio_setup import socketio

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
limiter = Limiter(key_func=get_remote_address)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    CORS(app)
    limiter.init_app(app)
    socketio.init_app(app)
    app.url_map.strict_slashes = False

    # Register the transactional mail client.
    from app.services.email import get_mail_client
    app.extensions['mail'] = get_mail_client(app.config)

    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        with app.app_context():
            @event.listens_for(db.engine, "connect")
            def enable_wal(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.close()

    from app.routes.auth import auth_bp
    from app.routes.users import users_bp
    from app.routes.companies import companies_bp
    from app.routes.watchlists import watchlists_bp
    from app.routes.filings import filings_bp
    from app.routes.events import events_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(users_bp, url_prefix='/users')
    app.register_blueprint(companies_bp, url_prefix='/companies')
    app.register_blueprint(watchlists_bp, url_prefix='/watchlists')
    app.register_blueprint(filings_bp, url_prefix='/filings')
    app.register_blueprint(events_bp, url_prefix='/events')

    from app.utils.error_handlers import register_error_handlers
    register_error_handlers(app)

    # Start Redis subscriber (no-op if already running in this process)
    from app.services.realtime.subscriber import start_subscriber
    start_subscriber(app, socketio)

    return app
