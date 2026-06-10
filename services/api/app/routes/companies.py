from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from marshmallow import ValidationError
import sqlalchemy as sa
from app import db
from app.models.company import Company
from app.utils.schemas import CompanySchema, CompanyCreateSchema

companies_bp = Blueprint('companies', __name__)
company_schema = CompanySchema()
companies_schema = CompanySchema(many=True)
create_schema = CompanyCreateSchema()


def _search_query(q: str):
    """Build a Company query that matches ticker and name, ordered by relevance.

    Priority: exact ticker > ticker prefix > name contains.
    """
    term = q.strip()
    query = Company.query.filter(
        sa.or_(
            Company.ticker.ilike(f'%{term}%'),
            Company.name.ilike(f'%{term}%'),
        )
    )
    # Order: exact ticker first, then ticker prefix, then everything else (name match)
    relevance = sa.case(
        (Company.ticker.ilike(term), 0),
        (Company.ticker.ilike(f'{term}%'), 1),
        else_=2,
    )
    return query.order_by(relevance, Company.name)


@companies_bp.route('/search', methods=['GET'])
@jwt_required()
def search_companies():
    """Lightweight typeahead endpoint — returns compact results (id, name, ticker)."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'q parameter is required'}), 400

    limit = request.args.get('limit', 10, type=int)
    limit = min(max(limit, 1), 50)

    results = _search_query(q).limit(limit).all()
    return jsonify({
        'results': [
            {'id': c.id, 'name': c.name, 'ticker': c.ticker}
            for c in results
        ],
    })


@companies_bp.route('/', methods=['GET'])
@jwt_required()
def get_companies():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    q = request.args.get('q', '').strip() or request.args.get('ticker', '').strip()

    if q:
        query = _search_query(q)
    else:
        query = Company.query.order_by(Company.name)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'companies': companies_schema.dump(pagination.items),
        'total': pagination.total,
        'page': page,
        'per_page': per_page
    })


@companies_bp.route('/<company_id>', methods=['GET'])
@jwt_required()
def get_company(company_id):
    company = Company.query.get_or_404(company_id)
    return jsonify({'company': company_schema.dump(company)})


@companies_bp.route('/', methods=['POST'])
@jwt_required()
def create_company():
    try:
        data = create_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    company = Company(**data)
    try:
        db.session.add(company)
        db.session.commit()
        return jsonify({'message': 'Company created', 'company': company_schema.dump(company)}), 201
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to create company'}), 500


@companies_bp.route('/<company_id>', methods=['PUT'])
@jwt_required()
def update_company(company_id):
    company = Company.query.get_or_404(company_id)
    try:
        data = create_schema.load(request.json, partial=True)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400
    for field, value in data.items():
        setattr(company, field, value)
    try:
        db.session.commit()
        return jsonify({'message': 'Company updated', 'company': company_schema.dump(company)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update company'}), 500


@companies_bp.route('/<company_id>', methods=['DELETE'])
@jwt_required()
def delete_company(company_id):
    company = Company.query.get_or_404(company_id)
    try:
        db.session.delete(company)
        db.session.commit()
        return jsonify({'message': 'Company deleted'})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete company'}), 500
