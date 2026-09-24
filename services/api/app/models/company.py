from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel
from app.models.associations import watchlist_companies


class Company(BaseModel):
    __tablename__ = 'company'

    name: so.Mapped[str] = so.mapped_column(sa.String(200), nullable=False, index=True)
    ticker: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True, unique=True, index=True)
    cik: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True, index=True)
    sic: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True)
    state_of_incorporation: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), nullable=True)
    # True while the symbol is in FMP's listed-stock universe (the daily
    # sync-companies run), False once it drops out, NULL for rows the sync
    # hasn't judged yet (e.g. auto-created by the event subscriber).
    listed: so.Mapped[Optional[bool]] = so.mapped_column(sa.Boolean, nullable=True, index=True)
    # FMP classification, from the daily sync-companies screener run (no
    # extra calls). `sector` is always one of feed_filters.SECTORS or NULL —
    # it backs the feed's sector filter, so a vendor spelling never leaks in.
    sector: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True, index=True)
    industry: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)
    exchange: so.Mapped[Optional[str]] = so.mapped_column(sa.String(16), nullable=True)
    # Legacy column; frontend now uses Logo.dev URLs derived from ticker
    logo_url: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)

    # Market data (FMP shares outstanding, last price and market cap, daily sync)
    shares_outstanding: so.Mapped[Optional[int]] = so.mapped_column(sa.BigInteger, nullable=True)
    shares_as_of: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    last_price: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    price_updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    market_cap: so.Mapped[Optional[int]] = so.mapped_column(sa.BigInteger, nullable=True, index=True)
    # ATR(14) in dollars, refreshed lazily by the price-reaction worker
    atr_14: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    atr_updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)

    filings: so.Mapped[List["Filing"]] = so.relationship(back_populates='company', cascade='all, delete-orphan')
    watchlists: so.Mapped[List["Watchlist"]] = so.relationship(
        secondary=watchlist_companies, back_populates='companies')
    aliases: so.Mapped[List["CompanyTickerAlias"]] = so.relationship(
        back_populates='company', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Company name={self.name}, ticker={self.ticker}, cik={self.cik}>"


class CompanyTickerAlias(BaseModel):
    """Another symbol that means this company.

    Two kinds: a former ticker (the company renamed, or a feed event was
    stored under the SEC's symbol while FMP lists another) and a secondary
    share class (GOOG for Alphabet, whose row is GOOGL). A live
    Company.ticker always wins over an alias, so a reused ticker resolves
    to its current owner.
    """
    __tablename__ = 'company_ticker_alias'

    ticker: so.Mapped[str] = so.mapped_column(sa.String(10), nullable=False, unique=True, index=True)
    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('company.id', ondelete='CASCADE'), nullable=False, index=True)
    # 'former' | 'share_class'
    kind: so.Mapped[str] = so.mapped_column(sa.String(16), nullable=False)

    company: so.Mapped["Company"] = so.relationship(back_populates='aliases')

    def __repr__(self):
        return f"<CompanyTickerAlias {self.ticker} → {self.company_id} ({self.kind})>"
