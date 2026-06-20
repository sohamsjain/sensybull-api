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
    # Legacy column; frontend now uses Logo.dev URLs derived from ticker
    logo_url: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)

    filings: so.Mapped[List["Filing"]] = so.relationship(back_populates='company', cascade='all, delete-orphan')
    watchlists: so.Mapped[List["Watchlist"]] = so.relationship(
        secondary=watchlist_companies, back_populates='companies')

    def __repr__(self):
        return f"<Company name={self.name}, ticker={self.ticker}, cik={self.cik}>"
