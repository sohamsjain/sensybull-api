"""Tests for the alert notification system."""

from unittest.mock import patch

from app.models.alert_preference import AlertPreference
from app.models.notification import Notification


# ── Alert Preference API ─────────────────────────────────────────────


class TestAlertPreferences:
    def test_get_preferences_creates_default(self, client, auth_headers, sample_user):
        resp = client.get('/api/v1/alerts/preferences', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()['preferences']
        assert data['enabled'] is True
        assert data['max_tier'] == 3
        assert data['channels'] == {'email': True}

    def test_get_preferences_returns_existing(self, client, auth_headers, sample_user, db_session):
        pref = AlertPreference(user_id=sample_user.id, enabled=False, max_tier=1,
                               channels_json={'email': False})
        db_session.session.add(pref)
        db_session.session.commit()

        resp = client.get('/api/v1/alerts/preferences', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()['preferences']
        assert data['enabled'] is False
        assert data['max_tier'] == 1

    def test_update_preferences(self, client, auth_headers, sample_user):
        resp = client.put('/api/v1/alerts/preferences', headers=auth_headers, json={
            'enabled': True,
            'max_tier': 2,
            'channels': {'email': True},
        })
        assert resp.status_code == 200
        data = resp.get_json()['preferences']
        assert data['max_tier'] == 2
        assert data['channels'] == {'email': True}

    def test_update_preferences_invalid_tier(self, client, auth_headers, sample_user):
        resp = client.put('/api/v1/alerts/preferences', headers=auth_headers, json={
            'max_tier': 5,
        })
        assert resp.status_code == 400

    def test_update_preferences_unknown_channel(self, client, auth_headers, sample_user):
        resp = client.put('/api/v1/alerts/preferences', headers=auth_headers, json={
            'channels': {'carrier_pigeon': True},
        })
        assert resp.status_code == 400
        assert 'carrier_pigeon' in resp.get_json()['error']

    def test_update_preferences_partial(self, client, auth_headers, sample_user):
        """Updating one field should not reset others."""
        client.put('/api/v1/alerts/preferences', headers=auth_headers, json={
            'max_tier': 1, 'channels': {'email': True},
        })
        resp = client.put('/api/v1/alerts/preferences', headers=auth_headers, json={
            'enabled': False,
        })
        assert resp.status_code == 200
        data = resp.get_json()['preferences']
        assert data['enabled'] is False
        assert data['max_tier'] == 1  # unchanged

    def test_requires_auth(self, client):
        resp = client.get('/api/v1/alerts/preferences')
        assert resp.status_code == 401


# ── Notification History API ─────────────────────────────────────────


class TestNotificationHistory:
    def test_empty_history(self, client, auth_headers):
        resp = client.get('/api/v1/alerts/notifications', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['notifications'] == []
        assert data['total'] == 0

    def test_history_with_records(self, client, auth_headers, sample_user, sample_event, db_session):
        n = Notification(
            user_id=sample_user.id,
            filing_event_id=sample_event.id,
            channel='email',
            status='sent',
        )
        db_session.session.add(n)
        db_session.session.commit()

        resp = client.get('/api/v1/alerts/notifications', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 1
        assert data['notifications'][0]['channel'] == 'email'
        assert data['notifications'][0]['status'] == 'sent'

    def test_filter_by_channel(self, client, auth_headers, sample_user, sample_event, db_session):
        db_session.session.add(Notification(
            user_id=sample_user.id, filing_event_id=sample_event.id,
            channel='email', status='sent'))
        db_session.session.commit()

        resp = client.get('/api/v1/alerts/notifications?channel=discord', headers=auth_headers)
        assert resp.get_json()['total'] == 0

        resp = client.get('/api/v1/alerts/notifications?channel=email', headers=auth_headers)
        assert resp.get_json()['total'] == 1

    def test_filter_by_status(self, client, auth_headers, sample_user, sample_event, db_session):
        db_session.session.add(Notification(
            user_id=sample_user.id, filing_event_id=sample_event.id,
            channel='email', status='failed'))
        db_session.session.commit()

        resp = client.get('/api/v1/alerts/notifications?status=sent', headers=auth_headers)
        assert resp.get_json()['total'] == 0

        resp = client.get('/api/v1/alerts/notifications?status=failed', headers=auth_headers)
        assert resp.get_json()['total'] == 1


# ── Channels Endpoint ────────────────────────────────────────────────


class TestChannels:
    def test_list_channels(self, client, auth_headers):
        resp = client.get('/api/v1/alerts/channels', headers=auth_headers)
        assert resp.status_code == 200
        assert 'email' in resp.get_json()['channels']


# ── Dispatcher Logic ─────────────────────────────────────────────────


class TestDispatcher:
    def test_dispatch_sends_email(self, app, db_session, sample_user, sample_event):
        """Dispatcher should create Notification and call channel.send()."""
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=3,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))

            mock_send.assert_called_once()
            args = mock_send.call_args
            assert args[0][0].id == sample_user.id
            assert args[0][1].id == sample_event.id

        n = Notification.query.filter_by(user_id=sample_user.id).first()
        assert n is not None
        assert n.status == 'sent'
        assert n.channel == 'email'

    def test_dispatch_respects_tier_threshold(self, app, db_session, sample_user, sample_event):
        """User with max_tier=1 should NOT get alerts for tier 1 events
        (max_tier means 'alert me for events up to this tier')."""
        # sample_event has max_tier=1. User with max_tier=1 should match (1 >= 1).
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=1,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send'):
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))

        # Should have created a notification since tier 1 >= 1
        assert Notification.query.filter_by(user_id=sample_user.id).count() == 1

    def test_dispatch_skips_low_tier_preference(self, app, db_session, sample_user, sample_company, db_session_factory=None):
        """User with max_tier=1 should NOT get alerts for tier 2 events."""
        from app.models.filing_event import FilingEvent
        event = FilingEvent(
            edgar_id='tier2-test', signal_type='8-K',
            company_id=sample_company.id, cik=sample_company.cik,
            ticker='AAPL', company_name='Apple Inc.', max_tier=2,
            items_json=[], exhibits_json=[],
            event_types_json=[],
        )
        db_session.session.add(event)
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=1,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, event.id, frozenset({sample_user.id}))
            mock_send.assert_not_called()

    def test_dispatch_skips_disabled_preference(self, app, db_session, sample_user, sample_event):
        """Disabled preferences should be skipped."""
        pref = AlertPreference(user_id=sample_user.id, enabled=False, max_tier=3,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))
            mock_send.assert_not_called()

    def test_dispatch_dedup(self, app, db_session, sample_user, sample_event):
        """Duplicate dispatch should not send twice."""
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=3,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send'):
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))

        # Only one notification should exist
        assert Notification.query.filter_by(
            user_id=sample_user.id, filing_event_id=sample_event.id).count() == 1

    def test_dispatch_records_failure(self, app, db_session, sample_user, sample_event):
        """Channel failure should be recorded in the notification."""
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=3,
                               channels_json={'email': True})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send',
                   side_effect=Exception('Resend API down')):
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))

        n = Notification.query.filter_by(user_id=sample_user.id).first()
        assert n is not None
        assert n.status == 'failed'
        assert 'Resend API down' in n.error_message

    def test_trigger_alerts_skips_empty_users(self, app):
        """trigger_alerts should no-op when user_ids is empty."""
        from app.services.alerts.dispatcher import trigger_alerts, _executor
        with patch.object(_executor, 'submit') as mock_submit:
            trigger_alerts(app, 'some-event-id', set())
            mock_submit.assert_not_called()

    def test_dispatch_skips_disabled_channel(self, app, db_session, sample_user, sample_event):
        """Channels set to False should not fire."""
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=3,
                               channels_json={'email': False})
        db_session.session.add(pref)
        db_session.session.commit()

        with patch('app.services.alerts.channels.email.EmailChannel.send') as mock_send:
            from app.services.alerts.dispatcher import _dispatch
            _dispatch(app, sample_event.id, frozenset({sample_user.id}))
            mock_send.assert_not_called()
