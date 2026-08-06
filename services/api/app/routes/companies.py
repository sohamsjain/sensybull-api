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


# timeframe / lookback whitelists for the bars proxy
BAR_TIMEFRAMES = {'1D': '1Day', '1H': '1Hour', '15Min': '15Min'}
BAR_LOOKBACK_DAYS = {'1M': 31, '3M': 93, '6M': 186, '1Y': 366}


@companies_bp.route('/<company_id>/bars', methods=['GET'])
@jwt_required()
def get_company_bars(company_id):
    """OHLCV bars for the company's ticker (Alpaca proxy, Redis-cached 5 min).

    Backs the frontend price chart with event markers.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.market_data import alpaca
    from app.services.market_data.cache import cache_get, cache_set

    company = Company.query.get_or_404(company_id)
    if not company.ticker:
        return jsonify({'error': 'no_ticker'}), 422

    timeframe = request.args.get('timeframe', '1D')
    lookback = request.args.get('lookback', '3M')
    if timeframe not in BAR_TIMEFRAMES:
        return jsonify({'error': f'timeframe must be one of {sorted(BAR_TIMEFRAMES)}'}), 400
    if lookback not in BAR_LOOKBACK_DAYS:
        return jsonify({'error': f'lookback must be one of {sorted(BAR_LOOKBACK_DAYS)}'}), 400

    cache_key = f'bars:{company.ticker}:{timeframe}:{lookback}'
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    symbol = alpaca.normalize_ticker(company.ticker)
    start = datetime.now(timezone.utc) - timedelta(days=BAR_LOOKBACK_DAYS[lookback])
    try:
        bars = alpaca.get_bars([symbol], BAR_TIMEFRAMES[timeframe], start.isoformat())
    except alpaca.AlpacaError:
        return jsonify({'error': 'Market data temporarily unavailable'}), 503

    response = {
        'ticker': company.ticker,
        'timeframe': timeframe,
        'lookback': lookback,
        'bars': [
            {'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v']}
            for b in bars.get(symbol, [])
        ],
    }
    cache_set(cache_key, response, 300)
    return jsonify(response)


QUOTE_CACHE_SECONDS = 60


@companies_bp.route('/<company_id>/quote', methods=['GET'])
@jwt_required()
def get_company_quote(company_id):
    """Last price and day change for the company's ticker.

    Alpaca snapshot proxy, Redis-cached 60s — backs the price shown beside
    the company name in the watchlist header. When Alpaca is unreachable
    this falls back to the price the daily sync stored on the company, so
    the header shows a (stale-flagged) number instead of nothing.
    """
    from app.services.market_data import alpaca
    from app.services.market_data.cache import cache_get, cache_set

    company = Company.query.get_or_404(company_id)
    if not company.ticker:
        return jsonify({'error': 'no_ticker'}), 422

    cache_key = f'quote:{company.ticker}'
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    symbol = alpaca.normalize_ticker(company.ticker)
    snapshot = None
    try:
        snapshot = alpaca.get_snapshots([symbol]).get(symbol)
    except alpaca.AlpacaError:
        snapshot = None

    price = alpaca.snapshot_price(snapshot) if snapshot else None
    if price is None:
        return _stale_quote(company)

    # Day change is measured against the previous session's close. Alpaca's
    # prevDailyBar trails dailyBar all session, so this stays "today's move"
    # during regular hours and after the close, and becomes "since yesterday"
    # pre-market, which is what a quote should read.
    prev_close = (snapshot.get('prevDailyBar') or {}).get('c')
    change = change_pct = None
    if prev_close:
        change = round(price - prev_close, 4)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    response = {
        'ticker': company.ticker,
        'price': price,
        'prev_close': prev_close,
        'change': change,
        'change_pct': change_pct,
        'as_of': alpaca.snapshot_time(snapshot),
        'stale': False,
    }
    cache_set(cache_key, response, QUOTE_CACHE_SECONDS)
    return jsonify(response)


def _stale_quote(company):
    """Last synced price when Alpaca has nothing for the symbol right now."""
    if company.last_price is None:
        return jsonify({'error': 'Market data temporarily unavailable'}), 503
    return jsonify({
        'ticker': company.ticker,
        'price': float(company.last_price),
        'prev_close': None,
        'change': None,
        'change_pct': None,
        'as_of': company.price_updated_at.isoformat() if company.price_updated_at else None,
        'stale': True,
    })


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
