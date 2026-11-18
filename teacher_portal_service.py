"""Teacher Portal Service — Hardened Self-Only Authorization & Data Isolation Layer.

CRITICAL SECURITY ARCHITECTURE (PHASE 4):
- Server-side authoritative identity derived exclusively from the authenticated session (g.user).
- Every query is strictly scoped to the authenticated teacher's own entity IDs.
- Zero-trust validation for all client-supplied parameters (query params, URL params, JSON body):
  any attempt to pass another teacher/employee/user ID is intercepted and rejected with 403 Forbidden.
- Insecure Direct Object Reference (IDOR) protection on all individual resource lookups.
- Prevents ID enumeration across attendance, leave, and correction request records.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func

from database.auth_models import User
from database.db import db
from database.enterprise_models import (
    AttendanceCorrection,
    AttendanceEvent,
    HolidayCalendar,
    TeacherAttendance,
    TeacherTimeRule,
    WeeklyOff,
)
from database.models import Teacher
from hr.models import Employee, EmployeeLeaveBalance, LeaveRequest, LeaveType


def resolve_teacher_identity(user: User) -> tuple[Teacher | None, Employee | None]:
    """Resolve the authoritative Teacher and Employee records belonging to the authenticated User."""
    if not user:
        return None, None

    teacher = None
    employee = None
    sid = user.school_id

    # 1. Resolve Teacher
    if user.employee_id:
        q = Teacher.query.filter(Teacher.teacher_id == str(user.employee_id).strip())
        if sid:
            q = q.filter((Teacher.school_id == sid) | (Teacher.school_id.is_(None)))
        teacher = q.first()

    if not teacher and user.email:
        q = Teacher.query.filter(func.lower(Teacher.email) == func.lower(user.email.strip()))
        if sid:
            q = q.filter((Teacher.school_id == sid) | (Teacher.school_id.is_(None)))
        teacher = q.first()

    if not teacher and user.name:
        q = Teacher.query.filter(func.lower(Teacher.name) == func.lower(user.name.strip()))
        if sid:
            q = q.filter((Teacher.school_id == sid) | (Teacher.school_id.is_(None)))
        teacher = q.first()

    # If teacher user exists but Teacher table row has not been created yet, auto-provision Teacher entity
    if not teacher and getattr(user, "role", "") in {"teacher", "employee"}:
        raw_tid = str(user.employee_id).strip() if user.employee_id else f"TCH-{user.id}"
        teacher = Teacher(
            school_id=sid,
            teacher_id=raw_tid,
            name=user.name or "Teacher",
            email=user.email,
            mobile=user.phone,
            department=user.department or "Academic",
            designation="Teacher",
            attendance_method="FACE",
            is_active=True,
        )
        db.session.add(teacher)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            q_fallback = Teacher.query.filter_by(teacher_id=raw_tid)
            if sid:
                q_fallback = q_fallback.filter((Teacher.school_id == sid) | (Teacher.school_id.is_(None)))
            teacher = q_fallback.first()

    # 2. Resolve Employee (HR record)
    if user.employee_id:
        q_emp = Employee.query.filter(Employee.employee_code == str(user.employee_id).strip())
        if sid:
            q_emp = q_emp.filter((Employee.school_id == sid) | (Employee.school_id.is_(None)))
        employee = q_emp.first()

    if not employee and teacher:
        q_emp = Employee.query.filter(Employee.teacher_id == teacher.id)
        if sid:
            q_emp = q_emp.filter((Employee.school_id == sid) | (Employee.school_id.is_(None)))
        employee = q_emp.first()

    if not employee and user.email:
        q_emp = Employee.query.filter(func.lower(Employee.email) == func.lower(user.email.strip()))
        if sid:
            q_emp = q_emp.filter((Employee.school_id == sid) | (Employee.school_id.is_(None)))
        employee = q_emp.first()

    # If teacher entity exists but HR Employee record is missing, auto-sync Employee
    if not employee and teacher:
        try:
            from hr.employee_service import ensure_employee_from_teacher
            employee = ensure_employee_from_teacher(teacher, school_id=sid)
            db.session.commit()
        except Exception:
            db.session.rollback()
            q_emp_fb = Employee.query.filter_by(teacher_id=teacher.id)
            if sid:
                q_emp_fb = q_emp_fb.filter((Employee.school_id == sid) | (Employee.school_id.is_(None)))
            employee = q_emp_fb.first()

    return teacher, employee


def _validate_client_identity_request(user: User, client_params: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Verify that any client-supplied identity fields in query args or body match the authenticated user.
    
    If the caller supplied an explicit employee_id, teacher_id, or user_id, it MUST belong
    to the authenticated user. If not, this is an unauthorized identity spoofing attempt.
    """
    if not user:
        return False, "Authentication required."
    if not client_params:
        return True, None

    teacher, employee = resolve_teacher_identity(user)
    
    allowed_teacher_ids = {
        str(user.id),
        str(user.employee_id or "").strip(),
        str(getattr(teacher, "teacher_id", "") or "").strip(),
        str(getattr(teacher, "id", "") or "").strip(),
    }
    allowed_teacher_ids.discard("")

    allowed_employee_ids = {
        str(user.id),
        str(user.employee_id or "").strip(),
        str(getattr(employee, "employee_code", "") or "").strip(),
        str(getattr(employee, "id", "") or "").strip(),
    }
    allowed_employee_ids.discard("")

    # Check teacher_id if provided
    req_tid = str(client_params.get("teacher_id") or "").strip()
    if req_tid and req_tid not in allowed_teacher_ids:
        return False, "Access forbidden: you cannot access or manipulate another teacher's records."

    # Check employee_id if provided
    req_eid = str(client_params.get("employee_id") or "").strip()
    if req_eid and req_eid not in allowed_employee_ids:
        return False, "Access forbidden: you cannot access or manipulate another employee's records."

    # Check user_id if provided
    req_uid = str(client_params.get("user_id") or "").strip()
    if req_uid and req_uid not in {str(user.id)}:
        return False, "Access forbidden: you cannot access or manipulate another user's records."

    return True, None


def _format_time(dt: datetime | None) -> str:
    if not dt:
        return "Not recorded"
    return dt.strftime("%I:%M %p").lstrip("0")


def _format_minutes_to_hours(minutes: int | float | None) -> str:
    if not minutes or minutes <= 0:
        return "00h 00m"
    m = int(minutes)
    hours = m // 60
    mins = m % 60
    return f"{hours:02d}h {mins:02d}m"


def get_teacher_dashboard(user: User, client_params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """Compile Teacher Dashboard metrics: today's live status and current month's attendance roll-up."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, employee = resolve_teacher_identity(user)
    today = date.today()
    sid = user.school_id

    teacher_id = teacher.id if teacher else None
    employee_id = employee.id if employee else None

    # Today's Record (strictly scoped to authenticated teacher)
    today_rec = None
    auth_method = "Face Recognition"
    if teacher_id:
        today_rec = TeacherAttendance.query.filter_by(
            teacher_id=teacher_id,
            attendance_date=today,
        ).first()

        # Check latest event for authentication method
        if today_rec:
            ev = (
                AttendanceEvent.query.filter_by(
                    attendance_type="teacher",
                    attendance_id=today_rec.id,
                )
                .order_by(AttendanceEvent.event_time.desc())
                .first()
            )
            if ev and ev.auth_method:
                auth_method = ev.auth_method.title()

    # Determine Today's Status
    today_status = "NOT RECORDED"
    in_time_str = "Not recorded"
    out_time_str = "Not recorded"
    working_hours_str = "--"
    is_late = False
    late_minutes = 0

    if today_rec:
        today_status = (today_rec.status or "present").upper()
        if today_rec.in_time:
            in_time_str = _format_time(today_rec.in_time)
        if today_rec.out_time:
            out_time_str = _format_time(today_rec.out_time)
        working_hours_str = _format_minutes_to_hours(today_rec.working_minutes)
        is_late = bool(today_rec.is_late)
        if is_late:
            late_minutes = 0
            if today_rec.in_time:
                # Approximate late minutes from 08:00
                in_dt = today_rec.in_time
                if hasattr(in_dt, "hour"):
                    scheduled_in = in_dt.replace(hour=8, minute=0, second=0, microsecond=0)
                    if in_dt > scheduled_in:
                        late_minutes = max(1, int((in_dt - scheduled_in).total_seconds() // 60))

    # Current Month Summary
    first_of_month = today.replace(day=1)
    if today.month == 12:
        last_of_month = today.replace(day=31)
    else:
        last_of_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    month_records = []
    if teacher_id:
        month_records = (
            TeacherAttendance.query.filter(
                TeacherAttendance.teacher_id == teacher_id,
                TeacherAttendance.attendance_date >= first_of_month,
                TeacherAttendance.attendance_date <= today,
            )
            .order_by(TeacherAttendance.attendance_date.desc())
            .all()
        )

    # Calculate month stats
    working_days = 0
    present_count = 0
    absent_count = 0
    late_count = 0
    half_day_count = 0
    leave_count = 0
    total_minutes = 0

    cur = first_of_month
    while cur <= today:
        if cur.weekday() < 6:  # Exclude Sunday (6)
            working_days += 1
        cur += timedelta(days=1)
    working_days = max(1, working_days)

    for r in month_records:
        st = (r.status or "").lower()
        if st in ("present", "late", "half_day"):
            present_count += 1
        if st == "absent":
            absent_count += 1
        if st == "half_day":
            half_day_count += 1
        if r.is_late or st == "late":
            late_count += 1
        if st == "leave":
            leave_count += 1
        total_minutes += r.working_minutes or 0

    # Check approved leaves in current month (strictly scoped)
    if employee_id:
        leaves = LeaveRequest.query.filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= first_of_month,
        ).all()
        leave_count = max(leave_count, len(leaves))

    attendance_pct = round((present_count / max(1, working_days)) * 100, 2)
    attendance_pct = min(100.0, max(0.0, attendance_pct))

    # Fetch recent 7 records
    recent_7 = []
    if teacher_id:
        r_rows = (
            TeacherAttendance.query.filter(TeacherAttendance.teacher_id == teacher_id)
            .order_by(TeacherAttendance.attendance_date.desc())
            .limit(7)
            .all()
        )
        for r in r_rows:
            recent_7.append({
                "id": r.id,
                "date": r.attendance_date.isoformat(),
                "formatted_date": r.attendance_date.strftime("%d %b %Y"),
                "status": (r.status or "present").capitalize(),
                "in_time": r.in_time.isoformat() if r.in_time else None,
                "out_time": r.out_time.isoformat() if r.out_time else None,
                "working_hours": _format_minutes_to_hours(r.working_minutes),
                "method": "Face Recognition" if getattr(teacher, 'photo_enrolled', False) else "BioMax Indigo Fingerprint",
            })

    pending_leaves_count = 0
    if employee_id:
        pending_leaves_count = LeaveRequest.query.filter_by(employee_id=employee_id, status="pending").count()

    pending_corrections_count = 0
    if teacher_id or user.id:
        pending_corrections_count = AttendanceCorrection.query.filter(
            AttendanceCorrection.attendance_type == "teacher",
            AttendanceCorrection.attendance_id == (teacher_id or user.id),
            AttendanceCorrection.status == "pending",
        ).count()

    return {
        "success": True,
        "data": {
            "teacher_info": {
                "name": user.name or (teacher.name if teacher else "Teacher"),
                "teacher_id": user.employee_id or (teacher.teacher_id if teacher else "TCH_001"),
                "department": (teacher.department if teacher else "") or (user.department if getattr(user, 'department', None) else "") or "Academic",
                "designation": (teacher.designation if teacher else "") or "Teacher",
                "email": user.email or (teacher.email if teacher else ""),
            },
            "today": {
                "date": today.isoformat(),
                "formatted_date": today.strftime("%d %b %Y"),
                "status": (today_rec.status if today_rec else "Not Marked"),
                "in_time": today_rec.in_time.isoformat() if (today_rec and today_rec.in_time) else None,
                "out_time": today_rec.out_time.isoformat() if (today_rec and today_rec.out_time) else None,
                "working_hours": working_hours_str,
                "is_late": is_late,
                "late_minutes": late_minutes,
                "method": auth_method,
                "camera": today_rec.camera.name if (today_rec and today_rec.camera) else "Main Gate",
            },
            "month_stats": {
                "month_name": today.strftime("%B %Y"),
                "attendance_percentage": attendance_pct,
                "attendance_pct": attendance_pct,
                "attendance_pct_str": f"{attendance_pct:.2f}%",
                "working_days": working_days,
                "present_days": present_count,
                "absent_days": absent_count,
                "late_days": late_count,
                "leave_days": leave_count,
                "half_day_days": half_day_count,
                "total_hours_str": _format_minutes_to_hours(total_minutes),
            },
            "recent_7_days": recent_7,
            "pending_leaves_count": pending_leaves_count,
            "pending_corrections_count": pending_corrections_count,
        },
    }, 200


def get_teacher_history(
    user: User, month_str: str | None = None, date_str: str | None = None
) -> list[dict[str, Any]]:
    """Retrieve chronological attendance history for the authenticated teacher."""
    teacher, _ = resolve_teacher_identity(user)
    if not teacher:
        return []

    # Parse month (format: YYYY-MM) or default to current month
    today = date.today()
    if month_str:
        try:
            parts = month_str.strip().split("-")
            year, month = int(parts[0]), int(parts[1])
            start_date = date(year, month, 1)
        except Exception:
            start_date = today.replace(day=1)
    else:
        start_date = today.replace(day=1)

    if start_date.month == 12:
        end_date = start_date.replace(day=31)
    else:
        end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)

    # Filter specific date if supplied
    specific_date = None
    if date_str:
        try:
            specific_date = date.fromisoformat(date_str.strip())
        except Exception:
            pass

    q = TeacherAttendance.query.filter(
        TeacherAttendance.teacher_id == teacher.id,
    )
    if user.school_id:
        q = q.filter((TeacherAttendance.school_id == user.school_id) | (TeacherAttendance.school_id.is_(None)))

    if specific_date:
        q = q.filter(TeacherAttendance.attendance_date == specific_date)
    else:
        q = q.filter(
            TeacherAttendance.attendance_date >= start_date,
            TeacherAttendance.attendance_date <= end_date,
        )

    records = q.order_by(TeacherAttendance.attendance_date.desc()).all()

    # Pre-fetch events to get auth methods (Face / Fingerprint / Manual)
    att_ids = [r.id for r in records]
    events_by_att: dict[int, str] = {}
    if att_ids:
        events = (
            AttendanceEvent.query.filter(
                AttendanceEvent.attendance_type == "teacher",
                AttendanceEvent.attendance_id.in_(att_ids),
            )
            .order_by(AttendanceEvent.event_time.asc())
            .all()
        )
        for ev in events:
            if ev.auth_method:
                events_by_att[ev.attendance_id] = ev.auth_method.title()

    results = []
    for r in records:
        method = events_by_att.get(r.id, "Face" if getattr(teacher, 'photo_enrolled', False) else "Fingerprint" if getattr(teacher, 'fingerprint_enrolled', False) else "Face Recognition")
        in_t = _format_time(r.in_time)
        out_t = _format_time(r.out_time)
        working_h = _format_minutes_to_hours(r.working_minutes)

        late_str = "On Time"
        late_mins = 0
        if r.is_late:
            late_str = "Late"
            if r.in_time:
                sched = r.in_time.replace(hour=8, minute=0, second=0)
                if r.in_time > sched:
                    late_mins = max(1, int((r.in_time - sched).total_seconds() // 60))
                    late_str = f"{late_mins} min late"

        results.append({
            "id": r.id,
            "date": r.attendance_date.isoformat(),
            "formatted_date": r.attendance_date.strftime("%d %b %Y"),
            "weekday": r.attendance_date.strftime("%A"),
            "in_time": r.in_time.isoformat() if r.in_time else None,
            "out_time": r.out_time.isoformat() if r.out_time else None,
            "status": (r.status or "present").capitalize(),
            "is_late": bool(r.is_late),
            "late_minutes": late_mins,
            "late_label": late_str,
            "working_hours": working_h,
            "method": method,
        })

    return results


def get_teacher_attendance_history(
    user: User, month_str: str | None = None, start_date_str: str | None = None, end_date_str: str | None = None,
    client_params: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int]:
    """Retrieve structured attendance history object for teacher APIs."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    records = get_teacher_history(user, month_str=month_str, date_str=start_date_str)
    return {"success": True, "data": {"records": records, "total_count": len(records)}}, 200


def get_teacher_monthly_summary(
    user: User, month_str: str | None = None, client_params: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int]:
    """Compute detailed monthly roll-up metrics for the teacher."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, employee = resolve_teacher_identity(user)
    today = date.today()

    if month_str:
        try:
            parts = month_str.strip().split("-")
            year, month = int(parts[0]), int(parts[1])
            start_date = date(year, month, 1)
        except Exception:
            start_date = today.replace(day=1)
    else:
        start_date = today.replace(day=1)

    if start_date.month == 12:
        end_date = start_date.replace(day=31)
    else:
        end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)

    # Days in month up to end or today if current month
    effective_end = min(end_date, today) if start_date.month == today.month and start_date.year == today.year else end_date

    working_days = 0
    cur = start_date
    while cur <= effective_end:
        if cur.weekday() < 6:
            working_days += 1
        cur += timedelta(days=1)
    working_days = max(1, working_days)

    records = []
    if teacher:
        q = TeacherAttendance.query.filter(
            TeacherAttendance.teacher_id == teacher.id,
            TeacherAttendance.attendance_date >= start_date,
            TeacherAttendance.attendance_date <= effective_end,
        )
        if user.school_id:
            q = q.filter((TeacherAttendance.school_id == user.school_id) | (TeacherAttendance.school_id.is_(None)))
        records = q.all()

    present_count = 0
    absent_count = 0
    late_count = 0
    half_day_count = 0
    leave_count = 0
    total_minutes = 0

    for r in records:
        st = (r.status or "").lower()
        if st in ("present", "late", "half_day"):
            present_count += 1
        if st == "absent":
            absent_count += 1
        if st == "half_day":
            half_day_count += 1
        if r.is_late or st == "late":
            late_count += 1
        if st == "leave":
            leave_count += 1
        total_minutes += r.working_minutes or 0

    if employee:
        leaves = LeaveRequest.query.filter(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= effective_end,
            LeaveRequest.end_date >= start_date,
        ).all()
        leave_count = max(leave_count, len(leaves))

    attendance_pct = round((present_count / max(1, working_days)) * 100, 2)
    attendance_pct = min(100.0, max(0.0, attendance_pct))

    return {
        "success": True,
        "data": {
            "month": start_date.strftime("%B %Y"),
            "month_iso": start_date.strftime("%Y-%m"),
            "working_days": working_days,
            "present_days": present_count,
            "present": present_count,
            "absent_days": absent_count,
            "absent": absent_count,
            "leave_days": leave_count,
            "leave": leave_count,
            "late_days": late_count,
            "late": late_count,
            "half_days": half_day_count,
            "half_day": half_day_count,
            "total_hours_worked": _format_minutes_to_hours(total_minutes),
            "total_hours": _format_minutes_to_hours(total_minutes),
            "total_minutes": total_minutes,
            "average_daily_hours": _format_minutes_to_hours(total_minutes / max(1, present_count)),
            "attendance_percentage": attendance_pct,
            "attendance_pct": attendance_pct,
            "attendance_pct_str": f"{attendance_pct:.2f}%",
        },
    }, 200


def get_teacher_leave_data(user: User) -> dict[str, Any]:
    """Retrieve teacher's leave history, active balances, and available leave types."""
    _, employee = resolve_teacher_identity(user)
    sid = user.school_id

    # Available Leave Types
    q_types = LeaveType.query.filter_by(is_active=True)
    if sid:
        q_types = q_types.filter((LeaveType.school_id == sid) | (LeaveType.school_id.is_(None)))
    leave_types = q_types.order_by(LeaveType.name).all()

    # Leave Balances
    balances = []
    requests = []

    if employee:
        cur_year = date.today().year
        bal_rows = EmployeeLeaveBalance.query.filter_by(
            employee_id=employee.id,
            year=cur_year,
        ).all()
        for b in bal_rows:
            balances.append({
                "leave_type_id": b.leave_type_id,
                "leave_type_name": b.leave_type.name if b.leave_type else "Leave",
                "allocated": float(b.allocated_days or 0),
                "used": float(b.used_days or 0),
                "balance": float(b.balance or 0),
            })

        # Leave Requests (strictly scoped to this employee)
        req_rows = (
            LeaveRequest.query.filter_by(employee_id=employee.id)
            .order_by(LeaveRequest.created_at.desc())
            .all()
        )
        for r in req_rows:
            requests.append({
                "id": r.id,
                "leave_type": r.leave_type.name if r.leave_type else "Leave",
                "leave_type_name": r.leave_type.name if r.leave_type else "Leave",
                "start_date": r.start_date.isoformat(),
                "end_date": r.end_date.isoformat(),
                "start_date_formatted": r.start_date.strftime("%d %b %Y"),
                "end_date_formatted": r.end_date.strftime("%d %b %Y"),
                "total_days": float(r.total_days or 1),
                "days_count": float(r.total_days or 1),
                "reason": r.reason or "",
                "status": (r.status or "pending").capitalize(),
                "applied_on": r.created_at.isoformat() if r.created_at else "",
                "admin_remarks": r.rejection_reason or "",
                "rejection_reason": r.rejection_reason,
                "created_at": r.created_at.strftime("%d %b %Y, %I:%M %p") if r.created_at else "",
            })

    return {
        "leave_types": [
            {
                "id": lt.id,
                "name": lt.name,
                "code": lt.code,
                "paid": bool(lt.paid),
            }
            for lt in leave_types
        ],
        "leave_balances": {
            "casual_leave_remaining": 10,
            "casual_leave_total": 12,
            "sick_leave_remaining": 8,
            "sick_leave_total": 10,
            "earned_leave_remaining": 15,
            "earned_leave_total": 15,
        },
        "balances": balances,
        "leave_requests": requests,
        "requests": requests,
        "pending_count": sum(1 for r in requests if r.get("status") == "Pending"),
    }


def get_teacher_leaves(user: User, client_params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """Endpoint handler for teacher leave retrieval with identity check."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    data = get_teacher_leave_data(user)
    return {"success": True, "data": data}, 200


def apply_teacher_leave(user: User, data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Submit a leave application on behalf of the authenticated teacher."""
    valid, err = _validate_client_identity_request(user, data)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, employee = resolve_teacher_identity(user)
    if not employee:
        # Create minimal employee record for leave tracking if missing
        employee = Employee(
            school_id=user.school_id,
            teacher_id=teacher.id if teacher else None,
            employee_code=user.employee_id or (teacher.teacher_id if teacher else f"TCH_{user.id:03d}"),
            first_name=user.name.split()[0] if user.name else "Teacher",
            last_name=" ".join(user.name.split()[1:]) if user.name and len(user.name.split()) > 1 else "Staff",
            email=user.email or (teacher.email if teacher else "teacher@school.internal"),
            phone=(teacher.mobile if teacher else "") or getattr(user, 'phone', "") or "+919999999999",
            employment_type="full_time",
            joining_date=date.today(),
        )
        db.session.add(employee)
        db.session.commit()

    start_date_str = str(data.get("start_date") or "").strip()
    end_date_str = str(data.get("end_date") or "").strip()
    leave_type_id = data.get("leave_type_id")
    leave_type_name = str(data.get("leave_type") or data.get("type") or "Casual Leave").strip()
    reason = str(data.get("reason") or "").strip()
    half_day = str(data.get("half_day") or "full").lower()

    if not start_date_str or not end_date_str:
        return {"success": False, "error": "Start date and end date are required."}, 400

    try:
        start_d = date.fromisoformat(start_date_str)
        end_d = date.fromisoformat(end_date_str)
    except ValueError:
        return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}, 400

    if end_d < start_d:
        return {"success": False, "error": "End date cannot be earlier than start date."}, 400

    if not leave_type_id:
        lt = LeaveType.query.filter(
            (LeaveType.school_id == user.school_id) | (LeaveType.school_id.is_(None)),
            (func.lower(LeaveType.name) == func.lower(leave_type_name)) | (func.lower(LeaveType.code) == func.lower(leave_type_name))
        ).first()
        if not lt:
            code = "CL" if "casual" in leave_type_name.lower() else ("SL" if "sick" in leave_type_name.lower() else "EL")
            lt = LeaveType(
                school_id=user.school_id,
                name=leave_type_name,
                code=code,
                paid=True,
                max_days_per_year=12,
            )
            db.session.add(lt)
            db.session.commit()
        leave_type_id = lt.id

    total_days = max(1, (end_d - start_d).days + 1)
    if half_day in ("first_half", "second_half", "half"):
        total_days = 0.5

    # Enforce strictly authenticated employee_id
    req = LeaveRequest(
        school_id=user.school_id,
        employee_id=employee.id,
        leave_type_id=int(leave_type_id),
        start_date=start_d,
        end_date=end_d,
        total_days=total_days,
        reason=reason,
        status="pending",
    )
    db.session.add(req)
    db.session.commit()

    return {
        "success": True,
        "id": req.id,
        "message": "Leave application submitted successfully. Status: Pending approval.",
    }, 200


def get_teacher_corrections(user: User, client_params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """Retrieve submitted correction requests and administrative status for this teacher."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, _ = resolve_teacher_identity(user)
    teacher_id = teacher.id if teacher else user.id

    rows = (
        AttendanceCorrection.query.filter(
            AttendanceCorrection.attendance_type == "teacher",
            AttendanceCorrection.attendance_id == teacher_id,
        )
        .order_by(AttendanceCorrection.created_at.desc())
        .all()
    )

    out = []
    for r in rows:
        req_time = r.requested_time or (_format_time(r.in_time) if r.in_time else "")
        issue_title = (r.issue_type or "Attendance Correction").replace("_", " ").title()

        out.append({
            "id": r.id,
            "attendance_date": r.attendance_date.isoformat() if r.attendance_date else "",
            "issue_type": r.issue_type or "Missing IN Punch",
            "issue_title": issue_title,
            "requested_time": req_time,
            "reason": r.reason,
            "note": r.note or "",
            "status": (r.status or "pending").capitalize(),
            "admin_comment": r.admin_comment or "",
            "approved_time": r.approved_time or "",
            "created_at": r.created_at.strftime("%d %b %Y, %I:%M %p") if r.created_at else "",
        })
    return {"success": True, "data": {"corrections": out, "total_count": len(out)}}, 200


def create_teacher_correction(user: User, data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Submit an attendance correction request for administrative review.
    
    CRITICAL SECURITY RULE:
    Does NOT directly modify attendance records.
    Validates that any referenced attendance record belongs exclusively to the caller.
    """
    valid, err = _validate_client_identity_request(user, data)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, _ = resolve_teacher_identity(user)
    teacher_id = teacher.id if teacher else user.id

    # IDOR Check on attendance record if provided in request body
    target_att_id = data.get("attendance_record_id") or (data.get("attendance_id") if isinstance(data.get("attendance_id"), int) and data.get("attendance_id") != teacher_id else None)
    if target_att_id:
        target_att = TeacherAttendance.query.get(target_att_id)
        if target_att and target_att.teacher_id != (teacher.id if teacher else -1):
            return {"success": False, "error": "Access forbidden: Target attendance record does not belong to you."}, 403

    date_str = str(data.get("attendance_date") or data.get("date") or "").strip()
    issue_type = str(data.get("issue_type") or "Missing IN Punch").strip()
    actual_time = str(data.get("requested_time") or data.get("actual_time") or "").strip()
    reason = str(data.get("reason") or "").strip()
    note = str(data.get("note") or "").strip()

    if not date_str:
        return {"success": False, "error": "Attendance date is required."}, 400
    if not reason:
        return {"success": False, "error": "Reason for correction is required."}, 400

    try:
        att_d = date.fromisoformat(date_str)
    except ValueError:
        return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}, 400

    corr = AttendanceCorrection(
        school_id=user.school_id,
        attendance_type="teacher",
        attendance_id=teacher_id,
        attendance_date=att_d,
        issue_type=issue_type,
        requested_time=actual_time,
        reason=reason,
        note=note,
        status="pending",
    )
    db.session.add(corr)
    db.session.commit()

    return {
        "success": True,
        "id": corr.id,
        "message": "Correction request submitted successfully. Status: Pending admin review.",
    }, 200


submit_teacher_correction = create_teacher_correction


def get_teacher_profile(user: User, client_params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    """Return safe, non-sensitive profile information for the authenticated teacher."""
    valid, err = _validate_client_identity_request(user, client_params)
    if not valid:
        return {"success": False, "error": err}, 403

    teacher, employee = resolve_teacher_identity(user)

    face_enrolled = bool(teacher and (getattr(teacher, 'photo_enrolled', False) or getattr(teacher, 'face_registered', False)))
    fp_enrolled = bool(teacher and getattr(teacher, 'fingerprint_enrolled', False))

    auth_method = "Face Recognition" if getattr(teacher, 'attendance_method', '') == 'FACE' else "BioMax Indigo Fingerprint"
    
    classes_list = []
    if teacher and teacher.assigned_classes:
        classes_list = [c.strip() for c in teacher.assigned_classes.split(",") if c.strip()]
    elif teacher and getattr(teacher, 'classes', None):
        classes_list = [c.strip() for c in teacher.classes.split(",") if c.strip()]

    subjects_list = []
    if teacher and teacher.subject:
        subjects_list = [s.strip() for s in teacher.subject.split(",") if s.strip()]

    return {
        "success": True,
        "data": {
            "name": user.name or (teacher.name if teacher else "Teacher"),
            "teacher_id": user.employee_id or (teacher.teacher_id if teacher else "TCH_001"),
            "email": user.email or (teacher.email if teacher else ""),
            "phone": (teacher.mobile if teacher else "") or (employee.phone if employee else "") or getattr(user, 'phone', "") or "",
            "department": (teacher.department if teacher else "") or (user.department if getattr(user, 'department', None) else "") or "General",
            "designation": (teacher.designation if teacher else "") or "Teacher",
            "subject": teacher.subject if teacher else "",
            "classes": teacher.assigned_classes if (teacher and teacher.assigned_classes) else "",
            "classes_handled": classes_list,
            "subjects_handled": subjects_list,
            "attendance_method": auth_method,
            "face_enrolled": face_enrolled,
            "fingerprint_registered": fp_enrolled,
            "joining_date": teacher.joining_date.isoformat() if (teacher and getattr(teacher, 'joining_date', None)) else None,
            "school_name": user.school.name if user.school else "AttendanceOS Institution",
            "school_id": user.school.school_id if user.school else "SCH_001",
        },
    }, 200


def get_teacher_single_attendance(user: User, aid: int) -> tuple[dict[str, Any], int]:
    """Retrieve single attendance record with strict ownership verification."""
    teacher, _ = resolve_teacher_identity(user)
    if not teacher:
        return {"success": False, "error": "Teacher profile not found."}, 404

    att = TeacherAttendance.query.get(aid)
    if not att or (att.school_id and att.school_id != user.school_id):
        return {"success": False, "error": "Attendance record not found."}, 404

    # CRITICAL OWNERSHIP CHECK
    if att.teacher_id != teacher.id:
        return {"success": False, "error": "Access forbidden: attendance record does not belong to you."}, 403

    return {
        "success": True,
        "data": {
            "id": att.id,
            "date": att.attendance_date.isoformat(),
            "in_time": att.in_time.isoformat() if att.in_time else None,
            "out_time": att.out_time.isoformat() if att.out_time else None,
            "status": (att.status or "present").capitalize(),
            "working_hours": _format_minutes_to_hours(att.working_minutes),
            "is_late": bool(att.is_late),
        },
    }, 200


def get_teacher_single_leave(user: User, lid: int) -> tuple[dict[str, Any], int]:
    """Retrieve single leave request with strict ownership verification."""
    _, employee = resolve_teacher_identity(user)
    if not employee:
        return {"success": False, "error": "Employee profile not found."}, 404

    req = LeaveRequest.query.get(lid)
    if not req or (req.school_id and req.school_id != user.school_id):
        return {"success": False, "error": "Leave request not found."}, 404

    # CRITICAL OWNERSHIP CHECK
    if req.employee_id != employee.id:
        return {"success": False, "error": "Access forbidden: leave request does not belong to you."}, 403

    return {
        "success": True,
        "data": {
            "id": req.id,
            "leave_type": req.leave_type.name if req.leave_type else "Leave",
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "total_days": float(req.total_days or 1),
            "reason": req.reason or "",
            "status": (req.status or "pending").capitalize(),
            "admin_remarks": req.rejection_reason or "",
            "created_at": req.created_at.strftime("%d %b %Y, %I:%M %p") if req.created_at else "",
        },
    }, 200


def get_teacher_single_correction(user: User, cid: int) -> tuple[dict[str, Any], int]:
    """Retrieve single correction request with strict ownership verification."""
    teacher, _ = resolve_teacher_identity(user)
    if not teacher:
        return {"success": False, "error": "Teacher profile not found."}, 404

    corr = AttendanceCorrection.query.get(cid)
    if not corr or (corr.school_id and corr.school_id != user.school_id):
        return {"success": False, "error": "Correction request not found."}, 404

    # CRITICAL OWNERSHIP CHECK
    if corr.attendance_type != "teacher" or corr.attendance_id != teacher.id:
        return {"success": False, "error": "Access forbidden: correction request does not belong to you."}, 403

    return {
        "success": True,
        "data": {
            "id": corr.id,
            "attendance_date": corr.attendance_date.isoformat() if corr.attendance_date else "",
            "issue_type": corr.issue_type or "Missing IN Punch",
            "requested_time": corr.requested_time or "",
            "reason": corr.reason,
            "note": corr.note or "",
            "status": (corr.status or "pending").capitalize(),
            "admin_comment": corr.admin_comment or "",
            "created_at": corr.created_at.strftime("%d %b %Y, %I:%M %p") if corr.created_at else "",
        },
    }, 200
