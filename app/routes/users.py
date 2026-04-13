from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from app import db
from app.models.user import User
from app.utils.schemas import UserSchema
from app.utils.auth import admin_required

users_bp = Blueprint('users', __name__)
user_schema = UserSchema()
users_schema = UserSchema(many=True)


@users_bp.route('/', methods=['GET'])
@jwt_required()
def get_all_users():
    users = User.query.all()
    return jsonify({'users': users_schema.dump(users), 'total': len(users)})


@users_bp.route('/<user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    current_user_id = get_jwt_identity()
    current_user = User.query.get_or_404(current_user_id)
    if user_id != current_user_id and not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    user = User.query.get_or_404(user_id)
    return jsonify({'user': user_schema.dump(user)})


@users_bp.route('/<user_id>', methods=['PUT'])
@jwt_required()
def update_user(user_id):
    current_user_id = get_jwt_identity()
    current_user = User.query.get_or_404(current_user_id)
    if user_id != current_user_id and not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    user = User.query.get_or_404(user_id)
    try:
        data = user_schema.load(request.json, partial=True)
    except ValidationError as e:
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400
    for field, value in data.items():
        if hasattr(user, field):
            setattr(user, field, value)
    try:
        db.session.commit()
        return jsonify({'message': 'User updated', 'user': user_schema.dump(user)})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to update user'}), 500


@users_bp.route('/<user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({'message': 'User deleted'})
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete user'}), 500
