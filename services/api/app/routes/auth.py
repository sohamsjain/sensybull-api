from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token, create_refresh_token, get_jwt_identity, jwt_required,
)
from marshmallow import ValidationError

from app import db, limiter
from app.models.alert_preference import AlertPreference
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user import User
from app.services.email.sender import (
    send_magic_link, send_password_changed, send_password_reset,
    send_verification, send_welcome,
)
from app.utils.auth import verify_apple_token, verify_google_token
from app.utils.schemas import (
    ChangePasswordSchema, EmailOnlySchema, ResetPasswordSchema, TokenSchema,
    UserLoginSchema, UserRegistrationSchema, UserSchema,
)
from app.utils.tokens import generate_token, hash_token

auth_bp = Blueprint('auth', __name__)
user_schema = UserSchema()
registration_schema = UserRegistrationSchema()
login_schema = UserLoginSchema()
email_only_schema = EmailOnlySchema()
token_schema = TokenSchema()
reset_password_schema = ResetPasswordSchema()
change_password_schema = ChangePasswordSchema()


# ---------- helpers ---------------------------------------------------------

def _issue_verification_email(user: User) -> None:
    """Create an email-verification token row and dispatch the email.

    Kept as a best-effort side-effect: failures must never break signup.
    """
    try:
        raw = generate_token()
        hours = current_app.config.get('EMAIL_VERIFY_TOKEN_HOURS', 24)
        token = AuthToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            purpose=AuthTokenPurpose.EMAIL_VERIFY,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            ip=request.remote_addr,
            user_agent=(request.user_agent.string if request.user_agent else None),
        )
        db.session.add(token)
        db.session.commit()
        send_verification(user, raw)
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to issue verification email for user_id=%s', user.id)


def _invalidate_tokens(user_id: str, purpose: str) -> None:
    """Mark every un-used token of the given purpose for this user as consumed."""
    AuthToken.query.filter_by(
        user_id=user_id, purpose=purpose, used_at=None,
    ).update({'used_at': datetime.now(timezone.utc)}, synchronize_session=False)


def _lookup_valid_token(raw: str, purpose: str) -> AuthToken | None:
    """Find an unused, unexpired token matching the raw value and purpose."""
    if not raw:
        return None
    hashed = hash_token(raw)
    token = AuthToken.query.filter_by(token_hash=hashed, purpose=purpose).first()
    if token is None or not token.is_valid():
        return None
    return token


# ---------- existing endpoints ---------------------------------------------

@auth_bp.route('/register', methods=['POST'])
def register():
    try:
        data = registration_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'User with this email already exists'}), 409

    user = User(name=data['name'], email=data['email'])
    user.set_password(data['password'])

    user.alert_preference = AlertPreference()

    try:
        db.session.add(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to create user'}), 500

    _issue_verification_email(user)

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({
        'message': 'User created successfully. Check your email to verify your account.',
        'user': user_schema.dump(user),
        'access_token': access_token,
        'refresh_token': refresh_token,
    }), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    try:
        data = login_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    user = User.query.filter_by(email=data['email']).first()
    if not user or not user.check_password(data['password']):
        return jsonify({'error': 'Invalid email or password'}), 401

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({
        'message': 'Login successful',
        'user': user_schema.dump(user),
        'access_token': access_token,
        'refresh_token': refresh_token
    })


@auth_bp.route('/google', methods=['POST'])
def google_login():
    token = request.json.get('token')
    if not token:
        return jsonify({'error': 'Token required'}), 400

    payload = verify_google_token(token)
    if not payload:
        return jsonify({'error': 'Invalid Google token'}), 401

    email = payload.get('email')
    name = payload.get('name', '')
    google_id = payload.get('sub')

    is_new_user = False
    user = User.query.filter_by(email=email).first()
    if not user:
        # Google has already verified the address for us.
        user = User(
            name=name, email=email, google_id=google_id,
            email_verified=True, email_verified_at=datetime.now(timezone.utc),
        )
        user.alert_preference = AlertPreference()
        db.session.add(user)
        is_new_user = True
    else:
        if not user.google_id:
            user.google_id = google_id
        # Trust Google's assertion to auto-verify a previously unverified account.
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to process Google login'}), 500

    if is_new_user:
        try:
            send_welcome(user)
        except Exception:
            current_app.logger.exception('Failed to send welcome email for user_id=%s', user.id)

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({
        'message': 'Google login successful',
        'user': user_schema.dump(user),
        'access_token': access_token,
        'refresh_token': refresh_token,
    })


@auth_bp.route('/apple', methods=['POST'])
def apple_login():
    id_token = request.json.get('id_token')
    if not id_token:
        return jsonify({'error': 'id_token required'}), 400

    payload = verify_apple_token(id_token)
    if not payload:
        return jsonify({'error': 'Invalid Apple token'}), 401

    apple_id = payload.get('sub')
    email = payload.get('email')
    if not email:
        return jsonify({'error': 'Email not provided by Apple'}), 400

    # Apple only sends the name on the very first authorization.
    user_data = request.json.get('user') or {}
    first_name = user_data.get('firstName', '')
    last_name = user_data.get('lastName', '')
    name = f"{first_name} {last_name}".strip() or email.split('@')[0]

    is_new_user = False
    user = User.query.filter_by(apple_id=apple_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()

    if not user:
        user = User(
            name=name, email=email, apple_id=apple_id,
            email_verified=True, email_verified_at=datetime.now(timezone.utc),
        )
        user.alert_preference = AlertPreference()
        db.session.add(user)
        is_new_user = True
    else:
        if not user.apple_id:
            user.apple_id = apple_id
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to process Apple login'}), 500

    if is_new_user:
        try:
            send_welcome(user)
        except Exception:
            current_app.logger.exception('Failed to send welcome email for user_id=%s', user.id)

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({
        'message': 'Apple login successful',
        'user': user_schema.dump(user),
        'access_token': access_token,
        'refresh_token': refresh_token,
    })


@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'access_token': create_access_token(identity=user_id),
        'user': user_schema.dump(user)
    })


@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'user': user_schema.dump(user)})


# ---------- magic link (passwordless email login) --------------------------

@auth_bp.route('/magic-link', methods=['POST'])
@limiter.limit('5 per hour')
def request_magic_link():
    try:
        data = email_only_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    user = User.query.filter_by(email=data['email']).first()
    if user:
        try:
            _invalidate_tokens(user.id, AuthTokenPurpose.MAGIC_LINK)
            raw = generate_token()
            minutes = current_app.config.get('MAGIC_LINK_TOKEN_MINUTES', 15)
            token = AuthToken(
                user_id=user.id,
                token_hash=hash_token(raw),
                purpose=AuthTokenPurpose.MAGIC_LINK,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
                ip=request.remote_addr,
                user_agent=(request.user_agent.string if request.user_agent else None),
            )
            db.session.add(token)
            db.session.commit()
            send_magic_link(user, raw)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                'Failed to issue magic-link token for user_id=%s', user.id
            )

    return jsonify({
        'message': 'If an account exists for that email, a sign-in link has been sent.'
    })


@auth_bp.route('/magic-link/verify', methods=['POST'])
@limiter.limit('10 per hour')
def verify_magic_link():
    try:
        data = token_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    token = _lookup_valid_token(data['token'], AuthTokenPurpose.MAGIC_LINK)
    if not token:
        return jsonify({'error': 'Invalid or expired link'}), 400

    user = User.query.get(token.user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    token.mark_used()
    _invalidate_tokens(user.id, AuthTokenPurpose.MAGIC_LINK)

    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to verify magic link'}), 500

    access_token = create_access_token(identity=user.id)
    refresh_token = create_refresh_token(identity=user.id)
    return jsonify({
        'message': 'Login successful',
        'user': user_schema.dump(user),
        'access_token': access_token,
        'refresh_token': refresh_token,
    })


# ---------- email verification ---------------------------------------------

@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    try:
        data = token_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    token = _lookup_valid_token(data['token'], AuthTokenPurpose.EMAIL_VERIFY)
    if not token:
        return jsonify({'error': 'Invalid or expired token'}), 400

    user = User.query.get(token.user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    first_verification = not user.email_verified
    user.email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    token.mark_used()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to verify email'}), 500

    if first_verification:
        try:
            send_welcome(user)
        except Exception:
            current_app.logger.exception('Failed to send welcome email for user_id=%s', user.id)

    return jsonify({'message': 'Email verified', 'user': user_schema.dump(user)})


@auth_bp.route('/resend-verification', methods=['POST'])
@limiter.limit('5 per hour')
def resend_verification():
    try:
        data = email_only_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    user = User.query.filter_by(email=data['email']).first()
    # Idempotent + enumeration-safe: always return 200. Only send when the
    # user exists and is still unverified.
    if user and not user.email_verified:
        _invalidate_tokens(user.id, AuthTokenPurpose.EMAIL_VERIFY)
        db.session.commit()
        _issue_verification_email(user)

    return jsonify({
        'message': 'If an account exists for that email, a verification link has been sent.'
    })


# ---------- forgot / reset / change password -------------------------------

@auth_bp.route('/forgot-password', methods=['POST'])
@limiter.limit('5 per hour')
def forgot_password():
    try:
        data = email_only_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    user = User.query.filter_by(email=data['email']).first()
    # Never reveal whether the account exists.
    if user and user.password_hash:
        try:
            _invalidate_tokens(user.id, AuthTokenPurpose.PASSWORD_RESET)
            raw = generate_token()
            hours = current_app.config.get('PASSWORD_RESET_TOKEN_HOURS', 1)
            token = AuthToken(
                user_id=user.id,
                token_hash=hash_token(raw),
                purpose=AuthTokenPurpose.PASSWORD_RESET,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
                ip=request.remote_addr,
                user_agent=(request.user_agent.string if request.user_agent else None),
            )
            db.session.add(token)
            db.session.commit()
            send_password_reset(user, raw)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                'Failed to issue password-reset token for user_id=%s', user.id
            )

    return jsonify({
        'message': 'If an account exists for that email, a reset link has been sent.'
    })


@auth_bp.route('/reset-password', methods=['POST'])
@limiter.limit('10 per hour')
def reset_password():
    try:
        data = reset_password_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    token = _lookup_valid_token(data['token'], AuthTokenPurpose.PASSWORD_RESET)
    if not token:
        return jsonify({'error': 'Invalid or expired token'}), 400

    user = User.query.get(token.user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    user.set_password(data['new_password'])
    token.mark_used()
    # Also kill any other outstanding reset tokens so a second link can't be used.
    _invalidate_tokens(user.id, AuthTokenPurpose.PASSWORD_RESET)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to reset password'}), 500

    try:
        send_password_changed(user)
    except Exception:
        current_app.logger.exception(
            'Failed to send password-changed email for user_id=%s', user.id
        )

    return jsonify({'message': 'Password has been reset. You can now log in.'})


@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
@limiter.limit('10 per hour')
def change_password():
    try:
        data = change_password_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if not user.check_password(data['current_password']):
        return jsonify({'error': 'Current password is incorrect'}), 401

    user.set_password(data['new_password'])
    # Kill any outstanding reset tokens - the account owner has authenticated.
    _invalidate_tokens(user.id, AuthTokenPurpose.PASSWORD_RESET)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to change password'}), 500

    try:
        send_password_changed(user)
    except Exception:
        current_app.logger.exception(
            'Failed to send password-changed email for user_id=%s', user.id
        )

    return jsonify({'message': 'Password changed successfully.'})
