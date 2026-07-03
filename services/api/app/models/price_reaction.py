# services/api/app/models/price_reaction.py
"""
PriceReaction — scheduled price measurement for a filing event.

Six rows per tickered FilingEvent (5m/15m/30m/1h/1d/1w after filing_date).
Rows double as a durable work queue: the reaction worker polls for
status="pending" AND measure_at <= now, so pending measurements survive
process restarts. The (filing_event_id, interval) unique constraint makes
row creation idempotent.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel

# interval label → seconds after filing_date
INTERVALS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1d": 86400,
    "1w": 7 * 86400,
}

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


class PriceReaction(BaseModel):
    __tablename__ = "price_reaction"

    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("filing_event.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: so.Mapped[str] = so.mapped_column(sa.String(10), nullable=False)
    interval: so.Mapped[str] = so.mapped_column(sa.String(8), nullable=False)
    measure_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False,
    )
    status: so.Mapped[str] = so.mapped_column(
        sa.String(12), nullable=False, default=STATUS_PENDING,
    )
    attempts: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False, default=0)

    # Last trade at/before filing time (shared across an event's intervals)
    baseline_price: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    baseline_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    # First trade at/after measure_at (may lag it: after-hours gaps)
    measured_price: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    measured_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)

    pct_change: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    atr_14: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    is_explosive: so.Mapped[bool] = so.mapped_column(sa.Boolean, nullable=False, default=False)
    error: so.Mapped[Optional[str]] = so.mapped_column(sa.String(200), nullable=True)

    filing_event: so.Mapped["FilingEvent"] = so.relationship(  # noqa: F821
        "FilingEvent", back_populates="price_reactions",
    )

    __table_args__ = (
        sa.UniqueConstraint("filing_event_id", "interval", name="uq_price_reaction_event_interval"),
        sa.Index("ix_price_reaction_due", "status", "measure_at"),
    )

    def __repr__(self):
        return (f"<PriceReaction event={self.filing_event_id} interval={self.interval} "
                f"status={self.status} pct={self.pct_change}>")
