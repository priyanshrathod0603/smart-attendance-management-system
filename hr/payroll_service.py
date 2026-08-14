"""Central payroll calculation service for AttendanceOS HR module.

DESIGN RULES
------------
* There is EXACTLY ONE attendance system: the existing TeacherAttendance /
  StudentAttendance tables written by attendance_service.py.  This service
  READS those tables — it never duplicates their logic.
* Attendance lookup uses Employee.teacher_id to find TeacherAttendance rows.
  employee.id ≠ teacher.id.
* Every payroll run is wrapped in a single DB transaction.  If any employee
  fails, the entire run is rolled back.
* After processing, a PayrollAttendanceSummary row is written for each
  employee — this is the immutable snapshot so finalized payroll figures
  cannot be silently changed by later attendance edits.
* Monetary values are always rounded to 2 decimal places.
* Net salary is never allowed to go below 0 (configurable via HR settings).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, or_, func, desc
from sqlalchemy.exc import IntegrityError

from database.db import db
from database.enterprise_models import (
    TeacherAttendance,
    HolidayCalendar,
    WeeklyOff,
)
from hr.models import (
    Employee,
    EmployeeSalaryStructure,
    EmployeeSalaryComponent,
    SalaryComponent,
    LeaveType,
    EmployeeLeaveBalance,
    LeaveRequest,
    OvertimeRecord,
    EmployeeBonus,
    EmployeeLoan,
    LoanRepayment,
    Reimbursement,
    PayrollPeriod,
    PayrollRecord,
    PayrollEarning,
    PayrollDeduction,
    PayrollAttendanceSummary,
    Payslip,
    HRAuditLog,
    EmployeeShift,
    Shift,
)


# ─────────────────────────────────────────────── helpers
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _round2(value: float | Decimal) -> Decimal:
    """Round monetary value to 2 decimal places (ROUND_HALF_UP)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(value) -> float:
    """Convert Decimal / None to float safely."""
    if value is None:
        return 0.0
    return float(value)


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
    """Append an HR audit log row (does NOT commit — caller owns the session)."""
    audit_log = HRAuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=performed_by,
        old_values=_make_json_safe(old_values),
        new_values=_make_json_safe(new_values),
        reason=reason,
    )
    db.session.add(audit_log)


# ─────────────────────────────────────────────── salary structure
def get_employee_salary_structure(
    employee_id: int, effective_date: Optional[date] = None
) -> Optional[EmployeeSalaryStructure]:
    """Return the active salary structure for *employee_id* on *effective_date*."""
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


# ─────────────────────────────────────────────── shift helper
def get_employee_shift(employee_id: int, effective_date: date) -> Optional[Shift]:
    """Return the employee's assigned shift for *effective_date*, or None."""
    assignment = EmployeeShift.query.filter(
        EmployeeShift.employee_id == employee_id,
        EmployeeShift.effective_from <= effective_date,
        or_(
            EmployeeShift.effective_to.is_(None),
            EmployeeShift.effective_to >= effective_date,
        ),
    ).first()
    return assignment.shift if assignment else None


# ─────────────────────────────────────────────── working-days counter
def get_working_days_in_period(
    start_date: date,
    end_date: date,
    applies_to: str = "teacher",
) -> int:
    """Count calendar working days in [start_date, end_date] (excl. weekends & holidays).

    Uses the HolidayCalendar + WeeklyOff tables already present in the
    existing system — no new holiday logic is duplicated.
    """
    # Pull all weekly-off weekday numbers for this scope (and 'all')
    weekly_off_rows = WeeklyOff.query.filter(
        or_(WeeklyOff.applies_to == applies_to, WeeklyOff.applies_to == "all")
    ).all()
    weekly_off_days = {row.weekday for row in weekly_off_rows}
    # Default: Saturday (5) and Sunday (6) if no explicit config
    if not weekly_off_days:
        weekly_off_days = {5, 6}

    # Pull holidays in the range
    holiday_dates = {
        row.holiday_date
        for row in HolidayCalendar.query.filter(
            HolidayCalendar.holiday_date >= start_date,
            HolidayCalendar.holiday_date <= end_date,
            or_(
                HolidayCalendar.applies_to == applies_to,
                HolidayCalendar.applies_to == "all",
            ),
        ).all()
    }

    working_days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() not in weekly_off_days and current not in holiday_dates:
            working_days += 1
        current += timedelta(days=1)
    return working_days


# ─────────────────────────────────────────────── attendance summary
def get_attendance_summary_for_employee(
    employee_id: int,
    start_date: date,
    end_date: date,
) -> Dict:
    """Aggregate TeacherAttendance rows for *employee_id* over the period.

    IMPORTANT: employee_id is the HR Employee PK.  We look up
    Employee.teacher_id to find the corresponding TeacherAttendance rows —
    these are NOT the same integer.
    """
    employee = Employee.query.get(employee_id)
    if not employee or not employee.teacher_id:
        # Employee has no linked teacher → zero attendance
        return _empty_summary(start_date, end_date)

    teacher_id = employee.teacher_id

    attendance_records = TeacherAttendance.query.filter(
        TeacherAttendance.teacher_id == teacher_id,
        TeacherAttendance.attendance_date >= start_date,
        TeacherAttendance.attendance_date <= end_date,
    ).all()

    summary = _empty_summary(start_date, end_date)
    summary["working_days"] = get_working_days_in_period(start_date, end_date)

    for rec in attendance_records:
        wm = rec.working_minutes or 0
        summary["total_working_minutes"] += wm

        if rec.is_late:
            summary["late_count"] += 1
        if rec.is_early_exit:
            summary["early_exit_count"] += 1
        if rec.overtime_minutes:
            summary["overtime_minutes"] += rec.overtime_minutes

        status = (rec.status or "").lower()
        if status == "absent":
            summary["absent_days"] += 1
        elif status == "half_day":
            summary["half_days"] += 1
        else:
            # present / late / early_exit / overtime all count as present
            summary["present_days"] += 1

    # Holidays in range
    weekly_off_rows = WeeklyOff.query.filter(
        or_(WeeklyOff.applies_to == "teacher", WeeklyOff.applies_to == "all")
    ).all()
    weekly_off_weekdays = {row.weekday for row in weekly_off_rows} or {5, 6}

    holiday_dates = {
        row.holiday_date
        for row in HolidayCalendar.query.filter(
            HolidayCalendar.holiday_date >= start_date,
            HolidayCalendar.holiday_date <= end_date,
            or_(
                HolidayCalendar.applies_to == "teacher",
                HolidayCalendar.applies_to == "all",
            ),
        ).all()
    }
    summary["holidays"] = len(holiday_dates)

    # Weekly offs
    current = start_date
    weekly_off_count = 0
    while current <= end_date:
        if current.weekday() in weekly_off_weekdays:
            weekly_off_count += 1
        current += timedelta(days=1)
    summary["weekly_offs"] = weekly_off_count

    # Paid / unpaid leave from approved leave requests
    approved_leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == "approved",
        or_(
            and_(
                LeaveRequest.start_date >= start_date,
                LeaveRequest.start_date <= end_date,
            ),
            and_(
                LeaveRequest.end_date >= start_date,
                LeaveRequest.end_date <= end_date,
            ),
            and_(
                LeaveRequest.start_date <= start_date,
                LeaveRequest.end_date >= end_date,
            ),
        ),
    ).all()

    for leave in approved_leaves:
        leave_start = max(leave.start_date, start_date)
        leave_end = min(leave.end_date, end_date)
        if leave_start <= leave_end:
            days = float((leave_end - leave_start).days + 1)
            lt = LeaveType.query.get(leave.leave_type_id)
            if lt and lt.paid:
                summary["paid_leave_days"] += days
            else:
                summary["unpaid_leave_days"] += days

    # Payable days = present + paid leave (half-days contribute 0.5)
    summary["payable_days"] = (
        summary["present_days"]
        + (summary["half_days"] * 0.5)
        + summary["paid_leave_days"]
    )
    return summary


def _empty_summary(start_date: date, end_date: date) -> Dict:
    return {
        "total_calendar_days": (end_date - start_date).days + 1,
        "working_days": 0,
        "present_days": 0,
        "absent_days": 0,
        "half_days": 0,
        "paid_leave_days": 0.0,
        "unpaid_leave_days": 0.0,
        "holidays": 0,
        "weekly_offs": 0,
        "late_count": 0,
        "early_exit_count": 0,
        "overtime_minutes": 0,
        "total_working_minutes": 0,
        "payable_days": 0.0,
    }


# ─────────────────────────────────────────────── daily salary
def calculate_daily_salary(
    salary_structure: EmployeeSalaryStructure,
    payroll_period: PayrollPeriod,
) -> Decimal:
    """Return daily salary for pro-ration.

    Monthly salary / working days in the period (not calendar days).
    """
    freq = salary_structure.pay_frequency
    basic = Decimal(str(salary_structure.basic_salary))

    if freq == "monthly":
        working_days = get_working_days_in_period(
            payroll_period.start_date, payroll_period.end_date
        )
        divisor = max(working_days, 1)
    elif freq == "bi-weekly":
        divisor = 10
    elif freq == "weekly":
        divisor = 5
    else:
        divisor = 22  # fallback

    return _round2(basic / divisor)


# ─────────────────────────────────────────────── earnings
def calculate_earnings(
    salary_structure: EmployeeSalaryStructure,
    payroll_period: PayrollPeriod,
    attendance_summary: Dict,
    approved_overtime_amount: Decimal,
    bonus_amount: Decimal,
    reimbursement_amount: Decimal,
) -> Tuple[Decimal, List[Dict]]:
    """Calculate gross earnings for one employee in one period.

    Returns (total_earnings, list_of_earning_dicts).
    Each dict has: component_id, component_name, amount.
    """
    details: List[Dict] = []
    basic = _round2(salary_structure.basic_salary)

    details.append({
        "component_id": None,
        "component_name": "Basic Salary",
        "amount": basic,
    })
    total = basic

    # Configured salary components (earnings only)
    emp_components = (
        EmployeeSalaryComponent.query
        .join(SalaryComponent)
        .filter(
            EmployeeSalaryComponent.salary_structure_id == salary_structure.id,
            SalaryComponent.component_type == "earning",
            SalaryComponent.is_active == True,
        )
        .all()
    )
    for ec in emp_components:
        comp = ec.component
        if comp.calculation_type == "fixed":
            amount = _round2(ec.amount)
        else:
            pct = float(ec.percentage or comp.percentage_of_component or 0)
            amount = _round2(basic * Decimal(str(pct)) / 100)

        if amount > 0:
            details.append({
                "component_id": comp.id,
                "component_name": comp.component_name,
                "amount": amount,
            })
            total += amount

    # Overtime (from approved OT records, passed in by caller)
    if approved_overtime_amount > 0:
        details.append({
            "component_id": None,
            "component_name": "Overtime",
            "amount": _round2(approved_overtime_amount),
        })
        total += _round2(approved_overtime_amount)

    # Bonus
    if bonus_amount > 0:
        details.append({
            "component_id": None,
            "component_name": "Bonus",
            "amount": _round2(bonus_amount),
        })
        total += _round2(bonus_amount)

    # Approved reimbursements
    if reimbursement_amount > 0:
        details.append({
            "component_id": None,
            "component_name": "Reimbursement",
            "amount": _round2(reimbursement_amount),
        })
        total += _round2(reimbursement_amount)

    return _round2(total), details


# ─────────────────────────────────────────────── deductions
def calculate_deductions(
    salary_structure: EmployeeSalaryStructure,
    payroll_period: PayrollPeriod,
    attendance_summary: Dict,
    gross_earnings: Decimal,
    loan_deduction: Decimal,
) -> Tuple[Decimal, List[Dict], Decimal, Decimal]:
    """Calculate total deductions.

    Returns (total_deductions, detail_list, leave_deduction, late_deduction).
    """
    details: List[Dict] = []
    total = Decimal("0.00")
    daily_salary = calculate_daily_salary(salary_structure, payroll_period)
    basic = Decimal(str(salary_structure.basic_salary))

    # Configured deduction components
    emp_components = (
        EmployeeSalaryComponent.query
        .join(SalaryComponent)
        .filter(
            EmployeeSalaryComponent.salary_structure_id == salary_structure.id,
            SalaryComponent.component_type == "deduction",
            SalaryComponent.is_active == True,
        )
        .all()
    )
    for ec in emp_components:
        comp = ec.component
        if comp.calculation_type == "fixed":
            amount = _round2(ec.amount)
        else:
            pct = float(ec.percentage or comp.percentage_of_component or 0)
            base = basic if comp.percentage_of_component else gross_earnings
            amount = _round2(base * Decimal(str(pct)) / 100)

        if amount > 0:
            details.append({
                "component_id": comp.id,
                "component_name": comp.component_name,
                "amount": amount,
            })
            total += amount

    # Unpaid leave deduction
    leave_ded = Decimal("0.00")
    unpaid_days = Decimal(str(attendance_summary.get("unpaid_leave_days", 0)))
    if unpaid_days > 0 and daily_salary > 0:
        leave_ded = _round2(daily_salary * unpaid_days)
        details.append({"component_id": None, "component_name": "Unpaid Leave Deduction",
                         "amount": leave_ded})
        total += leave_ded

    # Half-day deduction (half day = 0.5 × daily salary)
    half_days = Decimal(str(attendance_summary.get("half_days", 0)))
    if half_days > 0 and daily_salary > 0:
        hd_ded = _round2(daily_salary * Decimal("0.5") * half_days)
        details.append({"component_id": None, "component_name": "Half Day Deduction",
                         "amount": hd_ded})
        total += hd_ded
        leave_ded += hd_ded

    # Late deduction — uses configured policy
    late_ded = Decimal("0.00")
    late_count = attendance_summary.get("late_count", 0)
    policy = salary_structure.late_deduction_policy or "none"
    rate = Decimal(str(salary_structure.late_deduction_rate or 0))

    if policy == "fixed" and late_count > 0 and rate > 0:
        late_ded = _round2(rate * late_count)
    elif policy == "per_occurrence" and late_count > 0 and rate > 0:
        late_ded = _round2(rate * late_count)
    elif policy == "per_minute" and late_count > 0 and rate > 0:
        # rate here is treated as cost-per-minute × late occurrences
        # (actual late minutes would need a separate field; using count as proxy)
        late_ded = _round2(rate * late_count)
    # policy == "none" → late_ded = 0

    if late_ded > 0:
        details.append({"component_id": None, "component_name": "Late Deduction",
                         "amount": late_ded})
        total += late_ded

    # Loan deduction
    if loan_deduction > 0:
        details.append({"component_id": None, "component_name": "Loan Deduction",
                         "amount": loan_deduction})
        total += loan_deduction

    return _round2(total), details, _round2(leave_ded), _round2(late_ded)


# ─────────────────────────────────────────────── overtime calculation
def calculate_overtime_amount(
    employee_id: int,
    salary_structure: EmployeeSalaryStructure,
    attendance_summary: Dict,
) -> Decimal:
    """Calculate approved overtime pay for the period.

    Prefers approved OvertimeRecord rows over raw attendance minutes.
    Falls back to attendance summary if no OT records exist.
    """
    # Use approved OvertimeRecord rows if available
    approved_ot = OvertimeRecord.query.filter(
        OvertimeRecord.employee_id == employee_id,
        OvertimeRecord.status == "approved",
    ).all()

    if approved_ot:
        total = sum(
            float(r.calculated_amount) for r in approved_ot
        )
        return _round2(Decimal(str(total)))

    # Fallback: compute from attendance summary overtime_minutes
    ot_minutes = attendance_summary.get("overtime_minutes", 0)
    if ot_minutes <= 0:
        return Decimal("0.00")

    hourly_rate = _f(salary_structure.overtime_rate)
    if hourly_rate <= 0:
        # Derive hourly rate from basic salary (monthly / 26 / 8)
        hourly_rate = _f(salary_structure.basic_salary) / 26 / 8

    multiplier = _f(salary_structure.overtime_multiplier) or 1.0
    ot_hours = ot_minutes / 60.0
    return _round2(Decimal(str(ot_hours * hourly_rate * multiplier)))


# ─────────────────────────────────────────────── payroll period CRUD
def create_payroll_period(
    period_data: dict, performed_by: Optional[int] = None
) -> PayrollPeriod:
    """Create a new payroll period; raises ValueError on overlap."""
    start = period_data["start_date"]
    end = period_data["end_date"]

    existing = PayrollPeriod.query.filter(
        or_(
            and_(PayrollPeriod.start_date <= start, PayrollPeriod.end_date >= start),
            and_(PayrollPeriod.start_date <= end, PayrollPeriod.end_date >= end),
            and_(PayrollPeriod.start_date >= start, PayrollPeriod.end_date <= end),
        )
    ).first()
    if existing:
        raise ValueError(
            f"Payroll period overlaps with existing period '{existing.period_name}' "
            f"({existing.id})"
        )

    pp = PayrollPeriod(**period_data)
    db.session.add(pp)
    try:
        db.session.commit()
        _create_audit_log("payroll_period", pp.id, "create", performed_by, None, period_data)
        db.session.commit()
        return pp
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create payroll period: {exc}") from exc


# ─────────────────────────────────────────────── main payroll run
def process_payroll_for_period(
    payroll_period_id: int, performed_by: Optional[int] = None
) -> List[PayrollRecord]:
    """Process payroll for every active employee in a period.

    ALL records are created inside a single transaction.  If any employee
    calculation fails, the ENTIRE transaction is rolled back so we never
    produce partial / inconsistent payroll.
    """
    pp = PayrollPeriod.query.get(payroll_period_id)
    if not pp:
        raise ValueError(f"Payroll period {payroll_period_id} not found")
    if pp.status != "draft":
        raise ValueError(
            f"Payroll period {payroll_period_id} is not in 'draft' status "
            f"(current: {pp.status})"
        )

    old_status = pp.status
    pp.status = "processing"
    pp.processed_by = performed_by
    pp.processed_at = _utcnow()
    db.session.commit()

    records: List[PayrollRecord] = []

    try:
        employees = Employee.query.filter_by(is_active=True).all()

        for emp in employees:
            ss = get_employee_salary_structure(emp.id, pp.start_date)
            if not ss:
                # Skip employees without an active salary structure
                continue

            # ── attendance ──
            att = get_attendance_summary_for_employee(emp.id, pp.start_date, pp.end_date)

            # ── overtime ──
            ot_amount = calculate_overtime_amount(emp.id, ss, att)

            # ── bonus ──
            bonus_amount = Decimal(str(
                db.session.query(func.sum(EmployeeBonus.amount)).filter(
                    EmployeeBonus.employee_id == emp.id,
                    EmployeeBonus.payroll_period_id == payroll_period_id,
                    EmployeeBonus.status == "active",
                ).scalar() or 0
            ))

            # ── reimbursements ──
            reimb_amount = Decimal(str(
                db.session.query(func.sum(Reimbursement.approved_amount)).filter(
                    Reimbursement.employee_id == emp.id,
                    Reimbursement.status == "approved",
                    Reimbursement.paid_in_payroll == False,
                ).scalar() or 0
            ))

            # ── loan deduction ──
            active_loans = EmployeeLoan.query.filter(
                EmployeeLoan.employee_id == emp.id,
                EmployeeLoan.status == "active",
            ).all()

            loan_ded = Decimal("0.00")
            loan_repayment_rows: List[dict] = []
            for loan in active_loans:
                installment = Decimal(str(loan.installment_amount))
                remaining = Decimal(str(loan.remaining_amount))
                # Never deduct more than remaining balance
                deduct = min(installment, remaining)
                if deduct <= 0:
                    continue
                loan_ded += deduct
                loan_repayment_rows.append({
                    "loan": loan,
                    "amount": deduct,
                    "new_remaining": remaining - deduct,
                })

            # ── earnings ──
            gross_earnings, earning_details = calculate_earnings(
                ss, pp, att, ot_amount, bonus_amount, reimb_amount
            )

            # ── deductions ──
            total_deductions, deduction_details, leave_ded, late_ded = calculate_deductions(
                ss, pp, att, gross_earnings, loan_ded
            )

            # ── net salary ──
            total_earnings = gross_earnings
            net_salary = _round2(total_earnings - total_deductions)
            if net_salary < 0:
                net_salary = Decimal("0.00")

            # ── create PayrollRecord ──
            pr = PayrollRecord(
                payroll_period_id=pp.id,
                employee_id=emp.id,
                basic_salary=_round2(ss.basic_salary),
                gross_earnings=gross_earnings,
                total_earnings=total_earnings,
                total_deductions=total_deductions,
                overtime_amount=ot_amount,
                bonus_amount=bonus_amount,
                reimbursement_amount=reimb_amount,
                leave_deduction=leave_ded,
                late_deduction=late_ded,
                loan_deduction=loan_ded,
                other_deductions=Decimal("0.00"),
                net_salary=net_salary,
                payable_days=_round2(Decimal(str(att["payable_days"]))),
                status="processed",
            )
            db.session.add(pr)
            db.session.flush()  # get pr.id

            # ── attendance snapshot ──
            snapshot = PayrollAttendanceSummary(
                payroll_record_id=pr.id,
                employee_id=emp.id,
                total_calendar_days=att["total_calendar_days"],
                working_days=att["working_days"],
                present_days=att["present_days"],
                absent_days=att["absent_days"],
                half_days=att["half_days"],
                paid_leave_days=_round2(Decimal(str(att["paid_leave_days"]))),
                unpaid_leave_days=_round2(Decimal(str(att["unpaid_leave_days"]))),
                holidays=att["holidays"],
                weekly_offs=att["weekly_offs"],
                late_count=att["late_count"],
                early_exit_count=att["early_exit_count"],
                overtime_minutes=att["overtime_minutes"],
                total_working_minutes=att["total_working_minutes"],
                payable_days=_round2(Decimal(str(att["payable_days"]))),
            )
            db.session.add(snapshot)

            # ── earning rows ──
            for ed in earning_details:
                db.session.add(PayrollEarning(
                    payroll_record_id=pr.id,
                    component_id=ed.get("component_id"),
                    component_name_snapshot=ed["component_name"],
                    amount=_round2(ed["amount"]),
                ))

            # ── deduction rows ──
            for dd in deduction_details:
                db.session.add(PayrollDeduction(
                    payroll_record_id=pr.id,
                    component_id=dd.get("component_id"),
                    deduction_name_snapshot=dd["component_name"],
                    amount=_round2(dd["amount"]),
                ))

            # ── loan repayment rows ──
            for rep in loan_repayment_rows:
                loan = rep["loan"]
                db.session.add(LoanRepayment(
                    loan_id=loan.id,
                    payroll_record_id=pr.id,
                    amount=rep["amount"],
                    payment_date=pp.payment_date,
                    remaining_amount=rep["new_remaining"],
                ))
                loan.remaining_amount = rep["new_remaining"]
                if loan.remaining_amount <= 0:
                    loan.status = "completed"

            # ── mark reimbursements as paid ──
            Reimbursement.query.filter(
                Reimbursement.employee_id == emp.id,
                Reimbursement.status == "approved",
                Reimbursement.paid_in_payroll == False,
            ).update({"paid_in_payroll": True}, synchronize_session=False)

            records.append(pr)

        # ── update period status ──
        pp.status = "processed"
        _create_audit_log(
            "payroll_period", pp.id, "process", performed_by,
            {"status": old_status},
            {"status": "processed", "records_count": len(records)},
        )
        db.session.commit()
        return records

    except Exception as exc:
        db.session.rollback()
        # Revert period status to draft
        try:
            pp.status = old_status
            db.session.commit()
        except Exception:
            db.session.rollback()
        raise ValueError(f"Payroll processing failed — rolled back: {exc}") from exc


# ─────────────────────────────────────────────── finalization
def finalize_payroll_period(
    payroll_period_id: int, performed_by: Optional[int] = None
) -> PayrollPeriod:
    """Finalize a processed payroll period, making all records immutable."""
    pp = PayrollPeriod.query.get(payroll_period_id)
    if not pp:
        raise ValueError(f"Payroll period {payroll_period_id} not found")
    if pp.status != "processed":
        raise ValueError(
            f"Payroll period {payroll_period_id} must be 'processed' before finalizing "
            f"(current: {pp.status})"
        )

    old_status = pp.status
    pp.status = "finalized"
    pp.finalized_by = performed_by
    pp.finalized_at = _utcnow()

    # Mark all payroll records as finalized
    PayrollRecord.query.filter_by(payroll_period_id=pp.id).update(
        {"status": "finalized"}, synchronize_session=False
    )

    try:
        db.session.commit()
        _create_audit_log(
            "payroll_period", pp.id, "finalize", performed_by,
            {"status": old_status},
            {"status": "finalized", "finalized_at": pp.finalized_at.isoformat()},
        )
        db.session.commit()
        return pp
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to finalize payroll period: {exc}") from exc


# ─────────────────────────────────────────────── read helpers
def get_payroll_record(period_id: int, employee_id: int) -> Optional[PayrollRecord]:
    return PayrollRecord.query.filter_by(
        payroll_period_id=period_id, employee_id=employee_id
    ).first()


def get_payroll_records_for_period(period_id: int) -> List[PayrollRecord]:
    return PayrollRecord.query.filter_by(payroll_period_id=period_id).all()


# ─────────────────────────────────────────────── payslip
def create_payslip(
    payroll_record_id: int, performed_by: Optional[int] = None
) -> Payslip:
    pr = PayrollRecord.query.get(payroll_record_id)
    if not pr:
        raise ValueError(f"Payroll record {payroll_record_id} not found")

    existing = Payslip.query.filter_by(payroll_record_id=payroll_record_id).first()
    if existing:
        raise ValueError(f"Payslip already exists for payroll record {payroll_record_id}")

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    payslip_number = f"PS-{pr.id:06d}-{ts}"

    slip = Payslip(
        payroll_record_id=payroll_record_id,
        payslip_number=payslip_number,
        status="generated",
    )
    db.session.add(slip)
    try:
        db.session.commit()
        _create_audit_log(
            "payslip", slip.id, "create", performed_by, None,
            {"payroll_record_id": payroll_record_id, "payslip_number": payslip_number},
        )
        db.session.commit()
        return slip
    except Exception as exc:
        db.session.rollback()
        raise ValueError(f"Failed to create payslip: {exc}") from exc


# ─────────────────────────────────────────────── dashboard
def get_hr_dashboard_summary() -> Dict:
    """Return HR dashboard statistics derived from live data."""
    from datetime import date as _date
    today = _date.today()

    total_employees = Employee.query.count()
    active_employees = Employee.query.filter_by(is_active=True).count()

    present_today = TeacherAttendance.query.filter(
        TeacherAttendance.attendance_date == today,
        TeacherAttendance.status.in_(["present", "late", "half_day", "overtime", "early_exit"]),
    ).count()

    absent_today = TeacherAttendance.query.filter(
        TeacherAttendance.attendance_date == today,
        TeacherAttendance.status == "absent",
    ).count()

    late_today = TeacherAttendance.query.filter(
        TeacherAttendance.attendance_date == today,
        TeacherAttendance.is_late == True,
    ).count()

    on_leave_today = LeaveRequest.query.filter(
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today,
    ).count()

    half_day_today = TeacherAttendance.query.filter(
        TeacherAttendance.attendance_date == today,
        TeacherAttendance.status == "half_day",
    ).count()

    overtime_today = TeacherAttendance.query.filter(
        TeacherAttendance.attendance_date == today,
        TeacherAttendance.overtime_minutes > 0,
    ).count()

    current_period = (
        PayrollPeriod.query.filter(
            PayrollPeriod.start_date <= today,
            PayrollPeriod.end_date >= today,
            PayrollPeriod.status.in_(["draft", "processing", "processed"]),
        )
        .order_by(desc(PayrollPeriod.start_date))
        .first()
    )

    last_period = (
        PayrollPeriod.query.filter_by(status="finalized")
        .order_by(desc(PayrollPeriod.end_date))
        .first()
    )
    last_payroll_amount = 0.0
    if last_period:
        last_payroll_amount = float(
            db.session.query(func.sum(PayrollRecord.net_salary))
            .filter(PayrollRecord.payroll_period_id == last_period.id)
            .scalar()
            or 0
        )

    pending_leaves = LeaveRequest.query.filter_by(status="pending").count()
    pending_overtime = OvertimeRecord.query.filter_by(status="pending").count()
    pending_reimb = Reimbursement.query.filter_by(status="pending").count()

    outstanding_loans = float(
        db.session.query(func.sum(EmployeeLoan.remaining_amount))
        .filter(EmployeeLoan.status == "active")
        .scalar()
        or 0
    )

    return {
        "total_employees": total_employees,
        "active_employees": active_employees,
        "present_today": present_today,
        "absent_today": absent_today,
        "late_today": late_today,
        "on_leave_today": on_leave_today,
        "half_day_today": half_day_today,
        "overtime_today": overtime_today,
        "current_payroll_period": current_period.period_name if current_period else None,
        "last_payroll_amount": round(last_payroll_amount, 2),
        "pending_leave_requests": pending_leaves,
        "pending_overtime": pending_overtime,
        "pending_reimbursements": pending_reimb,
        "outstanding_employee_loans": round(outstanding_loans, 2),
    }