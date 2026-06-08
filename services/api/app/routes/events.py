# services/api/app/routes/events.py
"""
REST endpoints for filing event history.

GET  /events/              paginated event feed for the user's watchlist companies
GET  /events/<event_id>    single event detail
GET  /events/company/<company_id>   events for one company (with optional tier filter)
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
import sqlalchemy as sa

from app.models.filing_event import FilingEvent
from app.models.event_type import EventType
from app.models.watchlist import Watchlist

events_bp = Blueprint("events", __name__)


def _user_company_ids(user_id: str) -> set[str]:
    watchlists = Watchlist.query.filter_by(user_id=user_id).all()
    return {c.id for wl in watchlists for c in wl.companies}


def _apply_event_type_filter(q, event_type: str | None):
    """Filter by event_type using the EventType relationship."""
    if not event_type:
        return q
    return q.filter(FilingEvent.event_types.any(EventType.type_name == event_type))


@events_bp.route("/", methods=["GET"])
@jwt_required()
def get_events():
    user_id     = get_jwt_identity()
    page        = request.args.get("page", 1, type=int)
    per_page    = request.args.get("per_page", 50, type=int)
    max_tier    = request.args.get("max_tier", 3, type=int)    # filter: only <= this tier
    signal_type = request.args.get("signal_type")              # "8-K", "earnings", etc.
    event_type  = request.args.get("event_type")               # e.g. "Acquisition"

    company_ids = _user_company_ids(user_id)
    if not company_ids:
        return jsonify({"events": [], "total": 0, "page": page, "per_page": per_page})

    q = (
        FilingEvent.query
        .filter(FilingEvent.company_id.in_(company_ids))
        .filter(FilingEvent.max_tier <= max_tier)
    )
    if signal_type:
        q = q.filter(FilingEvent.signal_type == signal_type)
    q = _apply_event_type_filter(q, event_type)

    pagination = q.order_by(FilingEvent.filing_date.desc()).paginate(
        page=page, per_page=min(per_page, 200), error_out=False
    )
    return jsonify({
        "events": [e.to_ws_payload() for e in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })


@events_bp.route("/all", methods=["GET"])
def get_all_events():
    """Paginated feed of ALL events regardless of watchlist."""
    page        = request.args.get("page", 1, type=int)
    per_page    = request.args.get("per_page", 50, type=int)
    max_tier    = request.args.get("max_tier", 3, type=int)
    signal_type = request.args.get("signal_type")
    event_type  = request.args.get("event_type")

    q = FilingEvent.query.filter(FilingEvent.max_tier <= max_tier)
    if signal_type:
        q = q.filter(FilingEvent.signal_type == signal_type)
    q = _apply_event_type_filter(q, event_type)

    pagination = q.order_by(FilingEvent.filing_date.desc()).paginate(
        page=page, per_page=min(per_page, 200), error_out=False
    )
    return jsonify({
        "events": [e.to_ws_payload() for e in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })


EVENT_TYPES = [
    "M&A / Merger", "Acquisition", "Divestiture", "Activist Proxy",
    "Activist Initial", "Strategic Review", "Tender Offer", "Issuer Tender",
    "Going-Private", "Going Dark", "Spin-Off", "Capital Return", "Rights Issue",
    "Restructuring", "Insolvency", "Liquidation", "Delisting", "Busted M&A",
    "Litigation", "Domicile Change", "Earnings", "Leadership Change",
    "Debt / Financing", "Impairment", "Restatement", "Regulatory Action",
    "Cybersecurity Incident", "Material Agreement", "Dividend Change",
    "Bankruptcy", "Shelf Registration", "Share Offering", "Stock Split", "Other",
]


@events_bp.route("/types", methods=["GET"])
def get_event_types():
    """Return the canonical list of event type labels for filter UIs."""
    return jsonify({"event_types": EVENT_TYPES})


@events_bp.route("/catalysts", methods=["GET"])
def get_upcoming_catalysts():
    """Return upcoming catalysts across all events, ordered by date."""
    from datetime import date
    from app.models.catalyst import Catalyst

    limit = min(request.args.get("limit", 50, type=int), 200)
    cutoff = date.today()

    q = (
        Catalyst.query
        .filter(Catalyst.catalyst_date >= cutoff)
        .order_by(Catalyst.catalyst_date.asc())
        .limit(limit)
    )
    return jsonify({"catalysts": [
        {
            "id": c.id,
            "filing_event_id": c.filing_event_id,
            "event": c.event_description,
            "date": c.catalyst_date.isoformat() if c.catalyst_date else None,
            "ticker": c.ticker,
            "company_name": c.company_name,
        } for c in q.all()
    ]})


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

    page        = request.args.get("page", 1, type=int)
    per_page    = request.args.get("per_page", 50, type=int)
    max_tier    = request.args.get("max_tier", 3, type=int)
    signal_type = request.args.get("signal_type")
    event_type  = request.args.get("event_type")

    q = (
        FilingEvent.query
        .filter_by(company_id=company_id)
        .filter(FilingEvent.max_tier <= max_tier)
    )
    if signal_type:
        q = q.filter_by(signal_type=signal_type)
    q = _apply_event_type_filter(q, event_type)

    pagination = q.order_by(FilingEvent.filing_date.desc()).paginate(
        page=page, per_page=min(per_page, 200), error_out=False
    )
    return jsonify({
        "events": [e.to_ws_payload() for e in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })
