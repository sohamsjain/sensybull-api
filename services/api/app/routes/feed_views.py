# services/api/app/routes/feed_views.py
"""
Saved feed views — a reader's named filter combinations.

GET    /feed/views          the caller's views, in their order
POST   /feed/views          {name, filters} → the new view (201)
PUT    /feed/views/<id>     {name?, filters?, position?} → the updated view
DELETE /feed/views/<id>     {deleted: id}

`filters` takes the same keys as the feed endpoints' query string (scope,
important, event_type, sector, cap, source, sentiment, moved, since, q)
and is stored in canonical form, so what comes back is what the feed
endpoints will accept.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app import db
from app.models.feed_view import MAX_VIEWS_PER_USER, FeedView
from app.services.feed_filters import FilterError, parse_filters

feed_views_bp = Blueprint("feed_views", __name__)

MAX_NAME = 60


def _clean_name(raw):
    name = " ".join(str(raw or "").split())
    if not name:
        raise FilterError("name is required")
    if len(name) > MAX_NAME:
        raise FilterError(f"name is longer than {MAX_NAME} characters")
    return name


def _clean_filters(raw) -> dict:
    if not isinstance(raw, dict):
        raise FilterError("filters must be an object")
    return parse_filters(raw).to_dict()


def _own_view(view_id: str):
    view = db.session.get(FeedView, view_id)
    if view is None or view.user_id != get_jwt_identity():
        return None
    return view


@feed_views_bp.route("/views", methods=["GET"])
@jwt_required()
def list_views():
    views = (FeedView.query.filter_by(user_id=get_jwt_identity())
             .order_by(FeedView.position, FeedView.created_at).all())
    return jsonify({"views": [v.to_dict() for v in views]})


@feed_views_bp.route("/views", methods=["POST"])
@jwt_required()
def create_view():
    user_id = get_jwt_identity()
    body = request.get_json(silent=True) or {}
    try:
        name = _clean_name(body.get("name"))
        filters = _clean_filters(body.get("filters") or {})
    except FilterError as exc:
        return jsonify({"error": str(exc)}), 400

    existing = FeedView.query.filter_by(user_id=user_id).all()
    if len(existing) >= MAX_VIEWS_PER_USER:
        return jsonify({"error": f"at most {MAX_VIEWS_PER_USER} saved views"}), 409
    if any(v.name.lower() == name.lower() for v in existing):
        return jsonify({"error": "a view with that name already exists"}), 409

    view = FeedView(
        user_id=user_id, name=name, filters=filters,
        position=max((v.position for v in existing), default=-1) + 1,
    )
    db.session.add(view)
    db.session.commit()
    return jsonify({"view": view.to_dict()}), 201


@feed_views_bp.route("/views/<view_id>", methods=["PUT"])
@jwt_required()
def update_view(view_id):
    view = _own_view(view_id)
    if view is None:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        if "name" in body:
            name = _clean_name(body["name"])
            clash = (FeedView.query.filter_by(user_id=view.user_id)
                     .filter(FeedView.id != view.id).all())
            if any(v.name.lower() == name.lower() for v in clash):
                return jsonify({"error": "a view with that name already exists"}), 409
            view.name = name
        if "filters" in body:
            view.filters = _clean_filters(body["filters"])
        if "position" in body:
            if not isinstance(body["position"], int) or isinstance(body["position"], bool):
                raise FilterError("position must be an integer")
            view.position = body["position"]
    except FilterError as exc:
        return jsonify({"error": str(exc)}), 400
    db.session.commit()
    return jsonify({"view": view.to_dict()})


@feed_views_bp.route("/views/<view_id>", methods=["DELETE"])
@jwt_required()
def delete_view(view_id):
    view = _own_view(view_id)
    if view is None:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(view)
    db.session.commit()
    return jsonify({"deleted": view_id})
