from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class ShareEvent(BaseModel):
    """Analytics record for the shareable "Track on Sensybull" funnel.

    One row per funnel step (link opened, auth started, watchlist added, ...)
    with the attribution that arrived on the link. user_id is a plain column
    (no FK) so analytics survive account deletion and anonymous events are
    first-class.
    """
    __tablename__ = 'share_event'

    event: so.Mapped[str] = so.mapped_column(sa.String(32), nullable=False, index=True)
    symbol: so.Mapped[Optional[str]] = so.mapped_column(sa.String(10), nullable=True, index=True)

    # Attribution from the shared link
    ref: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    utm_source: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    utm_medium: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    utm_campaign: so.Mapped[Optional[str]] = so.mapped_column(sa.String(64), nullable=True)
    referrer: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)

    # Client context, derived server-side (never trusted from the body)
    device: so.Mapped[Optional[str]] = so.mapped_column(sa.String(16), nullable=True)
    browser: so.Mapped[Optional[str]] = so.mapped_column(sa.String(32), nullable=True)
    country: so.Mapped[Optional[str]] = so.mapped_column(sa.String(8), nullable=True)

    logged_in: so.Mapped[bool] = so.mapped_column(sa.Boolean, nullable=False, default=False)
    user_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(36), nullable=True, index=True)

    def __repr__(self):
        return f"<ShareEvent event={self.event}, symbol={self.symbol}>"
