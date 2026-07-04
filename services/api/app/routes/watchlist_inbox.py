# services/api/app/routes/watchlist_inbox.py
"""
Watchlist inbox endpoints.

Each company on a user's watchlists is an inbox entry: the company's
filing events are its history, and CompanyReadState tracks what the
user has seen and whether the company's alerts are muted.

GET  /watchlist/                       watchlist companies with unread counts + last-event previews
POST /watchlist/<company_id>/read      mark a company's history as read
PUT  /watchlist/<company_id>/mute      mute/unmute a company's alerts
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from app import db
from app.models.company import Company
from app.models.company_read_state import CompanyReadState
from app.models.filing_event import FilingEvent
from app.models.watchlist import Watchlist

watchlist_inbox_bp = Blueprint('watchlist_inbox', __name__)


def _user_company_ids(user_id: str) -> set[str]:
    watchlists = Watchlist.query.filter_by(user_id=user_id).all()
    return {c.id for wl in watchlists for c in wl.companies}


def _iso(dt: datetime | None) -> str | None:
    """Serialize with a UTC offset (SQLite strips tzinfo on storage)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Human-readable fallback labels for non-8-K form types
_FORM_LABELS = {
    '8-K': 'an 8-K',
    '4': 'a Form 4 insider transaction',
    'SC 13D': 'a 13D stake disclosure',
    'SC 13D/A': 'a 13D amendment',
    'SC 13G': 'a 13G passive stake',
    'SC TO-T': 'a tender offer',
    'SC TO-I': 'a self-tender',
    'SC 14D9': 'a tender-offer response',
    'SC 13E3': 'a going-private filing',
    'NT 10-K': 'a late-filing notice',
    'NT 10-Q': 'a late-filing notice',
}


def _form_label(signal_type: str) -> str:
    return _FORM_LABELS.get(signal_type, f'a {signal_type}')


def _event_preview(event: FilingEvent) -> dict:
    """Compact last-event payload for the watchlist inbox."""
    briefing = event.briefing_json or {}
    return {
        'id': event.id,
        'headline': briefing.get('headline') or f'{event.company_name} filed {_form_label(event.signal_type)}',
        'significance': briefing.get('significance'),
        'sentiment': briefing.get('sentiment'),
        'primary_event_type': briefing.get('primary_event_type'),
        'max_tier': event.max_tier,
        'signal_type': event.signal_type,
        'filing_date': _iso(event.filing_date),
        'received_at': _iso(event.created_at),
    }


def _read_state_payload(state: CompanyReadState) -> dict:
    return {
        'company_id': state.company_id,
        'last_read_at': _iso(state.last_read_at),
        'muted': state.muted,
    }


@watchlist_inbox_bp.route('/', methods=['GET'])
@jwt_required()
def get_watchlist_inbox():
    """Watchlist inbox: every watchlist company with unread count and last event.

    Sorted with unread companies first, then by most recent activity.
    """
    user_id = get_jwt_identity()
    company_ids = _user_company_ids(user_id)
    if not company_ids:
        # 'chats' key kept for one deploy cycle; TODO remove after web deploy
        return jsonify({'items': [], 'chats': [], 'total_unread': 0})

    companies = Company.query.filter(Company.id.in_(company_ids)).all()
    states = {
        s.company_id: s
        for s in CompanyReadState.query.filter(
            CompanyReadState.user_id == user_id,
            CompanyReadState.company_id.in_(company_ids),
        )
    }

    # Latest event per company (window function; supported by Postgres and SQLite 3.25+)
    rn = sa.func.row_number().over(
        partition_by=FilingEvent.company_id,
        order_by=FilingEvent.created_at.desc(),
    ).label('rn')
    ranked = (
        db.session.query(FilingEvent.id.label('event_id'), rn)
        .filter(FilingEvent.company_id.in_(company_ids))
        .subquery()
    )
    latest_ids = [
        row.event_id
        for row in db.session.query(ranked.c.event_id).filter(ranked.c.rn == 1)
    ]
    latest_by_company = {
        e.company_id: e
        for e in FilingEvent.query.filter(FilingEvent.id.in_(latest_ids))
    }

    # Unread counts in one query: events newer than each company's last_read_at
    # (no read state row = never opened = full history is unread)
    unread_conditions = []
    for cid in company_ids:
        state = states.get(cid)
        if state and state.last_read_at:
            unread_conditions.append(sa.and_(
                FilingEvent.company_id == cid,
                FilingEvent.created_at > state.last_read_at,
            ))
        else:
            unread_conditions.append(FilingEvent.company_id == cid)
    unread_counts = dict(
        db.session.query(FilingEvent.company_id, sa.func.count())
        .filter(sa.or_(*unread_conditions))
        .group_by(FilingEvent.company_id)
        .all()
    )

    items = []
    for company in companies:
        state = states.get(company.id)
        latest = latest_by_company.get(company.id)
        items.append({
            'company': {
                'id': company.id,
                'ticker': company.ticker,
                'name': company.name,
                'cik': company.cik,
                'logo_url': company.logo_url,
            },
            'last_event': _event_preview(latest) if latest else None,
            'last_activity_at': _iso(latest.created_at) if latest else None,
            'unread_count': unread_counts.get(company.id, 0),
            'muted': state.muted if state else False,
            'last_read_at': _iso(state.last_read_at) if state else None,
        })

    # Inbox ordering: most recent activity first (ISO strings sort correctly),
    # then a stable re-sort floats unread companies to the top.
    items.sort(key=lambda c: c['last_activity_at'] or '', reverse=True)
    items.sort(key=lambda c: c['unread_count'] == 0)

    return jsonify({
        'items': items,
        'chats': items,  # legacy key; TODO remove after web deploy
        'total_unread': sum(c['unread_count'] for c in items),
    })


@watchlist_inbox_bp.route('/<company_id>/read', methods=['POST'])
@jwt_required()
def mark_read(company_id):
    """Mark a company's event history as read (sets last_read_at to now)."""
    user_id = get_jwt_identity()
    if company_id not in _user_company_ids(user_id):
        return jsonify({'error': 'Access denied'}), 403

    state = CompanyReadState.upsert(
        db.session, user_id, company_id,
        last_read_at=datetime.now(timezone.utc),
    )
    try:
        db.session.commit()
        return jsonify({'message': 'Marked as read', 'read_state': _read_state_payload(state)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to mark as read'}), 500


@watchlist_inbox_bp.route('/<company_id>/mute', methods=['PUT'])
@jwt_required()
def set_mute(company_id):
    """Mute or unmute alert delivery for one company."""
    user_id = get_jwt_identity()
    if company_id not in _user_company_ids(user_id):
        return jsonify({'error': 'Access denied'}), 403

    muted = (request.json or {}).get('muted')
    if not isinstance(muted, bool):
        return jsonify({'error': 'muted (boolean) is required'}), 400

    state = CompanyReadState.upsert(db.session, user_id, company_id, muted=muted)
    try:
        db.session.commit()
        return jsonify({
            'message': 'Muted' if muted else 'Unmuted',
            'read_state': _read_state_payload(state),
        })
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update mute state'}), 500
