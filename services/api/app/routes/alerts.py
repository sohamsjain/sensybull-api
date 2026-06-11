from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from app import db
from app.models.alert_preference import AlertPreference
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services.alerts.channels import all_channel_names
from app.utils.schemas import (
    AlertPreferenceSchema,
    AlertPreferenceUpdateSchema,
    NotificationSchema,
)

alerts_bp = Blueprint('alerts', __name__)
pref_schema = AlertPreferenceSchema()
pref_update_schema = AlertPreferenceUpdateSchema()
notification_schema = NotificationSchema(many=True)


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
