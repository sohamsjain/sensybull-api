from datetime import datetime
from typing import Optional

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


class TokenBlocklist(BaseModel):
    """Revoked JWTs, keyed by their `jti` claim.

    A row here means the token has been explicitly revoked (e.g. on logout)
    and must be rejected even though it hasn't expired. Rows can be purged
    once `expires_at` has passed, since an expired JWT is rejected anyway.
    """
    __tablename__ = 'token_blocklist'

    jti: so.Mapped[str] = so.mapped_column(
        sa.String(36), nullable=False, unique=True, index=True,
    )
    token_type: so.Mapped[str] = so.mapped_column(
        sa.String(16), nullable=False,
    )
    user_id: so.Mapped[Optional[str]] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=True, index=True,
    )
    expires_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False,
    )

    def __repr__(self):
        return f'<TokenBlocklist jti={self.jti} type={self.token_type}>'
