from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from marshmallow import ValidationError
import sqlalchemy as sa
from app import db, limiter
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
    return query.order_by(relevance, Company.market_cap.desc().nullslast(), Company.name)


@companies_bp.route('/search', methods=['GET'])
@limiter.limit('120 per minute')
def search_companies():
    """Lightweight typeahead endpoint — returns compact results (id, name, ticker).

    Public: it backs the fundamentals pages' search box, which must work
    for signed-out readers. Companies with a fundamentals page rank first,
    then by market cap, so "APP" finds Apple before a shell company.
    """
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'q parameter is required'}), 400

    limit = request.args.get('limit', 10, type=int)
    limit = min(max(limit, 1), 50)

    results = _search_query(q).limit(limit).all()
    return jsonify({
        'results': [
            {
                'id': c.id, 'name': c.name, 'ticker': c.ticker,
                'market_cap': c.market_cap,
                'industry': c.fundamentals.industry if c.fundamentals else None,
                'has_fundamentals': bool(c.fundamentals and c.fundamentals.has_fundamentals),
            }
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
BAR_TIMEFRAMES = {'1D': '1day', '1H': '1hour', '15Min': '15min'}  # → FMP interval
BAR_LOOKBACK_DAYS = {'1M': 31, '3M': 93, '6M': 186, '1Y': 366, '2Y': 731, '5Y': 1827}
# A window that has already closed can never change; a window ending "now" can.
BARS_CACHE_SECONDS = 300
BARS_HISTORY_CACHE_SECONDS = 86400


def _parse_bars_end(raw: str):
    """Parse the `end` query param into an aware UTC datetime.

    Accepts a date ('2026-06-01' — read as that day's 00:00 UTC, so the page
    returns strictly earlier sessions) or an RFC-3339 timestamp. Returns None
    when the value can't be parsed.
    """
    from datetime import datetime, timezone

    value = raw.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@companies_bp.route('/<company_id>/bars', methods=['GET'])
@jwt_required()
def get_company_bars(company_id):
    """OHLCV bars for the company's ticker (FMP proxy, Redis-cached).

    Backs the frontend price chart with event markers. The optional `end`
    param walks backwards through history: the chart passes the earliest bar
    it already holds, and gets the window of `lookback` before that. Pages
    that end in the past are immutable, so they cache for a day.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.market_data import prices
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

    now = datetime.now(timezone.utc)
    raw_end = request.args.get('end', '').strip()
    end = None
    if raw_end:
        end = _parse_bars_end(raw_end)
        if end is None:
            return jsonify({'error': 'end must be an ISO date or timestamp'}), 400
        end = min(end, now)

    window_end = end or now
    start = window_end - timedelta(days=BAR_LOOKBACK_DAYS[lookback])
    end_iso = end.isoformat() if end else None

    cache_key = f'bars:{company.ticker}:{timeframe}:{lookback}:{end_iso or "now"}'
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    symbol = prices.normalize_ticker(company.ticker)
    try:
        bars = prices.get_bars(symbol, BAR_TIMEFRAMES[timeframe], start, end=end)
    except prices.MarketDataError:
        return jsonify({'error': 'Market data temporarily unavailable'}), 503

    response = {
        'ticker': company.ticker,
        'timeframe': timeframe,
        'lookback': lookback,
        'start': start.isoformat(),
        'end': end_iso,
        'bars': bars,
    }
    ttl = BARS_HISTORY_CACHE_SECONDS if end else BARS_CACHE_SECONDS
    cache_set(cache_key, response, ttl)
    return jsonify(response)


QUOTE_CACHE_SECONDS = 60
# A feed page asks for ~50 rows; the cap keeps one request from fanning out
# into an unbounded batch-quote call.
MAX_QUOTE_IDS = 120


@companies_bp.route('/quotes', methods=['GET'])
@jwt_required()
def get_company_quotes():
    """Quotes for many companies in one round trip.

    Backs the prices shown on every row of the feed and the watchlist, where
    asking per company would mean dozens of requests. Shares the per-ticker
    Redis cache with the single-company route, and folds every cache miss
    into one FMP batch-quote call.

    GET /companies/quotes?ids=<uuid>,<uuid>,...
      -> {"quotes": {"<company_id>": {...}, ...}}

    Companies that are unknown, have no ticker, or have no price at all are
    simply absent from the map — a missing price is not an error here.
    """
    from app.services.market_data import prices
    from app.services.market_data.cache import cache_get, cache_set

    raw = request.args.get('ids', '')
    ids = [i.strip() for i in raw.split(',') if i.strip()][:MAX_QUOTE_IDS]
    if not ids:
        return jsonify({'quotes': {}})

    try:
        companies = Company.query.filter(Company.id.in_(ids)).all()
    except sa.exc.DataError:
        # A malformed UUID in the list shouldn't 500 the whole row of prices
        db.session.rollback()
        return jsonify({'quotes': {}})

    quotes = {}
    pending = {}  # FMP symbol -> [company, ...]
    for company in companies:
        if not company.ticker:
            continue
        cached = cache_get(f'quote:{company.ticker}')
        if cached:
            quotes[str(company.id)] = cached
            continue
        pending.setdefault(prices.normalize_ticker(company.ticker), []).append(company)

    fmp_quotes = {}
    if pending:
        try:
            fmp_quotes = prices.get_quotes(list(pending))
        except prices.MarketDataError:
            fmp_quotes = {}

    for symbol, symbol_companies in pending.items():
        fmp_quote = fmp_quotes.get(symbol)
        for company in symbol_companies:
            quote = _quote_payload(company, fmp_quote)
            if quote is None:
                continue
            if not quote['stale']:
                cache_set(f'quote:{company.ticker}', quote, QUOTE_CACHE_SECONDS)
            quotes[str(company.id)] = quote

    return jsonify({'quotes': quotes})


@companies_bp.route('/<company_id>/quote', methods=['GET'])
@jwt_required()
def get_company_quote(company_id):
    """Last price and day change for the company's ticker.

    FMP quote proxy, Redis-cached 60s — backs the price shown beside
    the company name in the watchlist header. When FMP is unreachable
    this falls back to the price the daily sync stored on the company, so
    the header shows a (stale-flagged) number instead of nothing.
    """
    from app.services.market_data import prices
    from app.services.market_data.cache import cache_get, cache_set

    company = Company.query.get_or_404(company_id)
    if not company.ticker:
        return jsonify({'error': 'no_ticker'}), 422

    cache_key = f'quote:{company.ticker}'
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    symbol = prices.normalize_ticker(company.ticker)
    try:
        fmp_quote = prices.get_quotes([symbol]).get(symbol)
    except prices.MarketDataError:
        fmp_quote = None

    quote = _quote_payload(company, fmp_quote)
    if quote is None:
        return jsonify({'error': 'Market data temporarily unavailable'}), 503
    if not quote['stale']:
        cache_set(cache_key, quote, QUOTE_CACHE_SECONDS)
    return jsonify(quote)


def _quote_payload(company, fmp_quote):
    """Quote dict for a company, or None when there is no price to report.

    Falls back to the daily-synced price (flagged `stale`) when FMP has
    nothing for the symbol right now.
    """
    from app.services.market_data import prices

    price = prices.quote_price(fmp_quote)
    if price is None:
        if company.last_price is None:
            return None
        return {
            'ticker': company.ticker,
            'price': float(company.last_price),
            'prev_close': None,
            'change': None,
            'change_pct': None,
            'as_of': (
                company.price_updated_at.isoformat()
                if company.price_updated_at else None
            ),
            'stale': True,
        }

    # Day change is measured against the previous session's close. FMP's
    # quote is regular-session: pre-market it still reads yesterday's close,
    # so the change there is 0 until the open rather than an extended-hours
    # move.
    prev_close = prices.quote_prev_close(fmp_quote)
    change = change_pct = None
    if prev_close:
        change = round(price - prev_close, 4)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    return {
        'ticker': company.ticker,
        'price': price,
        'prev_close': prev_close,
        'change': change,
        'change_pct': change_pct,
        'as_of': prices.quote_time(fmp_quote),
        'stale': False,
    }


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
