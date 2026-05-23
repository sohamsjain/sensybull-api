"""Idempotently add the email-verification columns on `user` and create the
`auth_token` table.

Run once after pulling this change:

    python scripts/apply_email_schema.py

This is a lightweight alternative to initialising Flask-Migrate from
scratch. If/when you adopt `flask db init` + `flask db migrate`, capture
the equivalent Alembic operations there instead.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app, db  # noqa: E402
from app.models.auth_token import AuthToken  # noqa: E402 - ensures table registered
from app.models.user import User  # noqa: E402 - ensures User class is loaded


def _column_exists(conn, table: str, column: str) -> bool:
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)
    # Portable fallback for Postgres / MySQL / others that support info_schema.
    row = conn.exec_driver_sql(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    return row is not None


def main() -> None:
    app = create_app()
    with app.app_context():
        engine = db.engine

        # 1. Create any tables defined on the models that don't exist yet.
        #    This covers `auth_token` without touching existing tables.
        db.create_all()
        print('[ok] ensured all model tables exist (auth_token created if missing)')

        # 2. Add the two new columns to `user` if they aren't already there.
        with engine.begin() as conn:
            if not _column_exists(conn, 'user', 'email_verified'):
                conn.exec_driver_sql(
                    "ALTER TABLE \"user\" ADD COLUMN email_verified BOOLEAN "
                    "NOT NULL DEFAULT 0"
                )
                print('[ok] added user.email_verified')
            else:
                print('[skip] user.email_verified already exists')

            if not _column_exists(conn, 'user', 'email_verified_at'):
                conn.exec_driver_sql(
                    "ALTER TABLE \"user\" ADD COLUMN email_verified_at TIMESTAMP NULL"
                )
                print('[ok] added user.email_verified_at')
            else:
                print('[skip] user.email_verified_at already exists')

    print('done.')


if __name__ == '__main__':
    main()
