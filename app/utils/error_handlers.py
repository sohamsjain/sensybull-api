from flask import jsonify
from sqlalchemy.exc import IntegrityError
from marshmallow import ValidationError
from werkzeug.exceptions import NotFound, BadRequest, Unauthorized, Forbidden, MethodNotAllowed


def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def handle_validation_error(e):
        return jsonify({'error': 'Validation error', 'details': e.messages}), 400

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(e):
        return jsonify({'error': 'Database integrity error', 'message': 'Duplicate or invalid data'}), 400

    @app.errorhandler(NotFound)
    def handle_not_found(e):
        return jsonify({'error': 'Resource not found'}), 404

    @app.errorhandler(BadRequest)
    def handle_bad_request(e):
        return jsonify({'error': 'Bad request', 'message': str(e.description)}), 400

    @app.errorhandler(Unauthorized)
    def handle_unauthorized(e):
        return jsonify({'error': 'Unauthorized access'}), 401

    @app.errorhandler(Forbidden)
    def handle_forbidden(e):
        return jsonify({'error': 'Access forbidden'}), 403

    @app.errorhandler(500)
    def handle_internal_error(e):
        return jsonify({'error': 'Internal server error'}), 500

    @app.errorhandler(MethodNotAllowed)
    def handle_method_not_allowed(e):
        return jsonify({'error': 'Method not allowed'}), 405
