import uuid
from datetime import datetime, timezone
import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db


class BaseModel(db.Model):
    __abstract__ = True

    id: so.Mapped[str] = so.mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
