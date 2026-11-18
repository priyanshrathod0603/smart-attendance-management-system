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

import calendar
import re
import secrets
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
    CostCenter,
    JobLevel,
    JobGrade,
    EmploymentType,
    Team,
    EmployeeEmergencyContact,
    EmployeeDependent,
    EmployeeDepartmentHistory,
    EmployeeDesignationHistory,
    EmployeeManagerHistory,
    EmployeeStatusHistory,
    EmployeePromotion,
    EmployeeTimelineEvent,
    EmployeeDocument,
    PayrollRecord,
    PayrollEarning,
    PayrollDeduction,
    Payslip,
    PayrollAttendanceSummary,
)
from database.enterprise_models import (
    TeacherAttendance,
    AttendanceEvent,
    TeacherTimeRule,
    HolidayCalendar,
    WeeklyOff,
    AttendanceCorrection,
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


def _dec(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ─────────────────────────────────────────────── audit helper
def _create_audit_log(
    entity_type: str,
    entity_id: int,
    action: str,
    performed_by: Optional[int],
    old_values: Optional[dict],
    new_values: Optional[dict],
    reason: Optional[str] = None,
    school_id: Optional[str] = None,
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
            school_id=school_id,
        )
        db.session.add(log)
    except Exception:
        pass  # Audit log failure must never block the main operation


# ─────────────────────────────────────────────── Department
def _resolve_master_args(data, performed_by, school_id, kwargs):
    payload = dict(data or {})
    payload.update(kwargs)
    resolved_school_id = school_id or payload.get("school_id")
    resolved_performed_by = performed_by
    if resolved_school_id is None and performed_by is not None:
        resolved_school_id = performed_by
        resolved_performed_by = None
    if resolved_school_id is not None:
        payload["school_id"] = resolved_school_id
    return payload, resolved_performed_by, resolved_school_id


# ─────────────────────────────────────────────── Department
def create_department(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> Department:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    q_check = Department.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Department code '{payload['code']}' already exists")
    dept = Department(**payload)
    db.session.add(dept)
    try:
        db.session.commit()
        _create_audit_log("department", dept.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return dept
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create department: {exc}") from exc


def get_department(dept_id: int, school_id: Optional[str | int] = None) -> Optional[Department]:
    dept = Department.query.get(dept_id)
    if dept and school_id and dept.school_id and str(dept.school_id) != str(school_id):
        return None
    return dept


def get_departments(active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[Department]:
    if not isinstance(active_only, bool):
        school_id = active_only
        active_only = kwargs.get("active_only", False)
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    q = Department.query
    if school_id is not None:
        q = q.filter((Department.school_id == school_id) | (Department.school_id.is_(None)))
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(Department.name).all()


def update_department(
    dept_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> Department:
    dept = Department.query.get(dept_id)
    if not dept or (school_id and dept.school_id and str(dept.school_id) != str(school_id)):
        raise ValueError(f"Department {dept_id} not found")
    old = {"name": dept.name, "code": dept.code, "status": dept.status}
    if "code" in data and data["code"] != dept.code:
        q_chk = Department.query.filter_by(code=data["code"])
        if school_id or dept.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or dept.school_id)
        if q_chk.first():
            raise ValueError(f"Department code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(dept, k):
            setattr(dept, k, v)
    try:
        db.session.commit()
        _create_audit_log("department", dept.id, "update", performed_by, old, data, school_id=school_id or dept.school_id)
        db.session.commit()
        return dept
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update department: {exc}") from exc


# ─────────────────────────────────────────────── Designation
def create_designation(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> Designation:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    q_check = Designation.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Designation code '{payload['code']}' already exists")
    desig = Designation(**payload)
    db.session.add(desig)
    try:
        db.session.commit()
        _create_audit_log("designation", desig.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return desig
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create designation: {exc}") from exc


def get_designation(desig_id: int, school_id: Optional[str | int] = None) -> Optional[Designation]:
    desig = Designation.query.get(desig_id)
    if desig and school_id and desig.school_id and str(desig.school_id) != str(school_id):
        return None
    return desig


def get_designations(department_id: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> List[Designation]:
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    if "department_id" in kwargs and department_id is None:
        department_id = kwargs["department_id"]
    if department_id is not None and school_id is None and "department_id" not in kwargs:
        school_id = department_id
        department_id = None

    q = Designation.query
    if school_id is not None:
        q = q.filter((Designation.school_id == school_id) | (Designation.school_id.is_(None)))
    if department_id is not None:
        q = q.filter_by(department_id=department_id)
    return q.order_by(Designation.name).all()


def update_designation(
    desig_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> Designation:
    desig = Designation.query.get(desig_id)
    if not desig or (school_id and desig.school_id and str(desig.school_id) != str(school_id)):
        raise ValueError(f"Designation {desig_id} not found")
    old = {"name": desig.name, "code": desig.code, "status": desig.status}
    for k, v in data.items():
        if hasattr(desig, k):
            setattr(desig, k, v)
    try:
        db.session.commit()
        _create_audit_log("designation", desig.id, "update", performed_by, old, data, school_id=school_id or desig.school_id)
        db.session.commit()
        return desig
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update designation: {exc}") from exc


# ─────────────────────────────────────────────── Cost Center
def create_cost_center(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> CostCenter:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    q_check = CostCenter.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Cost Center code '{payload['code']}' already exists")
    cc = CostCenter(**payload)
    db.session.add(cc)
    try:
        db.session.commit()
        _create_audit_log("cost_center", cc.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return cc
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create cost center: {exc}") from exc


def get_cost_center(cost_center_id: int, school_id: Optional[str | int] = None) -> Optional[CostCenter]:
    cc = CostCenter.query.get(cost_center_id)
    if cc and school_id is not None and cc.school_id is not None and str(cc.school_id) != str(school_id):
        return None
    return cc


def get_cost_centers(active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[CostCenter]:
    if not isinstance(active_only, bool):
        school_id = active_only
        active_only = kwargs.get("active_only", False)
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    q = CostCenter.query
    if school_id is not None:
        q = q.filter((CostCenter.school_id == school_id) | (CostCenter.school_id.is_(None)))
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(CostCenter.name).all()


def update_cost_center(cost_center_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> CostCenter:
    cc = CostCenter.query.get(cost_center_id)
    if not cc or (school_id is not None and cc.school_id is not None and str(cc.school_id) != str(school_id)):
        raise ValueError(f"Cost Center {cost_center_id} not found")
    old = cc.to_dict()
    if "code" in data and data["code"] != cc.code:
        q_chk = CostCenter.query.filter_by(code=data["code"])
        if school_id or cc.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or cc.school_id)
        if q_chk.first():
            raise ValueError(f"Cost Center code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(cc, k) and k not in ("id", "school_id"):
            setattr(cc, k, v)
    try:
        db.session.commit()
        _create_audit_log("cost_center", cc.id, "update", performed_by, old, data, school_id=school_id or cc.school_id)
        db.session.commit()
        return cc
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update cost center: {exc}") from exc


def delete_cost_center(cost_center_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    cc = CostCenter.query.get(cost_center_id)
    if not cc or (school_id is not None and cc.school_id is not None and str(cc.school_id) != str(school_id)):
        raise ValueError(f"Cost Center {cost_center_id} not found")
    old = {"status": cc.status}
    cc.status = "inactive"
    try:
        db.session.commit()
        _create_audit_log("cost_center", cc.id, "update", performed_by, old, {"status": "inactive"}, school_id=school_id or cc.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete cost center: {exc}") from exc


# ─────────────────────────────────────────────── Job Level
def create_job_level(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> JobLevel:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    if "rank" in payload and "level_rank" not in payload:
        payload["level_rank"] = payload.pop("rank")
    q_check = JobLevel.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Job Level code '{payload['code']}' already exists")
    jl = JobLevel(**payload)
    db.session.add(jl)
    try:
        db.session.commit()
        _create_audit_log("job_level", jl.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return jl
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create job level: {exc}") from exc


def get_job_level(job_level_id: int, school_id: Optional[str | int] = None) -> Optional[JobLevel]:
    jl = JobLevel.query.get(job_level_id)
    if jl and school_id is not None and jl.school_id is not None and str(jl.school_id) != str(school_id):
        return None
    return jl


def get_job_levels(active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[JobLevel]:
    if not isinstance(active_only, bool):
        school_id = active_only
        active_only = kwargs.get("active_only", False)
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    q = JobLevel.query
    if school_id is not None:
        q = q.filter((JobLevel.school_id == school_id) | (JobLevel.school_id.is_(None)))
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(JobLevel.level_rank.asc(), JobLevel.name.asc()).all()


def update_job_level(job_level_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> JobLevel:
    jl = JobLevel.query.get(job_level_id)
    if not jl or (school_id is not None and jl.school_id is not None and str(jl.school_id) != str(school_id)):
        raise ValueError(f"Job Level {job_level_id} not found")
    if "rank" in data and "level_rank" not in data:
        data["level_rank"] = data.pop("rank")
    old = jl.to_dict()
    if "code" in data and data["code"] != jl.code:
        q_chk = JobLevel.query.filter_by(code=data["code"])
        if school_id or jl.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or jl.school_id)
        if q_chk.first():
            raise ValueError(f"Job Level code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(jl, k) and k not in ("id", "school_id"):
            setattr(jl, k, v)
    try:
        db.session.commit()
        _create_audit_log("job_level", jl.id, "update", performed_by, old, data, school_id=school_id or jl.school_id)
        db.session.commit()
        return jl
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update job level: {exc}") from exc


def delete_job_level(job_level_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    jl = JobLevel.query.get(job_level_id)
    if not jl or (school_id is not None and jl.school_id is not None and str(jl.school_id) != str(school_id)):
        raise ValueError(f"Job Level {job_level_id} not found")
    old = {"status": jl.status}
    jl.status = "inactive"
    try:
        db.session.commit()
        _create_audit_log("job_level", jl.id, "update", performed_by, old, {"status": "inactive"}, school_id=school_id or jl.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete job level: {exc}") from exc


# ─────────────────────────────────────────────── Job Grade
def create_job_grade(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> JobGrade:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    min_sal = payload.get("min_salary")
    max_sal = payload.get("max_salary")
    if min_sal is not None and max_sal is not None:
        if _dec(min_sal) > _dec(max_sal):
            raise ValueError("min_salary cannot be greater than max_salary")
    q_check = JobGrade.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Job Grade code '{payload['code']}' already exists")
    jg = JobGrade(**payload)
    db.session.add(jg)
    try:
        db.session.commit()
        _create_audit_log("job_grade", jg.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return jg
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create job grade: {exc}") from exc


def get_job_grade(job_grade_id: int, school_id: Optional[str | int] = None) -> Optional[JobGrade]:
    jg = JobGrade.query.get(job_grade_id)
    if jg and school_id is not None and jg.school_id is not None and str(jg.school_id) != str(school_id):
        return None
    return jg


def get_job_grades(active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[JobGrade]:
    if not isinstance(active_only, bool):
        school_id = active_only
        active_only = kwargs.get("active_only", False)
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    q = JobGrade.query
    if school_id is not None:
        q = q.filter((JobGrade.school_id == school_id) | (JobGrade.school_id.is_(None)))
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(JobGrade.name).all()


def update_job_grade(job_grade_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> JobGrade:
    jg = JobGrade.query.get(job_grade_id)
    if not jg or (school_id is not None and jg.school_id is not None and str(jg.school_id) != str(school_id)):
        raise ValueError(f"Job Grade {job_grade_id} not found")
    min_sal = data.get("min_salary", jg.min_salary)
    max_sal = data.get("max_salary", jg.max_salary)
    if min_sal is not None and max_sal is not None:
        if _dec(min_sal) > _dec(max_sal):
            raise ValueError("min_salary cannot be greater than max_salary")
    old = jg.to_dict()
    if "code" in data and data["code"] != jg.code:
        q_chk = JobGrade.query.filter_by(code=data["code"])
        if school_id or jg.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or jg.school_id)
        if q_chk.first():
            raise ValueError(f"Job Grade code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(jg, k) and k not in ("id", "school_id"):
            setattr(jg, k, v)
    try:
        db.session.commit()
        _create_audit_log("job_grade", jg.id, "update", performed_by, old, data, school_id=school_id or jg.school_id)
        db.session.commit()
        return jg
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update job grade: {exc}") from exc


def delete_job_grade(job_grade_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    jg = JobGrade.query.get(job_grade_id)
    if not jg or (school_id is not None and jg.school_id is not None and str(jg.school_id) != str(school_id)):
        raise ValueError(f"Job Grade {job_grade_id} not found")
    old = {"status": jg.status}
    jg.status = "inactive"
    try:
        db.session.commit()
        _create_audit_log("job_grade", jg.id, "update", performed_by, old, {"status": "inactive"}, school_id=school_id or jg.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete job grade: {exc}") from exc


# ─────────────────────────────────────────────── Employment Type
def create_employment_type(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> EmploymentType:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    q_check = EmploymentType.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Employment Type code '{payload['code']}' already exists")
    et = EmploymentType(**payload)
    db.session.add(et)
    try:
        db.session.commit()
        _create_audit_log("employment_type", et.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return et
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create employment type: {exc}") from exc


def get_employment_type(employment_type_id: int, school_id: Optional[str | int] = None) -> Optional[EmploymentType]:
    et = EmploymentType.query.get(employment_type_id)
    if et and school_id is not None and et.school_id is not None and str(et.school_id) != str(school_id):
        return None
    return et


def get_employment_types(active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[EmploymentType]:
    if not isinstance(active_only, bool):
        school_id = active_only
        active_only = kwargs.get("active_only", False)
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    q = EmploymentType.query
    if school_id is not None:
        q = q.filter((EmploymentType.school_id == school_id) | (EmploymentType.school_id.is_(None)))
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(EmploymentType.name).all()


def update_employment_type(employment_type_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> EmploymentType:
    et = EmploymentType.query.get(employment_type_id)
    if not et or (school_id is not None and et.school_id is not None and str(et.school_id) != str(school_id)):
        raise ValueError(f"Employment Type {employment_type_id} not found")
    old = et.to_dict()
    if "code" in data and data["code"] != et.code:
        q_chk = EmploymentType.query.filter_by(code=data["code"])
        if school_id or et.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or et.school_id)
        if q_chk.first():
            raise ValueError(f"Employment Type code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(et, k) and k not in ("id", "school_id"):
            setattr(et, k, v)
    try:
        db.session.commit()
        _create_audit_log("employment_type", et.id, "update", performed_by, old, data, school_id=school_id or et.school_id)
        db.session.commit()
        return et
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update employment type: {exc}") from exc


def delete_employment_type(employment_type_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    et = EmploymentType.query.get(employment_type_id)
    if not et or (school_id is not None and et.school_id is not None and str(et.school_id) != str(school_id)):
        raise ValueError(f"Employment Type {employment_type_id} not found")
    old = {"status": et.status}
    et.status = "inactive"
    try:
        db.session.commit()
        _create_audit_log("employment_type", et.id, "update", performed_by, old, {"status": "inactive"}, school_id=school_id or et.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete employment type: {exc}") from exc


# ─────────────────────────────────────────────── Team
def create_team(data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> Team:
    payload, performed_by, school_id = _resolve_master_args(data, performed_by, school_id, kwargs)
    q_check = Team.query.filter_by(code=payload.get("code"))
    if school_id is not None:
        q_check = q_check.filter_by(school_id=school_id)
    if q_check.first():
        raise ValueError(f"Team code '{payload['code']}' already exists")
    team = Team(**payload)
    db.session.add(team)
    try:
        db.session.commit()
        _create_audit_log("team", team.id, "create", performed_by, None, payload, school_id=school_id)
        db.session.commit()
        return team
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create team: {exc}") from exc


def get_team(team_id: int, school_id: Optional[str | int] = None) -> Optional[Team]:
    team = Team.query.get(team_id)
    if team and school_id is not None and team.school_id is not None and str(team.school_id) != str(school_id):
        return None
    return team


def get_teams(department_id: Optional[int] = None, active_only: bool = False, school_id: Optional[str | int] = None, **kwargs) -> List[Team]:
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    if "department_id" in kwargs and department_id is None:
        department_id = kwargs["department_id"]
    if "active_only" in kwargs:
        active_only = kwargs["active_only"]
    if department_id is not None and school_id is None and "department_id" not in kwargs:
        school_id = department_id
        department_id = None

    q = Team.query
    if school_id is not None:
        q = q.filter((Team.school_id == school_id) | (Team.school_id.is_(None)))
    if department_id:
        q = q.filter_by(department_id=department_id)
    if active_only:
        q = q.filter_by(status="active")
    return q.order_by(Team.name).all()


def update_team(team_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> Team:
    team = Team.query.get(team_id)
    if not team or (school_id is not None and team.school_id is not None and str(team.school_id) != str(school_id)):
        raise ValueError(f"Team {team_id} not found")
    old = team.to_dict()
    if "code" in data and data["code"] != team.code:
        q_chk = Team.query.filter_by(code=data["code"])
        if school_id or team.school_id:
            q_chk = q_chk.filter_by(school_id=school_id or team.school_id)
        if q_chk.first():
            raise ValueError(f"Team code '{data['code']}' already exists")
    for k, v in data.items():
        if hasattr(team, k) and k not in ("id", "school_id"):
            setattr(team, k, v)
    try:
        db.session.commit()
        _create_audit_log("team", team.id, "update", performed_by, old, data, school_id=school_id or team.school_id)
        db.session.commit()
        return team
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update team: {exc}") from exc


def delete_team(team_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    team = Team.query.get(team_id)
    if not team or (school_id is not None and team.school_id is not None and str(team.school_id) != str(school_id)):
        raise ValueError(f"Team {team_id} not found")
    old = {"status": team.status}
    team.status = "inactive"
    try:
        db.session.commit()
        _create_audit_log("team", team.id, "update", performed_by, old, {"status": "inactive"}, school_id=school_id or team.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete team: {exc}") from exc


# ─────────────────────────────────────────────── Reporting Hierarchy & Circular Prevention
def validate_reporting_hierarchy(
    employee_id: int,
    proposed_manager_id: Optional[int],
    school_id: Optional[str | int] = None,
) -> tuple[bool, str]:
    """Validate that assigning proposed_manager_id to employee_id doesn't create a self-loop or circular chain."""
    if not proposed_manager_id:
        return True, ""
    if int(proposed_manager_id) == int(employee_id):
        return False, "Employee cannot report to themselves"

    manager = Employee.query.get(proposed_manager_id)
    if not manager:
        return False, f"Reporting manager {proposed_manager_id} not found"
    if school_id and manager.school_id and str(manager.school_id) != str(school_id):
        return False, "Reporting manager must belong to the same institution"

    visited = {int(employee_id), int(proposed_manager_id)}
    curr = manager
    depth = 0
    max_depth = 100
    while curr.reporting_manager_id is not None and depth < max_depth:
        depth += 1
        if curr.reporting_manager_id == int(employee_id):
            return False, f"Circular reporting hierarchy detected: {manager.full_name} reports to {curr.full_name} which eventually leads to this employee"
        if curr.reporting_manager_id in visited:
            break
        visited.add(curr.reporting_manager_id)
        curr = Employee.query.get(curr.reporting_manager_id)
        if not curr:
            break
    return True, ""


# ─────────────────────────────────────────────── Employee CRUD
def create_employee(employee_data: Optional[dict] = None, performed_by: Optional[int] = None, school_id: Optional[str | int] = None, **kwargs) -> Employee:
    payload, performed_by, school_id = _resolve_master_args(employee_data, performed_by, school_id, kwargs)
    q_code = Employee.query.filter_by(employee_code=payload.get("employee_code"))
    q_email = Employee.query.filter_by(email=payload.get("email"))
    if school_id is not None:
        q_code = q_code.filter_by(school_id=school_id)
        q_email = q_email.filter_by(school_id=school_id)
    if q_code.first():
        raise ValueError(f"Employee code '{payload['employee_code']}' already exists")
    if q_email.first():
        raise ValueError(f"Email '{payload['email']}' already exists")
    if payload.get("teacher_id"):
        q_t = Employee.query.filter_by(teacher_id=payload["teacher_id"])
        if school_id is not None:
            q_t = q_t.filter_by(school_id=school_id)
        if q_t.first():
            raise ValueError(
                f"Teacher {payload['teacher_id']} is already linked to an employee"
            )

    if not payload.get("qr_identifier"):
        payload["qr_identifier"] = f"EOS-QR-{secrets.token_hex(12).upper()}"

    if not payload.get("employment_type"):
        payload["employment_type"] = "full_time"
    if not payload.get("employment_status"):
        payload["employment_status"] = "active"

    basic_salary = payload.pop("basic_salary", None)
    overtime_rate = payload.pop("overtime_rate", 0)
    pay_frequency = payload.pop("pay_frequency", "monthly")
    salary_effective_from = payload.pop("salary_effective_from", None)

    if payload.get("reporting_manager_id"):
        # Temporary instance ID 0 for validation
        is_valid, err_msg = validate_reporting_hierarchy(0, payload["reporting_manager_id"], school_id)
        if not is_valid:
            raise ValueError(err_msg)

    emp = Employee(**payload)
    db.session.add(emp)
    try:
        db.session.commit()

        # Seed initial history and timeline records
        joining = emp.joining_date or date.today()
        if emp.department_id:
            dept_hist = EmployeeDepartmentHistory(
                employee_id=emp.id,
                previous_department_id=None,
                new_department_id=emp.department_id,
                effective_date=joining,
                reason="Initial department assignment on employee onboarding",
                changed_by_id=performed_by,
                school_id=school_id or emp.school_id,
            )
            db.session.add(dept_hist)

        if emp.designation_id:
            desig_hist = EmployeeDesignationHistory(
                employee_id=emp.id,
                previous_designation_id=None,
                new_designation_id=emp.designation_id,
                effective_date=joining,
                reason="Initial designation assignment on employee onboarding",
                approved_by_id=performed_by,
                school_id=school_id or emp.school_id,
            )
            db.session.add(desig_hist)

        if emp.reporting_manager_id:
            mgr_hist = EmployeeManagerHistory(
                employee_id=emp.id,
                previous_manager_id=None,
                new_manager_id=emp.reporting_manager_id,
                effective_date=joining,
                reason="Initial manager assignment on employee onboarding",
                changed_by_id=performed_by,
                school_id=school_id or emp.school_id,
            )
            db.session.add(mgr_hist)

        status_hist = EmployeeStatusHistory(
            employee_id=emp.id,
            previous_status=None,
            new_status=emp.employment_status or "active",
            effective_date=joining,
            reason="Initial Onboarding",
            changed_by_id=performed_by,
            school_id=school_id or emp.school_id,
        )
        db.session.add(status_hist)

        if emp.emergency_contact_name and emp.emergency_contact_phone:
            emg_c = EmployeeEmergencyContact(
                employee_id=emp.id,
                name=emp.emergency_contact_name,
                relationship="Emergency Contact",
                phone=emp.emergency_contact_phone,
                is_primary=True,
                school_id=school_id or emp.school_id,
            )
            db.session.add(emg_c)

        event_dt = datetime.combine(joining, datetime.min.time(), tzinfo=timezone.utc)
        tl = EmployeeTimelineEvent(
            employee_id=emp.id,
            event_type="employee_created",
            title="Employee Profile Created",
            description=f"Joined organization with code {emp.employee_code}",
            event_time=event_dt,
            actor_id=performed_by,
            school_id=school_id or emp.school_id,
        )
        db.session.add(tl)

        if basic_salary is not None:
            create_salary_structure(emp.id, {
                "basic_salary": basic_salary,
                "gross_salary": basic_salary,
                "overtime_rate": overtime_rate or 0,
                "pay_frequency": pay_frequency or "monthly",
                "effective_from": salary_effective_from or joining,
                "status": "active"
            }, performed_by=performed_by, school_id=school_id)

        db.session.commit()

        # Don't log sensitive fields
        safe = {k: v for k, v in payload.items()
                if k not in ("bank_account_number", "pan_number")}
        _create_audit_log("employee", emp.id, "create", performed_by, None, safe, school_id=school_id)
        db.session.commit()
        return emp
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create employee: {exc}") from exc


class EmployeeResult(tuple):
    def __new__(cls, employees, total):
        return super(EmployeeResult, cls).__new__(cls, (employees, total))

    def __getitem__(self, item):
        if isinstance(item, str):
            if item == "employees":
                return self[0]
            elif item == "total":
                return self[1]
            raise KeyError(item)
        return super(EmployeeResult, self).__getitem__(item)

    def get(self, key, default=None):
        if key == "employees":
            return self[0]
        elif key == "total":
            return self[1]
        return default


def get_employee(employee_id: int, school_id: Optional[str | int] = None) -> Optional[Employee]:
    emp = Employee.query.get(employee_id)
    if emp and school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id):
        return None
    return emp


def get_employee_by_code(employee_code: str, school_id: Optional[str | int] = None) -> Optional[Employee]:
    q = Employee.query.filter_by(employee_code=employee_code)
    if school_id is not None:
        q = q.filter_by(school_id=school_id)
    return q.first()


def get_employee_by_teacher_id(teacher_id: int, school_id: Optional[str | int] = None) -> Optional[Employee]:
    q = Employee.query.filter_by(teacher_id=teacher_id)
    if school_id is not None:
        q = q.filter_by(school_id=school_id)
    return q.first()


def get_employees(
    filters: Optional[dict] = None, page: int = 1, per_page: int = 50, school_id: Optional[str | int] = None, **kwargs
) -> EmployeeResult:
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        filters = {}
    if "school_id" in kwargs and school_id is None:
        school_id = kwargs["school_id"]
    if "page" in kwargs:
        page = kwargs["page"]
    if "per_page" in kwargs:
        per_page = kwargs["per_page"]

    q = Employee.query
    if school_id is not None:
        q = q.filter((Employee.school_id == school_id) | (Employee.school_id.is_(None)))
    if filters:
        if filters.get("department_id"):
            q = q.filter(Employee.department_id == int(filters["department_id"]))
        if filters.get("designation_id"):
            q = q.filter(Employee.designation_id == int(filters["designation_id"]))
        if filters.get("team_id"):
            q = q.filter(Employee.team_id == int(filters["team_id"]))
        if filters.get("job_grade_id"):
            q = q.filter(Employee.job_grade_id == int(filters["job_grade_id"]))
        if filters.get("job_level_id"):
            q = q.filter(Employee.job_level_id == int(filters["job_level_id"]))
        if filters.get("cost_center_id"):
            q = q.filter(Employee.cost_center_id == int(filters["cost_center_id"]))
        if filters.get("reporting_manager_id"):
            q = q.filter(Employee.reporting_manager_id == int(filters["reporting_manager_id"]))
        if filters.get("campus"):
            q = q.filter(Employee.campus == filters["campus"])
        if filters.get("employment_type"):
            q = q.filter(Employee.employment_type == filters["employment_type"])
        if filters.get("employment_type_id"):
            q = q.filter(Employee.employment_type_id == int(filters["employment_type_id"]))
        if filters.get("employment_status"):
            q = q.filter(Employee.employment_status == filters["employment_status"])
        if filters.get("probation_status"):
            q = q.filter(Employee.probation_status == filters["probation_status"])
        if filters.get("is_active") is not None:
            val = filters["is_active"]
            if isinstance(val, str):
                val = val.lower() in ("true", "1", "yes")
            q = q.filter(Employee.is_active == val)
        if filters.get("search"):
            term = f"%{filters['search']}%"
            q = q.filter(
                or_(
                    Employee.first_name.ilike(term),
                    Employee.last_name.ilike(term),
                    Employee.employee_code.ilike(term),
                    Employee.email.ilike(term),
                    Employee.phone.ilike(term),
                )
            )
    total = q.count()
    employees = q.order_by(Employee.employee_code.asc()).offset((page - 1) * per_page).limit(per_page).all()
    return EmployeeResult(employees, total)


def ensure_employee_from_teacher(
    teacher: Teacher,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
) -> Employee:
    """Ensure a 1:1 fully connected HR Employee profile exists for a Teacher record.
    
    Guarantees:
    - Same unique Staff ID: employee_code matches teacher.teacher_id.
    - Preserves existing role, department, designation, contact, photo, and active status.
    - Connects teacher_id foreign key seamlessly.
    - Fully idempotent and transaction-safe.
    """
    sid = school_id or teacher.school_id
    # 1. Check if already linked by foreign key
    emp = Employee.query.filter_by(teacher_id=teacher.id).first()
    raw_tid = (teacher.teacher_id or "").strip()

    # 2. If not linked by FK, check by matching employee_code or EMP- prefix
    if not emp and raw_tid:
        q_code = Employee.query.filter(
            or_(
                Employee.employee_code == raw_tid,
                Employee.employee_code == f"EMP-{raw_tid}"
            )
        )
        if sid:
            q_code = q_code.filter((Employee.school_id == sid) | (Employee.school_id.is_(None)))
        emp = q_code.first()
        if emp:
            emp.teacher_id = teacher.id

    # 3. Resolve or auto-create Department
    dept = None
    if teacher.department:
        dept_q = Department.query.filter(
            or_(
                Department.name.ilike(teacher.department.strip()),
                Department.code.ilike(teacher.department.strip())
            )
        )
        if sid:
            dept_q = dept_q.filter((Department.school_id == sid) | (Department.school_id.is_(None)))
        dept = dept_q.first()
        if not dept:
            dept_code = re.sub(r'[^A-Z0-9]', '', teacher.department.strip().upper())[:10] or "DEPT"
            if Department.query.filter_by(code=dept_code).first():
                dept_code = f"{dept_code}_{teacher.id}"
            dept = Department(
                school_id=sid,
                name=teacher.department.strip(),
                code=dept_code,
                description=f"{teacher.department.strip()} Department",
                status="active"
            )
            db.session.add(dept)
            db.session.flush()

    if not dept:
        dept_q = Department.query.filter_by(code="ACAD")
        if sid:
            dept_q = dept_q.filter((Department.school_id == sid) | (Department.school_id.is_(None)))
        dept = dept_q.first()
        if not dept:
            dept = Department(
                school_id=sid,
                name="Academics",
                code="ACAD",
                description="Academic Department",
                status="active"
            )
            db.session.add(dept)
            db.session.flush()

    # 4. Resolve or auto-create Designation
    desig = None
    if teacher.designation:
        desig_q = Designation.query.filter(
            or_(
                Designation.name.ilike(teacher.designation.strip()),
                Designation.code.ilike(teacher.designation.strip())
            )
        )
        if sid:
            desig_q = desig_q.filter((Designation.school_id == sid) | (Designation.school_id.is_(None)))
        desig = desig_q.first()
        if not desig:
            desig_code = re.sub(r'[^A-Z0-9]', '', teacher.designation.strip().upper())[:10] or "TCH"
            if Designation.query.filter_by(code=desig_code).first():
                desig_code = f"{desig_code}_{teacher.id}"
            desig = Designation(
                school_id=sid,
                name=teacher.designation.strip(),
                code=desig_code,
                department_id=dept.id if dept else None,
                description=teacher.designation.strip(),
                status="active"
            )
            db.session.add(desig)
            db.session.flush()

    if not desig:
        desig_q = Designation.query.filter_by(code="TCH")
        if sid:
            desig_q = desig_q.filter((Designation.school_id == sid) | (Designation.school_id.is_(None)))
        desig = desig_q.first()
        if not desig:
            desig = Designation(
                school_id=sid,
                name="Teacher",
                code="TCH",
                department_id=dept.id if dept else None,
                description="Teaching Faculty",
                status="active"
            )
            db.session.add(desig)
            db.session.flush()

    # Split name safely
    parts = (teacher.name or "Teacher").strip().split(maxsplit=2)
    if len(parts) == 1:
        first_name, middle_name, last_name = parts[0], None, "Staff"
    elif len(parts) == 2:
        first_name, middle_name, last_name = parts[0], None, parts[1]
    else:
        first_name, middle_name, last_name = parts[0], parts[1], parts[2]

    joining_d = teacher.created_at.date() if getattr(teacher, "created_at", None) else date.today()
    emp_email = (teacher.email or f"teacher_{teacher.id}@school.edu").strip().lower()

    if emp:
        # Update existing employee with teacher master info
        emp.first_name = first_name
        emp.middle_name = middle_name
        emp.last_name = last_name
        if teacher.mobile:
            emp.phone = teacher.mobile
        if teacher.email:
            emp.email = teacher.email
        if teacher.photo_path:
            emp.profile_image = teacher.photo_path
        if dept:
            emp.department_id = dept.id
        if desig:
            emp.designation_id = desig.id
        emp.is_active = getattr(teacher, "is_active", True)
        emp.employment_status = "active" if emp.is_active else "inactive"
        if not emp.qr_identifier:
            emp.qr_identifier = f"EOS-QR-{secrets.token_hex(12).upper()}"
        db.session.flush()
        return emp

    # Generate candidate code matching teacher_id
    code_candidate = raw_tid if raw_tid else f"TCH-{teacher.id}"
    if Employee.query.filter_by(employee_code=code_candidate).first():
        code_candidate = f"EMP-{raw_tid}" if not raw_tid.startswith("EMP-") else f"{raw_tid}-{teacher.id}"

    if Employee.query.filter_by(email=emp_email).first():
        emp_email = f"teacher_{teacher.id}_{raw_tid.lower()}@school.edu"

    emp = Employee(
        school_id=sid,
        employee_code=code_candidate,
        teacher_id=teacher.id,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        gender="other",
        joining_date=joining_d,
        employment_type="full_time",
        employment_status="active" if getattr(teacher, "is_active", True) else "inactive",
        department_id=dept.id if dept else None,
        designation_id=desig.id if desig else None,
        phone=teacher.mobile or "0000000000",
        email=emp_email,
        profile_image=teacher.photo_path,
        qr_identifier=f"EOS-QR-{secrets.token_hex(12).upper()}",
        is_active=getattr(teacher, "is_active", True),
    )
    db.session.add(emp)
    db.session.flush()

    # Timeline event
    event_dt = datetime.combine(joining_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        school_id=sid,
        employee_id=emp.id,
        event_type="employee_created",
        title="Employee Profile Created",
        description=f"Master staff record initialized with Staff ID {emp.employee_code} ({teacher.name})",
        event_time=event_dt,
        actor_id=performed_by,
    )
    db.session.add(tl)
    db.session.flush()
    return emp


def ensure_teacher_from_employee(
    employee: Employee,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
) -> Optional[Teacher]:
    """If an employee represents a teacher/faculty member, ensure a linked Teacher record exists."""
    sid = school_id or employee.school_id
    if employee.teacher_id:
        t = Teacher.query.get(employee.teacher_id)
        if t:
            t.name = employee.full_name
            t.teacher_id = employee.employee_code
            if employee.email:
                t.email = employee.email
            if employee.phone:
                t.mobile = employee.phone
            if employee.department:
                t.department = employee.department.name
            if employee.designation:
                t.designation = employee.designation.name
            if employee.profile_image:
                t.photo_path = employee.profile_image
            t.is_active = employee.is_active
            db.session.flush()
            return t

    # Check if designation/department indicates teaching faculty
    is_teaching = False
    if employee.designation and any(x in employee.designation.name.lower() for x in ["teacher", "faculty", "professor", "instructor", "lecturer", "hod", "principal"]):
        is_teaching = True
    elif employee.department and any(x in employee.department.name.lower() for x in ["acad", "teach", "faculty", "science", "math", "english", "arts"]):
        is_teaching = True

    if not is_teaching:
        return None

    # Check existing teacher by teacher_id
    t = Teacher.query.filter_by(teacher_id=employee.employee_code).first()
    if not t:
        t = Teacher(
            school_id=sid,
            teacher_id=employee.employee_code,
            name=employee.full_name,
            email=employee.email,
            mobile=employee.phone,
            department=employee.department.name if employee.department else None,
            designation=employee.designation.name if employee.designation else None,
            photo_path=employee.profile_image,
            is_active=employee.is_active,
        )
        db.session.add(t)
        db.session.flush()

    employee.teacher_id = t.id
    db.session.flush()
    return t


def sync_teachers_to_employees(school_id: Optional[str | int] = None) -> dict:
    """Safely synchronize all existing Teachers into HR Employee records."""
    q = Teacher.query
    if school_id:
        q = q.filter((Teacher.school_id == school_id) | (Teacher.school_id.is_(None)))
    teachers = q.all()
    created = 0
    already_linked = 0
    skipped = 0

    for t in teachers:
        existing = Employee.query.filter_by(teacher_id=t.id).first()
        if existing:
            already_linked += 1
            # Ensure details are updated
            ensure_employee_from_teacher(t, school_id=school_id)
        else:
            ensure_employee_from_teacher(t, school_id=school_id)
            created += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Teacher sync failed: {exc}") from exc

    return {"created": created, "already_linked": already_linked, "skipped": skipped}


def update_employee(
    employee_id: int, update_data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> Employee:
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    old = {
        "employee_code": emp.employee_code,
        "first_name": emp.first_name,
        "last_name": emp.last_name,
        "email": emp.email,
        "department_id": emp.department_id,
        "designation_id": emp.designation_id,
        "reporting_manager_id": emp.reporting_manager_id,
        "employment_status": emp.employment_status,
        "is_active": emp.is_active,
    }

    if "employee_code" in update_data and update_data["employee_code"] != emp.employee_code:
        q_code = Employee.query.filter_by(employee_code=update_data["employee_code"])
        if school_id or emp.school_id:
            q_code = q_code.filter_by(school_id=school_id or emp.school_id)
        if q_code.first():
            raise ValueError(f"Employee code '{update_data['employee_code']}' already exists")

    if "email" in update_data and update_data["email"] != emp.email:
        q_email = Employee.query.filter_by(email=update_data["email"])
        if school_id or emp.school_id:
            q_email = q_email.filter_by(school_id=school_id or emp.school_id)
        if q_email.first():
            raise ValueError(f"Email '{update_data['email']}' already exists")

    if "teacher_id" in update_data and update_data["teacher_id"] != emp.teacher_id:
        if update_data["teacher_id"] is not None:
            q_t = Employee.query.filter_by(teacher_id=update_data["teacher_id"])
            if school_id or emp.school_id:
                q_t = q_t.filter_by(school_id=school_id or emp.school_id)
            conflict = q_t.first()
            if conflict and conflict.id != employee_id:
                raise ValueError(
                    f"Teacher {update_data['teacher_id']} is already linked to "
                    f"employee {conflict.id}"
                )

    if "reporting_manager_id" in update_data and update_data["reporting_manager_id"] != emp.reporting_manager_id:
        if update_data["reporting_manager_id"]:
            is_valid, msg = validate_reporting_hierarchy(employee_id, update_data["reporting_manager_id"], school_id or emp.school_id)
            if not is_valid:
                raise ValueError(msg)

    today = date.today()
    event_now = datetime.now(timezone.utc)
    if "department_id" in update_data and update_data["department_id"] != emp.department_id and update_data["department_id"] is not None:
        dept_id = int(update_data["department_id"])
        curr_dh = EmployeeDepartmentHistory.query.filter_by(employee_id=emp.id, end_date=None).all()
        for h in curr_dh:
            h.end_date = today
        new_dh = EmployeeDepartmentHistory(
            employee_id=emp.id,
            previous_department_id=emp.department_id,
            new_department_id=dept_id,
            effective_date=today,
            reason=update_data.get("department_change_reason", "Department updated"),
            changed_by_id=performed_by,
            school_id=school_id or emp.school_id,
        )
        db.session.add(new_dh)
        db.session.add(
            EmployeeTimelineEvent(
                employee_id=emp.id,
                event_type="department_transfer",
                title="Department Changed",
                description=f"Transferred to Department #{dept_id}",
                event_time=event_now,
                actor_id=performed_by,
                school_id=school_id or emp.school_id,
            )
        )

    if "designation_id" in update_data and update_data["designation_id"] != emp.designation_id and update_data["designation_id"] is not None:
        desig_id = int(update_data["designation_id"])
        curr_dsh = EmployeeDesignationHistory.query.filter_by(employee_id=emp.id, end_date=None).all()
        for h in curr_dsh:
            h.end_date = today
        new_dsh = EmployeeDesignationHistory(
            employee_id=emp.id,
            previous_designation_id=emp.designation_id,
            new_designation_id=desig_id,
            effective_date=today,
            reason=update_data.get("designation_change_reason", "Designation updated"),
            approved_by_id=performed_by,
            school_id=school_id or emp.school_id,
        )
        db.session.add(new_dsh)
        db.session.add(
            EmployeeTimelineEvent(
                employee_id=emp.id,
                event_type="designation_change",
                title="Designation Changed",
                description=f"Designation updated to #{desig_id}",
                event_time=event_now,
                actor_id=performed_by,
                school_id=school_id or emp.school_id,
            )
        )

    if "reporting_manager_id" in update_data and update_data["reporting_manager_id"] != emp.reporting_manager_id:
        mgr_id = update_data["reporting_manager_id"]
        if mgr_id:
            new_mh = EmployeeManagerHistory(
                employee_id=emp.id,
                previous_manager_id=emp.reporting_manager_id,
                new_manager_id=int(mgr_id),
                effective_date=today,
                reason=update_data.get("manager_change_reason", "Manager updated"),
                changed_by_id=performed_by,
                school_id=school_id or emp.school_id,
            )
            db.session.add(new_mh)
        db.session.add(
            EmployeeTimelineEvent(
                employee_id=emp.id,
                event_type="manager_change",
                title="Reporting Manager Changed",
                description=f"Reporting manager updated to #{mgr_id}" if mgr_id else "Reporting manager cleared",
                event_time=event_now,
                actor_id=performed_by,
                school_id=school_id or emp.school_id,
            )
        )

    if "employment_status" in update_data and update_data["employment_status"] != emp.employment_status:
        new_status = update_data["employment_status"]
        new_sh = EmployeeStatusHistory(
            employee_id=emp.id,
            previous_status=emp.employment_status,
            new_status=new_status,
            effective_date=today,
            reason=update_data.get("status_change_reason", "Status updated"),
            changed_by_id=performed_by,
            school_id=school_id or emp.school_id,
        )
        db.session.add(new_sh)
        if new_status in ("terminated", "resigned", "retired", "exited", "inactive"):
            emp.is_active = False
            if not emp.exit_date:
                emp.exit_date = today
            if "exit_reason" in update_data:
                emp.exit_reason = update_data["exit_reason"]
        elif new_status == "active":
            emp.is_active = True
            emp.exit_date = None
            emp.exit_reason = None

        db.session.add(
            EmployeeTimelineEvent(
                employee_id=emp.id,
                event_type="status_change",
                title="Employment Status Changed",
                description=f"Status changed from {emp.employment_status} to {new_status}",
                event_time=event_now,
                actor_id=performed_by,
                school_id=school_id or emp.school_id,
            )
        )

    for k, v in update_data.items():
        if hasattr(emp, k) and k not in (
            "id", "school_id", "department_change_reason", "designation_change_reason",
            "manager_change_reason", "status_change_reason"
        ):
            setattr(emp, k, v)

    # Sync back to linked Teacher to maintain Single Source of Truth
    if emp.teacher_id:
        t = Teacher.query.get(emp.teacher_id)
        if t:
            if "phone" in update_data:
                t.mobile = update_data["phone"]
            if "email" in update_data:
                t.email = update_data["email"]
            if "first_name" in update_data or "last_name" in update_data:
                t.name = emp.full_name
            if "is_active" in update_data or "employment_status" in update_data:
                t.is_active = emp.is_active

    if not emp.qr_identifier:
        emp.qr_identifier = f"EOS-QR-{secrets.token_hex(12).upper()}"

    try:
        db.session.commit()
        safe_new = {k: v for k, v in update_data.items()
                    if k not in ("bank_account_number", "pan_number")}
        _create_audit_log("employee", emp.id, "update", performed_by, old, safe_new, school_id=school_id or emp.school_id)
        db.session.commit()
        return emp
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update employee: {exc}") from exc


def delete_employee(employee_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    """Soft-delete an employee by setting is_active = False."""
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")
    old = {"is_active": emp.is_active}
    emp.is_active = False
    try:
        db.session.commit()
        _create_audit_log("employee", emp.id, "update", performed_by, old, {"is_active": False}, school_id=school_id or emp.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to deactivate employee: {exc}") from exc


# ─────────────────────────────────────────────── Employee Lifecycle & History Workflows
def transfer_employee_department(
    employee_id: int,
    new_department_id: int,
    effective_date: Optional[date] = None,
    reason: str = "",
    remarks: str = "",
    changed_by_id: Optional[int] = None,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
    **kwargs,
) -> EmployeeDepartmentHistory:
    eff_d = effective_date or kwargs.get("transfer_date") or date.today()
    if isinstance(eff_d, str):
        eff_d = datetime.strptime(eff_d, "%Y-%m-%d").date()
    actor_id = changed_by_id or performed_by or kwargs.get("actor_id")
    transfer_reason = reason or kwargs.get("transfer_reason") or remarks or "Department Transfer"

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError("Employee not found")
    new_dept = Department.query.get(new_department_id)
    if not new_dept or (school_id is not None and new_dept.school_id is not None and str(new_dept.school_id) != str(school_id)):
        raise ValueError("Department not found")
    if emp.department_id == new_department_id:
        raise ValueError(f"Employee is already in department {new_dept.name}")

    old_dept_name = emp.department.name if emp.department else "None"
    old_values = {"department_id": emp.department_id, "department_name": old_dept_name}

    curr_hist = EmployeeDepartmentHistory.query.filter_by(employee_id=emp.id, end_date=None).all()
    for h in curr_hist:
        h.end_date = eff_d - timedelta(days=1) if eff_d > h.effective_date else eff_d

    dept_hist = EmployeeDepartmentHistory(
        employee_id=emp.id,
        previous_department_id=emp.department_id,
        new_department_id=new_department_id,
        effective_date=eff_d,
        reason=transfer_reason,
        remarks=remarks,
        changed_by_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(dept_hist)
    emp.department_id = new_department_id

    event_dt = datetime.combine(eff_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        employee_id=emp.id,
        event_type="department_transfer",
        title="Department Transfer",
        description=f"Transferred from {old_dept_name} to {new_dept.name}. Reason: {transfer_reason}",
        event_time=event_dt,
        metadata_json={"from_dept_id": old_values["department_id"], "to_dept_id": new_department_id, "remarks": remarks},
        actor_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)

    try:
        db.session.commit()
        _create_audit_log(
            "employee", emp.id, "department_transfer", actor_id,
            old_values, {"department_id": new_department_id, "department_name": new_dept.name, "reason": transfer_reason},
            reason=transfer_reason, school_id=school_id or emp.school_id
        )
        db.session.commit()
        return dept_hist
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to transfer employee: {exc}") from exc


def change_employee_designation(
    employee_id: int,
    new_designation_id: int,
    effective_date: Optional[date] = None,
    reason: str = "",
    remarks: str = "",
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
    **kwargs,
) -> EmployeeDesignationHistory:
    eff_d = effective_date or date.today()
    if isinstance(eff_d, str):
        eff_d = datetime.strptime(eff_d, "%Y-%m-%d").date()
    actor_id = performed_by or kwargs.get("approved_by_id")
    desig_reason = reason or remarks or "Designation updated"

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")
    new_desig = Designation.query.get(new_designation_id)
    if not new_desig or (school_id is not None and new_desig.school_id is not None and str(new_desig.school_id) != str(school_id)):
        raise ValueError(f"Target designation {new_designation_id} not found")
    if emp.designation_id == new_designation_id:
        raise ValueError(f"Employee already holds designation {new_desig.name}")

    old_desig_name = emp.designation.name if emp.designation else "None"
    old_values = {"designation_id": emp.designation_id, "designation_name": old_desig_name}

    curr_hist = EmployeeDesignationHistory.query.filter_by(employee_id=emp.id, end_date=None).all()
    for h in curr_hist:
        h.end_date = eff_d - timedelta(days=1) if eff_d > h.effective_date else eff_d

    desig_hist = EmployeeDesignationHistory(
        employee_id=emp.id,
        previous_designation_id=emp.designation_id,
        new_designation_id=new_designation_id,
        effective_date=eff_d,
        reason=desig_reason,
        remarks=remarks,
        approved_by_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(desig_hist)
    emp.designation_id = new_designation_id

    event_dt = datetime.combine(eff_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        employee_id=emp.id,
        event_type="designation_change",
        title="Designation Change",
        description=f"Designation updated from {old_desig_name} to {new_desig.name}. Reason: {desig_reason}",
        event_time=event_dt,
        metadata_json={"from_desig_id": old_values["designation_id"], "to_desig_id": new_designation_id, "remarks": remarks},
        actor_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)

    try:
        db.session.commit()
        _create_audit_log(
            "employee", emp.id, "designation_change", actor_id,
            old_values, {"designation_id": new_designation_id, "designation_name": new_desig.name, "reason": desig_reason},
            reason=desig_reason, school_id=school_id or emp.school_id
        )
        db.session.commit()
        return desig_hist
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to change designation: {exc}") from exc


def change_employee_manager(
    employee_id: int,
    new_manager_id: Optional[int],
    effective_date: Optional[date] = None,
    reason: str = "",
    remarks: str = "",
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
    **kwargs,
) -> EmployeeManagerHistory:
    eff_d = effective_date or date.today()
    if isinstance(eff_d, str):
        eff_d = datetime.strptime(eff_d, "%Y-%m-%d").date()
    actor_id = performed_by or kwargs.get("changed_by_id")
    mgr_reason = reason or remarks or "Reporting manager change"

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    if new_manager_id:
        is_valid, msg = validate_reporting_hierarchy(employee_id, new_manager_id, school_id or emp.school_id)
        if not is_valid:
            raise ValueError(msg)

    old_mgr_name = emp.reporting_manager.full_name if emp.reporting_manager else "None"
    old_values = {"reporting_manager_id": emp.reporting_manager_id, "manager_name": old_mgr_name}

    new_mgr_name = "None"
    mgr_hist = None
    if new_manager_id:
        new_mgr = Employee.query.get(new_manager_id)
        new_mgr_name = new_mgr.full_name if new_mgr else f"ID {new_manager_id}"
        mgr_hist = EmployeeManagerHistory(
            employee_id=emp.id,
            previous_manager_id=emp.reporting_manager_id,
            new_manager_id=new_manager_id,
            effective_date=eff_d,
            reason=mgr_reason,
            remarks=remarks,
            changed_by_id=actor_id,
            school_id=school_id or emp.school_id,
        )
        db.session.add(mgr_hist)

    emp.reporting_manager_id = new_manager_id

    event_dt = datetime.combine(eff_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        employee_id=emp.id,
        event_type="manager_change",
        title="Reporting Manager Change",
        description=f"Manager changed from {old_mgr_name} to {new_mgr_name}. Reason: {mgr_reason}",
        event_time=event_dt,
        metadata_json={"from_manager_id": old_values["reporting_manager_id"], "to_manager_id": new_manager_id, "remarks": remarks},
        actor_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)

    try:
        db.session.commit()
        _create_audit_log(
            "employee", emp.id, "manager_change", actor_id,
            old_values, {"reporting_manager_id": new_manager_id, "manager_name": new_mgr_name, "reason": mgr_reason},
            reason=mgr_reason, school_id=school_id or emp.school_id
        )
        db.session.commit()
        return mgr_hist
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to change reporting manager: {exc}") from exc


def change_employee_status(
    employee_id: int,
    new_status: str,
    effective_date: Optional[date] = None,
    reason: str = "",
    exit_date: Optional[date] = None,
    exit_reason: Optional[str] = None,
    remarks: str = "",
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
    **kwargs,
) -> EmployeeStatusHistory:
    eff_d = effective_date or date.today()
    if isinstance(eff_d, str):
        eff_d = datetime.strptime(eff_d, "%Y-%m-%d").date()
    if isinstance(exit_date, str):
        exit_date = datetime.strptime(exit_date, "%Y-%m-%d").date()
    actor_id = performed_by or kwargs.get("changed_by_id")
    status_reason = reason or exit_reason or remarks or "Status transition"

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    old_status = emp.employment_status
    old_values = {"employment_status": old_status, "is_active": emp.is_active, "exit_date": emp.exit_date}

    status_hist = EmployeeStatusHistory(
        employee_id=emp.id,
        previous_status=old_status,
        new_status=new_status,
        effective_date=eff_d,
        reason=status_reason,
        remarks=remarks,
        changed_by_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(status_hist)

    emp.employment_status = new_status
    if new_status in ("terminated", "resigned", "retired", "exited", "inactive"):
        emp.is_active = False
        emp.exit_date = exit_date or eff_d
        emp.exit_reason = exit_reason or reason or status_reason
    elif new_status == "active":
        emp.is_active = True
        emp.exit_date = None
        emp.exit_reason = None

    event_dt = datetime.combine(eff_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        employee_id=emp.id,
        event_type="status_change",
        title="Status Transition",
        description=f"Employment status updated from {old_status} to {new_status}. Reason: {status_reason}",
        event_time=event_dt,
        metadata_json={"old_status": old_status, "new_status": new_status, "exit_reason": emp.exit_reason, "remarks": remarks},
        actor_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)

    try:
        db.session.commit()
        _create_audit_log(
            "employee", emp.id, "status_change", actor_id,
            old_values, {"employment_status": new_status, "is_active": emp.is_active, "reason": status_reason},
            reason=status_reason, school_id=school_id or emp.school_id
        )
        db.session.commit()
        return status_hist
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to change status: {exc}") from exc


def record_employee_promotion(
    employee_id: int,
    data: Optional[dict] = None,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
    **kwargs,
) -> EmployeePromotion:
    payload = dict(data or {})
    payload.update(kwargs)

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    to_desig = payload.get("new_designation_id") or payload.get("to_designation_id")
    to_grade = payload.get("new_job_grade_id") or payload.get("new_grade_id") or payload.get("to_grade_id")
    new_basic_salary = payload.get("new_basic_salary")
    if new_basic_salary is not None:
        new_basic_salary = Decimal(str(new_basic_salary))

    eff_d = payload.get("effective_date") or date.today()
    if isinstance(eff_d, str):
        eff_d = datetime.strptime(eff_d, "%Y-%m-%d").date()

    actor_id = performed_by or payload.get("approved_by_id")
    promo_reason = payload.get("notes") or payload.get("reason") or "Promotion"

    promo_data = {
        "employee_id": emp.id,
        "previous_designation_id": emp.designation_id,
        "new_designation_id": to_desig or emp.designation_id,
        "previous_grade_id": emp.job_grade_id,
        "new_grade_id": to_grade or emp.job_grade_id,
        "new_basic_salary": new_basic_salary,
        "effective_date": eff_d,
        "reason": promo_reason,
        "approved_by_id": actor_id,
        "school_id": school_id or emp.school_id,
    }
    promo = EmployeePromotion(**promo_data)
    db.session.add(promo)

    if to_desig and to_desig != emp.designation_id:
        change_employee_designation(
            emp.id, to_desig, eff_d,
            reason=f"Promotion: {promo_reason}", performed_by=actor_id, school_id=school_id or emp.school_id
        )

    if to_grade:
        emp.job_grade_id = to_grade

    if new_basic_salary is not None:
        create_salary_structure(
            emp.id,
            {
                "basic_salary": new_basic_salary,
                "gross_salary": new_basic_salary,
                "effective_from": eff_d,
                "status": "active",
            },
            performed_by=actor_id,
            school_id=school_id or emp.school_id,
        )

    event_dt = datetime.combine(eff_d, datetime.min.time(), tzinfo=timezone.utc)
    tl = EmployeeTimelineEvent(
        employee_id=emp.id,
        event_type="promotion",
        title="Promotion Recorded",
        description=f"Promoted on {eff_d}. Reason: {promo_reason}",
        event_time=event_dt,
        metadata_json=_make_json_safe(promo_data),
        actor_id=actor_id,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)

    try:
        db.session.commit()
        _create_audit_log(
            "employee_promotion", promo.id, "create", actor_id,
            None, promo_data, school_id=school_id or emp.school_id
        )
        db.session.commit()
        return promo
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to record promotion: {exc}") from exc


# ─────────────────────────────────────────────── Timeline Service
def create_timeline_event(
    employee_id: int,
    event_type: str,
    title: str,
    description: Optional[str] = None,
    event_time: Optional[datetime] = None,
    metadata_json: Optional[dict] = None,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
) -> EmployeeTimelineEvent:
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    tl = EmployeeTimelineEvent(
        employee_id=employee_id,
        event_type=event_type,
        title=title,
        description=description,
        event_time=event_time or datetime.now(timezone.utc),
        metadata_json=metadata_json,
        actor_id=performed_by,
        school_id=school_id or emp.school_id,
    )
    db.session.add(tl)
    try:
        db.session.commit()
        return tl
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create timeline event: {exc}") from exc


def get_employee_timeline(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    events = EmployeeTimelineEvent.query.filter_by(employee_id=employee_id).order_by(
        EmployeeTimelineEvent.event_time.desc(), EmployeeTimelineEvent.created_at.desc()
    ).all()
    return [e.to_dict() for e in events]


# ─────────────────────────────────────────────── History & Lifecycle Getters
def get_department_history(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        return []
    records = EmployeeDepartmentHistory.query.filter_by(employee_id=employee_id).order_by(EmployeeDepartmentHistory.effective_date.desc()).all()
    return [r.to_dict() for r in records]


def get_designation_history(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        return []
    records = EmployeeDesignationHistory.query.filter_by(employee_id=employee_id).order_by(EmployeeDesignationHistory.effective_date.desc()).all()
    return [r.to_dict() for r in records]


def get_manager_history(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        return []
    records = EmployeeManagerHistory.query.filter_by(employee_id=employee_id).order_by(EmployeeManagerHistory.effective_date.desc()).all()
    return [r.to_dict() for r in records]


def get_status_history(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        return []
    records = EmployeeStatusHistory.query.filter_by(employee_id=employee_id).order_by(EmployeeStatusHistory.effective_date.desc()).all()
    return [r.to_dict() for r in records]


def get_employee_promotions(employee_id: int, school_id: Optional[str | int] = None) -> List[dict]:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        return []
    records = EmployeePromotion.query.filter_by(employee_id=employee_id).order_by(EmployeePromotion.effective_date.desc()).all()
    return [r.to_dict() for r in records]


def get_employee_id_card_data(employee_id: int, school_id: Optional[str | int] = None) -> dict:
    emp = get_employee(employee_id, school_id=school_id)
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")
    qr_token = get_or_create_qr_identifier(emp)
    school_name = emp.school.name if getattr(emp, "school", None) and emp.school else "Springfield Academy"
    return {
        "employee_id": emp.id,
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "department_name": emp.department.name if emp.department else "N/A",
        "designation_name": emp.designation.name if emp.designation else "N/A",
        "job_grade_name": emp.job_grade.name if emp.job_grade else None,
        "job_level_name": emp.job_level.name if emp.job_level else None,
        "campus": emp.campus or "Main Campus",
        "school_name": school_name,
        "blood_group": getattr(emp, "blood_group", None),
        "emergency_contact_phone": emp.emergency_contact_phone,
        "qr_identifier": qr_token,
        "qr_verification_url": f"/api/hr/verify/{qr_token}",
    }


# ─────────────────────────────────────────────── Emergency Contacts
def create_emergency_contact(*args, **kwargs) -> EmployeeEmergencyContact:
    data = {}
    employee_id = None
    performed_by = None
    school_id = None

    if len(args) == 1 and isinstance(args[0], dict):
        data = dict(args[0])
    elif len(args) >= 2:
        if isinstance(args[0], int) and isinstance(args[1], dict):
            employee_id = args[0]
            data = dict(args[1])
            if len(args) > 2:
                performed_by = args[2]
            if len(args) > 3:
                school_id = args[3]
        elif isinstance(args[0], dict) and isinstance(args[1], int):
            data = dict(args[0])
            employee_id = args[1]
            if len(args) > 2:
                school_id = args[2]

    if "data" in kwargs and not data:
        data = dict(kwargs["data"])
    data.update({k: v for k, v in kwargs.items() if k not in ("data", "employee_id", "performed_by", "school_id")})
    if "employee_id" in kwargs and not employee_id:
        employee_id = kwargs["employee_id"]
    if "performed_by" in kwargs and not performed_by:
        performed_by = kwargs["performed_by"]
    if "school_id" in kwargs and not school_id:
        school_id = kwargs["school_id"]

    if not employee_id and "employee_id" in data:
        employee_id = data["employee_id"]

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    if "contact_name" in data and "name" not in data:
        data["name"] = data.pop("contact_name")
    if "phone_primary" in data and "phone" not in data:
        data["phone"] = data.pop("phone_primary")
    if "phone_secondary" in data and "alternate_phone" not in data:
        data["alternate_phone"] = data.pop("phone_secondary")

    data["employee_id"] = employee_id
    if school_id or emp.school_id:
        data["school_id"] = school_id or emp.school_id

    if data.get("is_primary"):
        EmployeeEmergencyContact.query.filter_by(employee_id=employee_id).update({"is_primary": False})

    contact = EmployeeEmergencyContact(**data)
    db.session.add(contact)
    try:
        db.session.commit()
        _create_audit_log("emergency_contact", contact.id, "create", performed_by, None, data, school_id=school_id or emp.school_id)
        db.session.commit()
        return contact
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create emergency contact: {exc}") from exc


def get_emergency_contacts(employee_id: int, school_id: Optional[str | int] = None) -> List[EmployeeEmergencyContact]:
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        return []
    return EmployeeEmergencyContact.query.filter_by(employee_id=employee_id).order_by(
        EmployeeEmergencyContact.is_primary.desc(), EmployeeEmergencyContact.name.asc()
    ).all()


def update_emergency_contact(
    contact_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> EmployeeEmergencyContact:
    contact = EmployeeEmergencyContact.query.get(contact_id)
    if not contact or (school_id is not None and contact.school_id is not None and str(contact.school_id) != str(school_id)):
        raise ValueError(f"Emergency contact {contact_id} not found")
    old = contact.to_dict()

    if data.get("is_primary"):
        EmployeeEmergencyContact.query.filter_by(employee_id=contact.employee_id).update({"is_primary": False})

    if "contact_name" in data and "name" not in data:
        data["name"] = data.pop("contact_name")
    if "phone_primary" in data and "phone" not in data:
        data["phone"] = data.pop("phone_primary")
    if "phone_secondary" in data and "alternate_phone" not in data:
        data["alternate_phone"] = data.pop("phone_secondary")

    for k, v in data.items():
        if hasattr(contact, k) and k not in ("id", "employee_id", "school_id"):
            setattr(contact, k, v)
    try:
        db.session.commit()
        _create_audit_log("emergency_contact", contact.id, "update", performed_by, old, data, school_id=school_id or contact.school_id)
        db.session.commit()
        return contact
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update emergency contact: {exc}") from exc


def delete_emergency_contact(contact_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None) -> bool:
    contact = EmployeeEmergencyContact.query.get(contact_id)
    if not contact or (school_id is not None and contact.school_id is not None and str(contact.school_id) != str(school_id)):
        raise ValueError(f"Emergency contact {contact_id} not found")
    old = contact.to_dict()
    db.session.delete(contact)
    try:
        db.session.commit()
        _create_audit_log("emergency_contact", contact_id, "delete", performed_by, old, None, school_id=school_id or contact.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete emergency contact: {exc}") from exc


# ─────────────────────────────────────────────── Dependents
def create_dependent(*args, **kwargs) -> EmployeeDependent:
    data = {}
    employee_id = None
    performed_by = None
    school_id = None

    if len(args) == 1 and isinstance(args[0], dict):
        data = dict(args[0])
    elif len(args) >= 2:
        if isinstance(args[0], int) and isinstance(args[1], dict):
            employee_id = args[0]
            data = dict(args[1])
            if len(args) > 2:
                performed_by = args[2]
            if len(args) > 3:
                school_id = args[3]
        elif isinstance(args[0], dict) and isinstance(args[1], int):
            data = dict(args[0])
            employee_id = args[1]
            if len(args) > 2:
                school_id = args[2]

    if "data" in kwargs and not data:
        data = dict(kwargs["data"])
    data.update({k: v for k, v in kwargs.items() if k not in ("data", "employee_id", "performed_by", "school_id")})
    if "employee_id" in kwargs and not employee_id:
        employee_id = kwargs["employee_id"]
    if "performed_by" in kwargs and not performed_by:
        performed_by = kwargs["performed_by"]
    if "school_id" in kwargs and not school_id:
        school_id = kwargs["school_id"]

    if not employee_id and "employee_id" in data:
        employee_id = data["employee_id"]

    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    if "full_name" in data and "name" not in data:
        data["name"] = data.pop("full_name")

    data["employee_id"] = employee_id
    if school_id or emp.school_id:
        data["school_id"] = school_id or emp.school_id

    dependent = EmployeeDependent(**data)
    db.session.add(dependent)
    try:
        db.session.commit()
        _create_audit_log("dependent", dependent.id, "create", performed_by, None, data, school_id=school_id or emp.school_id)
        db.session.commit()
        return dependent
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create dependent: {exc}") from exc


def get_dependents(employee_id: int, school_id: Optional[str | int] = None) -> List[EmployeeDependent]:
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        return []
    return EmployeeDependent.query.filter_by(employee_id=employee_id).order_by(EmployeeDependent.name.asc()).all()


def update_dependent(
    dependent_id: int, data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> EmployeeDependent:
    dependent = EmployeeDependent.query.get(dependent_id)
    if not dependent or (school_id is not None and dependent.school_id is not None and str(dependent.school_id) != str(school_id)):
        raise ValueError(f"Dependent {dependent_id} not found")
    old = dependent.to_dict()

    if "full_name" in data and "name" not in data:
        data["name"] = data.pop("full_name")

    for k, v in data.items():
        if hasattr(dependent, k) and k not in ("id", "employee_id", "school_id"):
            setattr(dependent, k, v)
    try:
        db.session.commit()
        _create_audit_log("dependent", dependent.id, "update", performed_by, old, data, school_id=school_id or dependent.school_id)
        db.session.commit()
        return dependent
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to update dependent: {exc}") from exc


def delete_dependent(*args, **kwargs) -> bool:
    dependent_id = None
    school_id = None
    performed_by = None

    if len(args) == 3:
        dependent_id = args[1]
        school_id = args[2]
    elif len(args) == 2:
        if isinstance(args[1], (int, str)) and not isinstance(args[0], dict):
            dep = EmployeeDependent.query.get(args[1])
            if dep:
                dependent_id = args[1]
                school_id = args[0]
            else:
                dependent_id = args[0]
                school_id = args[1]
    elif len(args) == 1:
        dependent_id = args[0]

    if "dependent_id" in kwargs:
        dependent_id = kwargs["dependent_id"]
    if "school_id" in kwargs:
        school_id = kwargs["school_id"]
    if "performed_by" in kwargs:
        performed_by = kwargs["performed_by"]

    dependent = EmployeeDependent.query.get(dependent_id)
    if not dependent or (school_id is not None and dependent.school_id is not None and str(dependent.school_id) != str(school_id)):
        raise ValueError(f"Dependent {dependent_id} not found")
    old = dependent.to_dict()
    db.session.delete(dependent)
    try:
        db.session.commit()
        _create_audit_log("dependent", dependent_id, "delete", performed_by, old, None, school_id=school_id or dependent.school_id)
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete dependent: {exc}") from exc


# ─────────────────────────────────────────────── QR & ID Card
def get_or_create_qr_identifier(employee: Employee) -> str:
    if not employee.qr_identifier:
        employee.qr_identifier = f"EOS-QR-{secrets.token_hex(12).upper()}"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return employee.qr_identifier


def verify_qr_identifier(qr_identifier: str) -> Optional[dict]:
    """Public verification lookup for employee ID card QR code.
    
    Returns only safe public-facing metadata; NEVER leaks bank details,
    pan numbers, salaries, or passwords.
    """
    if not qr_identifier or not isinstance(qr_identifier, str):
        return None
    emp = Employee.query.filter_by(qr_identifier=qr_identifier.strip()).first()
    if not emp:
        return None

    school_name = emp.school.name if getattr(emp, "school", None) and emp.school else "Springfield Academy"
    return {
        "verified": True,
        "valid": True,
        "employee": {
            "employee_code": emp.employee_code,
            "full_name": emp.full_name,
            "department": emp.department.name if emp.department else "N/A",
            "designation": emp.designation.name if emp.designation else "N/A",
            "employment_status": emp.employment_status,
            "is_active": emp.is_active,
            "campus": emp.campus or "Main Campus",
            "school_name": school_name,
            "joining_date": emp.joining_date.isoformat() if emp.joining_date else None,
            "profile_image": emp.profile_image,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    }


# Convenience Aliases
add_emergency_contact = create_emergency_contact
add_dependent = create_dependent



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
    employee_id: int, structure_data: dict, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> EmployeeSalaryStructure:
    structure_data["employee_id"] = employee_id
    if isinstance(structure_data.get("effective_from"), str):
        structure_data["effective_from"] = date.fromisoformat(structure_data["effective_from"])
    if isinstance(structure_data.get("effective_to"), str):
        structure_data["effective_to"] = date.fromisoformat(structure_data["effective_to"])

    eff_from = structure_data["effective_from"]
    eff_to = structure_data.get("effective_to")
    if "pay_frequency" in structure_data and structure_data["pay_frequency"]:
        structure_data["pay_frequency"] = str(structure_data["pay_frequency"]).lower()

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
            "employee_salary_structure", ss.id, "create", performed_by, None, structure_data, school_id=school_id
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
    leave_type_data: dict, performed_by: Optional[int] = None, school_id: Optional[str] = None
) -> LeaveType:
    if school_id:
        leave_type_data["school_id"] = school_id
    q_code = LeaveType.query.filter_by(code=leave_type_data.get("code"))
    if school_id:
        q_code = q_code.filter_by(school_id=school_id)
    if q_code.first():
        raise ValueError(f"Leave type code '{leave_type_data['code']}' already exists")
    lt = LeaveType(**leave_type_data)
    db.session.add(lt)
    try:
        db.session.commit()
        _create_audit_log("leave_type", lt.id, "create", performed_by, None, leave_type_data, school_id=school_id)
        db.session.commit()
        return lt
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create leave type: {exc}") from exc


def get_leave_types(active_only: bool = True, school_id: Optional[str] = None) -> List[LeaveType]:
    q = LeaveType.query
    if school_id:
        q = q.filter((LeaveType.school_id == school_id) | (LeaveType.school_id.is_(None)))
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(LeaveType.name).all()


# ─────────────────────────────────────────────── Shifts
def create_shift(shift_data: dict, performed_by: Optional[int] = None, school_id: Optional[str] = None) -> Shift:
    if school_id:
        shift_data["school_id"] = school_id
    q_code = Shift.query.filter_by(code=shift_data.get("code"))
    if school_id:
        q_code = q_code.filter_by(school_id=school_id)
    if q_code.first():
        raise ValueError(f"Shift code '{shift_data['code']}' already exists")
    shift = Shift(**shift_data)
    db.session.add(shift)
    try:
        db.session.commit()
        _create_audit_log("shift", shift.id, "create", performed_by, None, shift_data, school_id=school_id)
        db.session.commit()
        return shift
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create shift: {exc}") from exc


def get_shifts(active_only: bool = False, school_id: Optional[str] = None) -> List[Shift]:
    q = Shift.query
    if school_id:
        q = q.filter((Shift.school_id == school_id) | (Shift.school_id.is_(None)))
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(Shift.name).all()


def get_shift(shift_id: int, school_id: Optional[str] = None) -> Optional[Shift]:
    s = Shift.query.get(shift_id)
    if s and school_id and s.school_id and str(s.school_id) != str(school_id):
        return None
    return s


def update_shift(shift_id: int, shift_data: dict, performed_by: Optional[int] = None, school_id: Optional[str] = None) -> Shift:
    shift = Shift.query.get(shift_id)
    if not shift or (school_id and shift.school_id and str(shift.school_id) != str(school_id)):
        raise ValueError(f"Shift {shift_id} not found")
    old = {"name": shift.name, "code": shift.code, "is_active": shift.is_active}
    for k, v in shift_data.items():
        if hasattr(shift, k):
            setattr(shift, k, v)
    try:
        db.session.commit()
        _create_audit_log("shift", shift.id, "update", performed_by, old, shift_data, school_id=school_id or shift.school_id)
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
    emp = Employee.query.get(employee_id)
    if emp and emp.school_id:
        leave_data["school_id"] = emp.school_id

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
        }, school_id=lr.school_id)
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
            school_id=lr.school_id,
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
            school_id=lr.school_id,
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
            school_id=lr.school_id,
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
            school_id=leave_request.school_id,
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
    emp = Employee.query.get(employee_id)
    if emp and emp.school_id:
        overtime_data["school_id"] = emp.school_id
    if "overtime_minutes" in overtime_data:
        overtime_data["overtime_hours"] = (
            Decimal(str(overtime_data["overtime_minutes"])) / 60
        ).quantize(Decimal("0.01"))
    rec = OvertimeRecord(**overtime_data)
    db.session.add(rec)
    try:
        db.session.commit()
        _create_audit_log(
            "overtime_record", rec.id, "create", performed_by, None, overtime_data,
            school_id=rec.school_id,
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
            school_id=rec.school_id,
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
    emp = Employee.query.get(employee_id)
    if emp and emp.school_id:
        bonus_data["school_id"] = emp.school_id
    bonus = EmployeeBonus(**bonus_data)
    db.session.add(bonus)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_bonus", bonus.id, "create", performed_by, None, bonus_data,
            school_id=bonus.school_id,
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
    emp = Employee.query.get(employee_id)
    if emp and emp.school_id:
        loan_data["school_id"] = emp.school_id
    if "remaining_amount" not in loan_data:
        loan_data["remaining_amount"] = loan_data["principal_amount"]
    loan = EmployeeLoan(**loan_data)
    db.session.add(loan)
    try:
        db.session.commit()
        _create_audit_log("employee_loan", loan.id, "create", performed_by, None, loan_data, school_id=loan.school_id)
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

    if loan.school_id:
        repayment_data["school_id"] = loan.school_id

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
            "loan_repayment", repayment.id, "create", performed_by, None, repayment_data,
            school_id=loan.school_id,
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
    emp = Employee.query.get(employee_id)
    if emp and emp.school_id:
        data["school_id"] = emp.school_id
    if "approved_amount" not in data:
        data["approved_amount"] = 0.00
    reimb = Reimbursement(**data)
    db.session.add(reimb)
    try:
        db.session.commit()
        _create_audit_log("reimbursement", reimb.id, "create", performed_by, None, data, school_id=reimb.school_id)
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


# ─────────────────────────────────────────────── Employee Documents
def create_employee_document(
    employee_id: int,
    doc_data: dict,
    performed_by: Optional[int] = None,
    school_id: Optional[str | int] = None,
) -> EmployeeDocument:
    """Upload/record an employee document (appointment letter, ID proof, certificates, etc.)."""
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    payload = dict(doc_data)
    if "file_size_bytes" in payload and "file_size" not in payload:
        payload["file_size"] = payload.pop("file_size_bytes")
    payload["employee_id"] = employee_id
    if emp.school_id:
        payload["school_id"] = emp.school_id

    doc = EmployeeDocument(**payload)
    db.session.add(doc)

    # Timeline event
    db.session.add(
        EmployeeTimelineEvent(
            school_id=emp.school_id,
            employee_id=emp.id,
            event_type="document_uploaded",
            title=f"Document Uploaded: {doc.title}",
            description=f"Type: {doc.document_type.replace('_', ' ').title()} - File: {doc.file_name or doc.file_path}",
            event_time=datetime.now(timezone.utc),
            actor_id=performed_by,
        )
    )

    try:
        db.session.commit()
        _create_audit_log(
            "employee_document", doc.id, "create", performed_by, None, payload,
            school_id=doc.school_id,
        )
        db.session.commit()
        return doc
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to save document: {exc}") from exc


def get_employee_documents(
    employee_id: int, school_id: Optional[str | int] = None
) -> List[EmployeeDocument]:
    """Return all active documents for an employee."""
    emp = Employee.query.get(employee_id)
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        return []
    return EmployeeDocument.query.filter_by(employee_id=employee_id).order_by(
        EmployeeDocument.created_at.desc()
    ).all()


def delete_employee_document(
    doc_id: int, performed_by: Optional[int] = None, school_id: Optional[str | int] = None
) -> bool:
    """Delete an employee document record."""
    doc = EmployeeDocument.query.get(doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")
    if school_id is not None and doc.school_id is not None and str(doc.school_id) != str(school_id):
        raise ValueError(f"Document {doc_id} not found")

    old = doc.to_dict()
    db.session.delete(doc)
    try:
        db.session.commit()
        _create_audit_log(
            "employee_document", doc_id, "delete", performed_by, old, None,
            school_id=doc.school_id,
        )
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to delete document: {exc}") from exc


# ─────────────────────────────────────────────── Employee 360° Profile
def get_employee_360_profile(
    employee_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    school_id: Optional[str | int] = None,
) -> dict:
    """Return comprehensive 360° Profile for an employee."""
    emp = Employee.query.get(employee_id)
    if not emp:
        # Check by teacher_id or employee_code fallback
        emp = Employee.query.filter(
            or_(Employee.teacher_id == employee_id, Employee.employee_code == str(employee_id))
        ).first()
    if not emp:
        # Check if this ID is a Teacher record directly
        t = Teacher.query.get(employee_id)
        if not t:
            t = Teacher.query.filter_by(teacher_id=str(employee_id)).first()
        if t:
            emp = ensure_employee_from_teacher(t, school_id=school_id or t.school_id)
            db.session.commit()
    if not emp or (school_id is not None and emp.school_id is not None and str(emp.school_id) != str(school_id)):
        raise ValueError(f"Employee {employee_id} not found")

    today = date.today()
    if isinstance(start_date, str):
        try:
            start_date = date.fromisoformat(start_date)
        except Exception:
            start_date = today.replace(day=1)
    if isinstance(end_date, str):
        try:
            end_date = date.fromisoformat(end_date)
        except Exception:
            end_date = today

    if start_date is None:
        start_date = today.replace(day=1)
    if end_date is None:
        if start_date.month == today.month and start_date.year == today.year:
            end_date = today
        else:
            _, last_day = calendar.monthrange(start_date.year, start_date.month)
            end_date = start_date.replace(day=last_day)

    # 1. Overview
    active_ss = get_employee_salary_structure(emp.id, today)
    rule = None
    try:
        rule_q = TeacherTimeRule.query
        if emp.school_id:
            rule_q = rule_q.filter_by(school_id=emp.school_id)
        rule = rule_q.first()
    except Exception:
        pass

    shift_info = {
        "id": rule.id if rule else 1,
        "name": getattr(rule, "name", "General Faculty Shift") or "General Faculty Shift",
        "start_time": getattr(rule, "office_start", "08:00") or "08:00",
        "end_time": getattr(rule, "office_end", "14:00") or "14:00",
        "grace_minutes": 15,
    }

    joining_d = emp.joining_date or today
    tenure_days = max(0, (today - joining_d).days)
    years = tenure_days // 365
    months = (tenure_days % 365) // 30
    tenure_str = f"{years}y {months}m" if years > 0 else f"{months} months"

    teacher = None
    if emp.teacher_id:
        teacher = Teacher.query.get(emp.teacher_id)

    monthly_sal = _dec(active_ss.gross_salary) if (active_ss and active_ss.gross_salary) else (_dec(active_ss.basic_salary) if (active_ss and active_ss.basic_salary) else 0.0)

    overview = {
        **emp.to_dict(include_sensitive=False),
        "shift": shift_info,
        "assigned_shift": f"{shift_info['start_time']} - {shift_info['end_time']}",
        "tenure": tenure_str,
        "basic_salary": _dec(active_ss.basic_salary) if active_ss else 0.0,
        "gross_salary": _dec(active_ss.gross_salary) if active_ss else 0.0,
        "monthly_salary": monthly_sal,
        "ctc": _dec(active_ss.gross_salary * 12) if (active_ss and active_ss.gross_salary) else 0.0,
        "is_teacher": emp.teacher_id is not None,
    }

    if teacher:
        if not emp.profile_image and teacher.photo_path:
            overview["profile_image"] = teacher.photo_path
        if not overview.get("department_name") and teacher.department:
            overview["department_name"] = teacher.department
        if not overview.get("designation_name") and teacher.designation:
            overview["designation_name"] = teacher.designation
        overview["subject"] = teacher.subject
        overview["assigned_classes"] = teacher.assigned_classes
        overview["attendance_method"] = teacher.attendance_method
        overview["fingerprint_user_id"] = teacher.fingerprint_user_id
        overview["fingerprint_enrolled"] = teacher.fingerprint_enrolled

    # 2. Daily Attendance Records & Calculation
    teacher_id = emp.teacher_id
    att_records = []
    if teacher_id:
        att_records = TeacherAttendance.query.filter(
            TeacherAttendance.teacher_id == teacher_id,
            TeacherAttendance.attendance_date >= start_date,
            TeacherAttendance.attendance_date <= end_date,
        ).all()

    att_map = {r.attendance_date: r for r in att_records}

    # Pull Weekly Offs
    weekly_off_q = WeeklyOff.query.filter(
        or_(WeeklyOff.applies_to == "teacher", WeeklyOff.applies_to == "all")
    )
    if emp.school_id:
        weekly_off_q = weekly_off_q.filter(WeeklyOff.school_id == emp.school_id)
    weekly_off_rows = weekly_off_q.all()
    weekly_off_days = {w.weekday for w in weekly_off_rows} or {5, 6}

    # Pull Holidays
    hol_q = HolidayCalendar.query.filter(
        HolidayCalendar.holiday_date >= start_date,
        HolidayCalendar.holiday_date <= end_date,
        or_(HolidayCalendar.applies_to == "teacher", HolidayCalendar.applies_to == "all")
    )
    if emp.school_id:
        hol_q = hol_q.filter(HolidayCalendar.school_id == emp.school_id)
    holiday_rows = hol_q.all()
    holiday_map = {h.holiday_date: h for h in holiday_rows}

    # Pull Approved Leaves
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == emp.id,
        LeaveRequest.status == "approved",
        or_(
            and_(LeaveRequest.start_date >= start_date, LeaveRequest.start_date <= end_date),
            and_(LeaveRequest.end_date >= start_date, LeaveRequest.end_date <= end_date),
            and_(LeaveRequest.start_date <= start_date, LeaveRequest.end_date >= end_date),
        )
    ).all()

    # Build day-by-day attendance grid
    daily_history = []
    total_present = 0
    total_absent = 0
    total_late = 0
    total_late_mins = 0
    total_half_days = 0
    total_approved_leave_days = 0
    total_paid_leave_days = 0
    total_working_days = 0
    late_intelligence_records = []

    curr = start_date
    while curr <= end_date:
        is_wo = curr.weekday() in weekly_off_days
        is_hol = curr in holiday_map
        hol_obj = holiday_map.get(curr)

        # Check leave
        on_leave = None
        for lv in leaves:
            if lv.start_date <= curr <= lv.end_date:
                on_leave = lv
                break

        att = att_map.get(curr)

        if not is_wo and not is_hol:
            total_working_days += 1

        day_item = {
            "date": curr.isoformat(),
            "day_name": curr.strftime("%a"),
            "scheduled_shift": f"{shift_info['start_time']} - {shift_info['end_time']}",
            "check_in": None,
            "check_out": None,
            "status": "absent",
            "working_minutes": 0,
            "working_hours": "0h 0m",
            "total_working_hours": 0.0,
            "is_late": False,
            "late_minutes": 0,
            "is_early_exit": False,
            "early_departure": False,
            "is_half_day": False,
            "half_day": False,
            "missing_punch": False,
            "camera_name": None,
            "confidence": None,
            "note": None,
            "notes": None,
        }

        if att:
            day_item["check_in"] = att.in_time.strftime("%I:%M %p") if att.in_time else None
            day_item["check_out"] = att.out_time.strftime("%I:%M %p") if att.out_time else None
            wm = att.working_minutes or 0
            day_item["working_minutes"] = wm
            day_item["working_hours"] = f"{wm // 60}h {wm % 60}m"
            day_item["total_working_hours"] = round(wm / 60.0, 1)
            day_item["is_late"] = bool(att.is_late or (att.late_minutes and att.late_minutes > 0) if hasattr(att, "late_minutes") else att.is_late)
            day_item["is_early_exit"] = bool(att.is_early_exit)
            day_item["early_departure"] = bool(att.is_early_exit)
            day_item["camera_name"] = att.camera.name if getattr(att, "camera", None) and att.camera else None
            day_item["confidence"] = att.confidence

            # Calculate late minutes
            late_m = getattr(att, "late_minutes", 0) or 0
            if late_m == 0 and (att.is_late or att.status == "late") and att.in_time:
                try:
                    s_h, s_m = map(int, shift_info["start_time"].split(":"))
                    sched_mins = s_h * 60 + s_m
                    actual_mins = att.in_time.hour * 60 + att.in_time.minute
                    late_m = max(0, actual_mins - sched_mins)
                except Exception:
                    late_m = 15
            elif late_m == 0 and (att.is_late or att.status == "late"):
                late_m = 15
            day_item["late_minutes"] = late_m

            if att.status == "half_day" or (0 < wm < 240):
                day_item["is_half_day"] = True
                day_item["half_day"] = True
                day_item["status"] = "half_day"
                total_half_days += 1
                total_present += 1
            elif att.is_late or att.status == "late":
                day_item["status"] = "late"
                day_item["is_late"] = True
                total_late += 1
                total_late_mins += late_m
                total_present += 1
            elif att.status == "absent":
                day_item["status"] = "absent"
                total_absent += 1
            else:
                day_item["status"] = "present"
                total_present += 1

            if (att.in_time and not att.out_time) or (not att.in_time and att.out_time):
                day_item["missing_punch"] = True

            if day_item["is_late"]:
                late_intelligence_records.append({
                    "date": curr.isoformat(),
                    "day_name": curr.strftime("%a"),
                    "scheduled_time": shift_info["start_time"],
                    "scheduled_arrival": shift_info["start_time"],
                    "actual_time": day_item["check_in"] or "--",
                    "actual_arrival": day_item["check_in"] or "--",
                    "delay_minutes": late_m,
                    "policy": f"Grace period: {shift_info['grace_minutes']}m",
                    "policy_status": "Within Grace" if late_m <= shift_info['grace_minutes'] else "Late Arrival",
                    "status": "Within Grace" if late_m <= shift_info['grace_minutes'] else "Late Arrival",
                    "exception_approved": False,
                    "notes": "Biometric / camera detection",
                })
        else:
            # No attendance row
            if on_leave:
                is_paid = on_leave.leave_type.paid if on_leave.leave_type else True
                day_item["status"] = "on_leave"
                day_item["note"] = on_leave.reason
                day_item["notes"] = on_leave.reason
                total_approved_leave_days += 1
                if is_paid:
                    total_paid_leave_days += 1
            elif is_hol:
                day_item["status"] = "holiday"
                day_item["note"] = hol_obj.name if hol_obj else "Holiday"
                day_item["notes"] = hol_obj.name if hol_obj else "Holiday"
            elif is_wo:
                day_item["status"] = "weekly_off"
            else:
                day_item["status"] = "absent"
                total_absent += 1

        daily_history.append(day_item)
        curr += timedelta(days=1)

    # Compute overall percentage
    att_pct = round(
        ((total_present + (total_half_days * 0.5) + total_paid_leave_days) / max(total_working_days, 1)) * 100, 1
    ) if total_working_days > 0 else 100.0

    attendance_summary = {
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "total_calendar_days": (end_date - start_date).days + 1,
        "working_days": total_working_days,
        "present_days": total_present,
        "total_present_days": total_present,
        "absent_days": total_absent,
        "total_absent_days": total_absent,
        "late_count": total_late,
        "total_late_arrivals": total_late,
        "total_late_minutes": total_late_mins,
        "half_days": total_half_days,
        "total_half_days": total_half_days,
        "approved_leave_days": total_approved_leave_days,
        "total_approved_leave_days": total_approved_leave_days,
        "paid_leave_days": total_paid_leave_days,
        "holidays_count": len(holiday_map),
        "total_holidays": len(holiday_map),
        "weekly_offs_count": sum(1 for d in daily_history if d["status"] in ("Weekly Off", "weekly_off")),
        "attendance_percentage": att_pct,
        "monthly_summary": {
            "month": start_date.strftime("%B %Y"),
            "working_days": total_working_days,
            "present_days": total_present,
            "attendance_rate": att_pct,
        },
        "daily_records": list(reversed(daily_history)),
    }

    # 3. Late Arrival Intelligence Summary
    late_intel = {
        "monthly_late_count": total_late,
        "total_accumulated_late_minutes": total_late_mins,
        "average_delay_minutes": round(total_late_mins / max(total_late, 1), 1) if total_late else 0,
        "policy_grace_minutes": shift_info.get("grace_minutes", 15),
        "policy_threshold": f"{shift_info['grace_minutes']} minutes grace threshold",
        "late_records": list(reversed(late_intelligence_records)),
        "records": list(reversed(late_intelligence_records)),
    }

    # 4. Holidays & Leave History
    all_leave_requests = LeaveRequest.query.filter_by(employee_id=emp.id).order_by(
        LeaveRequest.created_at.desc()
    ).all()
    leave_req_dicts = [lr.to_dict() for lr in all_leave_requests]

    leave_balances = EmployeeLeaveBalance.query.filter_by(
        employee_id=emp.id, year=today.year
    ).all()
    leave_bal_dicts = [lb.to_dict() for lb in leave_balances]

    public_holidays = [
        {"date": h.holiday_date.isoformat(), "name": h.name, "type": getattr(h, "holiday_type", "national") or "national", "applies_to": h.applies_to}
        for h in HolidayCalendar.query.order_by(HolidayCalendar.holiday_date.asc()).all()
    ]

    weekly_off_dicts = [
        {"day_of_week": w, "day_name": calendar.day_name[w]} for w in sorted(weekly_off_days)
    ]

    holidays_leaves = {
        "public_holidays": public_holidays,
        "holidays": public_holidays,
        "weekly_off_weekdays": list(weekly_off_days),
        "weekly_offs": weekly_off_dicts,
        "leave_requests": leave_req_dicts,
        "leave_balances": leave_bal_dicts,
    }

    # 5. Salary & Payroll History
    salary_history = get_employee_salary_history(emp.id)
    salary_history_dicts = [
        {
            **sh.to_dict(),
            "components": [c.to_dict() for c in sh.salary_components]
        }
        for sh in salary_history
    ]

    payroll_records = PayrollRecord.query.filter_by(employee_id=emp.id).order_by(
        PayrollRecord.created_at.desc()
    ).all()
    payroll_record_dicts = []
    for pr in payroll_records:
        prd = pr.to_dict()
        prd["earnings"] = [e.to_dict() for e in pr.earnings]
        prd["deductions"] = [d.to_dict() for d in pr.deductions]
        if pr.attendance_summary:
            prd["attendance_summary"] = pr.attendance_summary.to_dict()
        prd["period_name"] = pr.payroll_period.period_name if pr.payroll_period else f"Period #{pr.payroll_period_id}"
        prd["start_date"] = pr.payroll_period.start_date.isoformat() if pr.payroll_period and pr.payroll_period.start_date else None
        prd["end_date"] = pr.payroll_period.end_date.isoformat() if pr.payroll_period and pr.payroll_period.end_date else None
        prd["payment_date"] = pr.payroll_period.payment_date.isoformat() if pr.payroll_period and pr.payroll_period.payment_date else None
        if pr.payslip:
            prd["payslip"] = pr.payslip.to_dict()
        payroll_record_dicts.append(prd)

    salary_payroll = {
        "current_structure": active_ss.to_dict() if active_ss else None,
        "current_salary_structure": active_ss.to_dict() if active_ss else None,
        "current_components": [c.to_dict() for c in active_ss.salary_components] if active_ss else [],
        "salary_revisions": salary_history_dicts,
        "payroll_records": payroll_record_dicts,
    }

    # 6. Documents
    docs = get_employee_documents(emp.id, school_id=school_id)
    doc_dicts = [d.to_dict() for d in docs]

    # 7. Timeline
    timeline = get_employee_timeline(emp.id, school_id=school_id)

    return {
        "overview": overview,
        "attendance": attendance_summary,
        "attendance_summary": attendance_summary,
        "attendance_history": list(reversed(daily_history)),
        "late_intelligence": late_intel,
        "holidays_and_leaves": holidays_leaves,
        "salary_and_payroll": salary_payroll,
        "documents": doc_dicts,
        "timeline": timeline,
    }