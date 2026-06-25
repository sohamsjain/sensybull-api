# services/api/app/models/company_fundamentals.py
"""
CompanyFundamentals — cached snapshot of a company's reported financials.

Sourced from SEC's free XBRL ``companyfacts`` API and keyed by CIK. The
snapshot is a normalized dict of the latest-reported balance-sheet / income
metrics (see ``app.services.fundamentals.extract``). Refreshed only when
stale, so the analysis worker doesn't re-download the (large) companyfacts
JSON on every filing.
"""
from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class CompanyFundamentals(BaseModel):
    __tablename__ = "company_fundamentals"

    cik: so.Mapped[str] = so.mapped_column(
        sa.String(20), nullable=False, unique=True, index=True,
    )
    # Normalized FundamentalsSnapshot (see app.services.fundamentals.extract)
    snapshot_json: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)
    # Latest reported period end (e.g. "2025-03-31") covered by the snapshot.
    as_of_period: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True)
    fetched_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<CompanyFundamentals cik={self.cik} as_of={self.as_of_period}>"
