from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class Filing(BaseModel):
    __tablename__ = 'filing'

    company_id: so.Mapped[str] = so.mapped_column(sa.String(36), sa.ForeignKey('company.id'), nullable=False, index=True)
    form_type: so.Mapped[str] = so.mapped_column(sa.String(20), nullable=False, index=True)
    filing_date: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True)
    period_of_report: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True)
    accession_number: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50), nullable=True, unique=True, index=True)
    description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    document_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500), nullable=True)

    company: so.Mapped["Company"] = so.relationship(back_populates='filings')

    def __repr__(self):
        return f"<Filing form_type={self.form_type}, company_id={self.company_id}>"
