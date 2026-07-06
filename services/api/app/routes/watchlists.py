from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from app import db, limiter
from app.models.watchlist import Watchlist
from app.models.company import Company
from app.models.company_read_state import CompanyReadState
from app.services.share_analytics import record_share_event
from app.utils.schemas import WatchlistSchema, WatchlistCreateSchema
from app.utils.tickers import normalize_symbol

DEFAULT_WATCHLIST_NAME = 'My Watchlist'

watchlists_bp = Blueprint('watchlists', __name__)
watchlist_schema = WatchlistSchema()
watchlists_schema = WatchlistSchema(many=True)
create_schema = WatchlistCreateSchema()


@watchlists_bp.route('/', methods=['GET'])
@jwt_required()
def get_watchlists():
    user_id = get_jwt_identity()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    pagination = Watchlist.query.filter_by(user_id=user_id).paginate(
        page=page, per_page=per_page, error_out=False)
    return jsonify({
        'watchlists': watchlists_schema.dump(pagination.items),
        'total': pagination.total,
        'page': page,
        'per_page': per_page
    })


@watchlists_bp.route('/<watchlist_id>', methods=['GET'])
@jwt_required()
def get_watchlist(watchlist_id):
    user_id = get_jwt_identity()
    watchlist = Watchlist.query.get_or_404(watchlist_id)
    if watchlist.user_id != user_id:
        return jsonify({'error': 'Access denied'}), 403
    return jsonify({'watchlist': watchlist_schema.dump(watchlist)})


@watchlists_bp.route('/', methods=['POST'])
@jwt_required()
def create_watchlist():
    user_id = get_jwt_identity()
    try:
        data = create_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    watchlist = Watchlist(user_id=user_id, **data)
    try:
        db.session.add(watchlist)
        db.session.commit()
        return jsonify({'message': 'Watchlist created', 'watchlist': watchlist_schema.dump(watchlist)}), 201
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to create watchlist'}), 500


@watchlists_bp.route('/<watchlist_id>', methods=['PUT'])
@jwt_required()
def update_watchlist(watchlist_id):
    user_id = get_jwt_identity()
    watchlist = Watchlist.query.get_or_404(watchlist_id)
    if watchlist.user_id != user_id:
        return jsonify({'error': 'Access denied'}), 403
    try:
        data = create_schema.load(request.json, partial=True)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400
    for field, value in data.items():
        setattr(watchlist, field, value)
    try:
        db.session.commit()
        return jsonify({'message': 'Watchlist updated', 'watchlist': watchlist_schema.dump(watchlist)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update watchlist'}), 500


@watchlists_bp.route('/<watchlist_id>', methods=['DELETE'])
@jwt_required()
def delete_watchlist(watchlist_id):
    user_id = get_jwt_identity()
    watchlist = Watchlist.query.get_or_404(watchlist_id)
    if watchlist.user_id != user_id:
        return jsonify({'error': 'Access denied'}), 403
    try:
        db.session.delete(watchlist)
        db.session.commit()
        return jsonify({'message': 'Watchlist deleted'})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete watchlist'}), 500


@watchlists_bp.route('/track', methods=['POST'])
@jwt_required()
@limiter.limit('30 per minute')
def track_company():
    """Idempotent one-call "track this ticker" for shared /add/<symbol> links.

    Validates the ticker, resolves the company, adds it to the user's default
    watchlist (their first list, created on demand — mirroring the frontend's
    default-watchlist behavior), and reports whether it was newly added.
    Re-posting the same symbol is always success, never a duplicate.
    """
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    symbol = normalize_symbol(data.get('symbol'))
    if not symbol:
        return jsonify({'error': 'invalid_symbol'}), 400

    company = Company.query.filter(Company.ticker.ilike(symbol)).first()
    if not company:
        return jsonify({'error': 'unknown_ticker'}), 404

    watchlist = (Watchlist.query.filter_by(user_id=user_id)
                 .order_by(Watchlist.created_at).first())
    if watchlist is None:
        watchlist = Watchlist(user_id=user_id, name=DEFAULT_WATCHLIST_NAME)
        db.session.add(watchlist)

    already_tracking = company in watchlist.companies
    if not already_tracking:
        watchlist.companies.append(company)
        # Fresh adds start with no unread backlog; never resets existing state.
        CompanyReadState.ensure(
            db.session, user_id, company.id,
            last_read_at=datetime.now(timezone.utc))

    # Funnel analytics (best-effort) — the API is authoritative for these two
    # steps; the client only reports the pre-add steps.
    record_share_event(
        'already_in_watchlist' if already_tracking else 'watchlist_added',
        symbol=symbol,
        attribution=data.get('attribution') if isinstance(data.get('attribution'), dict) else {},
        referrer=data.get('referrer'),
        user_id=user_id,
        logged_in=True,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to track company'}), 500

    return jsonify({
        'status': 'already_tracking' if already_tracking else 'added',
        'company': {'id': company.id, 'name': company.name, 'ticker': company.ticker},
        'watchlist_id': watchlist.id,
    })


@watchlists_bp.route('/<watchlist_id>/companies', methods=['POST'])
@jwt_required()
def add_company(watchlist_id):
    user_id = get_jwt_identity()
    watchlist = Watchlist.query.get_or_404(watchlist_id)
    if watchlist.user_id != user_id:
        return jsonify({'error': 'Access denied'}), 403

    company_id = request.json.get('company_id')
    if not company_id:
        return jsonify({'error': 'company_id is required'}), 400

    company = Company.query.get_or_404(company_id)
    if company in watchlist.companies:
        return jsonify({'error': 'Company already in watchlist'}), 409

    try:
        watchlist.companies.append(company)
        # Initialize read state so a freshly added company shows no unread
        # backlog. Never reset an existing state (e.g. re-add on a second
        # watchlist must not clear genuine unreads).
        CompanyReadState.ensure(
            db.session, user_id, company.id,
            last_read_at=datetime.now(timezone.utc))
        db.session.commit()
        return jsonify({'message': 'Company added to watchlist', 'watchlist': watchlist_schema.dump(watchlist)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to add company to watchlist'}), 500


@watchlists_bp.route('/<watchlist_id>/companies/<company_id>', methods=['DELETE'])
@jwt_required()
def remove_company(watchlist_id, company_id):
    user_id = get_jwt_identity()
    watchlist = Watchlist.query.get_or_404(watchlist_id)
    if watchlist.user_id != user_id:
        return jsonify({'error': 'Access denied'}), 403

    company = Company.query.get_or_404(company_id)
    if company not in watchlist.companies:
        return jsonify({'error': 'Company not in watchlist'}), 404

    try:
        watchlist.companies.remove(company)
        db.session.commit()
        return jsonify({'message': 'Company removed from watchlist'})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to remove company from watchlist'}), 500
