import logging
import re
import string
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from app import db
from app.models.alert_preference import AlertPreference
from app.models.channel_config import ChannelConfig
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services.alerts.channels import all_channel_names
from app.utils.schemas import (
    AlertPreferenceSchema,
    AlertPreferenceUpdateSchema,
    NotificationSchema,
)

log = logging.getLogger(__name__)

alerts_bp = Blueprint('alerts', __name__)
pref_schema = AlertPreferenceSchema()
pref_update_schema = AlertPreferenceUpdateSchema()
notification_schema = NotificationSchema(many=True)

# Phone validation: starts with +, 10-15 digits total
_PHONE_RE = re.compile(r'^\+\d{10,15}$')

# Channels that require verification before use
_VERIFIED_CHANNELS = {'sms', 'telegram', 'whatsapp'}

# Validation rules per channel for PUT /channels/<name>/config
_DISCORD_WEBHOOK_PREFIXES = (
    'https://discord.com/api/webhooks/',
    'https://discordapp.com/api/webhooks/',
)
_SLACK_WEBHOOK_PREFIX = 'https://hooks.slack.com/'


@alerts_bp.route('/preferences', methods=['GET'])
@jwt_required()
def get_preferences():
    """Get the current user's alert preferences (auto-creates defaults if none)."""
    user_id = get_jwt_identity()
    pref = AlertPreference.query.filter_by(user_id=user_id).first()
    if pref is None:
        pref = AlertPreference(user_id=user_id)
        db.session.add(pref)
        db.session.commit()
    return jsonify({'preferences': pref_schema.dump(pref)})


@alerts_bp.route('/preferences', methods=['PUT'])
@jwt_required()
def update_preferences():
    """Update alert preferences."""
    user_id = get_jwt_identity()
    try:
        data = pref_update_schema.load(request.json)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    # Validate channel names if provided
    if 'channels' in data:
        known = set(all_channel_names())
        unknown = set(data['channels'].keys()) - known
        if unknown:
            return jsonify({
                'error': f"Unknown channels: {', '.join(sorted(unknown))}",
                'available_channels': sorted(known),
            }), 400

    pref = AlertPreference.query.filter_by(user_id=user_id).first()
    if pref is None:
        pref = AlertPreference(user_id=user_id)
        db.session.add(pref)

    if 'enabled' in data:
        pref.enabled = data['enabled']
    if 'max_tier' in data:
        pref.max_tier = data['max_tier']
    if 'channels' in data:
        pref.channels_json = data['channels']

    try:
        db.session.commit()
        return jsonify({'message': 'Preferences updated', 'preferences': pref_schema.dump(pref)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update preferences'}), 500


@alerts_bp.route('/notifications', methods=['GET'])
@jwt_required()
def get_notifications():
    """Get paginated notification history for the current user."""
    user_id = get_jwt_identity()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = Notification.query.filter_by(user_id=user_id)

    channel = request.args.get('channel')
    if channel:
        query = query.filter_by(channel=channel)

    status = request.args.get('status')
    if status:
        query = query.filter_by(status=status)

    query = query.order_by(Notification.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'notifications': notification_schema.dump(pagination.items),
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
    })


@alerts_bp.route('/channels', methods=['GET'])
@jwt_required()
def list_channels():
    """List all available notification channel names."""
    return jsonify({'channels': all_channel_names()})


# ── Web Push subscriptions ───────────────────────────────────────────


@alerts_bp.route('/push/public-key', methods=['GET'])
@jwt_required()
def push_public_key():
    """VAPID public key the browser needs to subscribe. Null if push isn't configured."""
    return jsonify({'public_key': current_app.config.get('VAPID_PUBLIC_KEY') or None})


@alerts_bp.route('/push/subscriptions', methods=['POST'])
@jwt_required()
def create_push_subscription():
    """Register (or re-claim) a browser's push subscription for this user."""
    user_id = get_jwt_identity()
    data = request.json or {}
    endpoint = data.get('endpoint')
    keys = data.get('keys') or {}
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'error': 'endpoint and keys.p256dh/keys.auth are required'}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    created = sub is None
    if created:
        sub = PushSubscription(endpoint=endpoint, user_id=user_id,
                               p256dh=keys['p256dh'], auth=keys['auth'])
        db.session.add(sub)
    else:
        # Same browser re-subscribed (possibly under a different account)
        sub.user_id = user_id
        sub.p256dh = keys['p256dh']
        sub.auth = keys['auth']
    try:
        db.session.commit()
        return jsonify({'message': 'Subscribed'}), 201 if created else 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to save subscription'}), 500


@alerts_bp.route('/push/subscriptions', methods=['DELETE'])
@jwt_required()
def delete_push_subscription():
    """Remove this browser's push subscription."""
    user_id = get_jwt_identity()
    endpoint = (request.json or {}).get('endpoint')
    if not endpoint:
        return jsonify({'error': 'endpoint is required'}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint, user_id=user_id).first()
    if sub:
        db.session.delete(sub)
        db.session.commit()
    return jsonify({'message': 'Unsubscribed'})


# ── Channel configuration ───────────────────────────────────────────


@alerts_bp.route('/channels/<channel_name>/config', methods=['GET'])
@jwt_required()
def get_channel_config(channel_name):
    """Get the current user's configuration for a specific channel."""
    user_id = get_jwt_identity()

    known = set(all_channel_names())
    if channel_name not in known:
        return jsonify({'error': f'Unknown channel: {channel_name}'}), 404

    config = ChannelConfig.query.filter_by(
        user_id=user_id, channel=channel_name,
    ).first()

    if not config:
        return jsonify({'config': None, 'verified': False})

    return jsonify({
        'config': config.config_json,
        'verified': config.verified,
    })


@alerts_bp.route('/channels/<channel_name>/config', methods=['PUT'])
@jwt_required()
def update_channel_config(channel_name):
    """Save or update configuration for a specific channel."""
    user_id = get_jwt_identity()

    known = set(all_channel_names())
    if channel_name not in known:
        return jsonify({'error': f'Unknown channel: {channel_name}'}), 404

    # Telegram is only configurable via the link flow
    if channel_name == 'telegram':
        return jsonify({
            'error': 'Telegram is configured via the /telegram/link flow, not direct PUT',
        }), 400

    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    # ── Per-channel validation ──────────────────────────────────────
    error = _validate_channel_config(channel_name, data)
    if error:
        return jsonify({'error': error}), 400

    config = ChannelConfig.query.filter_by(
        user_id=user_id, channel=channel_name,
    ).first()

    if config is None:
        config = ChannelConfig(user_id=user_id, channel=channel_name)
        db.session.add(config)

    config.config_json = data

    # Channels that don't require verification are auto-verified
    if channel_name not in _VERIFIED_CHANNELS:
        config.verified = True
    else:
        # For SMS/WhatsApp, mark as verified on save (phone validation is enough
        # for now; a full OTP flow can be added later)
        config.verified = True

    try:
        db.session.commit()
        return jsonify({
            'message': f'{channel_name} configuration saved',
            'config': config.config_json,
            'verified': config.verified,
        })
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to save configuration'}), 500


@alerts_bp.route('/channels/<channel_name>/config', methods=['DELETE'])
@jwt_required()
def delete_channel_config(channel_name):
    """Remove a channel configuration for the current user."""
    user_id = get_jwt_identity()

    config = ChannelConfig.query.filter_by(
        user_id=user_id, channel=channel_name,
    ).first()

    if config:
        db.session.delete(config)
        db.session.commit()

    return jsonify({'message': f'{channel_name} configuration removed'})


def _validate_channel_config(channel_name: str, data: dict) -> str | None:
    """Return an error string if validation fails, else None."""
    if channel_name in ('sms', 'whatsapp'):
        phone = data.get('phone')
        if not phone:
            return 'phone is required'
        if not _PHONE_RE.match(phone):
            return 'phone must start with + followed by 10-15 digits'

    elif channel_name == 'discord':
        url = data.get('webhook_url')
        if not url:
            return 'webhook_url is required'
        if not url.startswith(_DISCORD_WEBHOOK_PREFIXES):
            return 'webhook_url must start with https://discord.com/api/webhooks/ or https://discordapp.com/api/webhooks/'

    elif channel_name == 'slack':
        url = data.get('webhook_url')
        if not url:
            return 'webhook_url is required'
        if not url.startswith(_SLACK_WEBHOOK_PREFIX):
            return 'webhook_url must start with https://hooks.slack.com/'

    elif channel_name == 'webhook':
        url = data.get('url')
        if not url:
            return 'url is required'
        if not url.startswith('https://'):
            return 'url must start with https://'

    return None


# ── Telegram link flow ───────────────────────────────────────────────


@alerts_bp.route('/telegram/link', methods=['POST'])
@jwt_required()
def telegram_link():
    """Generate a verification code for linking a Telegram account.

    The user sends /start <code> to the bot, which calls /telegram/webhook
    to complete the link.
    """
    user_id = get_jwt_identity()

    bot_token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    bot_username = current_app.config.get('TELEGRAM_BOT_USERNAME')
    if not bot_token:
        return jsonify({'error': 'Telegram bot is not configured'}), 503

    code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)

    config = ChannelConfig.query.filter_by(
        user_id=user_id, channel='telegram',
    ).first()

    if config is None:
        config = ChannelConfig(user_id=user_id, channel='telegram')
        db.session.add(config)

    config.config_json = {
        'pending_code': code,
        'code_expires': expires.isoformat(),
    }
    config.verified = False

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to generate link code'}), 500

    return jsonify({
        'code': code,
        'bot_username': bot_username or '',
        'instructions': f'Send /start {code} to the bot @{bot_username or "sensybull_bot"} on Telegram',
    })


@alerts_bp.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Receive updates from the Telegram Bot API.

    This endpoint has NO JWT auth — it is called directly by Telegram.
    The bot's webhook URL should be configured to point here.
    """
    import requests as http_requests

    update = request.json or {}
    message = update.get('message', {})
    text = message.get('text', '')
    chat = message.get('chat', {})
    chat_id = chat.get('id')

    if not text or not chat_id:
        return jsonify({'ok': True})  # Ignore non-text messages

    # Only handle /start commands with a code
    if not text.startswith('/start '):
        return jsonify({'ok': True})

    code = text.split(' ', 1)[1].strip()
    if not code:
        return jsonify({'ok': True})

    # Find the ChannelConfig with this pending code
    configs = ChannelConfig.query.filter_by(channel='telegram').all()
    matched = None
    for cfg in configs:
        config_data = cfg.config_json or {}
        if config_data.get('pending_code') == code:
            # Check expiry
            expires_str = config_data.get('code_expires')
            if expires_str:
                try:
                    expires = datetime.fromisoformat(expires_str)
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) > expires:
                        continue  # Expired
                except (ValueError, TypeError):
                    continue
            matched = cfg
            break

    if not matched:
        return jsonify({'ok': True})

    # Link the Telegram account
    matched.config_json = {'chat_id': str(chat_id)}
    matched.verified = True

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception('telegram_webhook: failed to save chat_id')
        return jsonify({'ok': True})

    # Send confirmation message to the user
    bot_token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    if bot_token:
        try:
            http_requests.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json={
                    'chat_id': chat_id,
                    'text': 'Your Telegram account has been linked to Sensybull. '
                            'You will now receive filing alerts here.',
                },
                timeout=10,
            )
        except Exception:
            log.warning('telegram_webhook: failed to send confirmation message')

    log.info('telegram_webhook: linked user=%s chat_id=%s', matched.user_id, chat_id)
    return jsonify({'ok': True})
