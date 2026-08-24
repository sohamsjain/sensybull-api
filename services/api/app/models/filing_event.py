# services/api/app/models/filing_event.py
"""
FilingEvent — persisted record of a processed event: an SEC filing or,
since the press-release rollout, a newswire press release (signal_type
"PR", source = wire name, edgar_id = synthetic "pr:<wire>:<guid>").

One row per unique edgar_id. Stores the full parsed payload so the
API can serve event history without calling the source again.
"""
from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class FilingEvent(BaseModel):
    __tablename__ = "filing_event"
    # NOTE: __table_args__ is defined ONCE at the bottom of the class —
    # a second assignment would silently discard this one.

    # Ingest-side identity
    edgar_id: so.Mapped[str] = so.mapped_column(
        sa.String(500), nullable=False, unique=True, index=True,
    )
    signal_type: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False, default="8-K", index=True,
    )
    # "edgar" for SEC filings, a wire name for press releases
    source: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False, default="edgar",
        server_default="edgar", index=True,
    )
    # Wire-reported issuing organization (press releases only)
    issuer_name: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500), nullable=True)

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

    # Simple, user-facing categories, e.g. ["Strategic Transactions", "Capital & Financing"]
    event_types_json: so.Mapped[Optional[list]] = so.mapped_column(sa.JSON, nullable=True)

    # Content fingerprints for cross-source dedup (see
    # services/ingest/press_release/fingerprint.py for how they're built)
    content_fingerprint: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(64), nullable=True, index=True,
    )
    headline_fingerprint: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    content_simhash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(16), nullable=True)

    # PR→8-K backfill: when the SEC filing for an already-published press
    # release arrives, the PR event gains the filing's identifiers here and
    # the duplicate 8-K feed item is suppressed (see realtime/pr_dedup.py).
    related_edgar_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(500), nullable=True, index=True,
    )
    related_filing_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500), nullable=True)
    related_accession_number: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True)

    # Relationships
    company: so.Mapped[Optional["Company"]] = so.relationship(  # noqa: F821
        "Company", backref=so.backref("filing_events", lazy="dynamic"),
    )
    event_types: so.Mapped[list["EventType"]] = so.relationship(  # noqa: F821
        "EventType", back_populates="filing_event",
        cascade="all, delete-orphan", lazy="selectin",
    )
    catalysts: so.Mapped[list["Catalyst"]] = so.relationship(  # noqa: F821
        "Catalyst", back_populates="filing_event",
        cascade="all, delete-orphan", lazy="selectin",
    )
    price_reactions: so.Mapped[list["PriceReaction"]] = so.relationship(  # noqa: F821
        "PriceReaction", back_populates="filing_event",
        cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        # created_at (from BaseModel) is the received-order sort key for /events/all
        sa.Index("ix_filing_event_created_at", "created_at"),
        sa.Index("ix_filing_event_ticker_date", "ticker", "filing_date"),
        sa.Index("ix_filing_event_cik_date", "cik", "filing_date"),
    )

    @staticmethod
    def _utc_iso(dt: datetime | None) -> str | None:
        """Ensure a datetime serializes with a UTC offset.

        SQLite strips timezone info on storage, so datetimes read back as
        naive even though they were stored as UTC.  Re-attach the offset so
        the frontend's ``new Date()`` converts to the user's local time.
        """
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    @property
    def important(self) -> bool:
        """Binary importance flag for the frontend's All/Important filter.

        The ingest pipeline still grades filings internally (tier + LLM
        significance); the product surface collapses that to one question —
        is this the kind of event that typically moves the stock?
        """
        briefing = self.briefing_json or {}
        sig = briefing.get("significance")
        if sig is not None:
            return sig == "High"
        return self.max_tier == 1

    def to_ws_payload(self) -> dict:
        """Serialize for WebSocket delivery to the frontend."""
        return {
            "id": self.id,
            "edgar_id": self.edgar_id,
            "signal_type": self.signal_type,
            "source": self.source or "edgar",
            "issuer_name": self.issuer_name,
            # Set on PR events once the matching SEC filing arrives
            "filing_url": self.related_filing_url,
            "related_accession_number": self.related_accession_number,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "company_id": self.company_id,
            "cik": self.cik,
            "filing_date": self._utc_iso(self.filing_date),
            "edgar_url": self.edgar_url,
            "accession_number": self.accession_number,
            "max_tier": self.max_tier,
            "important": self.important,
            "items": self.items_json or [],
            "exhibits": self.exhibits_json or [],
            "briefing": self.briefing_json,
            "event_types": [et.type_name for et in self.event_types] if self.event_types else self.event_types_json or [],
            "catalysts": [
                {"event": c.event_description, "date": c.catalyst_date.isoformat() if c.catalyst_date else None}
                for c in self.catalysts
            ] if self.catalysts else [],
            "received_at": self._utc_iso(self.created_at),
            "market_cap": self.company.market_cap if self.company else None,
            "price_reactions": {
                r.interval: {
                    "pct": r.pct_change,
                    "price": float(r.measured_price) if r.measured_price is not None else None,
                    "measured_at": self._utc_iso(r.measured_at),
                    "explosive": r.is_explosive,
                }
                for r in self.price_reactions if r.status == "done"
            },
            "explosive": any(r.is_explosive for r in self.price_reactions),
        }

    def __repr__(self):
        return f"<FilingEvent ticker={self.ticker} tier={self.max_tier} id={self.id}>"


# The app imports models individually (not the app.models package), so pull
# in PriceReaction here — the string-named relationship above can't resolve
# unless the class is registered with the mapper.
from app.models.price_reaction import PriceReaction  # noqa: E402, F401
