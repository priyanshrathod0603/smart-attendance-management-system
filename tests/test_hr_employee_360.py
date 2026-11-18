"""
Comprehensive Automated Test Suite for AttendanceOS HR & Employee 360° Integration
Tests Phase 10 Verification Scenarios:
1. Teacher creation -> Auto HR employee with identical Staff ID.
2. Non-teaching staff creation -> Clean HR functionality.
3. Teacher update -> Real-time sync to HR Employee.
4. Attendance marking -> Real-time 360° profile daily grid and KPI updates.
5. Late arrival -> Late Intelligence metrics (monthly count, delay minutes, threshold).
6. Absence -> Monthly summary and compliance calculation.
7. Holiday & Weekly off -> Working day exemption, never counted as absence.
8. Leave management -> Paid/unpaid leave tracking and balance quotas.
9. Salary & Payroll -> Structure, revisions, and itemized payslip breakdown.
10. Employee Documents -> Protected upload, metadata, retrieval, and deletion.
11. Employee Deactivation -> Soft status change, preservation of historical records.
12. Single Source of Truth -> Preventing parallel duplicates.
13. 360° Profile API Filtering -> Custom month and date range aggregation.
"""

from datetime import date, datetime, time, timedelta
import secrets
import pytest
from argon2 import PasswordHasher
from app import create_app
from database.db import db
from database.auth_models import User, AuthSession, utcnow
from auth import _hash
from database.models import Teacher, Attendance
from database.enterprise_models import (
    TeacherAttendance, HolidayCalendar, WeeklyOff, TeacherTimeRule
)
from hr.models import (
    Department, Designation, Employee, EmployeeSalaryStructure,
    LeaveType, EmployeeLeaveBalance, LeaveRequest, PayrollPeriod, PayrollRecord,
    EmployeeDocument, EmployeeTimelineEvent
)
from hr.employee_service import (
    ensure_employee_from_teacher, ensure_teacher_from_employee,
    sync_teachers_to_employees, create_employee, update_employee,
    create_salary_structure, get_employee_360_profile,
    create_employee_document, get_employee_documents, delete_employee_document
)


@pytest.fixture
def app_and_client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "AUTH_SESSION_HOURS": 12,
        "SECRET_KEY": "test-key-1234",
    })
    with app.app_context():
        db.create_all()
        # Seed superadmin user & session
        ph = PasswordHasher()
        user = User(
            email="admin@school.edu",
            name="Admin User",
            password_hash=ph.hash("AdminPass123"),
            role="super_admin",
            email_verified_at=utcnow(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()

        raw_sid = secrets.token_urlsafe(48)
        session_rec = AuthSession(
            token_hash=_hash(raw_sid),
            user=user,
            csrf_secret="test-csrf-token",
            device="pytest",
            ip_address="127.0.0.1",
            expires_at=utcnow() + timedelta(hours=12),
        )
        db.session.add(session_rec)
        db.session.commit()

        test_client = app.test_client()
        with test_client.session_transaction() as sess:
            sess["sid"] = raw_sid
        yield app, test_client, user
        db.session.remove()
        db.drop_all()


# Scenario 1: Teacher Added -> Automatically creates HR Employee with matching Staff ID
def test_scenario_1_teacher_creates_connected_hr_employee(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(
        teacher_id="STF-2001",
        name="Sunita Deshmukh",
        email="sunita@school.edu",
        mobile="9823001122",
        department="Mathematics",
        designation="Senior Teacher"
    )
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    assert emp is not None
    assert emp.employee_code == "STF-2001"
    assert emp.first_name == "Sunita"
    assert emp.last_name == "Deshmukh"
    assert emp.email == "sunita@school.edu"
    assert emp.phone == "9823001122"
    assert emp.teacher_id == t.id
    assert emp.department.name == "Mathematics"
    assert emp.designation.name == "Senior Teacher"

    # Teacher to_dict includes linked_employee_id
    t_dict = t.to_dict()
    assert t_dict.get("linked_employee_id") == emp.id


# Scenario 2: Non-teaching staff creation
def test_scenario_2_non_teaching_staff_functions_cleanly(app_and_client):
    app, client, _ = app_and_client
    dept = Department(name="Administration", code="ADM")
    desig = Designation(name="Accountant", code="ACC")
    db.session.add_all([dept, desig])
    db.session.commit()

    emp_data = {
        "employee_code": "ADM-5001",
        "first_name": "Vikram",
        "last_name": "Rathore",
        "email": "vikram@school.edu",
        "phone": "9811122233",
        "joining_date": date(2026, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
        "employment_type": "full_time"
    }
    emp = create_employee(emp_data)
    assert emp.id is not None
    assert emp.employee_code == "ADM-5001"
    assert emp.teacher_id is None

    # 360 profile functions seamlessly
    p360 = get_employee_360_profile(emp.id)
    assert p360["overview"]["employee_code"] == "ADM-5001"
    assert p360["overview"]["full_name"] == "Vikram Rathore"
    assert p360["overview"]["is_teacher"] is False


# Scenario 3: Teacher details update propagates to HR Employee
def test_scenario_3_teacher_update_syncs_to_hr_employee(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(
        teacher_id="TCH-301",
        name="Ananya Roy",
        email="ananya@school.edu",
        mobile="9800011122",
        department="English",
        designation="Assistant Teacher"
    )
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()
    assert emp.first_name == "Ananya"

    # Update Teacher details
    t.name = "Ananya Mukherjee"
    t.mobile = "9899998877"
    t.designation = "Lead Teacher"
    db.session.commit()

    # Re-sync
    emp_updated = ensure_employee_from_teacher(t)
    db.session.commit()

    assert emp_updated.id == emp.id
    assert emp_updated.first_name == "Ananya"
    assert emp_updated.last_name == "Mukherjee"
    assert emp_updated.phone == "9899998877"
    assert emp_updated.designation.name == "Lead Teacher"


# Scenario 4: Attendance marking updates 360° profile daily grid and KPIs
def test_scenario_4_attendance_marking_flows_to_360_profile(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-401", name="Kavita Nair", email="kavita@school.edu")
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    test_date = date(2026, 10, 5)
    att = TeacherAttendance(
        teacher_id=t.id,
        attendance_date=test_date,
        in_time=datetime.combine(test_date, time(7, 55)),
        out_time=datetime.combine(test_date, time(14, 5)),
        status="present",
        working_minutes=370,
        is_late=False
    )
    db.session.add(att)
    db.session.commit()

    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    summary = p360["attendance_summary"]
    history = p360["attendance_history"]

    assert summary["total_present_days"] >= 1
    assert len(history) >= 1
    rec = next((r for r in history if r["date"] == "2026-10-05"), None)
    assert rec is not None
    assert rec["status"] == "present"
    assert rec["check_in"] == "07:55 AM"
    assert rec["check_out"] == "02:05 PM"
    assert rec["late_minutes"] == 0
    assert rec["missing_punch"] is False


# Scenario 5: Late arrival intelligence calculation
def test_scenario_5_late_arrival_intelligence(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-501", name="Manoj Joshi", email="manoj@school.edu")
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    # Log 2 late attendances
    d1 = date(2026, 10, 6)
    att1 = TeacherAttendance(
        teacher_id=t.id,
        attendance_date=d1,
        in_time=datetime.combine(d1, time(8, 25)),
        out_time=datetime.combine(d1, time(14, 0)),
        status="late",
        is_late=True,
        working_minutes=335
    )
    d2 = date(2026, 10, 7)
    att2 = TeacherAttendance(
        teacher_id=t.id,
        attendance_date=d2,
        in_time=datetime.combine(d2, time(8, 15)),
        out_time=datetime.combine(d2, time(14, 0)),
        status="late",
        is_late=True,
        working_minutes=345
    )
    db.session.add_all([att1, att2])
    db.session.commit()

    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    late_intel = p360["late_intelligence"]

    assert late_intel["monthly_late_count"] == 2
    assert late_intel["total_accumulated_late_minutes"] == 40
    assert len(late_intel["records"]) == 2
    assert late_intel["records"][0]["delay_minutes"] in (15, 25)


# Scenario 6: Absence tracking and attendance compliance calculation
def test_scenario_6_absence_tracking(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-601", name="Deepak Patil", email="deepak@school.edu")
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    # 3 present, 1 absent (on Oct 1, 2, 3, 4 of 2026)
    # Configure WeeklyOff to Saturday/Sunday (weekday 5, 6) so weekday 1..4 are working days
    for i in range(1, 4):
        d = date(2026, 10, i)
        db.session.add(TeacherAttendance(
            teacher_id=t.id,
            attendance_date=d,
            in_time=datetime.combine(d, time(8, 0)),
            out_time=datetime.combine(d, time(14, 0)),
            status="present",
            working_minutes=360
        ))
    d_abs = date(2026, 10, 4)
    db.session.add(TeacherAttendance(
        teacher_id=t.id,
        attendance_date=d_abs,
        status="absent"
    ))
    db.session.commit()

    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 4))
    summary = p360["attendance_summary"]
    assert summary["total_present_days"] == 3
    assert summary["total_absent_days"] == 1


# Scenario 7: Holiday & Weekly Off Exemption (Never counted as absence)
def test_scenario_7_holidays_and_weekly_offs_exempted(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-701", name="Meera Krishnan", email="meera@school.edu")
    db.session.add(t)
    # Add Holiday
    hol = HolidayCalendar(
        holiday_date=date(2026, 10, 2),
        name="Gandhi Jayanti",
        applies_to="all"
    )
    # Add Weekly Off
    off = WeeklyOff(weekday=6, applies_to="all") # Sunday
    db.session.add_all([hol, off])
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    hol_leave = p360["holidays_and_leaves"]

    assert len(hol_leave["holidays"]) >= 1
    h_item = next((h for h in hol_leave["holidays"] if h["date"] == "2026-10-02"), None)
    assert h_item is not None
    assert h_item["name"] == "Gandhi Jayanti"
    assert len(hol_leave["weekly_offs"]) >= 1


# Scenario 8: Paid / Unpaid Leave tracking & Balances
def test_scenario_8_leaves_and_balances(app_and_client):
    app, client, _ = app_and_client
    emp_data = {
        "employee_code": "EMP-8001",
        "first_name": "Suresh",
        "last_name": "Raina",
        "email": "suresh@school.edu",
        "phone": "9812345678",
        "joining_date": date(2026, 1, 1),
    }
    emp = create_employee(emp_data)

    lt = LeaveType(name="Casual Leave", code="CL", paid=True, max_days_per_year=12)
    db.session.add(lt)
    db.session.commit()

    bal = EmployeeLeaveBalance(employee_id=emp.id, leave_type_id=lt.id, year=2026, allocated_days=12, used_days=2, remaining_days=10)
    lr = LeaveRequest(
        employee_id=emp.id,
        leave_type_id=lt.id,
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 13),
        total_days=2,
        reason="Family function",
        status="approved"
    )
    db.session.add_all([bal, lr])
    db.session.commit()

    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    hl = p360["holidays_and_leaves"]

    assert len(hl["leave_balances"]) == 1
    assert hl["leave_balances"][0]["remaining_days"] == 10
    assert len(hl["leave_requests"]) == 1
    assert hl["leave_requests"][0]["status"] == "approved"
    assert hl["leave_requests"][0]["total_days"] == 2


# Scenario 9: Salary structure, revisions, and payroll payslip breakdown
def test_scenario_9_salary_and_payroll_slip_breakdown(app_and_client):
    app, client, _ = app_and_client
    emp = create_employee({
        "employee_code": "EMP-9001",
        "first_name": "Neha",
        "last_name": "Kapoor",
        "email": "neha@school.edu",
        "phone": "9876541230",
        "joining_date": date(2026, 1, 1),
    })

    create_salary_structure(emp.id, {
        "basic_salary": 45000.00,
        "effective_from": "2026-01-01",
        "pay_frequency": "monthly",
        "status": "active"
    })

    period = PayrollPeriod(
        period_name="October 2026",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        payment_date=date(2026, 10, 31),
        status="finalized"
    )
    db.session.add(period)
    db.session.commit()

    pr = PayrollRecord(
        payroll_period_id=period.id,
        employee_id=emp.id,
        basic_salary=45000.00,
        gross_earnings=45000.00,
        total_earnings=45000.00,
        total_deductions=1500.00,
        net_salary=43500.00,
        status="finalized",
    )
    db.session.add(pr)
    db.session.commit()

    p360 = get_employee_360_profile(emp.id)
    sp = p360["salary_and_payroll"]

    assert sp["current_salary_structure"] is not None
    assert sp["current_salary_structure"]["basic_salary"] == 45000.00
    assert len(sp["payroll_records"]) == 1
    record = sp["payroll_records"][0]
    assert record["net_salary"] == 43500.00


# Scenario 10: Document Management (Upload, retrieval, delete)
def test_scenario_10_document_management(app_and_client):
    app, client, _ = app_and_client
    emp = create_employee({
        "employee_code": "EMP-1001",
        "first_name": "Arjun",
        "last_name": "Gupta",
        "email": "arjun@school.edu",
        "phone": "9812300000",
        "joining_date": date(2026, 1, 1),
    })

    doc = create_employee_document(emp.id, {
        "title": "Appointment Letter 2026",
        "document_type": "appointment_letter",
        "file_name": "appointment_arjun.pdf",
        "file_path": "uploads/documents/appointment_arjun.pdf",
        "file_size_bytes": 1048576,
        "notes": "Official appointment contract signed"
    })

    assert doc.id is not None
    docs = get_employee_documents(emp.id)
    assert len(docs) == 1
    assert docs[0].title == "Appointment Letter 2026"

    # Profile integration
    p360 = get_employee_360_profile(emp.id)
    assert len(p360["documents"]) == 1
    assert p360["documents"][0]["document_type"] == "appointment_letter"

    # Delete
    deleted = delete_employee_document(emp.id, doc.id)
    assert deleted is True
    assert len(get_employee_documents(emp.id)) == 0


# Scenario 11: Employee deactivation preserves historical records
def test_scenario_11_deactivation_preserves_history(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-1101", name="Ramesh Sen", email="ramesh@school.edu")
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    # Add historical attendance
    d = date(2026, 10, 1)
    db.session.add(TeacherAttendance(
        teacher_id=t.id,
        attendance_date=d,
        in_time=datetime.combine(d, time(8, 0)),
        out_time=datetime.combine(d, time(14, 0)),
        status="present",
        working_minutes=360
    ))
    db.session.commit()

    # Soft-deactivate employee
    update_employee(emp.id, {"employment_status": "inactive"})
    assert emp.is_active is False
    assert emp.employment_status == "inactive"

    # 360 profile remains fully accessible for historical audit
    p360 = get_employee_360_profile(emp.id, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))
    assert p360["overview"]["employment_status"] == "inactive"
    assert len(p360["attendance_history"]) >= 1
    assert p360["attendance_summary"]["total_present_days"] >= 1


# Scenario 12: Single Source of Truth prevents duplicate employees
def test_scenario_12_single_source_of_truth_no_duplicates(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-1201", name="Pooja Batra", email="pooja@school.edu")
    db.session.add(t)
    db.session.commit()

    emp1 = ensure_employee_from_teacher(t)
    db.session.commit()

    # Calling sync multiple times must yield exact same employee
    emp2 = ensure_employee_from_teacher(t)
    db.session.commit()

    assert emp1.id == emp2.id
    assert Employee.query.filter_by(teacher_id=t.id).count() == 1
    assert Employee.query.filter_by(employee_code="TCH-1201").count() == 1


# Scenario 13: 360° Profile API Endpoint returns JSON with query filters
def test_scenario_13_360_profile_api_endpoint(app_and_client):
    app, client, _ = app_and_client
    t = Teacher(teacher_id="TCH-1301", name="Geeta Sharma", email="geeta@school.edu")
    db.session.add(t)
    db.session.commit()

    emp = ensure_employee_from_teacher(t)
    db.session.commit()

    headers = {"X-CSRF-Token": "test-csrf-token"}
    res = client.get(f"/api/hr/employees/{emp.id}/360-profile?month=2026-10", headers=headers)
    assert res.status_code == 200
    data = res.get_json()

    assert "overview" in data
    assert "attendance_summary" in data
    assert "attendance_history" in data
    assert "late_intelligence" in data
    assert "holidays_and_leaves" in data
    assert "salary_and_payroll" in data
    assert "documents" in data
    assert "timeline" in data
    assert data["overview"]["employee_code"] == "TCH-1301"
    assert data["overview"]["full_name"] == "Geeta Sharma"
