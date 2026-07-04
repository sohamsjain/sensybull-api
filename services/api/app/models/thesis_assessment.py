# services/api/app/models/thesis_assessment.py
"""
ThesisAssessment — the record of one filing event judged against one
position's thesis.

Produced by the thesis-break engine (app/services/thesis) when a filing
arrives for a company a user holds. Stores the LLM's verdict (impact +
rationale) and the thesis_status transition it drove, so the UI can show
"why your thesis moved" with a citation back to the filing.

One row per (position, filing_event) — idempotent per event.
"""
from typing import Optional

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel

# LLM verdict on what the event does to the thesis
IMPACT_SUPPORTS = "supports"    # reinforces the reason for holding
IMPACT_NEUTRAL = "neutral"      # no bearing on the thesis
IMPACT_THREATENS = "threatens"  # challenges the thesis; needs review → watch
IMPACT_BREAKS = "breaks"        # contradicts the reason for holding → broken

VALID_IMPACT = {IMPACT_SUPPORTS, IMPACT_NEUTRAL, IMPACT_THREATENS, IMPACT_BREAKS}


class ThesisAssessment(BaseModel):
    __tablename__ = "thesis_assessment"

    position_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey("filing_event.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Denormalized for "my recent assessments" queries without a join.
    user_id: so.Mapped[str] = so.mapped_column(sa.String(36), nullable=False, index=True)

    impact: so.Mapped[str] = so.mapped_column(sa.String(12), nullable=False)
    rationale: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)

    # thesis_status transition this assessment drove (may be equal if no change)
    prior_status: so.Mapped[Optional[str]] = so.mapped_column(sa.String(12), nullable=True)
    new_status: so.Mapped[Optional[str]] = so.mapped_column(sa.String(12), nullable=True)

    model: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)

    position: so.Mapped["Position"] = so.relationship(  # noqa: F821
        "Position", backref=so.backref("assessments", lazy="dynamic",
                                       cascade="all, delete-orphan"),
    )
    filing_event: so.Mapped["FilingEvent"] = so.relationship("FilingEvent")  # noqa: F821

    __table_args__ = (
        sa.UniqueConstraint("position_id", "filing_event_id",
                            name="uq_assessment_position_event"),
    )

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "position_id": self.position_id,
            "filing_event_id": self.filing_event_id,
            "impact": self.impact,
            "rationale": self.rationale,
            "prior_status": self.prior_status,
            "new_status": self.new_status,
            "created_at": self._iso(self.created_at),
        }

    @staticmethod
    def _iso(dt):
        if dt is None:
            return None
        from datetime import timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def __repr__(self):
        return (f"<ThesisAssessment position={self.position_id} "
                f"impact={self.impact} {self.prior_status}->{self.new_status}>")
