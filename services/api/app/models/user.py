from datetime import datetime
from typing import Optional, List
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel
from werkzeug.security import generate_password_hash, check_password_hash


class User(BaseModel):
    __tablename__ = 'user'

    is_admin: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)
    name: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), unique=True, nullable=False, index=True)
    phone_number: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20), nullable=True, unique=True, index=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255), nullable=True)
    google_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), unique=True, index=True, nullable=True)
    apple_id: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100), unique=True, index=True, nullable=True)
    picture_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500), nullable=True)
    email_verified: so.Mapped[bool] = so.mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    email_verified_at: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime(timezone=True), nullable=True)

    watchlists: so.Mapped[List["Watchlist"]] = so.relationship(back_populates='user', cascade='all, delete-orphan')
    alert_preference: so.Mapped[Optional["AlertPreference"]] = so.relationship(
        back_populates='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User name={self.name}, email={self.email}>"
