from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from app import db
from app.models.user import User
from app.utils.auth import verify_google_token
from app.utils.schemas import UserRegistrationSchema, UserLoginSchema, UserSchema
from marshmallow import ValidationError

auth_bp = Blueprint('auth', __name__)
user_schema = UserSchema()
registration_schema = UserRegistrationSchema()
login_schema = UserLoginSchema()


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

    try:
        db.session.add(user)
        db.session.commit()
        access_token = create_access_token(identity=user.id)
        refresh_token = create_refresh_token(identity=user.id)
        return jsonify({
            'message': 'User created successfully',
            'user': user_schema.dump(user),
            'access_token': access_token,
            'refresh_token': refresh_token
        }), 201
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to create user'}), 500


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

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(name=name, email=email, google_id=google_id)
        db.session.add(user)
    elif not user.google_id:
        user.google_id = google_id

    try:
        db.session.commit()
        access_token = create_access_token(identity=user.id)
        refresh_token = create_refresh_token(identity=user.id)
        return jsonify({
            'message': 'Google login successful',
            'user': user_schema.dump(user),
            'access_token': access_token,
            'refresh_token': refresh_token
        })
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Failed to process Google login'}), 500


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
