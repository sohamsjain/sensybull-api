from typing import Optional, List
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel
from app.models.associations import watchlist_companies


class Watchlist(BaseModel):
    __tablename__ = 'watchlist'

    name: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    user_id: so.Mapped[str] = so.mapped_column(sa.String(36), sa.ForeignKey('user.id'), nullable=False, index=True)

    user: so.Mapped["User"] = so.relationship(back_populates='watchlists')
    companies: so.Mapped[List["Company"]] = so.relationship(
        secondary=watchlist_companies, back_populates='watchlists')

    def __repr__(self):
        return f"<Watchlist name={self.name}, user_id={self.user_id}>"
