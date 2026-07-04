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

    @jwt.token_in_blocklist_loader
    def _check_if_token_revoked(jwt_header, jwt_payload):
        # Only refresh tokens are revocable (on logout). Access tokens are
        # short-lived and checked on every request, so we skip the DB hit for
        # them and let them expire naturally.
        if jwt_payload.get('type') != 'refresh':
            return False
        from app.models.token_blocklist import TokenBlocklist
        jti = jwt_payload['jti']
        return db.session.query(TokenBlocklist.id).filter_by(jti=jti).first() is not None

    origins_raw = app.config.get('CORS_ALLOWED_ORIGINS') or app.config['FRONTEND_URL']
    allowed_origins = [o.strip() for o in origins_raw.split(',') if o.strip()]
    # supports_credentials lets the httpOnly refresh cookie flow cross-origin;
    # it requires an explicit origin allow-list (never a wildcard), which we have.
    CORS(app, origins=allowed_origins, supports_credentials=True)
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
            try:
                record.request_id = getattr(g, "request_id", "-")
            except RuntimeError:
                record.request_id = "-"
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

    # Register the Resend mail client.
    from app.services.email import ResendClient
    api_key = app.config.get('RESEND_API_KEY')
    if api_key:
        app.extensions['mail'] = ResendClient(api_key=api_key)
    else:
        app.extensions['mail'] = None
        app.logger.warning('RESEND_API_KEY not set — transactional emails disabled')

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
    from app.routes.alerts import alerts_bp
    from app.routes.chats import chats_bp
    from app.routes.movers import movers_bp

    # API v1 routes
    API_V1 = '/api/v1'
    app.register_blueprint(auth_bp, url_prefix=f'{API_V1}/auth')
    app.register_blueprint(users_bp, url_prefix=f'{API_V1}/users')
    app.register_blueprint(companies_bp, url_prefix=f'{API_V1}/companies')
    app.register_blueprint(watchlists_bp, url_prefix=f'{API_V1}/watchlists')
    app.register_blueprint(filings_bp, url_prefix=f'{API_V1}/filings')
    app.register_blueprint(events_bp, url_prefix=f'{API_V1}/events')
    app.register_blueprint(alerts_bp, url_prefix=f'{API_V1}/alerts')
    app.register_blueprint(chats_bp, url_prefix=f'{API_V1}/chats')
    app.register_blueprint(movers_bp, url_prefix=f'{API_V1}/movers')

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
            r = _redis.from_url(os.environ.get('REDIS_URL', ''))
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

    # Register CLI commands
    from app.cli import register_cli
    register_cli(app)

    # Ensure SEC companies are loaded (no-op if table already populated)
    from app.services.company_loader import ensure_companies_loaded
    ensure_companies_loaded(app)

    # Start Redis subscriber (skip when no Redis is configured, e.g. cron jobs)
    if os.environ.get("REDIS_URL"):
        from app.services.realtime.subscriber import start_subscriber
        start_subscriber(app, socketio)

    # Start the price-reaction worker (skip when Alpaca isn't configured)
    if os.environ.get("REDIS_URL") and os.environ.get("ALPACA_API_KEY_ID"):
        from app.services.market_data.reaction_worker import start_reaction_worker
        start_reaction_worker(app, socketio)

    return app
