# services/api/app/models/push_subscription.py
"""
PushSubscription — one browser's Web Push registration for a user.

A user can have several (laptop, phone, multiple browsers). Endpoints are
globally unique per browser registration; expired ones are pruned by the
push channel when the push service returns 404/410.
"""
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class PushSubscription(BaseModel):
    __tablename__ = 'push_subscription'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    endpoint: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    p256dh: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    auth: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)

    user: so.Mapped["User"] = so.relationship()  # noqa: F821

    __table_args__ = (
        sa.Index('uq_push_subscription_endpoint', 'endpoint', unique=True),
    )

    def to_subscription_info(self) -> dict:
        """Shape expected by pywebpush."""
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }

    def __repr__(self):
        return f"<PushSubscription user_id={self.user_id} endpoint={self.endpoint[:40]}...>"
