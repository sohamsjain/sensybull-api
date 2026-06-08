import logging
import os
import uuid

from flask import Flask, g, jsonify, request as flask_request
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

    origins_raw = app.config.get('CORS_ALLOWED_ORIGINS') or app.config['FRONTEND_URL']
    allowed_origins = [o.strip() for o in origins_raw.split(',') if o.strip()]
    CORS(app, origins=allowed_origins)
    limiter.init_app(app)
    socketio.init_app(app, cors_allowed_origins=allowed_origins)
    app.url_map.strict_slashes = False

    # ── Request ID middleware ─────────────────────────────────────────
    @app.before_request
    def _set_request_id():
        rid = flask_request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        g.request_id = rid

    @app.after_request
    def _add_request_id_header(response):
        rid = getattr(g, "request_id", None)
        if rid:
            response.headers["X-Request-ID"] = rid
        return response

    class _RequestIdFilter(logging.Filter):
        def filter(self, record):
            record.request_id = getattr(g, "request_id", "-")
            return True

    app.logger.addFilter(_RequestIdFilter())

    # ── Sentry error monitoring ───────────────────────────────────────
    dsn = app.config.get("SENTRY_DSN")
    if dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=float(app.config.get("SENTRY_TRACES_SAMPLE_RATE", 0.1)),
            environment=app.config.get("SENTRY_ENVIRONMENT", "production"),
        )

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

    # API v1 routes
    API_V1 = '/api/v1'
    app.register_blueprint(auth_bp, url_prefix=f'{API_V1}/auth')
    app.register_blueprint(users_bp, url_prefix=f'{API_V1}/users')
    app.register_blueprint(companies_bp, url_prefix=f'{API_V1}/companies')
    app.register_blueprint(watchlists_bp, url_prefix=f'{API_V1}/watchlists')
    app.register_blueprint(filings_bp, url_prefix=f'{API_V1}/filings')
    app.register_blueprint(events_bp, url_prefix=f'{API_V1}/events')

    from app.utils.error_handlers import register_error_handlers
    register_error_handlers(app)

    # ── API docs ──────────────────────────────────────────────────────
    @app.route('/docs/openapi.json')
    def openapi_json():
        from app.openapi import OPENAPI_SPEC
        return jsonify(OPENAPI_SPEC)

    @app.route('/docs')
    def swagger_ui():
        return (
            '<!DOCTYPE html><html><head><title>Sensybull API Docs</title>'
            '<link rel="stylesheet"'
            ' href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.18.2/swagger-ui.css"'
            ' crossorigin="anonymous">'
            '</head><body>'
            '<div id="swagger-ui"></div>'
            '<script'
            ' src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.18.2/swagger-ui-bundle.js"'
            ' crossorigin="anonymous"></script>'
            '<script>SwaggerUIBundle({url:"/docs/openapi.json",dom_id:"#swagger-ui",'
            'deepLinking:true,presets:[SwaggerUIBundle.presets.apis]})</script>'
            '</body></html>'
        )

    @app.route('/health')
    def health():
        """Health check for load balancers and Docker HEALTHCHECK."""
        import redis as _redis
        checks = {'api': 'ok'}
        status = 200
        try:
            r = _redis.from_url(app.config.get('REDIS_URL', os.environ.get('REDIS_URL', '')))
            r.ping()
            checks['redis'] = 'ok'
        except Exception:
            checks['redis'] = 'unreachable'
            status = 503
        try:
            db.session.execute(db.text('SELECT 1'))
            checks['database'] = 'ok'
        except Exception:
            checks['database'] = 'unreachable'
            status = 503
        return jsonify({'status': 'ok' if status == 200 else 'degraded', **checks}), status

    # Ensure SEC companies are loaded (no-op if table already populated)
    from app.services.company_loader import ensure_companies_loaded
    ensure_companies_loaded(app)

    # Start Redis subscriber (no-op if already running in this process)
    from app.services.realtime.subscriber import start_subscriber
    start_subscriber(app, socketio)

    return app
