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
from sqlalchemy.exc import IntegrityError
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
    def _find(session, user_id: str, company_id: str) -> "CompanyReadState | None":
        return session.query(CompanyReadState).filter_by(
            user_id=user_id, company_id=company_id).first()

    @staticmethod
    def _get_or_create(session, user_id: str, company_id: str,
                       **defaults) -> "CompanyReadState":
        """Get the row for (user, company), creating it with defaults if missing.

        Concurrent-safe: the INSERT runs inside a savepoint, so if another
        request creates the row first, only the savepoint rolls back (the
        caller's pending changes survive) and the winning row is returned.
        Does not commit; the caller owns the transaction.
        """
        state = CompanyReadState._find(session, user_id, company_id)
        if state is not None:
            return state
        try:
            with session.begin_nested():
                state = CompanyReadState(
                    user_id=user_id, company_id=company_id, **defaults)
                session.add(state)
            return state
        except IntegrityError:
            return CompanyReadState._find(session, user_id, company_id)

    @staticmethod
    def ensure(session, user_id: str, company_id: str,
               **defaults) -> "CompanyReadState":
        """Create the row with defaults if missing; never modifies an existing row."""
        return CompanyReadState._get_or_create(
            session, user_id, company_id, **defaults)

    @staticmethod
    def upsert(session, user_id: str, company_id: str, **values) -> "CompanyReadState":
        """Get-or-create the row for (user, company), applying the given values.

        Does not commit; the caller owns the transaction.
        """
        state = CompanyReadState._get_or_create(session, user_id, company_id)
        for field, value in values.items():
            setattr(state, field, value)
        return state

    def __repr__(self):
        return (f"<CompanyReadState user_id={self.user_id} company_id={self.company_id} "
                f"muted={self.muted}>")
