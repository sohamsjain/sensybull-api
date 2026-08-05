"""Tests for the watchlist inbox endpoints (/api/v1/watchlist)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

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


# ── Inbox list ────────────────────────────────────────────────────────


class TestInboxList:
    def test_requires_auth(self, client):
        resp = client.get('/api/v1/watchlist/')
        assert resp.status_code == 401

    def test_empty_without_watchlists(self, client, auth_headers):
        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['items'] == []
        assert data['total_unread'] == 0

    def test_legacy_chats_alias(self, client, auth_headers, sample_watchlist, sample_company):
        """Old frontend hits /api/v1/chats/ and reads the 'chats' key; keep both
        working until the web rename deploys."""
        resp = client.get('/api/v1/chats/', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['chats'] == data['items']
        assert len(data['chats']) == 1

    def test_company_without_events(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        data = resp.get_json()
        assert len(data['items']) == 1
        entry = data['items'][0]
        assert entry['company']['ticker'] == 'AAPL'
        assert entry['last_event'] is None
        assert entry['unread_count'] == 0
        assert entry['muted'] is False

    def test_last_event_preview_and_unread(self, client, auth_headers, db_session,
                                           sample_watchlist, sample_company):
        _make_event(db_session, sample_company, 'e1', headline='First')
        _make_event(db_session, sample_company, 'e2', headline='Latest',
                    created_at=datetime.now(timezone.utc) + timedelta(seconds=5))

        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        entry = resp.get_json()['items'][0]
        # No read state row: full history counts as unread
        assert entry['unread_count'] == 2
        assert entry['last_event']['headline'] == 'Latest'
        assert entry['last_event']['significance'] == 'High'
        assert entry['last_activity_at'] is not None

    def test_unread_respects_last_read_at(self, client, auth_headers, db_session,
                                          sample_user, sample_watchlist, sample_company):
        now = datetime.now(timezone.utc)
        _make_event(db_session, sample_company, 'old', created_at=now - timedelta(hours=2))
        _make_event(db_session, sample_company, 'new', created_at=now + timedelta(seconds=5))
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id,
            last_read_at=now - timedelta(hours=1)))
        db_session.session.commit()

        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        entry = resp.get_json()['items'][0]
        assert entry['unread_count'] == 1
        assert resp.get_json()['total_unread'] == 1

    def test_unread_entries_sort_first(self, client, auth_headers, db_session, sample_user,
                                     sample_watchlist, sample_company, sample_company_2):
        now = datetime.now(timezone.utc)
        sample_watchlist.companies.append(sample_company_2)
        # AAPL: newer activity but fully read; TSLA: older activity, unread
        _make_event(db_session, sample_company, 'aapl-1', created_at=now)
        _make_event(db_session, sample_company_2, 'tsla-1', created_at=now - timedelta(hours=3))
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, last_read_at=now + timedelta(seconds=1)))
        db_session.session.commit()

        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        entries = resp.get_json()['items']
        assert [c['company']['ticker'] for c in entries] == ['TSLA', 'AAPL']
        assert entries[0]['unread_count'] == 1
        assert entries[1]['unread_count'] == 0


# ── Mark read ────────────────────────────────────────────────────────


class TestMarkRead:
    def test_mark_read_clears_unread(self, client, auth_headers, db_session,
                                     sample_watchlist, sample_company):
        _make_event(db_session, sample_company, 'e1')

        resp = client.post(f'/api/v1/watchlist/{sample_company.id}/read', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['read_state']['last_read_at'] is not None

        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        assert resp.get_json()['items'][0]['unread_count'] == 0

    def test_mark_read_denied_outside_watchlist(self, client, auth_headers, sample_company_2,
                                                sample_watchlist):
        resp = client.post(f'/api/v1/watchlist/{sample_company_2.id}/read', headers=auth_headers)
        assert resp.status_code == 403

    def test_mark_read_preserves_mute(self, client, auth_headers, db_session, sample_user,
                                      sample_watchlist, sample_company):
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=True))
        db_session.session.commit()

        client.post(f'/api/v1/watchlist/{sample_company.id}/read', headers=auth_headers)
        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).first()
        assert state.muted is True
        assert state.last_read_at is not None


# ── Mute ─────────────────────────────────────────────────────────────


class TestMute:
    def test_mute_and_unmute(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.put(f'/api/v1/watchlist/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': True})
        assert resp.status_code == 200
        assert resp.get_json()['read_state']['muted'] is True

        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        assert resp.get_json()['items'][0]['muted'] is True

        resp = client.put(f'/api/v1/watchlist/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': False})
        assert resp.get_json()['read_state']['muted'] is False

    def test_mute_requires_boolean(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.put(f'/api/v1/watchlist/{sample_company.id}/mute',
                          headers=auth_headers, json={'muted': 'yes'})
        assert resp.status_code == 400

    def test_mute_denied_outside_watchlist(self, client, auth_headers, sample_company_2,
                                           sample_watchlist):
        resp = client.put(f'/api/v1/watchlist/{sample_company_2.id}/mute',
                          headers=auth_headers, json={'muted': True})
        assert resp.status_code == 403


# ── Bulk actions (multi-select) ──────────────────────────────────────


@pytest.fixture
def two_company_watchlist(db_session, sample_watchlist, sample_company_2):
    """sample_watchlist, extended with a second company (AAPL + TSLA)."""
    sample_watchlist.companies.append(sample_company_2)
    db_session.session.commit()
    return sample_watchlist


class TestBulkValidation:
    """Shared body validation across the three bulk endpoints."""

    ENDPOINTS = (
        ('post', '/api/v1/watchlist/read', {}),
        ('put', '/api/v1/watchlist/mute', {'muted': True}),
        ('post', '/api/v1/watchlist/remove', {}),
    )

    def _call(self, client, auth_headers, method, url, body):
        return getattr(client, method)(url, headers=auth_headers, json=body)

    @pytest.mark.parametrize('method,url,extra', ENDPOINTS)
    def test_requires_auth(self, client, method, url, extra):
        resp = getattr(client, method)(url, json={'company_ids': ['x'], **extra})
        assert resp.status_code == 401

    @pytest.mark.parametrize('method,url,extra', ENDPOINTS)
    @pytest.mark.parametrize('company_ids', [None, [], 'abc', [1, 2]])
    def test_rejects_bad_company_ids(self, client, auth_headers, sample_watchlist,
                                     method, url, extra, company_ids):
        body = dict(extra)
        if company_ids is not None:
            body['company_ids'] = company_ids
        resp = self._call(client, auth_headers, method, url, body)
        assert resp.status_code == 400

    @pytest.mark.parametrize('method,url,extra', ENDPOINTS)
    def test_rejects_oversized_batch(self, client, auth_headers, sample_watchlist,
                                     method, url, extra):
        resp = self._call(client, auth_headers, method, url,
                          {'company_ids': [f'c{i}' for i in range(501)], **extra})
        assert resp.status_code == 400

    @pytest.mark.parametrize('method,url,extra', ENDPOINTS)
    def test_denied_when_nothing_is_followed(self, client, auth_headers, sample_watchlist,
                                             sample_company_2, method, url, extra):
        resp = self._call(client, auth_headers, method, url,
                          {'company_ids': [sample_company_2.id], **extra})
        assert resp.status_code == 403

    @pytest.mark.parametrize('method,url,extra', ENDPOINTS)
    def test_unfollowed_ids_are_dropped_not_fatal(self, client, auth_headers,
                                                  sample_watchlist, sample_company,
                                                  sample_company_2, method, url, extra):
        """A stale id in the batch (removed in another tab) must not sink it."""
        resp = self._call(client, auth_headers, method, url,
                          {'company_ids': [sample_company.id, sample_company_2.id], **extra})
        assert resp.status_code == 200
        assert resp.get_json()['company_ids'] == [sample_company.id]


class TestBulkMarkRead:
    def test_clears_unread_for_all_selected(self, client, auth_headers, db_session,
                                            two_company_watchlist, sample_company,
                                            sample_company_2):
        _make_event(db_session, sample_company, 'b1')
        _make_event(db_session, sample_company_2, 'b2')

        resp = client.post('/api/v1/watchlist/read', headers=auth_headers, json={
            'company_ids': [sample_company.id, sample_company_2.id]})
        assert resp.status_code == 200
        assert resp.get_json()['updated'] == 2

        items = client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items']
        assert all(item['unread_count'] == 0 for item in items)

    def test_preserves_mute(self, client, auth_headers, db_session, sample_user,
                            two_company_watchlist, sample_company):
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=True))
        db_session.session.commit()

        client.post('/api/v1/watchlist/read', headers=auth_headers,
                    json={'company_ids': [sample_company.id]})
        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).first()
        assert state.muted is True
        assert state.last_read_at is not None

    def test_duplicate_ids_counted_once(self, client, auth_headers, sample_watchlist,
                                        sample_company):
        resp = client.post('/api/v1/watchlist/read', headers=auth_headers, json={
            'company_ids': [sample_company.id, sample_company.id]})
        assert resp.get_json()['updated'] == 1


class TestBulkMute:
    def test_mutes_and_unmutes_all_selected(self, client, auth_headers, two_company_watchlist,
                                            sample_company, sample_company_2):
        ids = [sample_company.id, sample_company_2.id]

        resp = client.put('/api/v1/watchlist/mute', headers=auth_headers,
                          json={'company_ids': ids, 'muted': True})
        assert resp.status_code == 200
        assert resp.get_json()['updated'] == 2
        items = client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items']
        assert all(item['muted'] for item in items)

        resp = client.put('/api/v1/watchlist/mute', headers=auth_headers,
                          json={'company_ids': ids, 'muted': False})
        assert resp.status_code == 200
        items = client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items']
        assert not any(item['muted'] for item in items)

    def test_requires_boolean(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.put('/api/v1/watchlist/mute', headers=auth_headers,
                          json={'company_ids': [sample_company.id], 'muted': 'yes'})
        assert resp.status_code == 400

    def test_preserves_last_read_at(self, client, auth_headers, db_session, sample_user,
                                    sample_watchlist, sample_company):
        read_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, last_read_at=read_at))
        db_session.session.commit()

        client.put('/api/v1/watchlist/mute', headers=auth_headers,
                   json={'company_ids': [sample_company.id], 'muted': True})
        state = CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).first()
        assert state.muted is True
        assert state.last_read_at is not None


class TestBulkRemove:
    def test_removes_all_selected(self, client, auth_headers, two_company_watchlist,
                                  sample_company, sample_company_2):
        resp = client.post('/api/v1/watchlist/remove', headers=auth_headers, json={
            'company_ids': [sample_company.id, sample_company_2.id]})
        assert resp.status_code == 200
        assert resp.get_json()['removed'] == 2
        assert client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items'] == []

    def test_keeps_unselected_companies(self, client, auth_headers, two_company_watchlist,
                                        sample_company, sample_company_2):
        client.post('/api/v1/watchlist/remove', headers=auth_headers,
                    json={'company_ids': [sample_company.id]})
        items = client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items']
        assert [item['company']['ticker'] for item in items] == ['TSLA']

    def test_removes_from_every_watchlist(self, client, auth_headers, db_session, sample_user,
                                          sample_watchlist, sample_company):
        """The UI shows one watchlist; removal must clear the company from all."""
        second = Watchlist(name='Other', user_id=sample_user.id)
        second.companies.append(sample_company)
        db_session.session.add(second)
        db_session.session.commit()

        resp = client.post('/api/v1/watchlist/remove', headers=auth_headers,
                           json={'company_ids': [sample_company.id]})
        assert resp.status_code == 200
        assert client.get('/api/v1/watchlist/', headers=auth_headers).get_json()['items'] == []
        assert second.companies == []

    def test_leaves_another_users_watchlist_alone(self, client, auth_headers, db_session,
                                                  sample_watchlist, sample_company):
        from app.models.user import User
        other = User(email='other@example.com', name='Other')
        other.set_password('otherpass123')
        db_session.session.add(other)
        db_session.session.commit()
        other_wl = Watchlist(name='Theirs', user_id=other.id)
        other_wl.companies.append(sample_company)
        db_session.session.add(other_wl)
        db_session.session.commit()

        client.post('/api/v1/watchlist/remove', headers=auth_headers,
                    json={'company_ids': [sample_company.id]})
        assert [c.id for c in other_wl.companies] == [sample_company.id]

    def test_read_state_survives_removal(self, client, auth_headers, db_session, sample_user,
                                         sample_watchlist, sample_company):
        """Re-adding a company shouldn't resurrect its history as unread."""
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id,
            last_read_at=datetime.now(timezone.utc)))
        db_session.session.commit()

        client.post('/api/v1/watchlist/remove', headers=auth_headers,
                    json={'company_ids': [sample_company.id]})
        assert CompanyReadState.query.filter_by(
            user_id=sample_user.id, company_id=sample_company.id).first() is not None


# ── Watchlist add initializes read state ─────────────────────────────


class TestWatchlistAddHook:
    def test_adding_company_starts_read(self, client, auth_headers, db_session, sample_user,
                                        sample_watchlist, sample_company_2):
        _make_event(db_session, sample_company_2, 'tsla-old')

        resp = client.post(f'/api/v1/watchlists/{sample_watchlist.id}/companies',
                           headers=auth_headers, json={'company_id': sample_company_2.id})
        assert resp.status_code == 200

        # Pre-existing history should not show as unread for a fresh add
        resp = client.get('/api/v1/watchlist/', headers=auth_headers)
        tsla = next(c for c in resp.get_json()['items'] if c['company']['ticker'] == 'TSLA')
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
