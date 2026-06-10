# services/api/app/models/company_read_state.py
"""
CompanyReadState — per-user, per-company chat state.

Powers the chat-style watchlist experience: tracks when a user last
opened a company's event history (so unread counts can be computed)
and whether the company is muted for alert delivery.

A row is created lazily — when a company is added to a watchlist, when
the user opens the chat, or when they toggle mute. No row means the
user has never opened the chat.
"""
from datetime import datetime, timezone
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class CompanyReadState(BaseModel):
    __tablename__ = 'company_read_state'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('company.id', ondelete='CASCADE'),
        nullable=False, index=True)
    # Null = chat never opened; unread counts fall back to full history
    last_read_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    muted: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false())
    updated_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc))

    user: so.Mapped["User"] = so.relationship()  # noqa: F821
    company: so.Mapped["Company"] = so.relationship()  # noqa: F821

    __table_args__ = (
        sa.UniqueConstraint('user_id', 'company_id', name='uq_read_state_user_company'),
    )

    @staticmethod
    def upsert(session, user_id: str, company_id: str, **values) -> "CompanyReadState":
        """Get-or-create the row for (user, company), applying any values.

        Does not commit; the caller owns the transaction.
        """
        state = session.query(CompanyReadState).filter_by(
            user_id=user_id, company_id=company_id).first()
        if state is None:
            state = CompanyReadState(user_id=user_id, company_id=company_id)
            session.add(state)
        for field, value in values.items():
            setattr(state, field, value)
        return state

    def __repr__(self):
        return (f"<CompanyReadState user_id={self.user_id} company_id={self.company_id} "
                f"muted={self.muted}>")
