from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from marshmallow import ValidationError
from app import db
from app.models.filing import Filing
from app.models.company import Company
from app.utils.schemas import FilingSchema, FilingCreateSchema

filings_bp = Blueprint('filings', __name__)
filing_schema = FilingSchema()
filings_schema = FilingSchema(many=True)
create_schema = FilingCreateSchema()


@filings_bp.route('/', methods=['GET'])
@jwt_required()
def get_filings():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    company_id = request.args.get('company_id')
    form_type = request.args.get('form_type')

    query = Filing.query
    if company_id:
        query = query.filter_by(company_id=company_id)
    if form_type:
        query = query.filter(Filing.form_type.ilike(f'%{form_type}%'))

    pagination = query.order_by(Filing.filing_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return jsonify({
        'filings': filings_schema.dump(pagination.items),
        'total': pagination.total,
        'page': page,
        'per_page': per_page
    })


@filings_bp.route('/<filing_id>', methods=['GET'])
@jwt_required()
def get_filing(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    return jsonify({'filing': filing_schema.dump(filing)})


@filings_bp.route('/', methods=['POST'])
@jwt_required()
def create_filing():
    try:
        data = create_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    Company.query.get_or_404(data['company_id'])

    filing = Filing(**data)
    try:
        db.session.add(filing)
        db.session.commit()
        return jsonify({'message': 'Filing created', 'filing': filing_schema.dump(filing)}), 201
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to create filing'}), 500


@filings_bp.route('/<filing_id>', methods=['PUT'])
@jwt_required()
def update_filing(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    try:
        data = create_schema.load(request.json, partial=True)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400
    for field, value in data.items():
        setattr(filing, field, value)
    try:
        db.session.commit()
        return jsonify({'message': 'Filing updated', 'filing': filing_schema.dump(filing)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update filing'}), 500


@filings_bp.route('/<filing_id>', methods=['DELETE'])
@jwt_required()
def delete_filing(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    try:
        db.session.delete(filing)
        db.session.commit()
        return jsonify({'message': 'Filing deleted'})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete filing'}), 500
