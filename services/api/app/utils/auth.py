import logging
from functools import wraps
from flask import jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.models.user import User
import requests
import jwt as pyjwt

log = logging.getLogger(__name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if not user or not user.is_admin:
                return jsonify({'error': 'Admin access required'}), 403
            return f(*args, **kwargs)
        except Exception:
            return jsonify({'error': 'Token is invalid'}), 401
    return decorated


def email_verified_required(f):
    """Require the current JWT user to have a verified email.

    Apply to routes that shouldn't be accessible until the user has
    confirmed email ownership (e.g. payments, sensitive profile changes).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if not user:
                return jsonify({'error': 'User not found'}), 404
            if not user.email_verified:
                return jsonify({
                    'error': 'Email not verified',
                    'code': 'email_not_verified',
                }), 403
            return f(*args, **kwargs)
        except Exception:
            return jsonify({'error': 'Token is invalid'}), 401
    return decorated


def get_current_user():
    try:
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        return User.query.get(user_id)
    except Exception:
        return None


def _fetch_jwk(jwks_url, kid):
    """Fetch a JWK by key ID from a JWKS endpoint."""
    response = requests.get(jwks_url, timeout=10)
    for k in response.json().get('keys', []):
        if k['kid'] == kid:
            return k
    return None


def verify_google_token(token):
    try:
        unverified_header = pyjwt.get_unverified_header(token)
        key = _fetch_jwk(
            'https://www.googleapis.com/oauth2/v3/certs',
            unverified_header.get('kid'),
        )
        if not key:
            return None
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(key)
        payload = pyjwt.decode(
            token, public_key, algorithms=['RS256'],
            audience=current_app.config['GOOGLE_CLIENT_ID']
        )
        return payload
    except Exception as e:
        log.warning("Google token verification failed: %s", e)
        return None


def exchange_google_code(code):
    """Exchange a Google authorization code for verified user info."""
    try:
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code': code,
                'client_id': current_app.config['GOOGLE_CLIENT_ID'],
                'client_secret': current_app.config['GOOGLE_CLIENT_SECRET'],
                'redirect_uri': 'postmessage',
                'grant_type': 'authorization_code',
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("Google code exchange HTTP %s: %s", resp.status_code, resp.text)
            return None
        id_token = resp.json().get('id_token')
        if not id_token:
            return None
        return verify_google_token(id_token)
    except Exception as e:
        log.warning("Google code exchange failed: %s", e)
        return None


def verify_apple_token(token):
    try:
        unverified_header = pyjwt.get_unverified_header(token)
        key = _fetch_jwk(
            'https://appleid.apple.com/auth/keys',
            unverified_header.get('kid'),
        )
        if not key:
            return None
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(key)
        payload = pyjwt.decode(
            token, public_key, algorithms=['RS256'],
            audience=current_app.config['APPLE_CLIENT_ID'],
            issuer='https://appleid.apple.com',
        )
        return payload
    except Exception as e:
        log.warning("Apple token verification failed: %s", e)
        return None
