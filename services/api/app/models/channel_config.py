import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class ChannelConfig(BaseModel):
    """Per-user, per-channel notification configuration.

    Stores channel-specific settings (e.g. phone number, webhook URL,
    Telegram chat ID) and whether the channel has been verified.
    """

    __tablename__ = 'channel_config'
    __table_args__ = (
        sa.UniqueConstraint('user_id', 'channel', name='uq_channel_config_user_channel'),
    )

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    channel: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False,
    )
    config_json: so.Mapped[dict] = so.mapped_column(
        sa.JSON, nullable=False, default=dict,
    )
    verified: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, default=False, nullable=False,
    )

    def __repr__(self):
        return f"<ChannelConfig user={self.user_id} channel={self.channel} verified={self.verified}>"
