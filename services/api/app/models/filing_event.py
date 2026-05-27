# services/api/app/models/filing_event.py
"""
FilingEvent — persisted record of a processed SEC filing.

One row per unique EDGAR entry ID. Stores the full parsed payload so the
API can serve event history without calling EDGAR again.
"""
from datetime import datetime
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class FilingEvent(BaseModel):
    __tablename__ = "filing_event"

    # Ingest-side identity
    edgar_id: so.Mapped[str] = so.mapped_column(
        sa.String(500), nullable=False, unique=True, index=True,
    )
    signal_type: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False, default="8-K", index=True,
    )

    # Company identity (denormalized for query speed; company_id may be null
    # if the company hasn't been loaded into the companies table yet)
    company_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(36), sa.ForeignKey("company.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    cik: so.Mapped[str] = so.mapped_column(sa.String(20), nullable=False, index=True)
    ticker: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True, index=True)
    company_name: so.Mapped[str] = so.mapped_column(sa.String(500), nullable=False)

    # Filing metadata
    filing_date: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True, index=True,
    )
    edgar_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500), nullable=True)
    accession_number: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(50), nullable=True, index=True,
    )

    # Signal priority
    max_tier: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False, default=3, index=True)

    # Full parsed payload (stored as JSON for schema flexibility)
    # Shape: list of {number, title, tier, category, text}
    items_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)
    # Shape: list of {type, description, url}
    exhibits_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)
    # Shape: {headline, bullets, company_context}
    briefing_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    # LLM-classified event types, e.g. ["Acquisition", "Debt / Financing"]
    event_types_json: so.Mapped[Optional[list]] = so.mapped_column(sa.JSON, nullable=True)

    # Relationships
    company: so.Mapped[Optional["Company"]] = so.relationship(  # noqa: F821
        "Company", backref=so.backref("filing_events", lazy="dynamic"),
    )

    __table_args__ = (
        sa.Index("ix_filing_event_ticker_date", "ticker", "filing_date"),
        sa.Index("ix_filing_event_cik_date", "cik", "filing_date"),
    )

    def to_ws_payload(self) -> dict:
        """Serialize for WebSocket delivery to the frontend."""
        return {
            "id": self.id,
            "edgar_id": self.edgar_id,
            "signal_type": self.signal_type,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "company_id": self.company_id,
            "cik": self.cik,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "edgar_url": self.edgar_url,
            "accession_number": self.accession_number,
            "max_tier": self.max_tier,
            "items": self.items_json or [],
            "exhibits": self.exhibits_json or [],
            "briefing": self.briefing_json,
            "event_types": self.event_types_json or [],
            "received_at": self.created_at.isoformat(),
        }

    def __repr__(self):
        return f"<FilingEvent ticker={self.ticker} tier={self.max_tier} id={self.id}>"
