#!/usr/bin/env python3
"""Additive, idempotent migration script for Multi-Tenant School ERP.

Usage:
    python scripts/migrate_multitenancy.py

Safely creates the `schools` table, adds `school_id` foreign key columns
to all tenant tables across SQLite and PostgreSQL, and backfills existing
records to a default institution (school_id="DEFAULT") so zero data is lost.
"""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from database.db import db
from database.tenant_models import School
from sqlalchemy import inspect, text


TENANT_TABLES = [
    "auth_users",
    "students",
    "teachers",
    "cameras",
    "attendance",
    "unknown_faces",
    "student_attendance",
    "teacher_attendance",
    "attendance_events",
    "teacher_time_rules",
    "student_time_rules",
    "holiday_calendar",
    "weekly_off",
    "attendance_settings",
    "attendance_logs",
    "attendance_corrections",
    "camera_sessions",
    "notifications",
    "attendance_time_rules",
    "attendance_session",
    "attendance_holiday",
    "attendance_audit_log",
    "departments",
    "designations",
    "employees",
    "employee_employment_history",
    "salary_components",
    "employee_salary_structures",
    "employee_salary_components",
    "leave_types",
    "employee_leave_balances",
    "leave_requests",
    "shifts",
    "employee_shifts",
    "overtime_records",
    "employee_bonuses",
    "employee_loans",
    "loan_repayments",
    "reimbursements",
    "payroll_periods",
    "payroll_records",
    "payroll_attendance_summaries",
    "payroll_earnings",
    "payroll_deductions",
    "payslips",
    "hr_audit_logs",
]


def run_migration() -> None:
    app = create_app()
    with app.app_context():
        dialect = db.engine.dialect.name
        print(f"Running multi-tenancy migration against dialect: {dialect}")

        # 1. Create all missing tables (including 'schools')
        db.create_all()
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        # 2. Ensure default school exists
        default_school = School.query.filter_by(school_id="DEFAULT").first()
        if not default_school:
            # Check if any school exists
            default_school = School.query.first()
            if not default_school:
                default_school = School(
                    school_id="DEFAULT",
                    name="Default Institution",
                    code="DEFAULT",
                    is_active=True,
                )
                db.session.add(default_school)
                db.session.commit()
                print(f"  + Created default school (id={default_school.id}, school_id='DEFAULT')")
            else:
                print(f"  . Found existing school (id={default_school.id}, school_id='{default_school.school_id}')")
        else:
            print(f"  . Found default school (id={default_school.id})")

        # 3. Add school_id column to every tenant table if missing
        for table in TENANT_TABLES:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "school_id" not in cols:
                print(f"  + Adding school_id to {table}...")
                if dialect == "sqlite":
                    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN school_id INTEGER REFERENCES schools(id)"))
                else:
                    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS school_id INTEGER REFERENCES schools(id)"))
                db.session.commit()

        # 4. Add phone and employee_id to auth_users if missing
        if "auth_users" in existing_tables:
            user_cols = {c["name"] for c in inspector.get_columns("auth_users")}
            if "phone" not in user_cols:
                if dialect == "sqlite":
                    db.session.execute(text("ALTER TABLE auth_users ADD COLUMN phone VARCHAR(30)"))
                else:
                    db.session.execute(text("ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS phone VARCHAR(30)"))
                db.session.commit()
                print("  + Added phone to auth_users")
            if "employee_id" not in user_cols:
                if dialect == "sqlite":
                    db.session.execute(text("ALTER TABLE auth_users ADD COLUMN employee_id VARCHAR(50)"))
                else:
                    db.session.execute(text("ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS employee_id VARCHAR(50)"))
                db.session.commit()
                print("  + Added employee_id to auth_users")

        # 5. Backfill existing rows with school_id = default_school.id
        for table in TENANT_TABLES:
            if table not in existing_tables:
                continue
            try:
                res = db.session.execute(
                    text(f"UPDATE {table} SET school_id = :sid WHERE school_id IS NULL"),
                    {"sid": default_school.id},
                )
                if res.rowcount > 0:
                    print(f"  + Backfilled {res.rowcount} row(s) in {table} with school_id={default_school.id}")
            except Exception as exc:
                print(f"  [WARN] Backfill for {table} skipped: {exc}")

        db.session.commit()
        print("Multi-tenancy migration completed successfully!")


if __name__ == "__main__":
    run_migration()
