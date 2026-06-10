from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from app import db
from app.models.watchlist import Watchlist
from app.models.company import Company
from app.models.company_read_state import CompanyReadState
from app.utils.schemas import WatchlistSchema, WatchlistCreateSchema

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
        # Start the chat "read" so a freshly added company shows no unread
        # backlog. Never reset an existing state (e.g. re-add on a second
        # watchlist must not clear genuine unreads).
        existing_state = CompanyReadState.query.filter_by(
            user_id=user_id, company_id=company.id).first()
        if existing_state is None:
            db.session.add(CompanyReadState(
                user_id=user_id, company_id=company.id,
                last_read_at=datetime.now(timezone.utc)))
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
