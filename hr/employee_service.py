"""Employee and HR management service for AttendanceOS.

Handles CRUD for employees, departments, designations, salary structures,
leave management, overtime, bonuses, loans, and reimbursements.

All monetary comparisons use Decimal arithmetic.
Audit logs are always written (non-raising) and committed with the
main transaction.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from database.db import db
from database.models import Teacher
from hr.models import (
    Department,
    Designation,
    Employee,
    EmployeeEmploymentHistory,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    Shift,
    EmployeeShift,
    LeaveType,
    EmployeeLeaveBalance,
    LeaveRequest,
    OvertimeRecord,
    EmployeeBonus,
    EmployeeLoan,
    LoanRepayment,
    Reimbursement,
    HRAuditLog,
)


def _make_json_safe(obj):
    if obj is None:
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(x) for x in obj]
    return obj


# ─────────────────────────────────────────────── audit helper
def _create_audit_log(
    entity_type: str,
    entity_id: int,
    action: str,
    performed_by: Optional[int],
    old_values: Optional[dict],
    new_values: Optional[dict],
    reason: Optional[str] = None,
) -> None:
    """Write an audit log row (does NOT commit — caller owns the session)."""
    try:
        log = HRAuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            performed_by=performed_by,
            old_values=_make_json_safe(old_values),
            new_values=_make_json_safe(new_values),
            reason=reason,
        )
        db.session.add(log)
    except Exception:
        pass  # Audit log failure must never block the main operation


# ─────────────────────────────────────────────── Department
def create_department(data: dict, performed_by: Optional[int] = None) -> Department:
    if Department.query.filter_by(code=data.get("code")).first():
        raise ValueError(f"Department code '{data['code']}' already exists")
    dept = Department(**data)
    db.session.add(dept)
    try:
        db.session.commit()
        _create_audit_log("department", dept.id, "create", performed_by, None, data)
        db.session.commit()
        return dept
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create department: {exc}") from exc


def get_department(dept_id: int) -> Optional[Department]:
    return Department.query.get(dept_id)


def get_departments(active_only: bool = False) -> List[Department]:
    q = Department.query
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(Department.name).all()


def update_department(
    dept_id: int, data: dict, performed_by: Optional[int] = None
) -> Department:
    dept = Department.query.get(dept_id)
    if not dept:
        raise ValueError(f"Department {dept_id} not found")
    old = {"name": dept.name, "code": dept.code, "status": dept.status}
    if "code" in data and data["code"] != dept.code:
        if Department.query.filter_by(code=data["code"]).first():
            raise ValueError(f"Department code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(dept, k):
            setattr(dept, k, v)
    try:
        db.session.commit()
        _create_audit_log("department", dept.id, "update", performed_by, old, data)
        db.session.commit()
        return dept
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update department: {exc}") from exc


# ─────────────────────────────────────────────── Designation
def create_designation(data: dict, performed_by: Optional[int] = None) -> Designation:
    if Designation.query.filter_by(code=data.get("code")).first():
        raise ValueError(f"Designation code '{data['code']}' already exists")
    desig = Designation(**data)
    db.session.add(desig)
    try:
        db.session.commit()
        _create_audit_log("designation", desig.id, "create", performed_by, None, data)
        db.session.commit()
        return desig
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create designation: {exc}") from exc


def get_designation(desig_id: int) -> Optional[Designation]:
    return Designation.query.get(desig_id)


def get_designations(department_id: Optional[int] = None) -> List[Designation]:
    q = Designation.query
    if department_id:
        q = q.filter_by(department_id=department_id)
    return q.order_by(Designation.name).all()


def update_designation(
    desig_id: int, data: dict, performed_by: Optional[int] = None
) -> Designation:
    desig = Designation.query.get(desig_id)
    if not desig:
        raise ValueError(f"Designation {desig_id} not found")
    old = {"name": desig.name, "code": desig.code, "status": desig.status}
    for k, v in data.items():
        if hasattr(desig, k):
            setattr(desig, k, v)
    try:
        db.session.commit()
        _create_audit_log("designation", desig.id, "update", performed_by, old, data)
        db.session.commit()
        return desig
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update designation: {exc}") from exc


# ─────────────────────────────────────────────── Employee CRUD
def create_employee(employee_data: dict, performed_by: Optional[int] = None) -> Employee:
    if Employee.query.filter_by(employee_code=employee_data.get("employee_code")).first():
        raise ValueError(f"Employee code '{employee_data['employee_code']}' already exists")
    if Employee.query.filter_by(email=employee_data.get("email")).first():
        raise ValueError(f"Email '{employee_data['email']}' already exists")
    if employee_data.get("teacher_id"):
        if Employee.query.filter_by(teacher_id=employee_data["teacher_id"]).first():
            raise ValueError(
                f"Teacher {employee_data['teacher_id']} is already linked to an employee"
            )
    emp = Employee(**employee_data)
    db.session.add(emp)
    try:
        db.session.commit()
        # Don't log sensitive fields
        safe = {k: v for k, v in employee_data.items()
                if k not in ("bank_account_number", "pan_number")}
        _create_audit_log("employee", emp.id, "create", performed_by, None, safe)
        db.session.commit()
        return emp
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create employee: {exc}") from exc


def get_employee(employee_id: int) -> Optional[Employee]:
    return Employee.query.get(employee_id)


def get_employee_by_code(employee_code: str) -> Optional[Employee]:
    return Employee.query.filter_by(employee_code=employee_code).first()


def get_employee_by_teacher_id(teacher_id: int) -> Optional[Employee]:
    return Employee.query.filter_by(teacher_id=teacher_id).first()


def get_employees(
    filters: Optional[dict] = None, page: int = 1, per_page: int = 50
) -> tuple[List[Employee], int]:
    q = Employee.query
    if filters:
        if filters.get("department_id"):
            q = q.filter(Employee.department_id == int(filters["department_id"]))
        if filters.get("designation_id"):
            q = q.filter(Employee.designation_id == int(filters["designation_id"]))
        if filters.get("employment_status"):
            q = q.filter(Employee.employment_status == filters["employment_status"])
        if filters.get("is_active") is not None:
            q = q.filter(Employee.is_active == filters["is_active"])
        if filters.get("search"):
            term = f"%{filters['search']}%"
            q = q.filter(
                or_(
                    Employee.first_name.ilike(term),
                    Employee.last_name.ilike(term),
                    Employee.employee_code.ilike(term),
                    Employee.email.ilike(term),
                )
            )
    total = q.count()
    employees = q.offset((page - 1) * per_page).limit(per_page).all()
    return employees, total


def sync_teachers_to_employees() -> dict:
    """Safely synchronize existing Teachers into HR Employee records.
    
    * Idempotent: Does nothing if Employee already exists for a teacher_id.
    * Non-destructive: Does NOT overwrite manually edited HR Employee fields.
    * Links teacher_id permanently on the created Employee record.
    """
    teachers = Teacher.query.all()
    created = 0
    already_linked = 0
    skipped = 0

    dept = Department.query.filter_by(code="ACAD").first()
    if not dept:
        dept = Department.query.filter_by(name="Academics").first()
    if not dept:
        dept = Department(name="Academics", code="ACAD", description="Academic Department", status="active")
        db.session.add(dept)
        db.session.flush()

    desig = Designation.query.filter_by(code="TCH").first()
    if not desig:
        desig = Designation.query.filter_by(name="Teacher").first()
    if not desig:
        desig = Designation(name="Teacher", code="TCH", description="Teaching Faculty", department_id=dept.id, status="active")
        db.session.add(desig)
        db.session.flush()

    for t in teachers:
        existing = Employee.query.filter_by(teacher_id=t.id).first()
        if existing:
            already_linked += 1
            continue

        raw_tid = (t.teacher_id or "").strip()
        if raw_tid.startswith("EMP-"):
            code_candidate = raw_tid
        elif raw_tid:
            code_candidate = f"EMP-{raw_tid}"
        else:
            code_candidate = f"EMP-T{t.id}"

        if Employee.query.filter_by(employee_code=code_candidate).first():
            code_candidate = f"EMP-T{t.id}"
            if Employee.query.filter_by(employee_code=code_candidate).first():
                code_candidate = f"EMP-T{t.id}-{raw_tid}"

        parts = (t.name or "Teacher").strip().split(maxsplit=2)
        if len(parts) == 1:
            first_name, middle_name, last_name = parts[0], None, "Staff"
        elif len(parts) == 2:
            first_name, middle_name, last_name = parts[0], None, parts[1]
        else:
            first_name, middle_name, last_name = parts[0], parts[1], parts[2]

        teacher_dept = dept
        if t.department:
            d_match = Department.query.filter(Department.name.ilike(t.department.strip())).first()
            if d_match:
                teacher_dept = d_match

        teacher_desig = desig
        if t.designation:
            ds_match = Designation.query.filter(Designation.name.ilike(t.designation.strip())).first()
            if ds_match:
                teacher_desig = ds_match

        joining_d = t.created_at.date() if getattr(t, "created_at", None) else date.today()
        emp_email = (t.email or f"teacher_{t.id}@school.edu").strip().lower()

        if Employee.query.filter_by(email=emp_email).first():
            emp_email = f"teacher_{t.id}_{raw_tid.lower()}@school.edu"

        emp = Employee(
            employee_code=code_candidate,
            teacher_id=t.id,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            gender="other",
            joining_date=joining_d,
            employment_type="full_time",
            employment_status="active" if getattr(t, "is_active", True) else "inactive",
            department_id=teacher_dept.id if teacher_dept else None,
            designation_id=teacher_desig.id if teacher_desig else None,
            phone=t.mobile or "0000000000",
            email=emp_email,
            profile_image=t.photo_path,
            is_active=getattr(t, "is_active", True),
        )
        db.session.add(emp)
        created += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Teacher sync failed: {exc}") from exc

    return {"created": created, "already_linked": already_linked, "skipped": skipped}


def update_employee(
    employee_id: int, update_data: dict, performed_by: Optional[int] = None
) -> Employee:
    emp = Employee.query.get(employee_id)
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")

    old = {
        "employee_code": emp.employee_code,
        "first_name": emp.first_name,
        "last_name": emp.last_name,
        "email": emp.email,
        "department_id": emp.department_id,
        "designation_id": emp.designation_id,
        "employment_status": emp.employment_status,
        "is_active": emp.is_active,
    }

    if "employee_code" in update_data and update_data["employee_code"] != emp.employee_code:
        if Employee.query.filter_by(employee_code=update_data["employee_code"]).first():
            raise ValueError(f"Employee code '{update_data['employee_code']}' already exists")

    if "email" in update_data and update_data["email"] != emp.email:
        if Employee.query.filter_by(email=update_data["email"]).first():
            raise ValueError(f"Email '{update_data['email']}' already exists")

    if "teacher_id" in update_data and update_data["teacher_id"] != emp.teacher_id:
        if update_data["teacher_id"] is not None:
            conflict = Employee.query.filter_by(
                teacher_id=update_data["teacher_id"]
            ).first()
            if conflict and conflict.id != employee_id:
                raise ValueError(
                    f"Teacher {update_data['teacher_id']} is already linked to "
                    f"employee {conflict.id}"
                )

    for k, v in update_data.items():
        if hasattr(emp, k):
            setattr(emp, k, v)

    try:
        db.session.commit()
        safe_new = {k: v for k, v in update_data.items()
                    if k not in ("bank_account_number", "pan_number")}
        _create_audit_log("employee", emp.id, "update", performed_by, old, safe_new)
        db.session.commit()
        return emp
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update employee: {exc}") from exc


def delete_employee(employee_id: int, performed_by: Optional[int] = None) -> bool:
    """Soft-delete an employee by setting is_active = False."""
    emp = Employee.query.get(employee_id)
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")
    old = {"is_active": emp.is_active}
    emp.is_active = False
    try:
        db.session.commit()
        _create_audit_log("employee", emp.id, "update", performed_by, old, {"is_active": False})
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to deactivate employee: {exc}") from exc


# ─────────────────────────────────────────────── Employment History
def create_employment_history(
    employee_id: int, history_data: dict, performed_by: Optional[int] = None
) -> EmployeeEmploymentHistory:
    history_data["employee_id"] = employee_id
    history = EmployeeEmploymentHistory(**history_data)
    db.session.add(history)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_employment_history", history.id, "create", performed_by, None, history_data
        )
        db.session.commit()
        return history
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create employment history: {exc}") from exc


# ─────────────────────────────────────────────── Salary Structure
def get_employee_salary_structure(
    employee_id: int, effective_date: Optional[date] = None
) -> Optional[EmployeeSalaryStructure]:
    if effective_date is None:
        effective_date = date.today()
    return EmployeeSalaryStructure.query.filter(
        EmployeeSalaryStructure.employee_id == employee_id,
        EmployeeSalaryStructure.effective_from <= effective_date,
        or_(
            EmployeeSalaryStructure.effective_to.is_(None),
            EmployeeSalaryStructure.effective_to >= effective_date,
        ),
        EmployeeSalaryStructure.status == "active",
    ).first()


def get_employee_salary_history(employee_id: int) -> List[EmployeeSalaryStructure]:
    """Return all historical and active salary structures for an employee ordered by effective date."""
    return EmployeeSalaryStructure.query.filter_by(
        employee_id=employee_id
    ).order_by(EmployeeSalaryStructure.effective_from.desc()).all()


def create_salary_structure(
    employee_id: int, structure_data: dict, performed_by: Optional[int] = None
) -> EmployeeSalaryStructure:
    structure_data["employee_id"] = employee_id
    eff_from = structure_data["effective_from"]
    eff_to = structure_data.get("effective_to")

    # If an existing active structure exists, automatically roll it over up to the new effective date
    # rather than failing with an overlapping error, preserving historical salary integrity.
    q = EmployeeSalaryStructure.query.filter(
        EmployeeSalaryStructure.employee_id == employee_id,
        EmployeeSalaryStructure.status == "active",
    )
    for existing in q.all():
        ex_from = existing.effective_from
        ex_to = existing.effective_to
        if ex_from < eff_from:
            if ex_to is None or ex_to >= eff_from:
                existing.effective_to = eff_from - timedelta(days=1)
                existing.status = "inactive"
        elif ex_from == eff_from:
            # Replaces/updates same-day structure
            existing.status = "inactive"
            existing.effective_to = eff_from
        else:
            # Future structure collision
            if eff_to is None or eff_to >= ex_from:
                existing.status = "inactive"

    ss = EmployeeSalaryStructure(**structure_data)
    db.session.add(ss)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_salary_structure", ss.id, "create", performed_by, None, structure_data
        )
        db.session.commit()
        return ss
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create salary structure: {exc}") from exc


def add_salary_component(
    salary_structure_id: int, component_data: dict, performed_by: Optional[int] = None
) -> EmployeeSalaryComponent:
    component_data["salary_structure_id"] = salary_structure_id
    if EmployeeSalaryComponent.query.filter_by(
        salary_structure_id=salary_structure_id,
        component_id=component_data["component_id"],
    ).first():
        raise ValueError(
            f"Component {component_data['component_id']} already in structure "
            f"{salary_structure_id}"
        )
    sc = EmployeeSalaryComponent(**component_data)
    db.session.add(sc)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_salary_component", sc.id, "create", performed_by, None, component_data
        )
        db.session.commit()
        return sc
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to add salary component: {exc}") from exc


# ─────────────────────────────────────────────── Leave Type
def create_leave_type(
    leave_type_data: dict, performed_by: Optional[int] = None
) -> LeaveType:
    if LeaveType.query.filter_by(code=leave_type_data.get("code")).first():
        raise ValueError(f"Leave type code '{leave_type_data['code']}' already exists")
    lt = LeaveType(**leave_type_data)
    db.session.add(lt)
    try:
        db.session.commit()
        _create_audit_log("leave_type", lt.id, "create", performed_by, None, leave_type_data)
        db.session.commit()
        return lt
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create leave type: {exc}") from exc


def get_leave_types(active_only: bool = True) -> List[LeaveType]:
    q = LeaveType.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(LeaveType.name).all()


# ─────────────────────────────────────────────── Shifts
def create_shift(shift_data: dict, performed_by: Optional[int] = None) -> Shift:
    if Shift.query.filter_by(code=shift_data.get("code")).first():
        raise ValueError(f"Shift code '{shift_data['code']}' already exists")
    shift = Shift(**shift_data)
    db.session.add(shift)
    try:
        db.session.commit()
        _create_audit_log("shift", shift.id, "create", performed_by, None, shift_data)
        db.session.commit()
        return shift
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create shift: {exc}") from exc


def get_shifts(active_only: bool = False) -> List[Shift]:
    q = Shift.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(Shift.name).all()


def get_shift(shift_id: int) -> Optional[Shift]:
    return Shift.query.get(shift_id)


def update_shift(shift_id: int, shift_data: dict, performed_by: Optional[int] = None) -> Shift:
    shift = Shift.query.get(shift_id)
    if not shift:
        raise ValueError(f"Shift {shift_id} not found")
    old = {"name": shift.name, "code": shift.code, "is_active": shift.is_active}
    for k, v in shift_data.items():
        if hasattr(shift, k):
            setattr(shift, k, v)
    try:
        db.session.commit()
        _create_audit_log("shift", shift.id, "update", performed_by, old, shift_data)
        db.session.commit()
        return shift
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update shift: {exc}") from exc


# ─────────────────────────────────────────────── Leave Requests
def create_leave_request(
    employee_id: int, leave_data: dict, performed_by: Optional[int] = None
) -> LeaveRequest:
    leave_data["employee_id"] = employee_id

    # Parse dates if strings
    if isinstance(leave_data.get("start_date"), str):
        leave_data["start_date"] = datetime.strptime(
            leave_data["start_date"], "%Y-%m-%d"
        ).date()
    if isinstance(leave_data.get("end_date"), str):
        leave_data["end_date"] = datetime.strptime(
            leave_data["end_date"], "%Y-%m-%d"
        ).date()

    leave_data["total_days"] = (
        leave_data["end_date"] - leave_data["start_date"]
    ).days + 1

    # Validate balance if the leave type requires it
    lt = LeaveType.query.get(leave_data.get("leave_type_id"))
    if lt and lt.requires_approval is False:
        # Auto-approve if no approval required
        leave_data.setdefault("status", "approved")

    lr = LeaveRequest(**leave_data)
    db.session.add(lr)
    try:
        db.session.commit()
        _create_audit_log("leave_request", lr.id, "create", performed_by, None, {
            "employee_id": employee_id,
            "leave_type_id": leave_data.get("leave_type_id"),
            "start_date": str(leave_data["start_date"]),
            "end_date": str(leave_data["end_date"]),
            "total_days": float(leave_data["total_days"]),
        })
        db.session.commit()
        return lr
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create leave request: {exc}") from exc


def approve_leave_request(
    leave_request_id: int,
    approved_by: int,
    performed_by: Optional[int] = None,
) -> LeaveRequest:
    lr = LeaveRequest.query.get(leave_request_id)
    if not lr:
        raise ValueError(f"Leave request {leave_request_id} not found")
    if lr.status != "pending":
        raise ValueError(
            f"Leave request {leave_request_id} is not pending (status: {lr.status})"
        )

    old = {"status": lr.status}
    lr.status = "approved"
    lr.approved_by = approved_by
    lr.approved_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
        _update_leave_balances_on_approval(lr)
        _create_audit_log(
            "leave_request", lr.id, "approve", performed_by, old,
            {"status": "approved", "approved_by": approved_by},
        )
        db.session.commit()
        return lr
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to approve leave request: {exc}") from exc


def reject_leave_request(
    leave_request_id: int,
    rejection_reason: str,
    performed_by: Optional[int] = None,
) -> LeaveRequest:
    lr = LeaveRequest.query.get(leave_request_id)
    if not lr:
        raise ValueError(f"Leave request {leave_request_id} not found")
    if lr.status != "pending":
        raise ValueError(
            f"Leave request {leave_request_id} is not pending (status: {lr.status})"
        )

    old = {"status": lr.status}
    lr.status = "rejected"
    lr.rejection_reason = rejection_reason
    try:
        db.session.commit()
        _create_audit_log(
            "leave_request", lr.id, "reject", performed_by, old,
            {"status": "rejected", "rejection_reason": rejection_reason},
        )
        db.session.commit()
        return lr
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to reject leave request: {exc}") from exc


def cancel_leave_request(
    leave_request_id: int, performed_by: Optional[int] = None
) -> LeaveRequest:
    lr = LeaveRequest.query.get(leave_request_id)
    if not lr:
        raise ValueError(f"Leave request {leave_request_id} not found")
    if lr.status not in ("pending", "approved"):
        raise ValueError(
            f"Leave request {leave_request_id} cannot be cancelled (status: {lr.status})"
        )
    old_status = lr.status
    lr.status = "cancelled"
    try:
        db.session.commit()
        _create_audit_log(
            "leave_request", lr.id, "update", performed_by,
            {"status": old_status}, {"status": "cancelled"},
        )
        db.session.commit()
        return lr
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to cancel leave request: {exc}") from exc


def _update_leave_balances_on_approval(leave_request: LeaveRequest) -> None:
    """Debit leave balance when a leave request is approved."""
    year = leave_request.start_date.year
    lb = EmployeeLeaveBalance.query.filter_by(
        employee_id=leave_request.employee_id,
        leave_type_id=leave_request.leave_type_id,
        year=year,
    ).first()

    if not lb:
        lt = LeaveType.query.get(leave_request.leave_type_id)
        alloc = lt.max_days_per_year if lt else 0
        lb = EmployeeLeaveBalance(
            employee_id=leave_request.employee_id,
            leave_type_id=leave_request.leave_type_id,
            year=year,
            allocated_days=alloc,
            used_days=0,
            pending_days=0,
            carried_forward_days=0,
            remaining_days=alloc,
        )
        db.session.add(lb)

    requested = Decimal(str(leave_request.total_days))
    # Prevent negative balance unless explicitly allowed
    if lb.remaining_days < requested:
        # Allow it but record that balance goes to 0
        requested = max(requested, Decimal("0"))

    lb.pending_days = Decimal(str(lb.pending_days)) + requested
    lb.remaining_days = max(Decimal(str(lb.remaining_days)) - requested, Decimal("0"))
    db.session.commit()


# ─────────────────────────────────────────────── Overtime
def create_overtime_record(
    employee_id: int, overtime_data: dict, performed_by: Optional[int] = None
) -> OvertimeRecord:
    overtime_data["employee_id"] = employee_id
    if "overtime_minutes" in overtime_data:
        overtime_data["overtime_hours"] = (
            Decimal(str(overtime_data["overtime_minutes"])) / 60
        ).quantize(Decimal("0.01"))
    rec = OvertimeRecord(**overtime_data)
    db.session.add(rec)
    try:
        db.session.commit()
        _create_audit_log(
            "overtime_record", rec.id, "create", performed_by, None, overtime_data
        )
        db.session.commit()
        return rec
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create overtime record: {exc}") from exc


def approve_overtime_record(
    overtime_id: int, approved_by: int, performed_by: Optional[int] = None
) -> OvertimeRecord:
    rec = OvertimeRecord.query.get(overtime_id)
    if not rec:
        raise ValueError(f"Overtime record {overtime_id} not found")
    if rec.status != "pending":
        raise ValueError(
            f"Overtime record {overtime_id} is not pending (status: {rec.status})"
        )
    old = {"status": rec.status}
    rec.status = "approved"
    rec.approved_by = approved_by
    rec.approved_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
        _create_audit_log(
            "overtime_record", rec.id, "approve", performed_by, old,
            {"status": "approved", "approved_by": approved_by},
        )
        db.session.commit()
        return rec
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to approve overtime record: {exc}") from exc


# ─────────────────────────────────────────────── Bonus
def create_employee_bonus(
    employee_id: int, bonus_data: dict, performed_by: Optional[int] = None
) -> EmployeeBonus:
    bonus_data["employee_id"] = employee_id
    bonus = EmployeeBonus(**bonus_data)
    db.session.add(bonus)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_bonus", bonus.id, "create", performed_by, None, bonus_data
        )
        db.session.commit()
        return bonus
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create bonus: {exc}") from exc


# ─────────────────────────────────────────────── Loans
def create_employee_loan(
    employee_id: int, loan_data: dict, performed_by: Optional[int] = None
) -> EmployeeLoan:
    loan_data["employee_id"] = employee_id
    if "remaining_amount" not in loan_data:
        loan_data["remaining_amount"] = loan_data["principal_amount"]
    loan = EmployeeLoan(**loan_data)
    db.session.add(loan)
    try:
        db.session.commit()
        _create_audit_log("employee_loan", loan.id, "create", performed_by, None, loan_data)
        db.session.commit()
        return loan
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create loan: {exc}") from exc


def create_loan_repayment(
    loan_id: int, repayment_data: dict, performed_by: Optional[int] = None
) -> LoanRepayment:
    repayment_data["loan_id"] = loan_id
    loan = EmployeeLoan.query.get(loan_id)
    if not loan:
        raise ValueError(f"Loan {loan_id} not found")

    amount = Decimal(str(repayment_data["amount"]))
    remaining = Decimal(str(loan.remaining_amount))
    if amount > remaining:
        raise ValueError(
            f"Repayment amount {amount} exceeds remaining loan balance {remaining}"
        )

    new_remaining = remaining - amount
    repayment_data["remaining_amount"] = new_remaining
    repayment = LoanRepayment(**repayment_data)
    db.session.add(repayment)
    loan.remaining_amount = new_remaining
    if new_remaining <= 0:
        loan.status = "completed"

    try:
        db.session.commit()
        _create_audit_log(
            "loan_repayment", repayment.id, "create", performed_by, None, repayment_data
        )
        db.session.commit()
        return repayment
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create loan repayment: {exc}") from exc


# ─────────────────────────────────────────────── Reimbursements
def create_reimbursement(
    employee_id: int, data: dict, performed_by: Optional[int] = None
) -> Reimbursement:
    data["employee_id"] = employee_id
    if "approved_amount" not in data:
        data["approved_amount"] = 0.00
    reimb = Reimbursement(**data)
    db.session.add(reimb)
    try:
        db.session.commit()
        _create_audit_log("reimbursement", reimb.id, "create", performed_by, None, data)
        db.session.commit()
        return reimb
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create reimbursement: {exc}") from exc


def approve_reimbursement(
    reimb_id: int,
    approved_by: int,
    approved_amount: Optional[float] = None,
    performed_by: Optional[int] = None,
) -> Reimbursement:
    reimb = Reimbursement.query.get(reimb_id)
    if not reimb:
        raise ValueError(f"Reimbursement {reimb_id} not found")
    if reimb.status != "pending":
        raise ValueError(
            f"Reimbursement {reimb_id} is not pending (status: {reimb.status})"
        )
    old = {"status": reimb.status}
    reimb.status = "approved"
    reimb.approved_by = approved_by
    reimb.approved_at = datetime.now(timezone.utc)
    if approved_amount is not None:
        reimb.approved_amount = approved_amount
    else:
        reimb.approved_amount = reimb.amount
    try:
        db.session.commit()
        _create_audit_log(
            "reimbursement", reimb.id, "approve", performed_by, old,
            {"status": "approved", "approved_by": approved_by,
             "approved_amount": float(reimb.approved_amount)},
        )
        db.session.commit()
        return reimb
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to approve reimbursement: {exc}") from exc