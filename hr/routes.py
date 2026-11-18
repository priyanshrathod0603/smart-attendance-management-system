"""HR and Payroll REST API Blueprint.

All routes are prefixed with /api/hr.

Endpoints
---------
Departments         GET/POST  /api/hr/departments
                    GET/PUT   /api/hr/departments/<id>

Designations        GET/POST  /api/hr/designations
                    GET/PUT   /api/hr/designations/<id>

Salary Components   GET/POST  /api/hr/salary-components
                    GET/PUT   /api/hr/salary-components/<id>

Employees           GET/POST  /api/hr/employees
                    GET/PUT/DEL /api/hr/employees/<id>
                    GET       /api/hr/employees/<id>/salary-structure
                    POST      /api/hr/employees/<id>/salary-structure
                    POST      /api/hr/employees/<id>/salary-structure/<sid>/components
                    GET       /api/hr/employees/<id>/leave-balances
                    POST      /api/hr/employees/<id>/leaves
                    POST      /api/hr/employees/<id>/overtime
                    POST      /api/hr/employees/<id>/bonuses
                    POST      /api/hr/employees/<id>/loans
                    GET       /api/hr/employees/<id>/loans
                    POST      /api/hr/employees/<id>/reimbursements

Leave Types         GET/POST  /api/hr/leave-types

Leave Requests      GET       /api/hr/leaves
                    PUT       /api/hr/leaves/<id>/approve
                    PUT       /api/hr/leaves/<id>/reject
                    PUT       /api/hr/leaves/<id>/cancel

Overtime            GET       /api/hr/overtime
                    PUT       /api/hr/overtime/<id>/approve

Reimbursements      GET       /api/hr/reimbursements
                    PUT       /api/hr/reimbursements/<id>/approve

Payroll Periods     GET/POST  /api/hr/payroll-periods
                    GET       /api/hr/payroll-periods/<id>
                    POST      /api/hr/payroll-periods/<id>/process
                    POST      /api/hr/payroll-periods/<id>/finalize
                    GET       /api/hr/payroll-periods/<id>/records
                    POST      /api/hr/payroll-periods/<id>/records/<rid>/payslip

Dashboard           GET       /api/hr/dashboard/summary
Audit               GET       /api/hr/audit-log
"""

from __future__ import annotations

import calendar
import os
from datetime import date, datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request, current_app

from auth import current_school_id, is_tenant_permitted
from database.db import db
from hr.employee_service import (
    # Department
    create_department, get_department, get_departments, update_department,
    # Designation
    create_designation, get_designation, get_designations, update_designation,
    # Cost Center, Job Level, Job Grade, Employment Type, Team
    create_cost_center, get_cost_center, get_cost_centers, update_cost_center, delete_cost_center,
    create_job_level, get_job_level, get_job_levels, update_job_level, delete_job_level,
    create_job_grade, get_job_grade, get_job_grades, update_job_grade, delete_job_grade,
    create_employment_type, get_employment_type, get_employment_types, update_employment_type, delete_employment_type,
    create_team, get_team, get_teams, update_team, delete_team,
    # Employee
    create_employee, get_employee, get_employee_by_code, get_employee_by_teacher_id,
    get_employees, update_employee, delete_employee, sync_teachers_to_employees,
    ensure_employee_from_teacher, ensure_teacher_from_employee,
    get_employee_360_profile, create_employee_document, get_employee_documents, delete_employee_document,
    # Lifecycle Workflows
    transfer_employee_department, change_employee_designation, change_employee_manager,
    change_employee_status, record_employee_promotion,
    # Timeline
    create_timeline_event, get_employee_timeline,
    # Emergency Contacts & Dependents
    create_emergency_contact, get_emergency_contacts, update_emergency_contact, delete_emergency_contact,
    create_dependent, get_dependents, update_dependent, delete_dependent,
    # QR & ID Card
    get_or_create_qr_identifier, verify_qr_identifier,
    # Employment history
    create_employment_history,
    # Salary
    get_employee_salary_structure, get_employee_salary_history,
    create_salary_structure, add_salary_component,
    # Shifts
    create_shift, get_shifts, get_shift, update_shift,
    # Leave
    create_leave_type, get_leave_types, create_leave_request,
    approve_leave_request, reject_leave_request, cancel_leave_request,
    # Overtime
    create_overtime_record, approve_overtime_record,
    # Bonus / loan / reimbursement
    create_employee_bonus, create_employee_loan, create_loan_repayment,
    create_reimbursement, approve_reimbursement,
)
from hr.models import (
    Department, Designation, Employee, SalaryComponent,
    EmployeeSalaryStructure, EmployeeSalaryComponent, Shift, EmployeeShift,
    LeaveType, EmployeeLeaveBalance, LeaveRequest,
    OvertimeRecord, EmployeeBonus, EmployeeLoan,
    Reimbursement, PayrollPeriod, PayrollRecord, HRAuditLog,
    CostCenter, JobLevel, JobGrade, EmploymentType, Team,
    EmployeeEmergencyContact, EmployeeDependent,
    EmployeeDepartmentHistory, EmployeeDesignationHistory,
    EmployeeManagerHistory, EmployeeStatusHistory, EmployeePromotion,
    EmployeeTimelineEvent, EmployeeDocument,
)
from hr.payroll_service import (
    create_payroll_period, process_payroll_for_period, finalize_payroll_period,
    get_payroll_record, get_payroll_records_for_period, create_payslip,
    get_hr_dashboard_summary,
)

hr_bp = Blueprint("hr", __name__, url_prefix="/api/hr")

# ---------------------------------------------------------------------------
# Helper: parse dates from request JSON
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _dates_from(data: dict, *keys: str) -> None:
    """In-place convert ISO date strings to date objects for listed keys."""
    for k in keys:
        if k in data and data[k] is not None:
            data[k] = _parse_date(data[k])


# ============================================================
# DEPARTMENTS
# ============================================================
@hr_bp.route("/departments", methods=["GET", "POST"])
def departments():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([d.to_dict() for d in get_departments(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        dept = create_department(data, performed_by=None, school_id=sid)
        return jsonify(dept.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/departments/<int:dept_id>", methods=["GET", "PUT"])
def department_detail(dept_id: int):
    sid = current_school_id()
    if request.method == "GET":
        dept = get_department(dept_id, school_id=sid)
        if not dept:
            return jsonify({"error": "Department not found"}), 404
        return jsonify(dept.to_dict())

    data = request.get_json() or {}
    try:
        dept = update_department(dept_id, data, performed_by=None, school_id=sid)
        return jsonify(dept.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# DESIGNATIONS
# ============================================================
@hr_bp.route("/designations", methods=["GET", "POST"])
def designations():
    sid = current_school_id()
    if request.method == "GET":
        dept_id = request.args.get("department_id")
        return jsonify([
            d.to_dict() for d in get_designations(
                department_id=int(dept_id) if dept_id else None,
                school_id=sid,
            )
        ])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code") or not data.get("department_id"):
        return jsonify({"error": "name, code, and department_id are required"}), 400
    try:
        desig = create_designation(data, performed_by=None, school_id=sid)
        return jsonify(desig.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/designations/<int:desig_id>", methods=["GET", "PUT"])
def designation_detail(desig_id: int):
    sid = current_school_id()
    if request.method == "GET":
        desig = get_designation(desig_id, school_id=sid)
        if not desig:
            return jsonify({"error": "Designation not found"}), 404
        return jsonify(desig.to_dict())

    data = request.get_json() or {}
    try:
        desig = update_designation(desig_id, data, performed_by=None, school_id=sid)
        return jsonify(desig.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# COST CENTERS
# ============================================================
@hr_bp.route("/cost-centers", methods=["GET", "POST"])
def cost_centers():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([c.to_dict() for c in get_cost_centers(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        cc = create_cost_center(data, performed_by=None, school_id=sid)
        return jsonify(cc.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/cost-centers/<int:cost_center_id>", methods=["GET", "PUT", "DELETE"])
def cost_center_detail(cost_center_id: int):
    sid = current_school_id()
    if request.method == "GET":
        cc = get_cost_center(cost_center_id, school_id=sid)
        if not cc:
            return jsonify({"error": "Cost Center not found"}), 404
        return jsonify(cc.to_dict())

    if request.method == "DELETE":
        try:
            delete_cost_center(cost_center_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Cost Center deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        cc = update_cost_center(cost_center_id, data, performed_by=None, school_id=sid)
        return jsonify(cc.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# JOB LEVELS
# ============================================================
@hr_bp.route("/job-levels", methods=["GET", "POST"])
def job_levels():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([jl.to_dict() for jl in get_job_levels(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        jl = create_job_level(data, performed_by=None, school_id=sid)
        return jsonify(jl.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/job-levels/<int:job_level_id>", methods=["GET", "PUT", "DELETE"])
def job_level_detail(job_level_id: int):
    sid = current_school_id()
    if request.method == "GET":
        jl = get_job_level(job_level_id, school_id=sid)
        if not jl:
            return jsonify({"error": "Job Level not found"}), 404
        return jsonify(jl.to_dict())

    if request.method == "DELETE":
        try:
            delete_job_level(job_level_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Job Level deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        jl = update_job_level(job_level_id, data, performed_by=None, school_id=sid)
        return jsonify(jl.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# JOB GRADES
# ============================================================
@hr_bp.route("/job-grades", methods=["GET", "POST"])
def job_grades():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([jg.to_dict() for jg in get_job_grades(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        jg = create_job_grade(data, performed_by=None, school_id=sid)
        return jsonify(jg.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/job-grades/<int:job_grade_id>", methods=["GET", "PUT", "DELETE"])
def job_grade_detail(job_grade_id: int):
    sid = current_school_id()
    if request.method == "GET":
        jg = get_job_grade(job_grade_id, school_id=sid)
        if not jg:
            return jsonify({"error": "Job Grade not found"}), 404
        return jsonify(jg.to_dict())

    if request.method == "DELETE":
        try:
            delete_job_grade(job_grade_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Job Grade deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        jg = update_job_grade(job_grade_id, data, performed_by=None, school_id=sid)
        return jsonify(jg.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# EMPLOYMENT TYPES
# ============================================================
@hr_bp.route("/employment-types", methods=["GET", "POST"])
def employment_types():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([et.to_dict() for et in get_employment_types(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        et = create_employment_type(data, performed_by=None, school_id=sid)
        return jsonify(et.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employment-types/<int:employment_type_id>", methods=["GET", "PUT", "DELETE"])
def employment_type_detail(employment_type_id: int):
    sid = current_school_id()
    if request.method == "GET":
        et = get_employment_type(employment_type_id, school_id=sid)
        if not et:
            return jsonify({"error": "Employment Type not found"}), 404
        return jsonify(et.to_dict())

    if request.method == "DELETE":
        try:
            delete_employment_type(employment_type_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Employment Type deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        et = update_employment_type(employment_type_id, data, performed_by=None, school_id=sid)
        return jsonify(et.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# TEAMS
# ============================================================
@hr_bp.route("/teams", methods=["GET", "POST"])
def teams():
    sid = current_school_id()
    if request.method == "GET":
        dept_id = request.args.get("department_id")
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([t.to_dict() for t in get_teams(department_id=int(dept_id) if dept_id else None, active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        team = create_team(data, performed_by=None, school_id=sid)
        return jsonify(team.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/teams/<int:team_id>", methods=["GET", "PUT", "DELETE"])
def team_detail(team_id: int):
    sid = current_school_id()
    if request.method == "GET":
        team = get_team(team_id, school_id=sid)
        if not team:
            return jsonify({"error": "Team not found"}), 404
        return jsonify(team.to_dict())

    if request.method == "DELETE":
        try:
            delete_team(team_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Team deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        team = update_team(team_id, data, performed_by=None, school_id=sid)
        return jsonify(team.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# SALARY COMPONENTS (global / school definitions)
# ============================================================
@hr_bp.route("/salary-components", methods=["GET", "POST"])
def salary_components():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "true").lower() == "true"
        component_type = request.args.get("type")
        q = SalaryComponent.query
        if sid:
            q = q.filter((SalaryComponent.school_id == sid) | (SalaryComponent.school_id.is_(None)))
        if active_only:
            q = q.filter_by(is_active=True)
        if component_type:
            q = q.filter_by(component_type=component_type)
        return jsonify([c.to_dict() for c in q.order_by(SalaryComponent.component_name).all()])

    data = request.get_json() or {}
    required = ["component_code", "component_name", "component_type", "calculation_type"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    
    chk_q = SalaryComponent.query.filter_by(component_code=data["component_code"])
    if sid:
        chk_q = chk_q.filter(SalaryComponent.school_id == sid)
    if chk_q.first():
        return jsonify({"error": f"Component code '{data['component_code']}' already exists"}), 400

    if sid:
        data["school_id"] = sid
    comp = SalaryComponent(**data)
    db.session.add(comp)
    try:
        db.session.commit()
        return jsonify(comp.to_dict()), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/salary-components/<int:comp_id>", methods=["GET", "PUT"])
def salary_component_detail(comp_id: int):
    comp = SalaryComponent.query.get(comp_id)
    if not comp or not is_tenant_permitted(comp.school_id):
        return jsonify({"error": "Salary component not found"}), 404

    if request.method == "GET":
        return jsonify(comp.to_dict())

    data = request.get_json() or {}
    for k, v in data.items():
        if hasattr(comp, k) and k not in ("id", "school_id"):
            setattr(comp, k, v)
    try:
        db.session.commit()
        return jsonify(comp.to_dict())
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/sync-teachers", methods=["POST"])
def sync_teachers():
    sid = current_school_id()
    try:
        res = sync_teachers_to_employees(school_id=sid)
        return jsonify(res)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to sync teachers: {exc}"}), 500


# ============================================================
# EMPLOYEES
# ============================================================
@hr_bp.route("/employees", methods=["GET", "POST"])
def employees():
    sid = current_school_id()
    if request.method == "GET":
        # Automatically sync existing teachers if initial employee list is requested
        if request.args.get("auto_sync", "true").lower() == "true":
            try:
                sync_teachers_to_employees(school_id=sid)
            except Exception as e:
                pass

        filters: Dict = {}
        for param in (
            "department_id", "designation_id", "team_id", "job_grade_id", "job_level_id",
            "cost_center_id", "reporting_manager_id", "campus", "employment_type",
            "employment_type_id", "employment_status", "probation_status", "is_active", "search"
        ):
            val = request.args.get(param)
            if val is not None:
                if param == "is_active":
                    filters["is_active"] = val.lower() == "true"
                elif param in ("department_id", "designation_id", "team_id", "job_grade_id", "job_level_id", "cost_center_id", "reporting_manager_id", "employment_type_id"):
                    try:
                        filters[param] = int(val)
                    except ValueError:
                        pass
                else:
                    filters[param] = val

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        emps, total = get_employees(filters, page, per_page, school_id=sid)
        return jsonify({
            "employees": [e.to_dict() for e in emps],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        })

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth", "joining_date", "confirmation_date", "probation_start_date", "probation_end_date", "exit_date")

    # Extract optional salary configuration fields if submitted together
    basic_sal = data.pop("basic_salary", None)
    ot_rate = data.pop("overtime_rate", None)
    pay_freq = data.pop("pay_frequency", "monthly")
    sal_eff_from = data.pop("salary_effective_from", None) or data.get("joining_date")

    required = ["employee_code", "first_name", "last_name", "email", "phone",
                "joining_date", "employment_type", "department_id", "designation_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    try:
        emp = create_employee(data, performed_by=None, school_id=sid)
        if basic_sal is not None:
            try:
                eff_d = _parse_date(sal_eff_from) if isinstance(sal_eff_from, str) else sal_eff_from
                create_salary_structure(
                    emp.id,
                    {
                        "basic_salary": basic_sal,
                        "gross_salary": basic_sal,
                        "overtime_rate": ot_rate,
                        "pay_frequency": pay_freq,
                        "effective_from": eff_d or emp.joining_date,
                        "status": "active",
                    },
                    performed_by=None,
                )
            except Exception as sal_err:
                # Log but keep employee
                pass
        return jsonify(emp.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "Internal error creating employee"}), 500


@hr_bp.route("/employees/<int:emp_id>", methods=["GET", "PUT", "DELETE"])
def employee_detail(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        return jsonify(emp.to_dict())

    if request.method == "DELETE":
        try:
            delete_employee(emp_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Employee deactivated"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth", "joining_date", "confirmation_date", "probation_start_date", "probation_end_date", "exit_date")
    try:
        emp = update_employee(emp_id, data, performed_by=None, school_id=sid)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/by-teacher/<int:teacher_id>", methods=["GET"])
def employee_by_teacher(teacher_id: int):
    sid = current_school_id()
    emp = get_employee_by_teacher_id(teacher_id, school_id=sid)
    if not emp:
        from database.models import Teacher
        t = Teacher.query.get(teacher_id)
        if not t:
            t = Teacher.query.filter_by(teacher_id=str(teacher_id)).first()
        if t and is_tenant_permitted(t.school_id):
            emp = ensure_employee_from_teacher(t, school_id=sid or t.school_id)
            db.session.commit()
    if not emp:
        return jsonify({"error": "Employee not found for this teacher_id"}), 404
    return jsonify(emp.to_dict())


@hr_bp.route("/employees/<int:emp_id>/360-profile", methods=["GET"])
def employee_360_profile(emp_id: int):
    sid = current_school_id()
    start_date_str = request.args.get("start")
    end_date_str = request.args.get("end")
    month_str = request.args.get("month")
    year_str = request.args.get("year")
    start_d = None
    end_d = None
    if start_date_str:
        start_d = _parse_date(start_date_str)
    if end_date_str:
        end_d = _parse_date(end_date_str)
    if month_str and year_str and not start_d:
        try:
            m = int(month_str)
            y = int(year_str)
            start_d = date(y, m, 1)
            _, last_day = calendar.monthrange(y, m)
            end_d = date(y, m, last_day)
        except Exception:
            pass
    try:
        profile = get_employee_360_profile(emp_id, start_date=start_d, end_date=end_d, school_id=sid)
        return jsonify(profile)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to load 360 profile: {exc}"}), 500


# ============================================================
# EMPLOYEE LIFECYCLE & WORKFLOWS
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/transfer-department", methods=["POST"])
def employee_transfer_department(emp_id: int):
    sid = current_school_id()
    data = request.get_json() or {}
    if not data.get("department_id") and not data.get("new_department_id"):
        return jsonify({"error": "new_department_id is required"}), 400
    new_dept_id = int(data.get("new_department_id") or data.get("department_id"))
    transfer_date = _parse_date(data.get("effective_date") or data.get("transfer_date")) or date.today()
    reason = data.get("reason", "")
    try:
        emp = transfer_employee_department(emp_id, new_dept_id, transfer_date, reason=reason, performed_by=None, school_id=sid)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/change-designation", methods=["POST"])
def employee_change_designation(emp_id: int):
    sid = current_school_id()
    data = request.get_json() or {}
    if not data.get("designation_id") and not data.get("new_designation_id"):
        return jsonify({"error": "new_designation_id is required"}), 400
    new_desig_id = int(data.get("new_designation_id") or data.get("designation_id"))
    effective_date = _parse_date(data.get("effective_date")) or date.today()
    reason = data.get("reason", "")
    try:
        emp = change_employee_designation(emp_id, new_desig_id, effective_date, reason=reason, performed_by=None, school_id=sid)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/change-manager", methods=["POST"])
def employee_change_manager(emp_id: int):
    sid = current_school_id()
    data = request.get_json() or {}
    new_manager_id = data.get("new_manager_id") or data.get("reporting_manager_id")
    if new_manager_id is not None and str(new_manager_id).strip() != "":
        new_manager_id = int(new_manager_id)
    else:
        new_manager_id = None
    effective_date = _parse_date(data.get("effective_date")) or date.today()
    reason = data.get("reason", "")
    try:
        emp = change_employee_manager(emp_id, new_manager_id, effective_date, reason=reason, performed_by=None, school_id=sid)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/change-status", methods=["POST"])
def employee_change_status(emp_id: int):
    sid = current_school_id()
    data = request.get_json() or {}
    new_status = data.get("new_status") or data.get("status")
    if not new_status:
        return jsonify({"error": "new_status is required"}), 400
    effective_date = _parse_date(data.get("effective_date")) or date.today()
    reason = data.get("reason", "")
    exit_reason = data.get("exit_reason")
    try:
        emp = change_employee_status(emp_id, new_status, effective_date, reason=reason, exit_reason=exit_reason, performed_by=None, school_id=sid)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/promotions", methods=["GET", "POST"])
def employee_promotions(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        promos = EmployeePromotion.query.filter_by(employee_id=emp_id).order_by(EmployeePromotion.effective_date.desc()).all()
        return jsonify([p.to_dict() for p in promos])

    data = request.get_json() or {}
    _dates_from(data, "effective_date")
    try:
        promo = record_employee_promotion(emp_id, data, performed_by=None, school_id=sid)
        return jsonify(promo.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# UNIFIED TIMELINE & HISTORIES
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/timeline", methods=["GET", "POST"])
def employee_timeline(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        timeline = get_employee_timeline(emp_id, school_id=sid)
        return jsonify(timeline)

    data = request.get_json() or {}
    if not data.get("event_type") or not data.get("title"):
        return jsonify({"error": "event_type and title are required"}), 400
    try:
        evt = create_timeline_event(
            emp_id,
            event_type=data["event_type"],
            title=data["title"],
            description=data.get("description"),
            metadata_json=data.get("metadata_json") or data.get("metadata"),
            performed_by=None,
            school_id=sid,
        )
        return jsonify(evt.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/history/department", methods=["GET"])
def employee_department_history(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404
    records = EmployeeDepartmentHistory.query.filter_by(employee_id=emp_id).order_by(EmployeeDepartmentHistory.effective_date.desc()).all()
    return jsonify([r.to_dict() for r in records])


@hr_bp.route("/employees/<int:emp_id>/history/designation", methods=["GET"])
def employee_designation_history(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404
    records = EmployeeDesignationHistory.query.filter_by(employee_id=emp_id).order_by(EmployeeDesignationHistory.effective_date.desc()).all()
    return jsonify([r.to_dict() for r in records])


@hr_bp.route("/employees/<int:emp_id>/history/manager", methods=["GET"])
def employee_manager_history(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404
    records = EmployeeManagerHistory.query.filter_by(employee_id=emp_id).order_by(EmployeeManagerHistory.effective_date.desc()).all()
    return jsonify([r.to_dict() for r in records])


@hr_bp.route("/employees/<int:emp_id>/history/status", methods=["GET"])
def employee_status_history(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404
    records = EmployeeStatusHistory.query.filter_by(employee_id=emp_id).order_by(EmployeeStatusHistory.effective_date.desc()).all()
    return jsonify([r.to_dict() for r in records])


# ============================================================
# EMERGENCY CONTACTS
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/emergency-contacts", methods=["GET", "POST"])
def employee_emergency_contacts(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        contacts = get_emergency_contacts(emp_id, school_id=sid)
        return jsonify([c.to_dict() for c in contacts])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("relationship") or not data.get("phone"):
        return jsonify({"error": "name, relationship, and phone are required"}), 400
    try:
        contact = create_emergency_contact(emp_id, data, performed_by=None, school_id=sid)
        return jsonify(contact.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/emergency-contacts/<int:contact_id>", methods=["PUT", "DELETE"])
def employee_emergency_contact_detail(emp_id: int, contact_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "DELETE":
        try:
            delete_emergency_contact(contact_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Emergency contact deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    try:
        contact = update_emergency_contact(contact_id, data, performed_by=None, school_id=sid)
        return jsonify(contact.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# DEPENDENTS
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/dependents", methods=["GET", "POST"])
def employee_dependents(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        deps = get_dependents(emp_id, school_id=sid)
        return jsonify([d.to_dict() for d in deps])

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth")
    if not data.get("name") or not data.get("relationship"):
        return jsonify({"error": "name and relationship are required"}), 400
    try:
        dep = create_dependent(emp_id, data, performed_by=None, school_id=sid)
        return jsonify(dep.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/dependents/<int:dep_id>", methods=["PUT", "DELETE"])
def employee_dependent_detail(emp_id: int, dep_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "DELETE":
        try:
            delete_dependent(dep_id, performed_by=None, school_id=sid)
            return jsonify({"message": "Dependent deleted"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth")
    try:
        dep = update_dependent(dep_id, data, performed_by=None, school_id=sid)
        return jsonify(dep.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# ID CARD & SECURE QR VERIFICATION
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/id-card", methods=["GET"])
def employee_id_card(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    qr_token = get_or_create_qr_identifier(emp)
    school_name = emp.school.name if getattr(emp, "school", None) and emp.school else "AttendanceOS Institution"

    card_data = {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "department": emp.department.name if emp.department else "N/A",
        "designation": emp.designation.name if emp.designation else "N/A",
        "job_grade": emp.job_grade.name if emp.job_grade else None,
        "job_level": emp.job_level.name if emp.job_level else None,
        "employment_type": emp.employment_type_obj.name if emp.employment_type_obj else emp.employment_type,
        "joining_date": emp.joining_date.isoformat() if emp.joining_date else None,
        "blood_group": getattr(emp, "blood_group", None),
        "emergency_phone": emp.emergency_contact_phone,
        "campus": emp.campus or "Main Campus",
        "school_name": school_name,
        "photo_url": emp.profile_image,
        "qr_identifier": qr_token,
        "verification_url": f"/api/hr/verify/{qr_token}",
    }
    return jsonify(card_data)


@hr_bp.route("/verify/<string:qr_identifier>", methods=["GET"])
def verify_id_card(qr_identifier: str):
    """Public ID Card QR Code verification endpoint."""
    res = verify_qr_identifier(qr_identifier)
    if not res:
        return jsonify({"verified": False, "valid": False, "error": "Invalid or expired ID card QR code"}), 404
    return jsonify(res)


# ============================================================
# EMPLOYEE DOCUMENTS
# ============================================================
@hr_bp.route("/employees/<int:emp_id>/documents", methods=["GET", "POST"])
def employee_documents(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        docs = get_employee_documents(emp_id, school_id=sid)
        return jsonify([d.to_dict() for d in docs])

    if request.content_type and "multipart/form-data" in request.content_type:
        file = request.files.get("file")
        title = request.form.get("title") or (file.filename if file else "Document")
        doc_type = request.form.get("document_type") or "other"
        notes = request.form.get("notes", "")

        file_path = request.form.get("file_path", "")
        file_name = ""
        file_size = 0
        mime_type = ""
        if file and file.filename:
            from werkzeug.utils import secure_filename
            upload_dir = os.path.join(current_app.config.get("KNOWN_FACES_DIR", "known_faces"), "..", "documents")
            upload_dir = os.path.abspath(upload_dir)
            os.makedirs(upload_dir, exist_ok=True)
            file_name = secure_filename(file.filename)
            saved_name = f"doc_{emp_id}_{int(datetime.now().timestamp())}_{file_name}"
            full_path = os.path.join(upload_dir, saved_name)
            file.save(full_path)
            file_path = f"/static/documents/{saved_name}"
            file_size = os.path.getsize(full_path)
            mime_type = file.content_type or "application/octet-stream"

        doc_data = {
            "document_type": doc_type,
            "title": title,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "mime_type": mime_type,
            "notes": notes,
        }
    else:
        doc_data = request.get_json() or {}
        if not doc_data.get("title") or not doc_data.get("document_type"):
            return jsonify({"error": "title and document_type are required"}), 400

    try:
        doc = create_employee_document(emp_id, doc_data, performed_by=None, school_id=sid)
        return jsonify(doc.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/<int:emp_id>/documents/<int:doc_id>", methods=["DELETE"])
def employee_document_delete(emp_id: int, doc_id: int):
    sid = current_school_id()
    try:
        delete_employee_document(doc_id, performed_by=None, school_id=sid)
        return jsonify({"message": "Document deleted successfully"})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Salary structure & history --
@hr_bp.route("/employees/<int:emp_id>/salary-history", methods=["GET"])
def employee_salary_history(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404
    history = get_employee_salary_history(emp_id)
    return jsonify([
        {
            **s.to_dict(),
            "components": [c.to_dict() for c in s.salary_components]
        }
        for s in history
    ])


@hr_bp.route("/employees/<int:emp_id>/salary-structure", methods=["GET", "POST"])
def employee_salary_structure(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        effective_date_str = request.args.get("date")
        eff_date = _parse_date(effective_date_str) if effective_date_str else None
        ss = get_employee_salary_structure(emp_id, eff_date)
        if not ss:
            return jsonify({"error": "No active salary structure found"}), 404
        result = ss.to_dict()
        result["components"] = [c.to_dict() for c in ss.salary_components]
        return jsonify(result)

    data = request.get_json() or {}
    _dates_from(data, "effective_from", "effective_to")
    if not data.get("basic_salary") or not data.get("effective_from"):
        return jsonify({"error": "basic_salary and effective_from are required"}), 400
    try:
        ss = create_salary_structure(emp_id, data, performed_by=None)
        return jsonify(ss.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route(
    "/employees/<int:emp_id>/salary-structure/<int:ss_id>/components",
    methods=["POST"]
)
def employee_salary_component(emp_id: int, ss_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    ss = EmployeeSalaryStructure.query.filter_by(id=ss_id, employee_id=emp_id).first()
    if not ss:
        return jsonify({"error": "Salary structure not found"}), 404

    data = request.get_json() or {}
    if not data.get("component_id") or data.get("amount") is None:
        return jsonify({"error": "component_id and amount are required"}), 400
    try:
        sc = add_salary_component(ss_id, data, performed_by=None)
        return jsonify(sc.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Leave balances --
@hr_bp.route("/employees/<int:emp_id>/leave-balances", methods=["GET"])
def employee_leave_balances(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    year = int(request.args.get("year", date.today().year))
    balances = EmployeeLeaveBalance.query.filter_by(
        employee_id=emp_id, year=year
    ).all()
    return jsonify([b.to_dict() for b in balances])


# -- Leave requests --
@hr_bp.route("/employees/<int:emp_id>/leaves", methods=["POST"])
def create_employee_leave(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    data = request.get_json() or {}
    required = ["leave_type_id", "start_date", "end_date"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    try:
        lr = create_leave_request(emp_id, data, performed_by=None)
        return jsonify(lr.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Overtime --
@hr_bp.route("/employees/<int:emp_id>/overtime", methods=["POST"])
def create_employee_overtime(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    data = request.get_json() or {}
    _dates_from(data, "overtime_date")
    required = ["overtime_date", "overtime_minutes", "hourly_rate", "calculated_amount"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    try:
        rec = create_overtime_record(emp_id, data, performed_by=None)
        return jsonify(rec.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Bonus --
@hr_bp.route("/employees/<int:emp_id>/bonuses", methods=["POST"])
def create_employee_bonus_route(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    data = request.get_json() or {}
    if not data.get("bonus_type") or not data.get("amount"):
        return jsonify({"error": "bonus_type and amount are required"}), 400
    try:
        bonus = create_employee_bonus(emp_id, data, performed_by=None)
        return jsonify(bonus.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Loans --
@hr_bp.route("/employees/<int:emp_id>/loans", methods=["GET", "POST"])
def employee_loans(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    if request.method == "GET":
        loans = EmployeeLoan.query.filter_by(employee_id=emp_id).all()
        return jsonify([l.to_dict() for l in loans])

    data = request.get_json() or {}
    _dates_from(data, "start_date", "end_date")
    required = ["loan_type", "principal_amount", "installment_amount", "start_date"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    try:
        loan = create_employee_loan(emp_id, data, performed_by=None)
        return jsonify(loan.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/loans/<int:loan_id>/repayments", methods=["POST"])
def loan_repayment_route(loan_id: int):
    loan = EmployeeLoan.query.get(loan_id)
    if not loan or not is_tenant_permitted(loan.school_id):
        return jsonify({"error": "Loan not found"}), 404

    data = request.get_json() or {}
    _dates_from(data, "payment_date")
    if not data.get("amount") or not data.get("payment_date"):
        return jsonify({"error": "amount and payment_date are required"}), 400
    try:
        rep = create_loan_repayment(loan_id, data, performed_by=None)
        return jsonify(rep.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# -- Reimbursements --
@hr_bp.route("/employees/<int:emp_id>/reimbursements", methods=["POST"])
def create_employee_reimbursement(emp_id: int):
    sid = current_school_id()
    emp = get_employee(emp_id, school_id=sid)
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    data = request.get_json() or {}
    _dates_from(data, "claim_date")
    if not data.get("reimbursement_type") or not data.get("amount") or not data.get("claim_date"):
        return jsonify({"error": "reimbursement_type, amount, and claim_date are required"}), 400
    try:
        reimb = create_reimbursement(emp_id, data, performed_by=None)
        return jsonify(reimb.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# LEAVE TYPES
# ============================================================
@hr_bp.route("/leave-types", methods=["GET", "POST"])
def leave_types():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "true").lower() == "true"
        return jsonify([lt.to_dict() for lt in get_leave_types(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        lt = create_leave_type(data, performed_by=None, school_id=sid)
        return jsonify(lt.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# SHIFTS
# ============================================================
@hr_bp.route("/shifts", methods=["GET", "POST"])
def shifts():
    sid = current_school_id()
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([s.to_dict() for s in get_shifts(active_only=active_only, school_id=sid)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code") or not data.get("start_time") or not data.get("end_time"):
        return jsonify({"error": "name, code, start_time, and end_time are required"}), 400
    try:
        shift = create_shift(data, performed_by=None, school_id=sid)
        return jsonify(shift.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/shifts/<int:shift_id>", methods=["GET", "PUT"])
def shift_detail(shift_id: int):
    sid = current_school_id()
    if request.method == "GET":
        s = get_shift(shift_id, school_id=sid)
        if not s:
            return jsonify({"error": "Shift not found"}), 404
        return jsonify(s.to_dict())

    data = request.get_json() or {}
    try:
        s = update_shift(shift_id, data, performed_by=None, school_id=sid)
        return jsonify(s.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/leaves", methods=["GET"])
def all_leaves():
    sid = current_school_id()
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = LeaveRequest.query
    if sid:
        q = q.filter((LeaveRequest.school_id == sid) | (LeaveRequest.school_id.is_(None)))
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    q = q.order_by(LeaveRequest.created_at.desc())
    return jsonify([lr.to_dict() for lr in q.all()])


@hr_bp.route("/leaves/<int:lr_id>/approve", methods=["PUT"])
def approve_leave(lr_id: int):
    lr = LeaveRequest.query.get(lr_id)
    if not lr or not is_tenant_permitted(lr.school_id):
        return jsonify({"error": "Leave request not found"}), 404

    data = request.get_json() or {}
    approved_by = data.get("approved_by")
    if not approved_by:
        return jsonify({"error": "approved_by is required"}), 400
    try:
        lr = approve_leave_request(lr_id, int(approved_by), performed_by=None)
        return jsonify(lr.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/leaves/<int:lr_id>/reject", methods=["PUT"])
def reject_leave(lr_id: int):
    lr = LeaveRequest.query.get(lr_id)
    if not lr or not is_tenant_permitted(lr.school_id):
        return jsonify({"error": "Leave request not found"}), 404

    data = request.get_json() or {}
    reason = data.get("rejection_reason", "")
    try:
        lr = reject_leave_request(lr_id, reason, performed_by=None)
        return jsonify(lr.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/leaves/<int:lr_id>/cancel", methods=["PUT"])
def cancel_leave(lr_id: int):
    lr = LeaveRequest.query.get(lr_id)
    if not lr or not is_tenant_permitted(lr.school_id):
        return jsonify({"error": "Leave request not found"}), 404

    try:
        lr = cancel_leave_request(lr_id, performed_by=None)
        return jsonify(lr.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# OVERTIME (admin view)
# ============================================================
@hr_bp.route("/overtime", methods=["GET"])
def all_overtime():
    sid = current_school_id()
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = OvertimeRecord.query
    if sid:
        q = q.filter((OvertimeRecord.school_id == sid) | (OvertimeRecord.school_id.is_(None)))
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    return jsonify([r.to_dict() for r in q.order_by(OvertimeRecord.overtime_date.desc()).all()])


@hr_bp.route("/overtime/<int:ot_id>/approve", methods=["PUT"])
def approve_overtime(ot_id: int):
    rec = OvertimeRecord.query.get(ot_id)
    if not rec or not is_tenant_permitted(rec.school_id):
        return jsonify({"error": "Overtime record not found"}), 404

    data = request.get_json() or {}
    approved_by = data.get("approved_by")
    if not approved_by:
        return jsonify({"error": "approved_by is required"}), 400
    try:
        rec = approve_overtime_record(ot_id, int(approved_by), performed_by=None)
        return jsonify(rec.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# REIMBURSEMENTS (admin view)
# ============================================================
@hr_bp.route("/reimbursements", methods=["GET"])
def all_reimbursements():
    sid = current_school_id()
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = Reimbursement.query
    if sid:
        q = q.filter((Reimbursement.school_id == sid) | (Reimbursement.school_id.is_(None)))
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    return jsonify([r.to_dict() for r in q.order_by(Reimbursement.claim_date.desc()).all()])


@hr_bp.route("/reimbursements/<int:reimb_id>/approve", methods=["PUT"])
def approve_reimb(reimb_id: int):
    reimb = Reimbursement.query.get(reimb_id)
    if not reimb or not is_tenant_permitted(reimb.school_id):
        return jsonify({"error": "Reimbursement not found"}), 404

    data = request.get_json() or {}
    approved_by = data.get("approved_by")
    if not approved_by:
        return jsonify({"error": "approved_by is required"}), 400
    try:
        reimb = approve_reimbursement(
            reimb_id, int(approved_by),
            approved_amount=data.get("approved_amount"),
            performed_by=None,
        )
        return jsonify(reimb.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# PAYROLL PERIODS
# ============================================================
@hr_bp.route("/payroll-periods", methods=["GET", "POST"])
def payroll_periods():
    sid = current_school_id()
    if request.method == "GET":
        q = PayrollPeriod.query
        if sid:
            q = q.filter((PayrollPeriod.school_id == sid) | (PayrollPeriod.school_id.is_(None)))
        status_filter = request.args.get("status")
        if status_filter:
            q = q.filter_by(status=status_filter)
        periods = q.order_by(PayrollPeriod.start_date.desc()).all()
        return jsonify([p.to_dict() for p in periods])

    data = request.get_json() or {}
    _dates_from(data, "start_date", "end_date", "payment_date")
    required = ["period_name", "start_date", "end_date", "payment_date"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400
    if sid:
        data["school_id"] = sid
    try:
        pp = create_payroll_period(data, performed_by=None)
        return jsonify(pp.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/payroll-periods/<int:period_id>", methods=["GET"])
def payroll_period_detail(period_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404
    return jsonify(pp.to_dict())


@hr_bp.route("/payroll-periods/<int:period_id>/process", methods=["POST"])
def process_payroll(period_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404
    try:
        records = process_payroll_for_period(period_id, performed_by=None)
        return jsonify({
            "message": f"Payroll processed for {len(records)} employees",
            "records_count": len(records),
            "payroll_records": [r.to_dict() for r in records],
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Payroll processing failed: {exc}"}), 500


@hr_bp.route("/payroll-periods/<int:period_id>/finalize", methods=["POST"])
def finalize_payroll(period_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404
    try:
        pp = finalize_payroll_period(period_id, performed_by=None)
        return jsonify({"message": "Payroll period finalized", "payroll_period": pp.to_dict()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/payroll-periods/<int:period_id>/records", methods=["GET"])
def payroll_period_records(period_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404

    records = get_payroll_records_for_period(period_id)
    result = []
    for rec in records:
        rd = rec.to_dict()
        rd["earnings"] = [e.to_dict() for e in rec.earnings]
        rd["deductions"] = [d.to_dict() for d in rec.deductions]
        if rec.attendance_summary:
            rd["attendance_summary"] = rec.attendance_summary.to_dict()
        result.append(rd)
    return jsonify(result)


@hr_bp.route("/payroll-periods/<int:period_id>/records/<int:record_id>", methods=["GET"])
def payroll_record_detail(period_id: int, record_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404

    rec = PayrollRecord.query.filter_by(id=record_id, payroll_period_id=period_id).first()
    if not rec or not is_tenant_permitted(rec.school_id):
        return jsonify({"error": "Payroll record not found"}), 404
    rd = rec.to_dict()
    rd["earnings"] = [e.to_dict() for e in rec.earnings]
    rd["deductions"] = [d.to_dict() for d in rec.deductions]
    if rec.attendance_summary:
        rd["attendance_summary"] = rec.attendance_summary.to_dict()
    return jsonify(rd)


@hr_bp.route(
    "/payroll-periods/<int:period_id>/records/<int:record_id>/payslip",
    methods=["POST"]
)
def create_payslip_route(period_id: int, record_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp or not is_tenant_permitted(pp.school_id):
        return jsonify({"error": "Payroll period not found"}), 404

    rec = PayrollRecord.query.filter_by(id=record_id, payroll_period_id=period_id).first()
    if not rec or not is_tenant_permitted(rec.school_id):
        return jsonify({"error": "Payroll record not found"}), 404
    try:
        slip = create_payslip(record_id, performed_by=None)
        return jsonify(slip.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# DASHBOARD SUMMARY
# ============================================================
@hr_bp.route("/dashboard/summary", methods=["GET"])
def dashboard_summary():
    sid = current_school_id()
    try:
        return jsonify(get_hr_dashboard_summary(school_id=sid))
    except Exception as exc:
        return jsonify({"error": f"Dashboard error: {exc}"}), 500


# ============================================================
# AUDIT LOG
# ============================================================
@hr_bp.route("/audit-log", methods=["GET"])
def audit_log():
    sid = current_school_id()
    entity_type = request.args.get("entity_type")
    entity_id = request.args.get("entity_id")
    performed_by = request.args.get("performed_by")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    q = HRAuditLog.query
    if sid:
        q = q.filter((HRAuditLog.school_id == sid) | (HRAuditLog.school_id.is_(None)))
    if entity_type:
        q = q.filter_by(entity_type=entity_type)
    if entity_id:
        q = q.filter_by(entity_id=int(entity_id))
    if performed_by:
        q = q.filter_by(performed_by=int(performed_by))

    total = q.count()
    logs = q.order_by(HRAuditLog.performed_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        "logs": [log.to_dict() for log in logs],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })