# services/api/app/models/event_analysis.py
"""
EventAnalysis — second-order analysis attached to a single FilingEvent.

Produced by the analysis worker after the instant briefing: combines
deterministically-computed financial ratios (``metrics_json``) with an LLM
interpretation (``insight_json``) and links to the ThesisRevision this event
generated. Kept separate from ``FilingEvent.briefing_json`` (which comes from
ingest) so the real-time briefing path is never touched and analysis can be
re-run independently.
"""
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel

# Status values for EventAnalysis.status / FilingEvent.analysis_status
ANALYSIS_PENDING = "pending"
ANALYSIS_DONE = "done"
ANALYSIS_FAILED = "failed"


class EventAnalysis(BaseModel):
    __tablename__ = "event_analysis"

    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("filing_event.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Deterministic numbers: {"playbook": str, "snapshot": {...}, "ratios": {...}}
    metrics_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)
    # LLM interpretation: {"insight", "bull_points", "bear_points",
    #                      "confidence", "caveats"}
    insight_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    thesis_revision_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("thesis_revision.id", ondelete="SET NULL"),
        nullable=True,
    )
    fundamentals_as_of: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(20), nullable=True,
    )
    status: so.Mapped[str] = so.mapped_column(
        sa.String(16), nullable=False, default=ANALYSIS_DONE,
    )
    model: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), nullable=True)

    filing_event: so.Mapped["FilingEvent"] = so.relationship(  # noqa: F821
        "FilingEvent", back_populates="analysis",
    )
    thesis_revision: so.Mapped[Optional["ThesisRevision"]] = so.relationship(  # noqa: F821
        "ThesisRevision", foreign_keys=[thesis_revision_id],
    )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "metrics": self.metrics_json or {},
            "insight": self.insight_json or {},
            "fundamentals_as_of": self.fundamentals_as_of,
            "thesis_revision_id": self.thesis_revision_id,
        }

    def __repr__(self):
        return f"<EventAnalysis event={self.filing_event_id} status={self.status}>"
