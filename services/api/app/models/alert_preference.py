from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class AlertPreference(BaseModel):
    __tablename__ = 'alert_preference'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        unique=True, nullable=False, index=True)
    enabled: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true())
    max_tier: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, default=3, server_default='3')
    channels_json: so.Mapped[dict] = so.mapped_column(
        sa.JSON, nullable=False, default=lambda: {'email': True})
    updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc))

    user: so.Mapped["User"] = so.relationship(back_populates='alert_preference')

    def __repr__(self):
        return f"<AlertPreference user_id={self.user_id} enabled={self.enabled} max_tier={self.max_tier}>"
