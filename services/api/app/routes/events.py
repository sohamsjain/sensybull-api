# services/api/app/routes/events.py
"""
REST endpoints for filing event history.

GET  /events/              paginated event feed for the user's watchlist companies
GET  /events/all           paginated feed of every event (public)
GET  /events/facets        how many events each filter option would show
GET  /events/filters       every option each feed filter offers (public)
GET  /events/<event_id>    single event detail
GET  /events/company/<company_id>   events for one company

The feed endpoints share one set of filters, applied in SQL by
app/services/feed_filters.py: important, event_type, sector, cap, source,
sentiment, moved, since, q (plus the legacy max_tier and signal_type).
Multi-value filters take a comma-separated list. An unknown value is a 400.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity, verify_jwt_in_request
from sqlalchemy.orm import joinedload

from app.models.filing_event import FilingEvent
from app.models.watchlist import Watchlist
from app.services import feed_filters
from app.services.feed_filters import FilterError, parse_filters

events_bp = Blueprint("events", __name__)

FACETS_CACHE_SECONDS = 60


def _user_company_ids(user_id: str) -> set[str]:
    watchlists = Watchlist.query.filter_by(user_id=user_id).all()
    return {c.id for wl in watchlists for c in wl.companies}


def _filters(scope: str):
    """The request's filters, or a 400 response."""
    try:
        f = parse_filters(request.args)
    except FilterError as exc:
        return None, (jsonify({"error": str(exc)}), 400)
    f.scope = scope
    return f, None


def _page(q, f, order):
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 50, type=int), 1), 200)
    q = feed_filters.apply_filters(q, f)
    pagination = q.order_by(order.desc(), FilingEvent.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "events": [e.to_ws_payload() for e in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
        "has_more": page * per_page < pagination.total,
        "filters": f.to_dict(),
    })


@events_bp.route("/", methods=["GET"])
@jwt_required()
def get_events():
    f, error = _filters("mine")
    if error:
        return error
    company_ids = _user_company_ids(get_jwt_identity())
    q = (
        FilingEvent.query
        .options(joinedload(FilingEvent.company))
        .filter(FilingEvent.company_id.in_(company_ids or {""}))
    )
    return _page(q, f, feed_filters.order_column("mine"))


@events_bp.route("/all", methods=["GET"])
def get_all_events():
    """Paginated feed of ALL events regardless of watchlist.

    Ordered by when we received each event (created_at), so the REST
    pages line up with the live socket stream the feed prepends onto.
    """
    f, error = _filters("all")
    if error:
        return error
    q = FilingEvent.query.options(joinedload(FilingEvent.company))
    return _page(q, f, feed_filters.order_column("all"))


@events_bp.route("/facets", methods=["GET"])
def get_facets():
    """Counts per filter option for the feed's filter panel.

    `scope=all` (default) counts the public stream and is shared-cached;
    `scope=mine` counts the caller's companies and needs a token. Each
    option is counted with every *other* active filter applied, so the
    panel shows what a click would actually return.
    """
    from app.services.market_data.cache import cache_get, cache_set

    scope = request.args.get("scope", "all")
    if scope not in feed_filters.SCOPES:
        return jsonify({"error": f"unknown scope: {scope!r}"}), 400
    f, error = _filters(scope)
    if error:
        return error

    if scope == "mine":
        verify_jwt_in_request()
        company_ids = _user_company_ids(get_jwt_identity()) or {""}

        def base():
            return FilingEvent.query.filter(FilingEvent.company_id.in_(company_ids))

        return jsonify(feed_filters.facet_counts(base, f))

    cache_key = f"facets:v1:{f.cache_key()}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    counts = feed_filters.facet_counts(lambda: FilingEvent.query, f)
    cache_set(cache_key, counts, FACETS_CACHE_SECONDS)
    return jsonify(counts)


@events_bp.route("/filters", methods=["GET"])
def get_filter_options():
    """Every option each feed filter accepts, with display labels."""
    return jsonify(feed_filters.options_payload(EVENT_TYPES))


@events_bp.route("/all/<event_id>", methods=["GET"])
def get_public_event(event_id):
    """Single event by id, no auth — filings are public data.

    Backs the frontend's shareable per-event permalinks.
    """
    event = FilingEvent.query.get_or_404(event_id)
    return jsonify({"event": event.to_ws_payload()})


# Mirrors services/ingest/briefing.py EVENT_TYPES — keep the two in sync.
# Deliberately a small list of highly material categories (July 2026
# rollback: 8-K is the only ingested form; the long taxonomy went with the
# other forms). "Regulatory / Clinical" added with press-release ingestion
# (FDA decisions / trial results reach the wire before any filing).
EVENT_TYPES = [
    "Acquisition", "Material Agreement", "Earnings", "Bankruptcy",
    "Debt / Financing", "Restructuring", "Leadership Change", "Delisting",
    "Restatement", "Cybersecurity Incident", "Regulatory / Clinical", "Other",
]


@events_bp.route("/types", methods=["GET"])
def get_event_types():
    """Return the canonical list of event type labels for filter UIs."""
    return jsonify({"event_types": EVENT_TYPES})


@events_bp.route("/<event_id>", methods=["GET"])
@jwt_required()
def get_event(event_id):
    user_id     = get_jwt_identity()
    event       = FilingEvent.query.get_or_404(event_id)
    company_ids = _user_company_ids(user_id)

    # Only return events for the user's watchlist companies
    if event.company_id and event.company_id not in company_ids:
        return jsonify({"error": "Access denied"}), 403

    return jsonify({"event": event.to_ws_payload()})


@events_bp.route("/company/<company_id>", methods=["GET"])
@jwt_required()
def get_company_events(company_id):
    user_id     = get_jwt_identity()
    company_ids = _user_company_ids(user_id)

    if company_id not in company_ids:
        return jsonify({"error": "Access denied"}), 403

    f, error = _filters("mine")
    if error:
        return error
    q = (
        FilingEvent.query
        .options(joinedload(FilingEvent.company))
        .filter_by(company_id=company_id)
    )
    return _page(q, f, FilingEvent.filing_date)
