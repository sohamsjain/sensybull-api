from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from config import Config
from sqlalchemy import event

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    CORS(app)
    app.url_map.strict_slashes = False

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

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(users_bp, url_prefix='/users')
    app.register_blueprint(companies_bp, url_prefix='/companies')
    app.register_blueprint(watchlists_bp, url_prefix='/watchlists')
    app.register_blueprint(filings_bp, url_prefix='/filings')

    from app.utils.error_handlers import register_error_handlers
    register_error_handlers(app)

    return app
