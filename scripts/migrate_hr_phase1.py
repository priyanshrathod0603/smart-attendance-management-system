"""Additive, idempotent migration script for Phase 1 HR & Organization tables.

Usage:
    python scripts/migrate_hr_phase1.py

Supports both SQLite and PostgreSQL without destroying existing tables or data.
"""
from __future__ import annotations

import os
import sys
import uuid

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from database.db import db
from database.tenant_models import School
from hr.models import (
    CostCenter, JobLevel, JobGrade, EmploymentType,
    Department, Team, Designation, Employee,
    EmployeeEmergencyContact, EmployeeDependent,
    EmployeeDepartmentHistory, EmployeeDesignationHistory,
    EmployeeManagerHistory, EmployeeStatusHistory,
    EmployeePromotion, EmployeeTimelineEvent,
)
from sqlalchemy import inspect, text


DEFAULT_EMPLOYMENT_TYPES = [
    ("full_time", "Full-time", "Standard full-time employment", True),
    ("part_time", "Part-time", "Part-time employment", True),
    ("contract", "Contract", "Contract-based employment", True),
    ("intern", "Intern", "Internship / Trainee", True),
    ("temporary", "Temporary", "Temporary / Seasonal employment", True),
    ("visiting_faculty", "Visiting Faculty", "Visiting or guest teaching faculty", False),
    ("consultant", "Consultant", "Specialist / Professional consultant", False),
]


def run_phase1_migration() -> None:
    app = create_app()
    with app.app_context():
        dialect = db.engine.dialect.name
        print(f"Running HR Phase 1 database migration against dialect: {dialect}")
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        # 1. Create missing tables (CostCenter, JobLevel, JobGrade, Team, EmploymentType, Dependents, EmergencyContacts, History, Timeline)
        db.create_all()

        new_inspector = inspect(db.engine)
        updated_tables = set(new_inspector.get_table_names())
        added_tables = sorted(list(updated_tables - existing_tables))

        print("  Table creation summary:")
        print(f"    Existing tables: {len(existing_tables)}")
        print(f"    New Phase 1 tables added: {len(added_tables)}")
        for t in added_tables:
            print(f"      + {t}")

        # 2. Add Phase 1 columns to existing tables if missing
        cols_to_add = {
            "departments": [
                ("parent_department_id", "INTEGER"),
                ("campus", "VARCHAR(120)"),
                ("cost_center_id", "INTEGER"),
            ],
            "designations": [
                ("job_level_id", "INTEGER"),
                ("job_grade_id", "INTEGER"),
            ],
            "employees": [
                ("team_id", "INTEGER"),
                ("job_grade_id", "INTEGER"),
                ("job_level_id", "INTEGER"),
                ("cost_center_id", "INTEGER"),
                ("campus", "VARCHAR(120)"),
                ("exit_date", "DATE"),
                ("exit_reason", "TEXT"),
                ("probation_status", "VARCHAR(30) DEFAULT 'not_applicable'"),
                ("probation_start_date", "DATE"),
                ("probation_end_date", "DATE"),
                ("probation_duration_days", "INTEGER"),
                ("probation_extension_reason", "TEXT"),
                ("notice_period_days", "INTEGER DEFAULT 30"),
                ("notice_period_unit", "VARCHAR(20) DEFAULT 'days'"),
                ("custom_fields", "JSON" if dialect == "sqlite" else "JSONB"),
                ("qr_identifier", "VARCHAR(64)"),
            ],
        }

        for table, col_list in cols_to_add.items():
            if table in updated_tables:
                current_cols = {c["name"] for c in new_inspector.get_columns(table)}
                for col_name, col_type in col_list:
                    if col_name not in current_cols:
                        print(f"    + Adding column {col_name} to {table}")
                        if dialect == "sqlite":
                            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                        else:
                            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                        db.session.commit()

        # 3. Seed default employment types for schools
        schools = School.query.all()
        if not schools:
            default_school = School(school_id="DEFAULT", name="Default School", code="DEFAULT", is_active=True)
            db.session.add(default_school)
            db.session.commit()
            schools = [default_school]

        for s in schools:
            for code, name, desc, is_def in DEFAULT_EMPLOYMENT_TYPES:
                existing = EmploymentType.query.filter_by(school_id=s.id, code=code).first()
                if not existing:
                    db.session.add(EmploymentType(
                        school_id=s.id,
                        code=code,
                        name=name,
                        description=desc,
                        is_default=is_def,
                        status="active",
                    ))
        db.session.commit()
        print("  + Seeded default employment types across institutions.")

        # 4. Backfill qr_identifier for employees without one
        emps_without_qr = Employee.query.filter(
            (Employee.qr_identifier.is_(None)) | (Employee.qr_identifier == "")
        ).all()
        for emp in emps_without_qr:
            emp.qr_identifier = f"EMP-QR-{uuid.uuid4().hex[:12].upper()}"
        if emps_without_qr:
            db.session.commit()
            print(f"  + Generated secure QR identifiers for {len(emps_without_qr)} existing employees.")

        print("\nPhase 1 HR & Organization migration completed successfully.")


if __name__ == "__main__":
    run_phase1_migration()
