from datetime import datetime
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class Notification(BaseModel):
    __tablename__ = 'notification'
    __table_args__ = (
        sa.UniqueConstraint('user_id', 'filing_event_id', 'channel',
                            name='uq_notification_user_event_channel'),
        sa.Index('ix_notification_user_created', 'user_id', 'created_at'),
    )

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('filing_event.id', ondelete='CASCADE'),
        nullable=False, index=True)
    channel: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False, index=True)
    status: so.Mapped[str] = so.mapped_column(
        sa.String(16), nullable=False, default='pending', server_default='pending',
        index=True)
    error_message: so.Mapped[Optional[str]] = so.mapped_column(
        sa.Text, nullable=True)
    sent_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)

    user: so.Mapped["User"] = so.relationship()
    filing_event: so.Mapped["FilingEvent"] = so.relationship()

    def __repr__(self):
        return f"<Notification user_id={self.user_id} channel={self.channel} status={self.status}>"
