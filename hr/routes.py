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

from datetime import date, datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from database.db import db
from hr.employee_service import (
    # Department
    create_department, get_department, get_departments, update_department,
    # Designation
    create_designation, get_designation, get_designations, update_designation,
    # Employee
    create_employee, get_employee, get_employee_by_code, get_employee_by_teacher_id,
    get_employees, update_employee, delete_employee, sync_teachers_to_employees,
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
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([d.to_dict() for d in get_departments(active_only=active_only)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        dept = create_department(data, performed_by=None)
        return jsonify(dept.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/departments/<int:dept_id>", methods=["GET", "PUT"])
def department_detail(dept_id: int):
    if request.method == "GET":
        dept = get_department(dept_id)
        if not dept:
            return jsonify({"error": "Department not found"}), 404
        return jsonify(dept.to_dict())

    data = request.get_json() or {}
    try:
        dept = update_department(dept_id, data, performed_by=None)
        return jsonify(dept.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# DESIGNATIONS
# ============================================================
@hr_bp.route("/designations", methods=["GET", "POST"])
def designations():
    if request.method == "GET":
        dept_id = request.args.get("department_id")
        return jsonify([
            d.to_dict() for d in get_designations(
                department_id=int(dept_id) if dept_id else None
            )
        ])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code") or not data.get("department_id"):
        return jsonify({"error": "name, code, and department_id are required"}), 400
    try:
        desig = create_designation(data, performed_by=None)
        return jsonify(desig.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/designations/<int:desig_id>", methods=["GET", "PUT"])
def designation_detail(desig_id: int):
    if request.method == "GET":
        desig = get_designation(desig_id)
        if not desig:
            return jsonify({"error": "Designation not found"}), 404
        return jsonify(desig.to_dict())

    data = request.get_json() or {}
    try:
        desig = update_designation(desig_id, data, performed_by=None)
        return jsonify(desig.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# SALARY COMPONENTS (global definitions)
# ============================================================
@hr_bp.route("/salary-components", methods=["GET", "POST"])
def salary_components():
    if request.method == "GET":
        active_only = request.args.get("active_only", "true").lower() == "true"
        component_type = request.args.get("type")
        q = SalaryComponent.query
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
    if SalaryComponent.query.filter_by(component_code=data["component_code"]).first():
        return jsonify({"error": f"Component code '{data['component_code']}' already exists"}), 400

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
    if not comp:
        return jsonify({"error": "Salary component not found"}), 404

    if request.method == "GET":
        return jsonify(comp.to_dict())

    data = request.get_json() or {}
    for k, v in data.items():
        if hasattr(comp, k) and k != "id":
            setattr(comp, k, v)
    try:
        db.session.commit()
        return jsonify(comp.to_dict())
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/sync-teachers", methods=["POST"])
def sync_teachers():
    try:
        res = sync_teachers_to_employees()
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
    if request.method == "GET":
        # Automatically sync existing teachers if initial employee list is requested
        if request.args.get("auto_sync", "true").lower() == "true":
            try:
                sync_teachers_to_employees()
            except Exception as e:
                pass

        filters: Dict = {}
        for param in ("department_id", "designation_id", "employment_status", "is_active", "search"):
            val = request.args.get(param)
            if val is not None:
                if param == "is_active":
                    filters["is_active"] = val.lower() == "true"
                elif param in ("department_id", "designation_id"):
                    filters[param] = int(val)
                else:
                    filters[param] = val

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        emps, total = get_employees(filters, page, per_page)
        return jsonify({
            "employees": [e.to_dict() for e in emps],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        })

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth", "joining_date", "confirmation_date")

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
        emp = create_employee(data, performed_by=None)
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
    if request.method == "GET":
        emp = get_employee(emp_id)
        if not emp:
            return jsonify({"error": "Employee not found"}), 404
        return jsonify(emp.to_dict())

    if request.method == "DELETE":
        try:
            delete_employee(emp_id, performed_by=None)
            return jsonify({"message": "Employee deactivated"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    data = request.get_json() or {}
    _dates_from(data, "date_of_birth", "joining_date", "confirmation_date")
    try:
        emp = update_employee(emp_id, data, performed_by=None)
        return jsonify(emp.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/employees/by-teacher/<int:teacher_id>", methods=["GET"])
def employee_by_teacher(teacher_id: int):
    emp = get_employee_by_teacher_id(teacher_id)
    if not emp:
        return jsonify({"error": "Employee not found for this teacher_id"}), 404
    return jsonify(emp.to_dict())


# -- Salary structure & history --
@hr_bp.route("/employees/<int:emp_id>/salary-history", methods=["GET"])
def employee_salary_history(emp_id: int):
    emp = get_employee(emp_id)
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
    year = int(request.args.get("year", date.today().year))
    balances = EmployeeLeaveBalance.query.filter_by(
        employee_id=emp_id, year=year
    ).all()
    return jsonify([b.to_dict() for b in balances])


# -- Leave requests --
@hr_bp.route("/employees/<int:emp_id>/leaves", methods=["POST"])
def create_employee_leave(emp_id: int):
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
    if request.method == "GET":
        active_only = request.args.get("active_only", "true").lower() == "true"
        return jsonify([lt.to_dict() for lt in get_leave_types(active_only=active_only)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "name and code are required"}), 400
    try:
        lt = create_leave_type(data, performed_by=None)
        return jsonify(lt.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# SHIFTS
# ============================================================
@hr_bp.route("/shifts", methods=["GET", "POST"])
def shifts():
    if request.method == "GET":
        active_only = request.args.get("active_only", "false").lower() == "true"
        return jsonify([s.to_dict() for s in get_shifts(active_only=active_only)])

    data = request.get_json() or {}
    if not data.get("name") or not data.get("code") or not data.get("start_time") or not data.get("end_time"):
        return jsonify({"error": "name, code, start_time, and end_time are required"}), 400
    try:
        shift = create_shift(data, performed_by=None)
        return jsonify(shift.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/shifts/<int:shift_id>", methods=["GET", "PUT"])
def shift_detail(shift_id: int):
    if request.method == "GET":
        s = get_shift(shift_id)
        if not s:
            return jsonify({"error": "Shift not found"}), 404
        return jsonify(s.to_dict())

    data = request.get_json() or {}
    try:
        s = update_shift(shift_id, data, performed_by=None)
        return jsonify(s.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ============================================================
# LEAVE REQUESTS (admin view)
# ============================================================
@hr_bp.route("/leaves", methods=["GET"])
def all_leave_requests():
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = LeaveRequest.query
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    q = q.order_by(LeaveRequest.created_at.desc())
    return jsonify([lr.to_dict() for lr in q.all()])


@hr_bp.route("/leaves/<int:lr_id>/approve", methods=["PUT"])
def approve_leave(lr_id: int):
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
    data = request.get_json() or {}
    reason = data.get("rejection_reason", "")
    try:
        lr = reject_leave_request(lr_id, reason, performed_by=None)
        return jsonify(lr.to_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/leaves/<int:lr_id>/cancel", methods=["PUT"])
def cancel_leave(lr_id: int):
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
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = OvertimeRecord.query
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    return jsonify([r.to_dict() for r in q.order_by(OvertimeRecord.overtime_date.desc()).all()])


@hr_bp.route("/overtime/<int:ot_id>/approve", methods=["PUT"])
def approve_overtime(ot_id: int):
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
    status_filter = request.args.get("status")
    emp_id = request.args.get("employee_id")
    q = Reimbursement.query
    if status_filter:
        q = q.filter_by(status=status_filter)
    if emp_id:
        q = q.filter_by(employee_id=int(emp_id))
    return jsonify([r.to_dict() for r in q.order_by(Reimbursement.claim_date.desc()).all()])


@hr_bp.route("/reimbursements/<int:reimb_id>/approve", methods=["PUT"])
def approve_reimb(reimb_id: int):
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
    if request.method == "GET":
        q = PayrollPeriod.query
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
    try:
        pp = create_payroll_period(data, performed_by=None)
        return jsonify(pp.to_dict()), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/payroll-periods/<int:period_id>", methods=["GET"])
def payroll_period_detail(period_id: int):
    pp = PayrollPeriod.query.get(period_id)
    if not pp:
        return jsonify({"error": "Payroll period not found"}), 404
    return jsonify(pp.to_dict())


@hr_bp.route("/payroll-periods/<int:period_id>/process", methods=["POST"])
def process_payroll(period_id: int):
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
    try:
        pp = finalize_payroll_period(period_id, performed_by=None)
        return jsonify({"message": "Payroll period finalized", "payroll_period": pp.to_dict()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@hr_bp.route("/payroll-periods/<int:period_id>/records", methods=["GET"])
def payroll_period_records(period_id: int):
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
    rec = PayrollRecord.query.filter_by(id=record_id, payroll_period_id=period_id).first()
    if not rec:
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
    rec = PayrollRecord.query.filter_by(id=record_id, payroll_period_id=period_id).first()
    if not rec:
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
    try:
        return jsonify(get_hr_dashboard_summary())
    except Exception as exc:
        return jsonify({"error": f"Dashboard error: {exc}"}), 500


# ============================================================
# AUDIT LOG
# ============================================================
@hr_bp.route("/audit-log", methods=["GET"])
def audit_log():
    entity_type = request.args.get("entity_type")
    entity_id = request.args.get("entity_id")
    performed_by = request.args.get("performed_by")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    q = HRAuditLog.query
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