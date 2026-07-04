# services/api/app/routes/positions.py
"""
Position endpoints — a user's holdings and the thesis behind each.

A Position is the primitive that lets the platform reason *for* an
investor instead of merely informing them: it pins the reason for holding
a company so incoming filings can be judged against it.

GET    /positions/                 list the user's positions (with company + thesis status)
POST   /positions/                 open a position (one per company; upserts thesis on conflict)
GET    /positions/<position_id>    single position
PUT    /positions/<position_id>    update size / basis / thesis / status
DELETE /positions/<position_id>    close (delete) a position
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError

from app import db
from app.models.company import Company
from app.models.position import Position
from app.models.thesis_assessment import ThesisAssessment
from app.utils.schemas import (
    PositionSchema,
    PositionCreateSchema,
    PositionUpdateSchema,
)

positions_bp = Blueprint("positions", __name__)

position_schema = PositionSchema()
positions_schema = PositionSchema(many=True)
create_schema = PositionCreateSchema()
update_schema = PositionUpdateSchema()


@positions_bp.route("/", methods=["GET"])
@jwt_required()
def list_positions():
    user_id = get_jwt_identity()
    status = request.args.get("thesis_status")  # optional: intact|watch|broken

    q = Position.query.filter_by(user_id=user_id)
    if status:
        q = q.filter(Position.thesis_status == status)
    positions = q.order_by(Position.created_at.desc()).all()
    return jsonify({"positions": positions_schema.dump(positions)})


@positions_bp.route("/assessments", methods=["GET"])
@jwt_required()
def recent_assessments():
    """Recent thesis assessments across all of the user's positions.

    Backs the "thesis alerts" surface. Optional ?impact= filter
    (supports|neutral|threatens|breaks) and ?limit= (default 50, max 200).
    """
    user_id = get_jwt_identity()
    impact = request.args.get("impact")
    limit = min(request.args.get("limit", 50, type=int), 200)

    q = ThesisAssessment.query.filter_by(user_id=user_id)
    if impact:
        q = q.filter(ThesisAssessment.impact == impact)
    rows = q.order_by(ThesisAssessment.created_at.desc()).limit(limit).all()
    return jsonify({"assessments": [a.to_payload() for a in rows]})


@positions_bp.route("/<position_id>", methods=["GET"])
@jwt_required()
def get_position(position_id):
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403
    return jsonify({"position": position_schema.dump(position)})


@positions_bp.route("/<position_id>/assessments", methods=["GET"])
@jwt_required()
def position_assessments(position_id):
    """Thesis-assessment history for one position, newest first."""
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403
    rows = (
        ThesisAssessment.query
        .filter_by(position_id=position.id)
        .order_by(ThesisAssessment.created_at.desc())
        .all()
    )
    return jsonify({"assessments": [a.to_payload() for a in rows]})


@positions_bp.route("/", methods=["POST"])
@jwt_required()
def create_position():
    user_id = get_jwt_identity()
    try:
        data = create_schema.load(request.json or {})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400

    company = db.session.get(Company, data["company_id"])
    if company is None:
        return jsonify({"error": "Company not found"}), 404

    # One position per (user, company): opening an existing one updates it
    # rather than erroring, so the "track this" action is idempotent.
    position = Position.query.filter_by(
        user_id=user_id, company_id=company.id
    ).first()
    created = position is None
    if created:
        position = Position(user_id=user_id, company_id=company.id)
        db.session.add(position)

    for field, value in data.items():
        if field == "company_id":
            continue
        setattr(position, field, value)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to save position"}), 500

    status_code = 201 if created else 200
    return jsonify({
        "message": "Position opened" if created else "Position updated",
        "position": position_schema.dump(position),
    }), status_code


@positions_bp.route("/<position_id>", methods=["PUT"])
@jwt_required()
def update_position(position_id):
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        data = update_schema.load(request.json or {}, partial=True)
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400

    for field, value in data.items():
        setattr(position, field, value)

    # A manual thesis_status change is a review action — stamp it so the
    # UI can show "you last reviewed this on ...".
    if "thesis_status" in data:
        position.thesis_reviewed_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to update position"}), 500

    return jsonify({
        "message": "Position updated",
        "position": position_schema.dump(position),
    })


@positions_bp.route("/<position_id>", methods=["DELETE"])
@jwt_required()
def delete_position(position_id):
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        db.session.delete(position)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to delete position"}), 500

    return jsonify({"message": "Position closed"})
