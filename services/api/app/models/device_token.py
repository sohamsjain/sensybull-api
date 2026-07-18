# services/api/app/models/device_token.py
"""
DeviceToken — one mobile device's Expo push registration for a user.

The mobile app (sensybull-app) registers its Expo push token after sign-in;
alerts are delivered through Expo's push service alongside Web Push. Tokens
are globally unique — re-registering an existing token re-claims it for the
current user (same device, different account). Dead tokens are pruned by the
push channel when Expo reports DeviceNotRegistered.
"""
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class DeviceToken(BaseModel):
    __tablename__ = 'device_token'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    # Expo push token, e.g. "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
    token: so.Mapped[str] = so.mapped_column(sa.String(255), nullable=False)
    platform: so.Mapped[str] = so.mapped_column(sa.String(16), nullable=False)  # "ios" | "android"

    user: so.Mapped["User"] = so.relationship()  # noqa: F821

    __table_args__ = (
        sa.Index('uq_device_token_token', 'token', unique=True),
    )

    def __repr__(self):
        return f"<DeviceToken user_id={self.user_id} platform={self.platform} token={self.token[:24]}...>"
