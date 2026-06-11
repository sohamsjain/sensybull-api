"""Tests for the chat-style watchlist endpoints (/api/v1/chats)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.alert_preference import AlertPreference
from app.models.company_read_state import CompanyReadState
from app.models.filing_event import FilingEvent
from app.models.notification import Notification
from app.models.watchlist import Watchlist


def _make_event(db_session, company, edgar_id, created_at=None, max_tier=2,
                headline='Test headline'):
    event = FilingEvent(
        edgar_id=edgar_id,
        signal_type='8-K',
        company_id=company.id,
        cik=company.cik,
        ticker=company.ticker,
        company_name=company.name,
        max_tier=max_tier,
        items_json=[],
        exhibits_json=[],
        briefing_json={'headline': headline, 'significance': 'High', 'sentiment': 'Neutral'},
        event_types_json=['Acquisition'],
    )
    if created_at is not None:
        event.created_at = created_at
    db_session.session.add(event)
    db_session.session.commit()
    return event


# ── Chat list ────────────────────────────────────────────────────────


class TestChatList:
    def test_requires_auth(self, client):
        resp = client.get('/api/v1/chats/')
        assert resp.status_code == 401

    def test_empty_without_watchlists(self, client, auth_headers):
        resp = client.get('/api/v1/chats/', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['chats'] == []
        assert data['total_unread'] == 0

    def test_company_without_events(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.get('/api/v1/chats/', headers=auth_headers)
        data = resp.get_json()
        assert len(data['chats']) == 1
        chat = data['chats'][0]
        assert chat['company']['ticker'] == 'AAPL'
        assert chat['last_event'] is None
        assert chat['unread_count'] == 0
        assert chat['muted'] is False

    def test_last_event_preview_and_unread(self, client, auth_headers, db_session,
                                           sample_watchlist, sample_company):
        _make_event(db_session, sample_company, 'e1', headline='First')
        _make_event(db_session, sample_company, 'e2', headline='Latest',
                    created_at=datetime.now(timezone.utc) + timedelta(seconds=5))

        resp = client.get('/api/v1/chats/', headers=auth_headers)
        chat = resp.get_json()['chats'][0]
        # No read state row: full history counts as unread
        assert chat['unread_count'] == 2
        assert chat['last_event']['headline'] == 'Latest'
        assert chat['last_event']['significance'] == 'High'
        assert chat['last_activity_at'] is not None

    def test_unread_respects_last_read_at(self, client, auth_headers, db_session,
                                          sample_user, sample_watchlist, sample_company):
        now = datetime.now(timezone.utc)
        _make_event(db_session, sample_company, 'old', created_at=now - timedelta(hours=2))
        _make_event(db_session, sample_company, 'new', created_at=now + timedelta(seconds=5))
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id,
            last_read_at=now - timedelta(hours=1)))
        db_session.session.commit()

        resp = client.get('/api/v1/chats/', headers=auth_headers)
        chat = resp.get_json()['chats'][0]
        assert chat['unread_count'] == 1
        assert resp.get_json()['total_unread'] == 1

    def test_unread_chats_sort_first(self, client, auth_headers, db_session, sample_user,
                                     sample_watchlist, sample_company, sample_company_2):
        now = datetime.now(timezone.utc)
        sample_watchlist.companies.append(sample_company_2)
        # AAPL: newer activity but fully read; TSLA: older activity, unread
        _make_event(db_session, sample_company, 'aapl-1', created_at=now)
        _make_event(db_session, sample_company_2, 'tsla-1', created_at=now - timedelta(hours=3))
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, last_read_at=now + timedelta(seconds=1)))
        db_session.session.commit()

        resp = client.get('/api/v1/chats/', headers=auth_headers)
        chats = resp.get_json()['chats']
        assert [c['company']['ticker'] for c in chats] == ['TSLA', 'AAPL']
        assert chats[0]['unread_count'] == 1
        assert chats[1]['unread_count'] == 0


# ── Mark read ────────────────────────────────────────────────────────


class TestMarkRead:
    def test_mark_read_clears_unread(self, client, auth_headers, db_session,
                                     sample_watchlist, sample_company):
        _make_event(db_session, sample_company, 'e1')

        resp = client.post(f'/api/v1/chats/{sample_company.id}/read', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['read_state']['last_read_at'] is not None

        resp = client.get('/api/v1/chats/', headers=auth_headers)
        assert resp.get_json()['chats'][0]['unread_count'] == 0

    def test_mark_read_denied_outside_watchlist(self, client, auth_headers, sample_company_2,
                                                sample_watchlist):
        resp = client.post(f'/api/v1/chats/{sample_company_2.id}/read', headers=auth_headers)
        assert resp.status_code == 403

    def test_mark_read_preserves_mute(self, client, auth_headers, db_session, sample_user,
                                      sample_watchlist, sample_company):
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=True))
        db_session.session.commit()

        client.post(f'/api/v1/chats/{sample_company.id}/read', headers=auth_headers)
        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).first()
        assert state.muted is True
        assert state.last_read_at is not None


# ── Mute ─────────────────────────────────────────────────────────────


class TestMute:
    def test_mute_and_unmute(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.put(f'/api/v1/chats/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': True})
        assert resp.status_code == 200
        assert resp.get_json()['read_state']['muted'] is True

        resp = client.get('/api/v1/chats/', headers=auth_headers)
        assert resp.get_json()['chats'][0]['muted'] is True

        resp = client.put(f'/api/v1/chats/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': False})
        assert resp.get_json()['read_state']['muted'] is False

    def test_mute_requires_boolean(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.put(f'/api/v1/chats/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': 'yes'})
        assert resp.status_code == 400

    def test_mute_denied_outside_watchlist(self, client, auth_headers, sample_company_2,
                                           sample_watchlist):
        resp = client.put(f'/api/v1/chats/{sample_company_2.id}/mute',
                          headers=auth_headers, json={'muted': True})
        assert resp.status_code == 403


# ── Watchlist add initializes read state ─────────────────────────────


class TestWatchlistAddHook:
    def test_adding_company_starts_read(self, client, auth_headers, db_session, sample_user,
                                        sample_watchlist, sample_company_2):
        _make_event(db_session, sample_company_2, 'tsla-old')

        resp = client.post(f'/api/v1/watchlists/{sample_watchlist.id}/companies',
                           headers=auth_headers, json={'company_id': sample_company_2.id})
        assert resp.status_code == 200

        # Pre-existing history should not show as unread for a fresh add
        resp = client.get('/api/v1/chats/', headers=auth_headers)
        tsla = next(c for c in resp.get_json()['chats'] if c['company']['ticker'] == 'TSLA')
        assert tsla['unread_count'] == 0

    def test_re_add_does_not_reset_state(self, client, auth_headers, db_session, sample_user,
                                         sample_watchlist, sample_company_2):
        old_read = datetime.now(timezone.utc) - timedelta(days=2)
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company_2.id,
            last_read_at=old_read, muted=True))
        wl2 = Watchlist(name='Second', user_id=sample_user.id)
        db_session.session.add(wl2)
        db_session.session.commit()

        resp = client.post(f'/api/v1/watchlists/{wl2.id}/companies',
                           headers=auth_headers, json={'company_id': sample_company_2.id})
        assert resp.status_code == 200

        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company_2.id).first()
        assert state.muted is True
        assert abs((state.last_read_at.replace(tzinfo=timezone.utc) - old_read).total_seconds()) < 1


# ── Concurrent read-state creation ───────────────────────────────────


class TestReadStateRace:
    """Two requests can race to create the same (user, company) row; the
    loser's INSERT must recover via the unique constraint, not 500."""

    def _patch_find_miss_once(self):
        """Make the first _find lookup miss, simulating a row created by a
        concurrent request between the SELECT and the INSERT."""
        real_find = CompanyReadState._find
        calls = []

        def fake_find(session, user_id, company_id):
            if not calls:
                calls.append(1)
                return None
            return real_find(session, user_id, company_id)

        return patch.object(CompanyReadState, '_find', staticmethod(fake_find))

    def test_upsert_recovers_from_lost_race(self, db_session, sample_user, sample_company):
        existing = CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=False)
        db_session.session.add(existing)
        db_session.session.commit()

        with self._patch_find_miss_once():
            state = CompanyReadState.upsert(
                db_session.session, sample_user.id, sample_company.id, muted=True)
        db_session.session.commit()

        assert state.id == existing.id
        assert state.muted is True
        assert CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).count() == 1

    def test_ensure_lost_race_preserves_existing_row(self, db_session, sample_user, sample_company):
        old_read = datetime.now(timezone.utc) - timedelta(days=3)
        existing = CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id,
            last_read_at=old_read, muted=True)
        db_session.session.add(existing)
        db_session.session.commit()

        with self._patch_find_miss_once():
            state = CompanyReadState.ensure(
                db_session.session, sample_user.id, sample_company.id,
                last_read_at=datetime.now(timezone.utc))
        db_session.session.commit()

        assert state.id == existing.id
        assert state.muted is True
        assert abs((state.last_read_at.replace(tzinfo=timezone.utc) - old_read).total_seconds()) < 1

    def test_lost_race_keeps_callers_pending_changes(self, client, auth_headers, db_session,
                                                     sample_user, sample_watchlist,
                                                     sample_company_2):
        """A lost race inside add_company must not roll back the watchlist append."""
        existing = CompanyReadState(
            user_id=sample_user.id, company_id=sample_company_2.id, muted=True)
        db_session.session.add(existing)
        db_session.session.commit()

        with self._patch_find_miss_once():
            resp = client.post(
                f'/api/v1/watchlists/{sample_watchlist.id}/companies',
                headers=auth_headers, json={'company_id': sample_company_2.id})

        assert resp.status_code == 200
        tickers = {c['ticker'] for c in resp.get_json()['watchlist']['companies']}
        assert 'TSLA' in tickers
        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company_2.id).first()
        assert state.muted is True  # existing row untouched


# ── Dispatcher respects mute ─────────────────────────────────────────


class TestDispatcherMute:
    def _pref(self, db_session, user):
        pref = AlertPreference(user_id=user.id, enabled=True, max_tier=3,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

    def test_muted_company_sends_nothing(self, app, db_session, sample_user,
                                         sample_company, sample_event):
        self._pref(db_session, sample_user)
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=True))
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))
            mock_send.assert_not_called()
        assert Notification.query.count() == 0

    def test_unmuted_company_still_sends(self, app, db_session, sample_user,
                                         sample_company, sample_event):
        self._pref(db_session, sample_user)
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=False))
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))
            mock_send.assert_called_once()
