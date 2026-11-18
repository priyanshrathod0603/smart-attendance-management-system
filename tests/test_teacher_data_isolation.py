"""Targeted Security Test Suite for Phase 4: Self-Only Backend Authorization & Data Isolation.

Verifies:
1. Teacher A can access only Teacher A's data.
2. Query param spoofing (?teacher_id=B, ?employee_id=B, ?user_id=B) is rejected with 403 Forbidden.
3. Request body spoofing ({"teacher_id": B, ...}) is rejected with 403 Forbidden.
4. Resource ID enumeration on attendance, leaves, and corrections is blocked with 403 Forbidden.
5. Mass-data and administrative endpoints are rejected with 403 Forbidden.
6. Direct attendance mutation by teachers is rejected with 403 Forbidden.
7. Admin access remains intact and functional.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
import pytest
from app import create_app
from database.auth_models import User
from database.models import Teacher
from database.db import db
from database.tenant_models import School
from database.enterprise_models import (
    TeacherAttendance, AttendanceCorrection,
)
from hr.models import Employee, LeaveRequest, LeaveType
from argon2 import PasswordHasher

ph = PasswordHasher()
PASSWORD = "Password123"


@pytest.fixture
def app_client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "AUTH_SESSION_HOURS": 12,
        "AUTH_VERIFICATION_HOURS": 24,
        "GOOGLE_CLIENT_ID": None,
        "GOOGLE_CLIENT_SECRET": None,
        "MICROSOFT_CLIENT_ID": None,
        "MICROSOFT_CLIENT_SECRET": None,
        "APPLE_CLIENT_ID": None,
        "APPLE_TEAM_ID": None,
    })
    with app.app_context():
        db.drop_all()
        db.create_all()
    with app.test_client() as client:
        yield app, client
    with app.app_context():
        db.session.remove()
        db.drop_all()


def form_csrf(response):
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert token, f"the HTML form must include a CSRF token: status {response.status_code}"
    return token.group(1)


def page_csrf(response):
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert token, "authenticated pages must publish the CSRF token for API calls"
    return token.group(1)


def seed_two_teachers_and_admin(app):
    """Seed test school, Admin, Teacher A (Priya), and Teacher B (Rahul) with distinct records."""
    with app.app_context():
        from auth import utcnow
        school = School(school_id="SCH_SEC_01", name="Security Academy", contact_email="admin@sec.com")
        db.session.add(school)
        db.session.commit()

        # 1. Admin User
        admin_user = User(
            school_id=school.id,
            email="admin@sec.com",
            name="Admin User",
            role="institution_admin",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )

        # 2. Teacher A (Priya Sharma)
        user_a = User(
            school_id=school.id,
            email="priya@sec.com",
            name="Priya Sharma",
            role="teacher",
            employee_id="TCH_001",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )

        # 3. Teacher B (Rahul Verma)
        user_b = User(
            school_id=school.id,
            email="rahul@sec.com",
            name="Rahul Verma",
            role="teacher",
            employee_id="TCH_002",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )
        db.session.add_all([admin_user, user_a, user_b])
        db.session.commit()

        # Teacher records
        teacher_a = Teacher(
            teacher_id="TCH_001",
            name="Priya Sharma",
            department="Mathematics",
            designation="Senior PGT",
            subject="Mathematics",
            mobile="+919876543210",
            email="priya@sec.com",
            school_id=school.id,
            is_active=True,
        )
        teacher_b = Teacher(
            teacher_id="TCH_002",
            name="Rahul Verma",
            department="Physics",
            designation="PGT",
            subject="Physics",
            mobile="+919876543211",
            email="rahul@sec.com",
            school_id=school.id,
            is_active=True,
        )
        db.session.add_all([teacher_a, teacher_b])
        db.session.commit()

        # Employee records
        emp_a = Employee(
            school_id=school.id,
            teacher_id=teacher_a.id,
            employee_code="TCH_001",
            first_name="Priya",
            last_name="Sharma",
            email="priya@sec.com",
            phone="+919876543210",
            employment_type="full_time",
            joining_date=date.today() - timedelta(days=365),
        )
        emp_b = Employee(
            school_id=school.id,
            teacher_id=teacher_b.id,
            employee_code="TCH_002",
            first_name="Rahul",
            last_name="Verma",
            email="rahul@sec.com",
            phone="+919876543211",
            employment_type="full_time",
            joining_date=date.today() - timedelta(days=300),
        )
        db.session.add_all([emp_a, emp_b])
        db.session.commit()

        # Leave Type
        lt = LeaveType(
            school_id=school.id,
            name="Casual Leave",
            code="CL",
            paid=True,
            max_days_per_year=12,
        )
        db.session.add(lt)
        db.session.commit()

        # Attendance records
        today = date.today()
        att_a = TeacherAttendance(
            teacher_id=teacher_a.id,
            school_id=school.id,
            attendance_date=today,
            in_time=datetime.now(timezone.utc).replace(hour=8, minute=5, second=0),
            status="Present",
            is_late=False,
            working_minutes=480,
            confidence=0.98,
        )
        att_b = TeacherAttendance(
            teacher_id=teacher_b.id,
            school_id=school.id,
            attendance_date=today,
            in_time=datetime.now(timezone.utc).replace(hour=8, minute=45, second=0),
            status="Late",
            is_late=True,
            working_minutes=440,
            confidence=0.92,
        )
        db.session.add_all([att_a, att_b])
        db.session.commit()

        # Leave requests
        leave_a = LeaveRequest(
            school_id=school.id,
            employee_id=emp_a.id,
            leave_type_id=lt.id,
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=11),
            total_days=2.0,
            reason="Teacher A personal leave",
            status="pending",
        )
        leave_b = LeaveRequest(
            school_id=school.id,
            employee_id=emp_b.id,
            leave_type_id=lt.id,
            start_date=today + timedelta(days=12),
            end_date=today + timedelta(days=13),
            total_days=2.0,
            reason="Teacher B medical leave",
            status="approved",
        )
        db.session.add_all([leave_a, leave_b])
        db.session.commit()

        # Correction requests
        corr_a = AttendanceCorrection(
            school_id=school.id,
            attendance_type="teacher",
            attendance_id=teacher_a.id,
            attendance_date=today - timedelta(days=1),
            issue_type="Missing OUT Punch",
            requested_time="16:00",
            reason="Teacher A camera exit issue",
            status="pending",
        )
        corr_b = AttendanceCorrection(
            school_id=school.id,
            attendance_type="teacher",
            attendance_id=teacher_b.id,
            attendance_date=today - timedelta(days=2),
            issue_type="Missing IN Punch",
            requested_time="08:00",
            reason="Teacher B fingerprint unreadable",
            status="pending",
        )
        db.session.add_all([corr_a, corr_b])
        db.session.commit()

        return {
            "school_id": school.id,
            "admin_id": admin_user.id,
            "user_a_id": user_a.id,
            "user_b_id": user_b.id,
            "teacher_a_id": teacher_a.id,
            "teacher_b_id": teacher_b.id,
            "emp_a_id": emp_a.id,
            "emp_b_id": emp_b.id,
            "att_a_id": att_a.id,
            "att_b_id": att_b.id,
            "leave_a_id": leave_a.id,
            "leave_b_id": leave_b.id,
            "corr_a_id": corr_a.id,
            "corr_b_id": corr_b.id,
        }


def _login_as_teacher_a(client):
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_SEC_01",
        "email": "priya@sec.com",
        "password": PASSWORD,
    }, follow_redirects=True)
    csrf = page_csrf(dash)
    return {"X-CSRF-Token": csrf, "Content-Type": "application/json", "Accept": "application/json"}


def test_teacher_can_access_own_attendance_only(app_client):
    """Test 1: Teacher A accesses own dashboard and attendance history."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)
    headers = _login_as_teacher_a(client)

    # 1. Dashboard returns Teacher A info and attendance
    res = client.get("/api/teacher/dashboard", headers=headers)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["teacher_info"]["name"] == "Priya Sharma"
    assert data["teacher_info"]["teacher_id"] == "TCH_001"
    assert data["today"]["status"] == "Present"

    # 2. History returns only Teacher A records
    res_hist = client.get("/api/teacher/attendance-history", headers=headers)
    assert res_hist.status_code == 200
    records = res_hist.get_json()["data"]["records"]
    assert len(records) >= 1
    assert all(r["status"] == "Present" for r in records)


def test_query_parameter_identity_spoofing_rejected(app_client):
    """Test 5: Passing another teacher's ID in query params is rejected with 403 Forbidden."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)
    headers = _login_as_teacher_a(client)

    spoofed_queries = [
        "/api/teacher/dashboard?teacher_id=TCH_002",
        "/api/teacher/dashboard?employee_id=TCH_002",
        "/api/teacher/dashboard?user_id=3",
        "/api/teacher/attendance-history?teacher_id=TCH_002",
        "/api/teacher/attendance-history?employee_id=TCH_002",
        "/api/teacher/monthly-summary?teacher_id=TCH_002",
        "/api/teacher/leaves?employee_id=TCH_002",
        "/api/teacher/corrections?teacher_id=TCH_002",
        "/api/teacher/profile?teacher_id=TCH_002",
    ]

    for endpoint in spoofed_queries:
        res = client.get(endpoint, headers=headers)
        assert res.status_code == 403, f"Expected 403 on {endpoint}, got {res.status_code}"
        json_data = res.get_json()
        assert json_data["success"] is False
        assert "forbidden" in json_data["error"].lower()


def test_request_body_identity_spoofing_rejected(app_client):
    """Test 6: Passing another teacher's ID in request body is rejected with 403 Forbidden."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)
    headers = _login_as_teacher_a(client)

    today = date.today()

    # 1. Attempt to apply leave on behalf of Teacher B
    res_leave = client.post("/api/teacher/leaves", headers=headers, json={
        "employee_id": "TCH_002",
        "leave_type": "Casual Leave",
        "start_date": (today + timedelta(days=20)).isoformat(),
        "end_date": (today + timedelta(days=21)).isoformat(),
        "reason": "Spoofed leave application",
    })
    assert res_leave.status_code == 403, f"Expected 403 on spoofed leave POST, got {res_leave.status_code}"
    assert res_leave.get_json()["success"] is False

    # 2. Attempt to submit correction request for Teacher B
    res_corr = client.post("/api/teacher/corrections", headers=headers, json={
        "teacher_id": "TCH_002",
        "attendance_date": today.isoformat(),
        "issue_type": "Missing IN Punch",
        "reason": "Spoofed correction request",
    })
    assert res_corr.status_code == 403, f"Expected 403 on spoofed correction POST, got {res_corr.status_code}"
    assert res_corr.get_json()["success"] is False

    # 3. Attempt to target Teacher B's specific attendance record ID in correction request
    res_corr_idor = client.post("/api/teacher/corrections", headers=headers, json={
        "attendance_record_id": info["att_b_id"],
        "attendance_date": today.isoformat(),
        "issue_type": "Incorrect Punch Time",
        "reason": "IDOR attempt on Teacher B attendance record",
    })
    assert res_corr_idor.status_code == 403, f"Expected 403 on IDOR correction POST, got {res_corr_idor.status_code}"


def test_resource_id_enumeration_protected(app_client):
    """Tests 7, 8, 9: Direct Object Reference lookups by ID enforce ownership."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)
    headers = _login_as_teacher_a(client)

    # 1. Attendance ID lookup:
    # Teacher A owns att_a_id -> 200
    res_own_att = client.get(f"/api/teacher/attendance/{info['att_a_id']}", headers=headers)
    assert res_own_att.status_code == 200
    assert res_own_att.get_json()["data"]["id"] == info["att_a_id"]

    # Teacher A attempts to lookup Teacher B's attendance -> 403
    res_other_att = client.get(f"/api/teacher/attendance/{info['att_b_id']}", headers=headers)
    assert res_other_att.status_code == 403

    # 2. Leave ID lookup:
    # Teacher A owns leave_a_id -> 200
    res_own_leave = client.get(f"/api/teacher/leaves/{info['leave_a_id']}", headers=headers)
    assert res_own_leave.status_code == 200
    assert res_own_leave.get_json()["data"]["id"] == info["leave_a_id"]

    # Teacher A attempts to lookup Teacher B's leave -> 403
    res_other_leave = client.get(f"/api/teacher/leaves/{info['leave_b_id']}", headers=headers)
    assert res_other_leave.status_code == 403

    # 3. Correction request ID lookup:
    # Teacher A owns corr_a_id -> 200
    res_own_corr = client.get(f"/api/teacher/corrections/{info['corr_a_id']}", headers=headers)
    assert res_own_corr.status_code == 200
    assert res_own_corr.get_json()["data"]["id"] == info["corr_a_id"]

    # Teacher A attempts to lookup Teacher B's correction request -> 403
    res_other_corr = client.get(f"/api/teacher/corrections/{info['corr_b_id']}", headers=headers)
    assert res_other_corr.status_code == 403


def test_teacher_blocked_from_admin_and_mass_data_endpoints(app_client):
    """Test 10 & 11: Teacher cannot access mass-data, admin endpoints, or mutate attendance."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)
    headers = _login_as_teacher_a(client)

    restricted_endpoints = [
        ("GET", "/api/users"),
        ("GET", "/api/cameras"),
        ("GET", "/api/students"),
        ("GET", "/api/teachers"),
        ("GET", f"/api/teachers/{info['teacher_b_id']}/profile"),
        ("GET", f"/api/teachers/{info['teacher_b_id']}/attendance"),
        ("GET", "/api/hr/employees"),
        ("GET", f"/api/hr/employees/{info['emp_b_id']}"),
        ("GET", "/api/enterprise/attendance"),
        ("POST", "/api/enterprise/attendance"),
        ("GET", "/api/devices/fingerprint/config"),
        ("PUT", f"/api/attendance/{info['att_a_id']}"),
        ("DELETE", f"/api/attendance/{info['att_a_id']}"),
    ]

    for method, path in restricted_endpoints:
        if method == "GET":
            res = client.get(path, headers=headers)
        elif method == "PUT":
            res = client.put(path, headers=headers, json={"status": "Present"})
        elif method == "DELETE":
            res = client.delete(path, headers=headers)
        elif method == "POST":
            res = client.post(path, headers=headers, json={"status": "Present"})

        assert res.status_code == 403, f"Expected 403 on {method} {path}, got {res.status_code}"


def test_admin_access_intact_and_unrestricted(app_client):
    """Test 12: Admin role retains full access to employee, attendance, and institution records."""
    app, client = app_client
    info = seed_two_teachers_and_admin(app)

    # Login as Admin
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_SEC_01",
        "email": "admin@sec.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json", "Accept": "application/json"}

    # 1. Admin can access mass-data endpoints
    res_users = client.get("/api/users", headers=headers)
    assert res_users.status_code == 200

    res_teachers = client.get("/api/teachers", headers=headers)
    assert res_teachers.status_code == 200
    assert len(res_teachers.get_json()) >= 2

    # 2. Admin can access specific teacher profile and attendance
    res_prof_b = client.get(f"/api/teachers/{info['teacher_b_id']}/profile", headers=headers)
    assert res_prof_b.status_code == 200
    assert res_prof_b.get_json()["name"] == "Rahul Verma"

    res_att_b = client.get(f"/api/teachers/{info['teacher_b_id']}/attendance", headers=headers)
    assert res_att_b.status_code == 200
