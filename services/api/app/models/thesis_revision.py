# services/api/app/models/thesis_revision.py
"""
ThesisRevision — one entry in a company's append-only investment thesis.

The thesis is never overwritten: every material filing appends a new
revision that restates the full current story (``narrative``) plus a
``change_summary`` describing what this update added. The *current* thesis
is simply the highest-``version`` revision for the company; the ordered set
of revisions is the story arc over time.

Deliberately unbiased: ``points_json`` carries both bull and bear points and
the narrative avoids buy/sell/price-target language (see the analysis prompt
in ``app.services.analysis.llm``).
"""
from datetime import datetime
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class ThesisRevision(BaseModel):
    __tablename__ = "thesis_revision"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The filing that triggered this revision (nullable for a seed/manual thesis).
    filing_event_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("filing_event.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False, default=1)

    # Full current story (self-contained — reading the latest gives the whole thesis).
    narrative: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    # What this revision changed relative to the prior one.
    change_summary: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    # {"bull": [...], "bear": [...], "uncertainties": [...]}
    points_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    # When the thesis is "as of" (filing date or fundamentals period).
    as_of: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
    )
    model: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), nullable=True)

    company: so.Mapped["Company"] = so.relationship(  # noqa: F821
        "Company", foreign_keys=[company_id],
    )
    filing_event: so.Mapped[Optional["FilingEvent"]] = so.relationship(  # noqa: F821
        "FilingEvent", foreign_keys=[filing_event_id],
    )

    __table_args__ = (
        sa.Index("ix_thesis_revision_company_version", "company_id", "version"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "narrative": self.narrative,
            "change_summary": self.change_summary,
            "points": self.points_json or {},
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "filing_event_id": self.filing_event_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<ThesisRevision company={self.company_id} v={self.version}>"
