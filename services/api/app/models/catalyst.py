# services/api/app/models/catalyst.py
"""
Catalyst — upcoming date/event extracted from a filing briefing.

Each FilingEvent can have 0+ Catalyst rows representing key dates
that investors should track (vote dates, tender deadlines, close dates, etc.).
"""
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class Catalyst(BaseModel):
    __tablename__ = "catalyst"

    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("filing_event.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_description: so.Mapped[str] = so.mapped_column(
        sa.String(500), nullable=False,
    )
    catalyst_date: so.Mapped[Optional[sa.Date]] = so.mapped_column(
        sa.Date, nullable=True, index=True,
    )
    ticker: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(10), nullable=True, index=True,
    )
    company_name: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(500), nullable=True,
    )

    filing_event: so.Mapped["FilingEvent"] = so.relationship(  # noqa: F821
        "FilingEvent", back_populates="catalysts",
    )

    def __repr__(self):
        return f"<Catalyst date={self.catalyst_date} event={self.event_description[:40]}>"
