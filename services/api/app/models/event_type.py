# services/api/app/models/event_type.py
"""
EventType — structured event classification linked to a FilingEvent.

Each FilingEvent can have 1-3 EventType rows. The `attributes` JSON column
stores flexible, event-specific data (e.g. deal terms for an Acquisition)
without requiring schema migrations.
"""
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class EventType(BaseModel):
    __tablename__ = "event_type"

    filing_event_id: so.Mapped[str] = so.mapped_column(
        sa.String(36),
        sa.ForeignKey("filing_event.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type_name: so.Mapped[str] = so.mapped_column(
        sa.String(100), nullable=False, index=True,
    )
    attributes: so.Mapped[Optional[dict]] = so.mapped_column(
        sa.JSON, nullable=True, default=None,
    )

    filing_event: so.Mapped["FilingEvent"] = so.relationship(  # noqa: F821
        "FilingEvent", back_populates="event_types",
    )

    def __repr__(self):
        return f"<EventType name={self.type_name} event={self.filing_event_id}>"
