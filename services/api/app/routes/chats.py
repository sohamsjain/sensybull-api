# services/api/app/routes/chats.py
"""
Chat-style watchlist endpoints.

Each company on a user's watchlists is a "chat": the company's filing
events are its message history, and CompanyReadState tracks what the
user has seen and whether the chat is muted.

GET  /chats/                       chat list with unread counts + last-event previews
POST /chats/<company_id>/read      mark a company's history as read
PUT  /chats/<company_id>/mute      mute/unmute a company's alerts
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

chats_bp = Blueprint('chats', __name__)


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


def _event_preview(event: FilingEvent) -> dict:
    """Compact 'last message' payload for the chat list."""
    briefing = event.briefing_json or {}
    return {
        'id': event.id,
        'headline': briefing.get('headline') or f'{event.company_name} filed an {event.signal_type}',
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


@chats_bp.route('/', methods=['GET'])
@jwt_required()
def get_chats():
    """Chat list: every watchlist company with unread count and last event.

    Sorted with unread chats first, then by most recent activity, so the
    list reads like a messaging inbox.
    """
    user_id = get_jwt_identity()
    company_ids = _user_company_ids(user_id)
    if not company_ids:
        return jsonify({'chats': [], 'total_unread': 0})

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
    # (no read state row = chat never opened = full history is unread)
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

    chats = []
    for company in companies:
        state = states.get(company.id)
        latest = latest_by_company.get(company.id)
        chats.append({
            'company': {
                'id': company.id,
                'ticker': company.ticker,
                'name': company.name,
                'cik': company.cik,
            },
            'last_event': _event_preview(latest) if latest else None,
            'last_activity_at': _iso(latest.created_at) if latest else None,
            'unread_count': unread_counts.get(company.id, 0),
            'muted': state.muted if state else False,
            'last_read_at': _iso(state.last_read_at) if state else None,
        })

    # Inbox ordering: most recent activity first (ISO strings sort correctly),
    # then a stable re-sort floats unread chats to the top.
    chats.sort(key=lambda c: c['last_activity_at'] or '', reverse=True)
    chats.sort(key=lambda c: c['unread_count'] == 0)

    return jsonify({
        'chats': chats,
        'total_unread': sum(c['unread_count'] for c in chats),
    })


@chats_bp.route('/<company_id>/read', methods=['POST'])
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


@chats_bp.route('/<company_id>/mute', methods=['PUT'])
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
