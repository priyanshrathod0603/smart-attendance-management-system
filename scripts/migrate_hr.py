"""Additive, idempotent migration script for HR and Payroll tables.

Usage:
    python scripts/migrate_hr.py

Supports both SQLite and PostgreSQL without destroying existing tables or data.
"""
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from database.db import db
from sqlalchemy import inspect


def run_hr_migration() -> None:
    app = create_app()
    with app.app_context():
        print(f"Running HR database migration against dialect: {db.engine.dialect.name}")
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        
        # db.create_all() is safe and additive — it creates missing tables without altering or dropping existing ones
        db.create_all()
        
        new_inspector = inspect(db.engine)
        updated_tables = set(new_inspector.get_table_names())
        added_tables = sorted(list(updated_tables - existing_tables))
        
        print(f"Migration completed successfully.")
        print(f"  Existing tables preserved: {len(existing_tables)}")
        print(f"  New HR tables added: {len(added_tables)}")
        if added_tables:
            for t in added_tables:
                print(f"    + {t}")
        else:
            print("  (All HR tables were already present)")


if __name__ == "__main__":
    run_hr_migration()
