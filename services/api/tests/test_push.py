"""Tests for browser Web Push: subscription routes and the push channel."""

from unittest.mock import Mock, patch

from app.models.push_subscription import PushSubscription


SUB_PAYLOAD = {
    'endpoint': 'https://fcm.googleapis.com/fcm/send/abc123',
    'keys': {'p256dh': 'p256dh-key', 'auth': 'auth-key'},
}


# ── Subscription routes ──────────────────────────────────────────────


class TestPushSubscriptionRoutes:
    def test_channels_list_includes_push(self, client, auth_headers):
        resp = client.get('/api/v1/alerts/channels', headers=auth_headers)
        assert 'push' in resp.get_json()['channels']

    def test_public_key_null_when_unconfigured(self, client, auth_headers):
        resp = client.get('/api/v1/alerts/push/public-key', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['public_key'] is None

    def test_subscribe(self, client, auth_headers, sample_user):
        resp = client.post('/api/v1/alerts/push/subscriptions',
                           headers=auth_headers, json=SUB_PAYLOAD)
        assert resp.status_code == 201
        sub = PushSubscription.query.filter_by(user_id=sample_user.id).first()
        assert sub.endpoint == SUB_PAYLOAD['endpoint']
        assert sub.p256dh == 'p256dh-key'

    def test_resubscribe_same_endpoint_updates(self, client, auth_headers, sample_user):
        client.post('/api/v1/alerts/push/subscriptions',
                    headers=auth_headers, json=SUB_PAYLOAD)
        updated = {**SUB_PAYLOAD, 'keys': {'p256dh': 'new-key', 'auth': 'new-auth'}}
        resp = client.post('/api/v1/alerts/push/subscriptions',
                           headers=auth_headers, json=updated)
        assert resp.status_code == 200
        assert PushSubscription.query.count() == 1
        assert PushSubscription.query.first().p256dh == 'new-key'

    def test_subscribe_requires_keys(self, client, auth_headers):
        resp = client.post('/api/v1/alerts/push/subscriptions',
                           headers=auth_headers,
                           json={'endpoint': 'https://example.com/x'})
        assert resp.status_code == 400

    def test_unsubscribe(self, client, auth_headers, sample_user):
        client.post('/api/v1/alerts/push/subscriptions',
                    headers=auth_headers, json=SUB_PAYLOAD)
        resp = client.delete('/api/v1/alerts/push/subscriptions',
                             headers=auth_headers,
                             json={'endpoint': SUB_PAYLOAD['endpoint']})
        assert resp.status_code == 200
        assert PushSubscription.query.count() == 0

    def test_requires_auth(self, client):
        assert client.post('/api/v1/alerts/push/subscriptions',
                           json=SUB_PAYLOAD).status_code == 401


# ── Push channel delivery ────────────────────────────────────────────


def _add_subscription(db_session, user, endpoint='https://push.example/1'):
    sub = PushSubscription(user_id=user.id, endpoint=endpoint,
                           p256dh='k', auth='a')
    db_session.session.add(sub)
    db_session.session.commit()
    return sub


class TestPushChannel:
    def _send(self, app, user, event):
        from app.services.alerts.channels.push import PushChannel
        PushChannel().send(user, event, app)

    def test_send_delivers_payload(self, app, db_session, sample_user, sample_event):
        _add_subscription(db_session, sample_user)
        app.config['VAPID_PRIVATE_KEY'] = 'test-private-key'
        try:
            with patch('pywebpush.webpush') as mock_webpush:
                self._send(app, sample_user, sample_event)
            mock_webpush.assert_called_once()
            kwargs = mock_webpush.call_args.kwargs
            assert kwargs['subscription_info']['endpoint'] == 'https://push.example/1'
            assert 'Test headline' in kwargs['data']
            assert kwargs['vapid_private_key'] == 'test-private-key'
            assert kwargs['vapid_claims']['sub'].startswith('mailto:')
        finally:
            app.config['VAPID_PRIVATE_KEY'] = None

    def test_send_skips_without_vapid_key(self, app, db_session, sample_user, sample_event):
        _add_subscription(db_session, sample_user)
        with patch('pywebpush.webpush') as mock_webpush:
            self._send(app, sample_user, sample_event)
            mock_webpush.assert_not_called()

    def test_expired_subscription_pruned(self, app, db_session, sample_user, sample_event):
        from pywebpush import WebPushException
        _add_subscription(db_session, sample_user)
        app.config['VAPID_PRIVATE_KEY'] = 'test-private-key'
        gone = WebPushException('Gone', response=Mock(status_code=410))
        try:
            with patch('pywebpush.webpush', side_effect=gone):
                # Pruning is not a delivery failure — must not raise
                self._send(app, sample_user, sample_event)
            assert PushSubscription.query.count() == 0
        finally:
            app.config['VAPID_PRIVATE_KEY'] = None

    def test_all_failures_raise(self, app, db_session, sample_user, sample_event):
        from pywebpush import WebPushException
        import pytest
        _add_subscription(db_session, sample_user)
        app.config['VAPID_PRIVATE_KEY'] = 'test-private-key'
        boom = WebPushException('Server error', response=Mock(status_code=500))
        try:
            with patch('pywebpush.webpush', side_effect=boom):
                with pytest.raises(WebPushException):
                    self._send(app, sample_user, sample_event)
            # Subscription kept — transient failure, not an expired endpoint
            assert PushSubscription.query.count() == 1
        finally:
            app.config['VAPID_PRIVATE_KEY'] = None

    def test_dispatcher_routes_to_push(self, app, db_session, sample_user, sample_event):
        from app.models.alert_preference import AlertPreference
        _add_subscription(db_session, sample_user)
        pref = AlertPreference(user_id=sample_user.id, enabled=True, max_tier=3,
                               channels_json={'push': True})
        db_session.session.add(pref)
        db_session.session.commit()
        app.config['VAPID_PRIVATE_KEY'] = 'test-private-key'
        try:
            with patch('pywebpush.webpush') as mock_webpush:
                from app.services.alerts.dispatcher import _dispatch
                _dispatch(app, sample_event.id, frozenset({sample_user.id}))
                mock_webpush.assert_called_once()
        finally:
            app.config['VAPID_PRIVATE_KEY'] = None
