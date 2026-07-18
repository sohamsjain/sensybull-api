"""Tests for the mobile (sensybull-app) surface: body-delivered refresh
tokens for X-Client: mobile, header-based refresh, and Expo device tokens."""

from unittest.mock import Mock, patch

from app.models.device_token import DeviceToken


EXPO_TOKEN = 'ExponentPushToken[abc123DEF456ghi789JKL0]'


# ── Mobile auth flow ─────────────────────────────────────────────────


class TestMobileAuth:
    def test_login_returns_refresh_token_in_body_for_mobile(self, client, sample_user):
        resp = client.post('/api/v1/auth/login',
                           headers={'X-Client': 'mobile'},
                           json={'email': sample_user.email, 'password': 'testpass123'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['access_token']
        assert data['refresh_token']
        # No refresh cookie for mobile clients — the token lives in the body only
        cookies = resp.headers.getlist('Set-Cookie')
        assert not any('refresh_token_cookie' in c for c in cookies)

    def test_login_keeps_cookie_flow_for_browsers(self, client, sample_user):
        resp = client.post('/api/v1/auth/login',
                           json={'email': sample_user.email, 'password': 'testpass123'})
        assert resp.status_code == 200
        assert 'refresh_token' not in resp.get_json()
        cookies = resp.headers.getlist('Set-Cookie')
        assert any('refresh_token_cookie' in c for c in cookies)

    def test_refresh_accepts_bearer_refresh_token(self, client, sample_user):
        login = client.post('/api/v1/auth/login',
                            headers={'X-Client': 'mobile'},
                            json={'email': sample_user.email, 'password': 'testpass123'})
        refresh_token = login.get_json()['refresh_token']

        resp = client.post('/api/v1/auth/refresh',
                           headers={'Authorization': f'Bearer {refresh_token}'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['access_token']
        assert data['user']['email'] == sample_user.email

    def test_logout_revokes_bearer_refresh_token(self, client, sample_user):
        login = client.post('/api/v1/auth/login',
                            headers={'X-Client': 'mobile'},
                            json={'email': sample_user.email, 'password': 'testpass123'})
        refresh_token = login.get_json()['refresh_token']

        resp = client.post('/api/v1/auth/logout',
                           headers={'Authorization': f'Bearer {refresh_token}'})
        assert resp.status_code == 200

        # The blocklisted token no longer refreshes
        resp = client.post('/api/v1/auth/refresh',
                           headers={'Authorization': f'Bearer {refresh_token}'})
        assert resp.status_code == 401


# ── Expo device token routes ─────────────────────────────────────────


class TestDeviceTokenRoutes:
    def test_register(self, client, auth_headers, sample_user):
        resp = client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                           json={'token': EXPO_TOKEN, 'platform': 'ios'})
        assert resp.status_code == 201
        device = DeviceToken.query.filter_by(user_id=sample_user.id).first()
        assert device.token == EXPO_TOKEN
        assert device.platform == 'ios'

    def test_reregister_reclaims_token(self, client, auth_headers):
        client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                    json={'token': EXPO_TOKEN, 'platform': 'ios'})
        resp = client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                           json={'token': EXPO_TOKEN, 'platform': 'android'})
        assert resp.status_code == 200
        assert DeviceToken.query.count() == 1
        assert DeviceToken.query.first().platform == 'android'

    def test_register_rejects_bad_token(self, client, auth_headers):
        resp = client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                           json={'token': 'not-a-token', 'platform': 'ios'})
        assert resp.status_code == 400

    def test_register_rejects_bad_platform(self, client, auth_headers):
        resp = client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                           json={'token': EXPO_TOKEN, 'platform': 'windows'})
        assert resp.status_code == 400

    def test_delete(self, client, auth_headers):
        client.post('/api/v1/alerts/push/devices', headers=auth_headers,
                    json={'token': EXPO_TOKEN, 'platform': 'ios'})
        resp = client.delete('/api/v1/alerts/push/devices', headers=auth_headers,
                             json={'token': EXPO_TOKEN})
        assert resp.status_code == 200
        assert DeviceToken.query.count() == 0

    def test_requires_auth(self, client):
        resp = client.post('/api/v1/alerts/push/devices',
                           json={'token': EXPO_TOKEN, 'platform': 'ios'})
        assert resp.status_code == 401


# ── Expo delivery in PushChannel ─────────────────────────────────────


class TestExpoPushDelivery:
    def _register(self, db_session, user, token=EXPO_TOKEN):
        device = DeviceToken(user_id=user.id, token=token, platform='ios')
        db_session.session.add(device)
        db_session.session.commit()
        return device

    def test_sends_to_registered_devices(self, app, db_session, sample_user, sample_event):
        from app.services.alerts.channels.push import PushChannel
        self._register(db_session, sample_user)

        ok = Mock()
        ok.json.return_value = {'data': [{'status': 'ok'}]}
        ok.raise_for_status = Mock()
        with patch('requests.post', return_value=ok) as post:
            PushChannel().send(sample_user, sample_event, app)

        assert post.called
        messages = post.call_args.kwargs['json']
        assert messages[0]['to'] == EXPO_TOKEN
        assert messages[0]['data']['event_id'] == sample_event.id

    def test_prunes_dead_tokens(self, app, db_session, sample_user, sample_event):
        from app.services.alerts.channels.push import PushChannel
        self._register(db_session, sample_user)

        gone = Mock()
        gone.json.return_value = {'data': [{
            'status': 'error', 'message': 'not registered',
            'details': {'error': 'DeviceNotRegistered'},
        }]}
        gone.raise_for_status = Mock()
        with patch('requests.post', return_value=gone):
            # Pruning a dead token is not a delivery failure
            PushChannel().send(sample_user, sample_event, app)

        assert DeviceToken.query.count() == 0

    def test_no_devices_is_a_noop(self, app, sample_user, sample_event):
        from app.services.alerts.channels.push import PushChannel
        with patch('requests.post') as post:
            PushChannel().send(sample_user, sample_event, app)
        assert not post.called
