# services/api/app/models/feed_view.py
"""
FeedView — a reader's saved feed lens ("Small-cap biotech, clinical news").

`filters` is the canonical dict from feed_filters.FeedFilters.to_dict():
scope, importance, event types, sectors, cap buckets, source, sentiment,
price move, time window and search text. It is re-validated through
parse_filters on every write, so a stored view is always one the feed
endpoints accept.
"""
from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel

MAX_VIEWS_PER_USER = 20


class FeedView(BaseModel):
    __tablename__ = 'feed_view'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    name: so.Mapped[str] = so.mapped_column(sa.String(60), nullable=False)
    filters: so.Mapped[dict] = so.mapped_column(sa.JSON, nullable=False)
    position: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, default=0, server_default='0')
    updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'filters': self.filters,
            'position': self.position,
        }

    def __repr__(self):
        return f"<FeedView {self.name!r} user={self.user_id}>"
