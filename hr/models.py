"""HR and Payroll models for AttendanceOS extension.

Design notes
------------
* Every model is additive — existing tables are never modified.
* Teachers and school staff are treated as employees; students are NOT.
* Employee.teacher_id is a nullable FK to the existing ``teachers`` table,
  so the HR layer can reference the attendance / face-recognition data that
  already exists for a teacher without duplicating it.
* PayrollAttendanceSummary is a snapshot table: finalized payroll writes one
  row per employee per period, snapping the attendance counters at processing
  time.  Future attendance corrections therefore cannot silently alter
  finalized payroll figures.
* to_dict() is defined on every model so routes can safely call it.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Index, UniqueConstraint,
    String, Integer, Boolean, Date, DateTime,
    Float, Numeric, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON
from sqlalchemy.sql import func

from database.db import db


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _dec(v) -> float | None:
    """Safely convert a Decimal (SQLite/Postgres) to float for JSON."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


# ============================================================
# DEPARTMENTS
# ============================================================
class Department(db.Model):
    """School / institution departments."""

    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    # Self-referential through Employee — set after Employee is defined.
    head_employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id", use_alter=True, name="fk_department_head_employee"),
        nullable=True
    )
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    designations = db.relationship(
        "Designation", back_populates="department",
        foreign_keys="Designation.department_id",
        cascade="all, delete-orphan"
    )
    employees = db.relationship(
        "Employee", back_populates="department",
        foreign_keys="Employee.department_id"
    )
    head_employee = db.relationship(
        "Employee", foreign_keys=[head_employee_id]
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_department_status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "head_employee_id": self.head_employee_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# DESIGNATIONS
# ============================================================
class Designation(db.Model):
    """Job designations / positions within departments."""

    __tablename__ = "designations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    department = db.relationship(
        "Department", back_populates="designations",
        foreign_keys=[department_id]
    )
    employees = db.relationship(
        "Employee", back_populates="designation",
        foreign_keys="Employee.designation_id"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_designation_status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "department_id": self.department_id,
            "department_name": self.department.name if self.department else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEES
# ============================================================
class Employee(db.Model):
    """HR Employee layer that references (but does not replace) the existing
    ``teachers`` table.  ``teacher_id`` is nullable so non-teacher staff can
    also be represented.
    """

    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(50), unique=True, nullable=False)
    # Nullable FK to existing teachers table
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True, unique=True)
    first_name = db.Column(db.String(60), nullable=False)
    middle_name = db.Column(db.String(60), nullable=True)
    last_name = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(20), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    joining_date = db.Column(db.Date, nullable=False)
    confirmation_date = db.Column(db.Date, nullable=True)
    employment_type = db.Column(db.String(30), nullable=False)
    employment_status = db.Column(db.String(20), default="active", nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    designation_id = db.Column(db.Integer, db.ForeignKey("designations.id"), nullable=True)
    reporting_manager_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)

    phone = db.Column(db.String(20), nullable=False)
    alternate_phone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(60), nullable=True)
    state = db.Column(db.String(60), nullable=True)
    pincode = db.Column(db.String(10), nullable=True)
    emergency_contact_name = db.Column(db.String(120), nullable=True)
    emergency_contact_phone = db.Column(db.String(20), nullable=True)

    # Bank / financial — NEVER log these fields in full
    bank_name = db.Column(db.String(120), nullable=True)
    bank_account_number = db.Column(db.String(50), nullable=True)   # Encrypted in production
    bank_ifsc = db.Column(db.String(20), nullable=True)
    pan_number = db.Column(db.String(20), nullable=True)             # Encrypted in production
    aadhaar_last4 = db.Column(db.String(4), nullable=True)           # Last 4 digits only

    profile_image = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # ---- Relationships ----
    teacher = db.relationship("Teacher", foreign_keys=[teacher_id])

    department = db.relationship(
        "Department", back_populates="employees",
        foreign_keys=[department_id]
    )
    designation = db.relationship(
        "Designation", back_populates="employees",
        foreign_keys=[designation_id]
    )
    reporting_manager = db.relationship(
        "Employee", remote_side=[id],
        foreign_keys=[reporting_manager_id],
        backref=db.backref("subordinates", lazy="dynamic")
    )

    employment_history = db.relationship(
        "EmployeeEmploymentHistory", back_populates="employee",
        foreign_keys="EmployeeEmploymentHistory.employee_id",
        cascade="all, delete-orphan"
    )
    salary_structures = db.relationship(
        "EmployeeSalaryStructure", back_populates="employee",
        cascade="all, delete-orphan"
    )
    leave_balances = db.relationship(
        "EmployeeLeaveBalance", back_populates="employee",
        cascade="all, delete-orphan"
    )
    leave_requests = db.relationship(
        "LeaveRequest", back_populates="employee",
        foreign_keys="LeaveRequest.employee_id",
        cascade="all, delete-orphan"
    )
    overtime_records = db.relationship(
        "OvertimeRecord", back_populates="employee",
        foreign_keys="OvertimeRecord.employee_id",
        cascade="all, delete-orphan"
    )
    bonuses = db.relationship(
        "EmployeeBonus", back_populates="employee",
        cascade="all, delete-orphan"
    )
    loans = db.relationship(
        "EmployeeLoan", back_populates="employee",
        cascade="all, delete-orphan"
    )
    reimbursements = db.relationship(
        "Reimbursement", back_populates="employee",
        foreign_keys="Reimbursement.employee_id",
        cascade="all, delete-orphan"
    )
    payroll_records = db.relationship(
        "PayrollRecord", back_populates="employee",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "employment_type IN ('full_time', 'part_time', 'contract', 'probation')",
            name="ck_employee_employment_type"
        ),
        CheckConstraint(
            "employment_status IN ('active', 'inactive', 'terminated', 'resigned')",
            name="ck_employee_employment_status"
        ),
    )

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    def to_dict(self, include_sensitive: bool = False) -> dict:
        data = {
            "id": self.id,
            "employee_code": self.employee_code,
            "teacher_id": self.teacher_id,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "gender": self.gender,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "joining_date": self.joining_date.isoformat() if self.joining_date else None,
            "confirmation_date": (
                self.confirmation_date.isoformat() if self.confirmation_date else None
            ),
            "employment_type": self.employment_type,
            "employment_status": self.employment_status,
            "department_id": self.department_id,
            "department_name": self.department.name if self.department else None,
            "designation_id": self.designation_id,
            "designation_name": self.designation.name if self.designation else None,
            "reporting_manager_id": self.reporting_manager_id,
            "reporting_manager_name": (
                self.reporting_manager.full_name if self.reporting_manager else None
            ),
            "phone": self.phone,
            "alternate_phone": self.alternate_phone,
            "email": self.email,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "pincode": self.pincode,
            "emergency_contact_name": self.emergency_contact_name,
            "emergency_contact_phone": self.emergency_contact_phone,
            "bank_name": self.bank_name,
            "bank_ifsc": self.bank_ifsc,
            "aadhaar_last4": self.aadhaar_last4,
            "profile_image": self.profile_image,
            "notes": self.notes,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        # Sensitive financial fields — never include unless explicitly requested
        # and even then, the caller is responsible for access control.
        if include_sensitive:
            data["bank_account_number"] = self.bank_account_number
            data["pan_number"] = self.pan_number
        return data


# ============================================================
# EMPLOYMENT HISTORY
# ============================================================
class EmployeeEmploymentHistory(db.Model):
    """Track employee job history: joining, promotions, transfers, exit."""

    __tablename__ = "employee_employment_history"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    designation_id = db.Column(db.Integer, db.ForeignKey("designations.id"), nullable=True)
    reporting_manager_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    employment_type = db.Column(db.String(30), nullable=False)
    joining_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    reason = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    employee = db.relationship(
        "Employee", back_populates="employment_history",
        foreign_keys=[employee_id]
    )
    department = db.relationship("Department", foreign_keys=[department_id])
    designation = db.relationship("Designation", foreign_keys=[designation_id])
    reporting_manager = db.relationship("Employee", foreign_keys=[reporting_manager_id])

    __table_args__ = (
        CheckConstraint(
            "employment_type IN ('full_time', 'part_time', 'contract', 'probation')",
            name="ck_emp_history_employment_type"
        ),
        CheckConstraint("status IN ('active', 'ended')", name="ck_emp_history_status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "department_id": self.department_id,
            "designation_id": self.designation_id,
            "reporting_manager_id": self.reporting_manager_id,
            "employment_type": self.employment_type,
            "joining_date": self.joining_date.isoformat() if self.joining_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# SALARY COMPONENTS (global component definitions)
# ============================================================
class SalaryComponent(db.Model):
    """Configurable salary components (earnings and deductions).

    component_type: 'earning' | 'deduction'
    calculation_type: 'fixed' | 'percentage'
    """

    __tablename__ = "salary_components"

    id = db.Column(db.Integer, primary_key=True)
    component_code = db.Column(db.String(20), unique=True, nullable=False)
    component_name = db.Column(db.String(100), nullable=False)
    component_type = db.Column(db.String(10), nullable=False)
    calculation_type = db.Column(db.String(10), nullable=False)
    default_value = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    percentage_of_component = db.Column(db.Integer, nullable=True)
    is_taxable = db.Column(db.Boolean, default=False, nullable=False)
    affects_pf = db.Column(db.Boolean, default=False, nullable=False)
    affects_esi = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee_components = db.relationship(
        "EmployeeSalaryComponent", back_populates="component",
        cascade="all, delete-orphan"
    )
    payroll_earnings = db.relationship("PayrollEarning", back_populates="component")
    payroll_deductions = db.relationship("PayrollDeduction", back_populates="component")

    __table_args__ = (
        CheckConstraint(
            "component_type IN ('earning', 'deduction')",
            name="ck_salary_component_type"
        ),
        CheckConstraint(
            "calculation_type IN ('fixed', 'percentage')",
            name="ck_salary_calculation_type"
        ),
        CheckConstraint(
            "percentage_of_component IS NULL OR "
            "(percentage_of_component >= 0 AND percentage_of_component <= 100)",
            name="ck_percentage_range"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "component_code": self.component_code,
            "component_name": self.component_name,
            "component_type": self.component_type,
            "calculation_type": self.calculation_type,
            "default_value": _dec(self.default_value),
            "percentage_of_component": self.percentage_of_component,
            "is_taxable": self.is_taxable,
            "affects_pf": self.affects_pf,
            "affects_esi": self.affects_esi,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE SALARY STRUCTURE
# ============================================================
class EmployeeSalaryStructure(db.Model):
    """Employee salary structure with effective date ranges.

    Only ONE active structure per employee per effective date is allowed;
    this is enforced at the service layer.
    """

    __tablename__ = "employee_salary_structures"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)
    pay_frequency = db.Column(db.String(20), default="monthly", nullable=False)
    basic_salary = db.Column(db.Numeric(10, 2), nullable=False)
    gross_salary = db.Column(db.Numeric(10, 2), nullable=True)
    annual_ctc = db.Column(db.Numeric(10, 2), nullable=True)
    # Hourly rate for overtime pay calculation
    overtime_rate = db.Column(db.Numeric(10, 2), nullable=True)
    # Per-occurrence or per-minute rate depending on policy
    late_deduction_rate = db.Column(db.Numeric(10, 2), nullable=True)
    half_day_deduction_rate = db.Column(db.Numeric(10, 2), nullable=True)
    unpaid_leave_deduction_method = db.Column(
        db.String(20), default="daily_salary", nullable=False
    )
    # Configurable late deduction policy: none | fixed | per_minute | per_occurrence
    late_deduction_policy = db.Column(db.String(20), default="none", nullable=False)
    # Overtime multiplier: 1.0 | 1.5 | 2.0
    overtime_multiplier = db.Column(db.Numeric(3, 2), default=1.00, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship("Employee", back_populates="salary_structures")
    salary_components = db.relationship(
        "EmployeeSalaryComponent", back_populates="salary_structure",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "pay_frequency IN ('monthly', 'bi-weekly', 'weekly')",
            name="ck_pay_frequency"
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_salary_structure_status"
        ),
        CheckConstraint(
            "late_deduction_policy IN ('none', 'fixed', 'per_minute', 'per_occurrence')",
            name="ck_late_deduction_policy"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "pay_frequency": self.pay_frequency,
            "basic_salary": _dec(self.basic_salary),
            "gross_salary": _dec(self.gross_salary),
            "annual_ctc": _dec(self.annual_ctc),
            "overtime_rate": _dec(self.overtime_rate),
            "late_deduction_rate": _dec(self.late_deduction_rate),
            "half_day_deduction_rate": _dec(self.half_day_deduction_rate),
            "unpaid_leave_deduction_method": self.unpaid_leave_deduction_method,
            "late_deduction_policy": self.late_deduction_policy,
            "overtime_multiplier": _dec(self.overtime_multiplier),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE SALARY COMPONENTS (per-structure overrides)
# ============================================================
class EmployeeSalaryComponent(db.Model):
    """Specific salary component amounts for an employee's salary structure."""

    __tablename__ = "employee_salary_components"

    id = db.Column(db.Integer, primary_key=True)
    salary_structure_id = db.Column(
        db.Integer, db.ForeignKey("employee_salary_structures.id"), nullable=False
    )
    component_id = db.Column(
        db.Integer, db.ForeignKey("salary_components.id"), nullable=False
    )
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    percentage = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    salary_structure = db.relationship(
        "EmployeeSalaryStructure", back_populates="salary_components"
    )
    component = db.relationship("SalaryComponent", back_populates="employee_components")

    __table_args__ = (
        UniqueConstraint(
            "salary_structure_id", "component_id",
            name="uq_salary_structure_component"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "salary_structure_id": self.salary_structure_id,
            "component_id": self.component_id,
            "component_name": self.component.component_name if self.component else None,
            "component_type": self.component.component_type if self.component else None,
            "amount": _dec(self.amount),
            "percentage": self.percentage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# LEAVE TYPES
# ============================================================
class LeaveType(db.Model):
    """Types of leave available to employees (CL, SL, EL, etc.)."""

    __tablename__ = "leave_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    paid = db.Column(db.Boolean, default=True, nullable=False)
    max_days_per_year = db.Column(db.Integer, default=0, nullable=False)
    carry_forward = db.Column(db.Boolean, default=False, nullable=False)
    requires_approval = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    leave_balances = db.relationship(
        "EmployeeLeaveBalance", back_populates="leave_type",
        cascade="all, delete-orphan"
    )
    leave_requests = db.relationship(
        "LeaveRequest", back_populates="leave_type",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("max_days_per_year >= 0", name="ck_leave_type_max_days"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "paid": self.paid,
            "max_days_per_year": self.max_days_per_year,
            "carry_forward": self.carry_forward,
            "requires_approval": self.requires_approval,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE LEAVE BALANCES
# ============================================================
class EmployeeLeaveBalance(db.Model):
    """Per-employee, per-year leave balance tracking."""

    __tablename__ = "employee_leave_balances"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    allocated_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    used_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    pending_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    carried_forward_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    remaining_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship("Employee", back_populates="leave_balances")
    leave_type = db.relationship("LeaveType", back_populates="leave_balances")

    __table_args__ = (
        UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_employee_leave_year"),
        CheckConstraint("year >= 2000", name="ck_leave_balance_year"),
        CheckConstraint("allocated_days >= 0", name="ck_leave_balance_allocated"),
        CheckConstraint("used_days >= 0", name="ck_leave_balance_used"),
        CheckConstraint("pending_days >= 0", name="ck_leave_balance_pending"),
        CheckConstraint("carried_forward_days >= 0", name="ck_leave_balance_carried"),
        CheckConstraint("remaining_days >= 0", name="ck_leave_balance_remaining"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "leave_type_id": self.leave_type_id,
            "leave_type_name": self.leave_type.name if self.leave_type else None,
            "year": self.year,
            "allocated_days": _dec(self.allocated_days),
            "used_days": _dec(self.used_days),
            "pending_days": _dec(self.pending_days),
            "carried_forward_days": _dec(self.carried_forward_days),
            "remaining_days": _dec(self.remaining_days),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# LEAVE REQUESTS
# ============================================================
class LeaveRequest(db.Model):
    """Employee leave requests with approval workflow."""

    __tablename__ = "leave_requests"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    leave_type_id = db.Column(db.Integer, db.ForeignKey("leave_types.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    total_days = db.Column(db.Numeric(5, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship(
        "Employee", back_populates="leave_requests", foreign_keys=[employee_id]
    )
    leave_type = db.relationship("LeaveType", back_populates="leave_requests")
    approver = db.relationship("Employee", foreign_keys=[approved_by])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_leave_request_status"
        ),
        CheckConstraint("total_days > 0", name="ck_leave_request_total_days"),
        CheckConstraint("end_date >= start_date", name="ck_leave_request_date_range"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "leave_type_id": self.leave_type_id,
            "leave_type_name": self.leave_type.name if self.leave_type else None,
            "leave_type_code": self.leave_type.code if self.leave_type else None,
            "is_paid": self.leave_type.paid if self.leave_type else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "total_days": _dec(self.total_days),
            "reason": self.reason,
            "status": self.status,
            "approved_by": self.approved_by,
            "approved_by_name": self.approver.full_name if self.approver else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# SHIFT MANAGEMENT
# ============================================================
class Shift(db.Model):
    """Work shift definitions."""

    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    start_time = db.Column(db.String(5), nullable=False)       # HH:MM
    end_time = db.Column(db.String(5), nullable=False)         # HH:MM
    break_minutes = db.Column(db.Integer, default=0, nullable=False)
    grace_minutes = db.Column(db.Integer, default=0, nullable=False)
    minimum_work_minutes = db.Column(db.Integer, default=480, nullable=False)
    overtime_after_minutes = db.Column(db.Integer, default=480, nullable=False)
    is_overnight = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee_shifts = db.relationship(
        "EmployeeShift", back_populates="shift", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("break_minutes >= 0", name="ck_shift_break_minutes"),
        CheckConstraint("grace_minutes >= 0", name="ck_shift_grace_minutes"),
        CheckConstraint("minimum_work_minutes > 0", name="ck_shift_minimum_work_minutes"),
        CheckConstraint(
            "overtime_after_minutes >= minimum_work_minutes",
            name="ck_shift_overtime_after"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "break_minutes": self.break_minutes,
            "grace_minutes": self.grace_minutes,
            "minimum_work_minutes": self.minimum_work_minutes,
            "overtime_after_minutes": self.overtime_after_minutes,
            "is_overnight": self.is_overnight,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE SHIFTS
# ============================================================
class EmployeeShift(db.Model):
    """Employee shift assignments (time-bound)."""

    __tablename__ = "employee_shifts"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    employee = db.relationship("Employee", backref=db.backref("employee_shifts", lazy="dynamic"))
    shift = db.relationship("Shift", back_populates="employee_shifts")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "shift_id": self.shift_id,
            "shift_name": self.shift.name if self.shift else None,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# OVERTIME RECORDS
# ============================================================
class OvertimeRecord(db.Model):
    """Overtime records — one row per employee per overtime event.

    Approval is required before overtime is paid (configurable).
    """

    __tablename__ = "overtime_records"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    # Optional link to teacher_attendance row that triggered this OT
    attendance_id = db.Column(
        db.Integer, db.ForeignKey("teacher_attendance.id"), nullable=True
    )
    overtime_date = db.Column(db.Date, nullable=False)
    overtime_minutes = db.Column(db.Integer, nullable=False)
    overtime_hours = db.Column(db.Numeric(5, 2), nullable=False)
    hourly_rate = db.Column(db.Numeric(10, 2), nullable=False)
    multiplier = db.Column(db.Numeric(3, 2), default=1.00, nullable=False)
    calculated_amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship(
        "Employee", back_populates="overtime_records", foreign_keys=[employee_id]
    )
    approver = db.relationship("Employee", foreign_keys=[approved_by])
    attendance = db.relationship("TeacherAttendance")

    __table_args__ = (
        CheckConstraint("overtime_minutes >= 0", name="ck_overtime_minutes"),
        CheckConstraint("overtime_hours >= 0", name="ck_overtime_hours"),
        CheckConstraint("hourly_rate >= 0", name="ck_overtime_hourly_rate"),
        CheckConstraint("multiplier >= 0", name="ck_overtime_multiplier"),
        CheckConstraint("calculated_amount >= 0", name="ck_overtime_calculated_amount"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_overtime_status"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "attendance_id": self.attendance_id,
            "overtime_date": self.overtime_date.isoformat() if self.overtime_date else None,
            "overtime_minutes": self.overtime_minutes,
            "overtime_hours": _dec(self.overtime_hours),
            "hourly_rate": _dec(self.hourly_rate),
            "multiplier": _dec(self.multiplier),
            "calculated_amount": _dec(self.calculated_amount),
            "status": self.status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE BONUS
# ============================================================
class EmployeeBonus(db.Model):
    """Employee bonuses and incentives."""

    __tablename__ = "employee_bonuses"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    payroll_period_id = db.Column(
        db.Integer, db.ForeignKey("payroll_periods.id"), nullable=True
    )
    bonus_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    taxable = db.Column(db.Boolean, default=True, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship("Employee", back_populates="bonuses")
    payroll_period = db.relationship("PayrollPeriod", back_populates="bonuses")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_bonus_amount"),
        CheckConstraint(
            "status IN ('active', 'paid', 'cancelled')", name="ck_bonus_status"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "payroll_period_id": self.payroll_period_id,
            "bonus_type": self.bonus_type,
            "description": self.description,
            "amount": _dec(self.amount),
            "taxable": self.taxable,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# EMPLOYEE LOANS
# ============================================================
class EmployeeLoan(db.Model):
    """Employee loans and salary advances."""

    __tablename__ = "employee_loans"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    loan_type = db.Column(db.String(30), nullable=False)
    principal_amount = db.Column(db.Numeric(10, 2), nullable=False)
    remaining_amount = db.Column(db.Numeric(10, 2), nullable=False)
    installment_amount = db.Column(db.Numeric(10, 2), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="active", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship("Employee", back_populates="loans")
    repayments = db.relationship(
        "LoanRepayment", back_populates="loan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "loan_type IN ('salary_advance', 'personal_loan', 'emergency_loan')",
            name="ck_loan_type"
        ),
        CheckConstraint("principal_amount >= 0", name="ck_loan_principal"),
        CheckConstraint("remaining_amount >= 0", name="ck_loan_remaining"),
        CheckConstraint("installment_amount >= 0", name="ck_loan_installment"),
        CheckConstraint(
            "status IN ('active', 'completed', 'defaulted')", name="ck_loan_status"
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_loan_date_range"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "loan_type": self.loan_type,
            "principal_amount": _dec(self.principal_amount),
            "remaining_amount": _dec(self.remaining_amount),
            "installment_amount": _dec(self.installment_amount),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# LOAN REPAYMENTS
# ============================================================
class LoanRepayment(db.Model):
    """Loan repayment records, linked to payroll runs."""

    __tablename__ = "loan_repayments"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("employee_loans.id"), nullable=False)
    payroll_record_id = db.Column(
        db.Integer, db.ForeignKey("payroll_records.id"), nullable=True
    )
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    remaining_amount = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    loan = db.relationship("EmployeeLoan", back_populates="repayments")
    payroll_record = db.relationship("PayrollRecord", back_populates="loan_repayments")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_loan_repayment_amount"),
        CheckConstraint("remaining_amount >= 0", name="ck_loan_repayment_remaining"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "loan_id": self.loan_id,
            "payroll_record_id": self.payroll_record_id,
            "amount": _dec(self.amount),
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "remaining_amount": _dec(self.remaining_amount),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# REIMBURSEMENTS
# ============================================================
class Reimbursement(db.Model):
    """Employee expense reimbursements."""

    __tablename__ = "reimbursements"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    reimbursement_type = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text, nullable=True)
    claim_date = db.Column(db.Date, nullable=False)
    approved_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    paid_in_payroll = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    employee = db.relationship(
        "Employee", back_populates="reimbursements", foreign_keys=[employee_id]
    )
    approver = db.relationship("Employee", foreign_keys=[approved_by])

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_reimbursement_amount"),
        CheckConstraint("approved_amount >= 0", name="ck_reimbursement_approved_amount"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'paid')",
            name="ck_reimbursement_status"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "reimbursement_type": self.reimbursement_type,
            "amount": _dec(self.amount),
            "description": self.description,
            "claim_date": self.claim_date.isoformat() if self.claim_date else None,
            "approved_amount": _dec(self.approved_amount),
            "status": self.status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "paid_in_payroll": self.paid_in_payroll,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# PAYROLL PERIODS
# ============================================================
class PayrollPeriod(db.Model):
    """Payroll processing periods.

    Statuses: draft → processing → processed → finalized | cancelled
    Only one period can exist for a given start_date + end_date pair.
    """

    __tablename__ = "payroll_periods"

    id = db.Column(db.Integer, primary_key=True)
    period_name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="draft", nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    processed_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    finalized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finalized_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    processed_by_user = db.relationship("Employee", foreign_keys=[processed_by])
    finalized_by_user = db.relationship("Employee", foreign_keys=[finalized_by])
    payroll_records = db.relationship(
        "PayrollRecord", back_populates="payroll_period", cascade="all, delete-orphan"
    )
    bonuses = db.relationship("EmployeeBonus", back_populates="payroll_period")

    __table_args__ = (
        UniqueConstraint("start_date", "end_date", name="uq_payroll_period_dates"),
        CheckConstraint(
            "status IN ('draft', 'processing', 'processed', 'finalized', 'cancelled')",
            name="ck_payroll_period_status"
        ),
        CheckConstraint("end_date >= start_date", name="ck_payroll_period_date_range"),
        CheckConstraint("payment_date >= end_date", name="ck_payroll_payment_date"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "period_name": self.period_name,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "status": self.status,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "processed_by": self.processed_by,
            "finalized_at": self.finalized_at.isoformat() if self.finalized_at else None,
            "finalized_by": self.finalized_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# PAYROLL RECORDS (one per employee per period)
# ============================================================
class PayrollRecord(db.Model):
    """Immutable payroll record for one employee for one period.

    After the parent PayrollPeriod is finalized, this record must NOT be
    directly mutated.  Corrections must go through a separate revision
    workflow.
    """

    __tablename__ = "payroll_records"

    id = db.Column(db.Integer, primary_key=True)
    payroll_period_id = db.Column(
        db.Integer, db.ForeignKey("payroll_periods.id"), nullable=False
    )
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    basic_salary = db.Column(db.Numeric(10, 2), nullable=False)
    gross_earnings = db.Column(db.Numeric(10, 2), nullable=False)
    total_earnings = db.Column(db.Numeric(10, 2), nullable=False)
    total_deductions = db.Column(db.Numeric(10, 2), nullable=False)
    overtime_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    bonus_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    reimbursement_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    leave_deduction = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    late_deduction = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    loan_deduction = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    other_deductions = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    net_salary = db.Column(db.Numeric(10, 2), nullable=False)
    payable_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(),
                           onupdate=func.now(), nullable=False)

    # Relationships
    payroll_period = db.relationship("PayrollPeriod", back_populates="payroll_records")
    employee = db.relationship("Employee", back_populates="payroll_records")
    earnings = db.relationship(
        "PayrollEarning", back_populates="payroll_record", cascade="all, delete-orphan"
    )
    deductions = db.relationship(
        "PayrollDeduction", back_populates="payroll_record", cascade="all, delete-orphan"
    )
    loan_repayments = db.relationship("LoanRepayment", back_populates="payroll_record")
    # One-to-one with Payslip
    payslip = db.relationship(
        "Payslip", back_populates="payroll_record",
        uselist=False, cascade="all, delete-orphan"
    )
    # One-to-one with attendance snapshot
    attendance_summary = db.relationship(
        "PayrollAttendanceSummary", back_populates="payroll_record",
        uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "payroll_period_id", "employee_id",
            name="uq_payroll_record_period_employee"
        ),
        CheckConstraint(
            "status IN ('pending', 'processed', 'finalized')",
            name="ck_payroll_record_status"
        ),
        CheckConstraint("net_salary >= 0", name="ck_payroll_record_net_salary_non_negative"),
        CheckConstraint("basic_salary >= 0", name="ck_payroll_record_basic_salary"),
        CheckConstraint("gross_earnings >= 0", name="ck_payroll_record_gross_earnings"),
        CheckConstraint("total_earnings >= 0", name="ck_payroll_record_total_earnings"),
        CheckConstraint("total_deductions >= 0", name="ck_payroll_record_total_deductions"),
        CheckConstraint("payable_days >= 0", name="ck_payroll_record_payable_days"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payroll_period_id": self.payroll_period_id,
            "employee_id": self.employee_id,
            "employee_name": self.employee.full_name if self.employee else None,
            "employee_code": self.employee.employee_code if self.employee else None,
            "basic_salary": _dec(self.basic_salary),
            "gross_earnings": _dec(self.gross_earnings),
            "total_earnings": _dec(self.total_earnings),
            "total_deductions": _dec(self.total_deductions),
            "overtime_amount": _dec(self.overtime_amount),
            "bonus_amount": _dec(self.bonus_amount),
            "reimbursement_amount": _dec(self.reimbursement_amount),
            "leave_deduction": _dec(self.leave_deduction),
            "late_deduction": _dec(self.late_deduction),
            "loan_deduction": _dec(self.loan_deduction),
            "other_deductions": _dec(self.other_deductions),
            "net_salary": _dec(self.net_salary),
            "payable_days": _dec(self.payable_days),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# PAYROLL ATTENDANCE SUMMARY (snapshot — immutable after creation)
# ============================================================
class PayrollAttendanceSummary(db.Model):
    """Snapshot of attendance counters taken at payroll processing time.

    CRITICAL: This table records the attendance figures as they were when
    payroll was processed.  Subsequent attendance edits, leave corrections, or
    holiday additions do NOT automatically alter these numbers.  Any correction
    must go through a payroll revision workflow.
    """

    __tablename__ = "payroll_attendance_summaries"

    id = db.Column(db.Integer, primary_key=True)
    payroll_record_id = db.Column(
        db.Integer, db.ForeignKey("payroll_records.id"), nullable=False, unique=True
    )
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    # Calendar dimensions
    total_calendar_days = db.Column(db.Integer, default=0, nullable=False)
    working_days = db.Column(db.Integer, default=0, nullable=False)
    # Attendance counters
    present_days = db.Column(db.Integer, default=0, nullable=False)
    absent_days = db.Column(db.Integer, default=0, nullable=False)
    half_days = db.Column(db.Integer, default=0, nullable=False)
    paid_leave_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    unpaid_leave_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    holidays = db.Column(db.Integer, default=0, nullable=False)
    weekly_offs = db.Column(db.Integer, default=0, nullable=False)
    late_count = db.Column(db.Integer, default=0, nullable=False)
    early_exit_count = db.Column(db.Integer, default=0, nullable=False)
    overtime_minutes = db.Column(db.Integer, default=0, nullable=False)
    total_working_minutes = db.Column(db.Integer, default=0, nullable=False)
    payable_days = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    payroll_record = db.relationship(
        "PayrollRecord", back_populates="attendance_summary"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payroll_record_id": self.payroll_record_id,
            "employee_id": self.employee_id,
            "total_calendar_days": self.total_calendar_days,
            "working_days": self.working_days,
            "present_days": self.present_days,
            "absent_days": self.absent_days,
            "half_days": self.half_days,
            "paid_leave_days": _dec(self.paid_leave_days),
            "unpaid_leave_days": _dec(self.unpaid_leave_days),
            "holidays": self.holidays,
            "weekly_offs": self.weekly_offs,
            "late_count": self.late_count,
            "early_exit_count": self.early_exit_count,
            "overtime_minutes": self.overtime_minutes,
            "total_working_minutes": self.total_working_minutes,
            "payable_days": _dec(self.payable_days),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# PAYROLL EARNINGS
# ============================================================
class PayrollEarning(db.Model):
    """One earning line-item in a payroll record."""

    __tablename__ = "payroll_earnings"

    id = db.Column(db.Integer, primary_key=True)
    payroll_record_id = db.Column(
        db.Integer, db.ForeignKey("payroll_records.id"), nullable=False
    )
    component_id = db.Column(
        db.Integer, db.ForeignKey("salary_components.id"), nullable=True
    )
    component_name_snapshot = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    payroll_record = db.relationship("PayrollRecord", back_populates="earnings")
    component = db.relationship("SalaryComponent", back_populates="payroll_earnings")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payroll_earning_amount"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payroll_record_id": self.payroll_record_id,
            "component_id": self.component_id,
            "component_name_snapshot": self.component_name_snapshot,
            "amount": _dec(self.amount),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# PAYROLL DEDUCTIONS
# ============================================================
class PayrollDeduction(db.Model):
    """One deduction line-item in a payroll record."""

    __tablename__ = "payroll_deductions"

    id = db.Column(db.Integer, primary_key=True)
    payroll_record_id = db.Column(
        db.Integer, db.ForeignKey("payroll_records.id"), nullable=False
    )
    component_id = db.Column(
        db.Integer, db.ForeignKey("salary_components.id"), nullable=True
    )
    deduction_name_snapshot = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    payroll_record = db.relationship("PayrollRecord", back_populates="deductions")
    component = db.relationship("SalaryComponent", back_populates="payroll_deductions")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payroll_deduction_amount"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payroll_record_id": self.payroll_record_id,
            "component_id": self.component_id,
            "deduction_name_snapshot": self.deduction_name_snapshot,
            "amount": _dec(self.amount),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# PAYSLIPS
# ============================================================
class Payslip(db.Model):
    """Generated payslips (one-to-one with PayrollRecord)."""

    __tablename__ = "payslips"

    id = db.Column(db.Integer, primary_key=True)
    payroll_record_id = db.Column(
        db.Integer, db.ForeignKey("payroll_records.id"), nullable=False, unique=True
    )
    payslip_number = db.Column(db.String(50), unique=True, nullable=False)
    generated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    file_path = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="generated", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    payroll_record = db.relationship("PayrollRecord", back_populates="payslip")

    __table_args__ = (
        CheckConstraint(
            "status IN ('generated', 'sent', 'viewed')", name="ck_payslip_status"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payroll_record_id": self.payroll_record_id,
            "payslip_number": self.payslip_number,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "file_path": self.file_path,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# HR AUDIT LOG
# ============================================================
class HRAuditLog(db.Model):
    """Audit trail for all HR and Payroll changes.

    Never log full bank account numbers, PAN, Aadhaar, passwords or
    biometric embeddings in old_values / new_values.
    """

    __tablename__ = "hr_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)
    performed_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    performed_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address = db.Column(db.String(45), nullable=True)
    old_values = db.Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    new_values = db.Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    reason = db.Column(db.Text, nullable=True)

    # Relationships
    performer = db.relationship("Employee", foreign_keys=[performed_by])

    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'delete', 'approve', 'reject', "
            "'process', 'finalize')",
            name="ck_hr_audit_action"
        ),
        Index("ix_hr_audit_entity", "entity_type", "entity_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "performed_by": self.performed_by,
            "performed_at": self.performed_at.isoformat() if self.performed_at else None,
            "ip_address": self.ip_address,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "reason": self.reason,
        }