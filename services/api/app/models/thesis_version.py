# services/api/app/models/thesis_version.py
"""
ThesisVersion — append-only snapshot of a position's thesis at each edit.

A thesis is a living claim: as the story changes, investors revise it. Each
revision is snapshotted here so the UI can show how the thesis drifted over
time ("you started with a margin story; now it's a buyback story") and so
past assessments stay interpretable against the thesis text they judged.

One row per (position, version); version 1 is the thesis the position was
opened with.
"""
from typing import Optional

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel

# who produced this revision
SOURCE_USER = "user"      # typed/edited by the investor
SOURCE_ASSIST = "assist"  # accepted from the AI drafting assistant

VALID_SOURCE = {SOURCE_USER, SOURCE_ASSIST}


class ThesisVersion(BaseModel):
    __tablename__ = "thesis_version"

    position_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)

    # Snapshot of both thesis forms at this revision.
    thesis: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    thesis_structured: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    source: so.Mapped[str] = so.mapped_column(
        sa.String(8), nullable=False, default=SOURCE_USER, server_default=SOURCE_USER,
    )

    position: so.Mapped["Position"] = so.relationship(  # noqa: F821
        "Position", backref=so.backref("thesis_versions", lazy="dynamic",
                                       cascade="all, delete-orphan"),
    )

    __table_args__ = (
        sa.UniqueConstraint("position_id", "version", name="uq_thesis_version_position_version"),
    )

    def to_payload(self) -> dict:
        from datetime import timezone
        created = self.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return {
            "id": self.id,
            "position_id": self.position_id,
            "version": self.version,
            "thesis": self.thesis,
            "thesis_structured": self.thesis_structured,
            "source": self.source,
            "created_at": created.isoformat() if created else None,
        }

    def __repr__(self):
        return f"<ThesisVersion position={self.position_id} v{self.version}>"
