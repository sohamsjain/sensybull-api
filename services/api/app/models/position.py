# services/api/app/models/position.py
"""
Position — a user's stake in a company, plus the *thesis* for holding it.

This is the primitive that turns a watchlist (a list of tickers) into
something the platform can reason about. A watchlist entry says "I care
about this company"; a Position says *why* — a claim that can later be
confirmed, put on watch, or broken by an incoming filing.

One row per (user, company). The thesis text is the anchor the
thesis-break engine (see services/api/app/services/alerts) evaluates new
filing events against.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel

# thesis_status lifecycle
THESIS_INTACT = "intact"   # nothing has challenged the thesis
THESIS_WATCH = "watch"     # a filing threatens the thesis; needs review
THESIS_BROKEN = "broken"   # a filing contradicts the reason for holding

VALID_THESIS_STATUS = {THESIS_INTACT, THESIS_WATCH, THESIS_BROKEN}
VALID_DIRECTION = {"long", "short"}


class Position(BaseModel):
    __tablename__ = "position"

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # "long" or "short" — a short position inverts what counts as a threat.
    direction: so.Mapped[str] = so.mapped_column(
        sa.String(5), nullable=False, default="long", server_default="long",
    )
    # Nullable: a user can track a thesis without disclosing size/basis.
    shares: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(20, 4), nullable=True)
    cost_basis: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)

    # The reason for holding — the claim the thesis-break engine checks against.
    thesis: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    thesis_status: so.Mapped[str] = so.mapped_column(
        sa.String(12), nullable=False, default=THESIS_INTACT,
        server_default=THESIS_INTACT, index=True,
    )
    # When a filing last flipped the status, and which event did it.
    thesis_reviewed_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
    )
    last_assessment_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(36), nullable=True,
    )

    opened_at: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    notes: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)

    updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: so.Mapped["User"] = so.relationship(back_populates="positions")  # noqa: F821
    company: so.Mapped["Company"] = so.relationship(  # noqa: F821
        "Company", backref=so.backref("positions", lazy="dynamic"),
    )

    __table_args__ = (
        sa.UniqueConstraint("user_id", "company_id", name="uq_position_user_company"),
    )

    def __repr__(self):
        return (f"<Position user={self.user_id} company={self.company_id} "
                f"dir={self.direction} thesis_status={self.thesis_status}>")
