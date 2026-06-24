from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


class AuthTokenPurpose:
    """String-enum-like constants for AuthToken.purpose."""
    EMAIL_VERIFY = 'email_verify'
    PASSWORD_RESET = 'password_reset'
    MAGIC_LINK = 'magic_link'


class AuthToken(BaseModel):
    """Single-use token issued for email verification or password reset.

    Only the SHA-256 hash of the raw token is stored. The raw value is
    delivered to the user via email and never persisted server-side.
    """
    __tablename__ = 'auth_token'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    token_hash: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False, index=True,
    )
    purpose: so.Mapped[str] = so.mapped_column(
        sa.String(32), nullable=False, index=True,
    )
    expires_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False,
    )
    used_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
    )
    ip: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(45), nullable=True,
    )
    user_agent: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(255), nullable=True,
    )

    user: so.Mapped['User'] = so.relationship(  # noqa: F821
        'User', backref=so.backref('auth_tokens', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        sa.Index('ix_auth_token_hash_purpose', 'token_hash', 'purpose'),
    )

    def is_valid(self) -> bool:
        if self.used_at is not None:
            return False
        # expires_at is stored timezone-aware; compare against aware now.
        return self.expires_at > datetime.now(timezone.utc)

    def mark_used(self) -> None:
        self.used_at = datetime.now(timezone.utc)

    def __repr__(self):
        return f'<AuthToken purpose={self.purpose} user_id={self.user_id} used={self.used_at is not None}>'
