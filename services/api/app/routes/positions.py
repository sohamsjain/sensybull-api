# services/api/app/routes/positions.py
"""
Position endpoints — a user's holdings and the thesis behind each.

A Position is the primitive that lets the platform reason *for* an
investor instead of merely informing them: it pins the reason for holding
a company so incoming filings can be judged against it.

GET    /positions/                 list the user's positions (with company + thesis status)
POST   /positions/                 open a position (one per company; upserts thesis on conflict)
POST   /positions/draft-thesis     AI assistant: raw notes → structured falsifiable thesis
GET    /positions/assessments      recent thesis assessments across the user's positions
GET    /positions/scorecard        track record: verdicts vs subsequent price moves
GET    /positions/<position_id>    single position
PUT    /positions/<position_id>    update size / basis / thesis / status
DELETE /positions/<position_id>    close (delete) a position
GET    /positions/<position_id>/assessments   assessment history for one position
GET    /positions/<position_id>/versions      thesis revision history
POST   /positions/<position_id>/analyst       chat with the per-position analyst

Thesis lifecycle: any change to the thesis (text or structure) bumps
thesis_version, snapshots a ThesisVersion row, resets thesis_status to
intact (old verdicts judged the old thesis), and queues a retroactive
backtest of the new thesis against the company's recent filings.
"""
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError

from app import db
from app.models.company import Company
from app.models.position import Position, THESIS_INTACT
from app.models.thesis_assessment import ThesisAssessment
from app.models.thesis_version import ThesisVersion
from app.utils.schemas import (
    AnalystRequestSchema,
    PositionSchema,
    PositionCreateSchema,
    PositionUpdateSchema,
    ThesisDraftSchema,
)

positions_bp = Blueprint("positions", __name__)

position_schema = PositionSchema()
positions_schema = PositionSchema(many=True)
create_schema = PositionCreateSchema()
update_schema = PositionUpdateSchema()
draft_schema = ThesisDraftSchema()
analyst_schema = AnalystRequestSchema()


def _thesis_text_from_structured(structured: dict) -> str:
    """Derive the free-text thesis from the structured form, so every
    consumer of the text field (triage prompt, deferral query) keeps working
    when a user only fills in the structure."""
    parts = [structured.get("core_claim") or ""]
    assumptions = structured.get("assumptions") or []
    if assumptions:
        parts.append("Assumptions: " + " ".join(
            f"({i}) {a}" for i, a in enumerate(assumptions, start=1)))
    kill = structured.get("kill_criteria") or []
    if kill:
        parts.append("Would exit if: " + "; ".join(kill))
    return " ".join(p for p in parts if p).strip()


def _apply_thesis_change(position: Position, data: dict, created: bool) -> bool:
    """Apply thesis fields from a validated payload; version on change.

    Returns True when the thesis actually changed (caller queues the
    retroactive backtest after commit).
    """
    touched = "thesis" in data or "thesis_structured" in data
    if not touched:
        return False

    new_structured = data.get("thesis_structured",
                              position.thesis_structured if not created else None)
    if "thesis" in data and data["thesis"]:
        new_text = data["thesis"]
    elif new_structured:
        # No explicit text this request — keep the text in lockstep with
        # the structure it describes.
        new_text = _thesis_text_from_structured(new_structured)
    else:
        new_text = data.get("thesis", position.thesis if not created else None)

    changed = (new_text or None) != (position.thesis or None) or \
              (new_structured or None) != (position.thesis_structured or None)
    if not changed:
        return False

    position.thesis = new_text
    position.thesis_structured = new_structured
    position.thesis_version = (position.thesis_version or 0) + 1
    db.session.add(ThesisVersion(
        position=position,
        version=position.thesis_version,
        thesis=new_text,
        thesis_structured=new_structured,
        source=data.get("thesis_source", "user"),
    ))
    # Old verdicts judged the old thesis. Unless this same request pins an
    # explicit status, a rewritten thesis starts intact again.
    if "thesis_status" not in data:
        position.thesis_status = THESIS_INTACT
        position.thesis_reviewed_at = None
    # Stale backtests of the previous version are informational only — drop
    # them so the new version gets a clean backtest. Live assessments stay:
    # they're part of the position's history.
    if not created:
        ThesisAssessment.query.filter_by(
            position_id=position.id, retroactive=True,
        ).delete(synchronize_session=False)
    return True


def _queue_backtest(position: Position) -> None:
    from app.services.thesis import engine, llm
    if llm.is_configured() and position.thesis:
        engine.trigger_retroactive_assessments(
            current_app._get_current_object(), position.id)


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
    Retroactive backtest rows are excluded unless ?include_retroactive=1.
    """
    user_id = get_jwt_identity()
    impact = request.args.get("impact")
    limit = min(request.args.get("limit", 50, type=int), 200)
    include_retro = request.args.get("include_retroactive", 0, type=int)

    q = ThesisAssessment.query.filter_by(user_id=user_id)
    if impact:
        q = q.filter(ThesisAssessment.impact == impact)
    if not include_retro:
        q = q.filter(ThesisAssessment.retroactive.is_(False))
    rows = q.order_by(ThesisAssessment.created_at.desc()).limit(limit).all()
    return jsonify({"assessments": [a.to_payload() for a in rows]})


@positions_bp.route("/scorecard", methods=["GET"])
@jwt_required()
def scorecard():
    """Track record: how the user's thesis verdicts lined up with the tape.

    Joins live (non-retroactive) assessments to the measured 1d/1w price
    reactions of the filings they judged. The headline stat this backs:
    "your thesis-break verdicts preceded an average −X% move".
    """
    from app.models.price_reaction import PriceReaction

    user_id = get_jwt_identity()
    assessments = (
        ThesisAssessment.query
        .filter_by(user_id=user_id)
        .filter(ThesisAssessment.retroactive.is_(False))
        .all()
    )

    counts: dict[str, int] = {}
    for a in assessments:
        counts[a.impact] = counts.get(a.impact, 0) + 1

    event_ids = {a.filing_event_id for a in assessments}
    reactions: dict[tuple[str, str], float] = {}
    if event_ids:
        rows = (
            PriceReaction.query
            .filter(PriceReaction.filing_event_id.in_(event_ids))
            .filter(PriceReaction.interval.in_(("1d", "1w")))
            .filter(PriceReaction.status == "done")
            .filter(PriceReaction.pct_change.isnot(None))
            .all()
        )
        reactions = {(r.filing_event_id, r.interval): r.pct_change for r in rows}

    # impact → interval → list of subsequent moves
    moves: dict[str, dict[str, list[float]]] = {}
    for a in assessments:
        for interval in ("1d", "1w"):
            pct = reactions.get((a.filing_event_id, interval))
            if pct is None:
                continue
            moves.setdefault(a.impact, {}).setdefault(interval, []).append(pct)

    avg_move = {
        impact: {
            interval: round(sum(vals) / len(vals), 2)
            for interval, vals in by_interval.items() if vals
        }
        for impact, by_interval in moves.items()
    }

    status_counts: dict[str, int] = {}
    for (status,) in (Position.query.filter_by(user_id=user_id)
                      .with_entities(Position.thesis_status).all()):
        status_counts[status] = status_counts.get(status, 0) + 1

    return jsonify({"scorecard": {
        "assessed_filings": len(assessments),
        "verdict_counts": counts,
        "avg_move_after_verdict": avg_move,
        "position_status_counts": status_counts,
    }})


@positions_bp.route("/draft-thesis", methods=["POST"])
@jwt_required()
def draft_thesis():
    """AI drafting assistant: raw investor notes → structured thesis."""
    try:
        data = draft_schema.load(request.json or {})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400

    company_name = ticker = None
    if data.get("company_id"):
        company = db.session.get(Company, data["company_id"])
        if company is not None:
            company_name, ticker = company.name, company.ticker

    from app.services.thesis import llm
    draft = llm.draft_thesis(data["raw_text"], company_name=company_name,
                             ticker=ticker, direction=data["direction"])
    if draft is None:
        return jsonify({"error": "Thesis drafting is unavailable"}), 503
    draft.pop("model", None)
    return jsonify({"draft": draft})


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
    """Thesis-assessment history for one position, newest first.

    Includes retroactive backtest rows (flagged in the payload) — for a
    single position they ARE the "how would this thesis have held up" view.
    """
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


@positions_bp.route("/<position_id>/versions", methods=["GET"])
@jwt_required()
def position_versions(position_id):
    """Thesis revision history for one position, newest first."""
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403
    rows = (
        ThesisVersion.query
        .filter_by(position_id=position.id)
        .order_by(ThesisVersion.version.desc())
        .all()
    )
    return jsonify({"versions": [v.to_payload() for v in rows]})


@positions_bp.route("/<position_id>/analyst", methods=["POST"])
@jwt_required()
def position_analyst(position_id):
    """Chat with the per-position analyst (tool-using LLM over this
    company's filings, price reactions, and past assessments).

    The client owns the conversation: it sends the full message history and
    receives the next assistant reply.
    """
    user_id = get_jwt_identity()
    position = Position.query.get_or_404(position_id)
    if position.user_id != user_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        data = analyst_schema.load(request.json or {})
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": e.messages}), 400
    if data["messages"][-1]["role"] != "user":
        return jsonify({"error": "Last message must be from the user"}), 400

    from app.services.thesis import analyst
    result = analyst.run_analyst(position, data["messages"])
    if result is None or not result.get("reply"):
        return jsonify({"error": "The analyst is unavailable"}), 503
    return jsonify({"reply": result["reply"], "tools_used": result["tools_used"]})


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
        if field in ("company_id", "thesis", "thesis_structured", "thesis_source"):
            continue
        setattr(position, field, value)
    thesis_changed = _apply_thesis_change(position, data, created)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to save position"}), 500

    if thesis_changed:
        _queue_backtest(position)

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
        if field in ("thesis", "thesis_structured", "thesis_source"):
            continue
        setattr(position, field, value)
    thesis_changed = _apply_thesis_change(position, data, created=False)

    # A manual thesis_status change is a review action — stamp it so the
    # UI can show "you last reviewed this on ...".
    if "thesis_status" in data:
        position.thesis_reviewed_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to update position"}), 500

    if thesis_changed:
        _queue_backtest(position)

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
