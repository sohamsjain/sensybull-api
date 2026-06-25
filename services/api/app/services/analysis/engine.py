"""
engine.py — orchestrates second-order analysis for one FilingEvent.

Pulls fundamentals, runs the matching playbook (deterministic ratios), loads the
company's current thesis + recent history, asks the LLM for an unbiased insight
and a revised thesis, then persists an EventAnalysis + a new ThesisRevision and
advances the company's current-thesis pointer.

Separated from the worker (which only owns queue plumbing) so it can be unit
tested directly with the LLM + fundamentals mocked. Must run inside an app
context. Raises on LLM failure so the caller can fall back.
"""
import logging
from datetime import datetime, timezone

import sqlalchemy as sa

from app.models.event_analysis import EventAnalysis, ANALYSIS_DONE
from app.services.analysis.llm import generate_analysis
from app.services.analysis.playbooks import run_playbook
from app.services.fundamentals import get_company_snapshot

log = logging.getLogger(__name__)

_HISTORY_LIMIT = 5


def get_current_thesis(company_id: str):
    """Return the company's current ThesisRevision (pointer, else latest), or None."""
    from app import db
    from app.models.company import Company
    from app.models.thesis_revision import ThesisRevision

    if not company_id:
        return None
    company = db.session.get(Company, company_id)
    if company and company.current_thesis_revision_id:
        rev = db.session.get(ThesisRevision, company.current_thesis_revision_id)
        if rev:
            return rev
    return (ThesisRevision.query
            .filter_by(company_id=company_id)
            .order_by(ThesisRevision.version.desc())
            .first())


def _recent_history(company_id: str, exclude_id: str, limit: int = _HISTORY_LIMIT):
    from app.models.filing_event import FilingEvent

    if not company_id:
        return []
    rows = (FilingEvent.query
            .filter(FilingEvent.company_id == company_id, FilingEvent.id != exclude_id)
            .order_by(FilingEvent.filing_date.desc().nullslast(), FilingEvent.created_at.desc())
            .limit(limit).all())
    out = []
    for r in rows:
        when = (r.filing_date or r.created_at)
        date_str = when.date().isoformat() if when else "—"
        types = ", ".join(r.event_types_json or [])
        headline = (r.briefing_json or {}).get("headline", "")
        out.append(f"{date_str} — {types}: {headline}".strip())
    return out


def analyze_event(event_id: str) -> EventAnalysis:
    """Run analysis for a persisted FilingEvent and store the result.

    Returns the persisted EventAnalysis (status 'done'). Raises if the event is
    missing or the LLM call fails (worker handles the fallback)."""
    from app import db
    from app.models.company import Company
    from app.models.filing_event import FilingEvent
    from app.models.thesis_revision import ThesisRevision

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        raise ValueError(f"analyze_event: FilingEvent {event_id} not found")

    briefing = event.briefing_json or {}
    deal_terms = briefing.get("deal_terms") or {}
    event_types = event.event_types_json or [et.type_name for et in event.event_types]

    # 1. Fundamentals (may be None for non-XBRL filers — analysis still proceeds).
    snapshot = get_company_snapshot(event.cik)

    # 2. Deterministic playbook ratios.
    pb = run_playbook(event_types, snapshot, deal_terms)

    # 3. Prior thesis + recent history for continuity.
    prior = get_current_thesis(event.company_id)
    history = _recent_history(event.company_id, event.id)

    ctx = {
        "company_name": event.company_name,
        "ticker": event.ticker,
        "event": {
            "headline": briefing.get("headline"),
            "summary": briefing.get("summary"),
            "event_types": event_types,
            "significance": briefing.get("significance"),
            "sentiment": briefing.get("sentiment"),
            "deal_terms": deal_terms,
        },
        "playbook": pb.playbook,
        "computed": pb.lines,
        "ratios": pb.ratios,
        "fundamentals": {
            "as_of": (snapshot or {}).get("as_of"),
            "metrics": (snapshot or {}).get("metrics", {}),
            "missing": (snapshot or {}).get("missing", ["all"]),
        },
        "prior_thesis": prior.narrative if prior else None,
        "history": history,
    }

    # 4. LLM interpretation + thesis revision (raises on failure → caller falls back).
    llm_out = generate_analysis(ctx)

    as_of = None
    if snapshot and snapshot.get("as_of"):
        try:
            as_of = datetime.fromisoformat(snapshot["as_of"]).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            as_of = None
    as_of = as_of or event.filing_date

    # 5. Persist a new thesis revision (only when we know the company).
    revision = None
    if event.company_id:
        next_version = (db.session.query(sa.func.max(ThesisRevision.version))
                        .filter(ThesisRevision.company_id == event.company_id).scalar() or 0) + 1
        revision = ThesisRevision(
            company_id=event.company_id,
            filing_event_id=event.id,
            version=next_version,
            narrative=llm_out["thesis_narrative"],
            change_summary=llm_out["thesis_change_summary"],
            points_json=llm_out["thesis_points"],
            as_of=as_of,
            model=llm_out.get("model"),
        )
        db.session.add(revision)
        db.session.flush()  # get revision.id

    # 6. Persist the event analysis.
    analysis = EventAnalysis(
        filing_event_id=event.id,
        metrics_json=pb.to_metrics(snapshot or {}),
        insight_json={
            "insight": llm_out["insight"],
            "bull_points": llm_out["bull_points"],
            "bear_points": llm_out["bear_points"],
            "confidence": llm_out["confidence"],
            "caveats": llm_out["caveats"],
        },
        thesis_revision_id=revision.id if revision else None,
        fundamentals_as_of=(snapshot or {}).get("as_of"),
        status=ANALYSIS_DONE,
        model=llm_out.get("model"),
    )
    db.session.add(analysis)

    event.analysis_status = ANALYSIS_DONE
    if revision:
        company = db.session.get(Company, event.company_id)
        if company:
            company.current_thesis_revision_id = revision.id

    db.session.commit()
    log.info("analysis: done event=%s playbook=%s thesis_v=%s",
             event_id, pb.playbook, revision.version if revision else "—")
    return analysis
