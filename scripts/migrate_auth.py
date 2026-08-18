#!/usr/bin/env python3
"""One-time migration: add is_active column and backfill email_verified_at.

Safe to run repeatedly (idempotent).  Works with SQLite and PostgreSQL.
"""
from __future__ import annotations

import os
import sys

# Ensure project root is on path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from database.db import db  # noqa: E402


def migrate() -> None:
    app = create_app()
    with app.app_context():
        inspector = db.inspect(db.engine)

        # --- add is_active column if missing ---
        cols = {c["name"] for c in inspector.get_columns("auth_users")}
        if "is_active" not in cols:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                db.session.execute(db.text("ALTER TABLE auth_users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
            else:
                db.session.execute(db.text(
                    "ALTER TABLE auth_users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"
                ))
            print("  + added is_active column")

        # --- backfill email_verified_at for existing unverified users ---
        from database.auth_models import User, utcnow
        unverified = User.query.filter(User.email_verified_at.is_(None)).all()
        now = utcnow()
        for user in unverified:
            user.email_verified_at = now
        if unverified:
            print(f"  + verified {len(unverified)} existing user(s)")
        else:
            print("  . no unverified users to backfill")

        db.session.commit()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
