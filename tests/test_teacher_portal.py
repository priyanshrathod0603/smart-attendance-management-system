"""Targeted test suite for Phase 3: Teacher Portal functionality, API routes, and RBAC enforcement."""
from __future__ import annotations

import re
from datetime import date, timedelta
import pytest
from app import create_app
from database.auth_models import User
from database.models import Teacher
from database.db import db
from database.tenant_models import School
from database.enterprise_models import (
    TeacherAttendance, AttendanceCorrection, AttendanceSetting,
)
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


def seed_teacher_data(app):
    """Seed test school, teacher user, teacher profile and initial attendance records."""
    with app.app_context():
        from auth import utcnow
        school = School(school_id="SCH_PORTAL_01", name="Portal Academy", contact_email="admin@portal.com")
        db.session.add(school)
        db.session.commit()

        teacher_user = User(
            school_id=school.id,
            email="priya.sharma@portal.com",
            name="Priya Sharma",
            role="teacher",
            employee_id="TCH_101",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )
        db.session.add(teacher_user)
        db.session.commit()

        teacher_rec = Teacher(
            teacher_id="TCH_101",
            name="Priya Sharma",
            department="Mathematics",
            designation="Senior PGT",
            subject="Algebra & Calculus",
            assigned_classes="Class 10-A, Class 12-B",
            mobile="+919876543210",
            email="priya.sharma@portal.com",
            school_id=school.id,
            is_active=True,
            attendance_method="FACE",
        )
        db.session.add(teacher_rec)
        db.session.commit()



        # Seed today's attendance
        today = date.today()
        from datetime import datetime, timezone
        att_today = TeacherAttendance(
            teacher_id=teacher_rec.id,
            school_id=school.id,
            attendance_date=today,
            in_time=datetime.now(timezone.utc).replace(hour=8, minute=5, second=0),
            status="Present",
            is_late=False,
            working_minutes=480,
            confidence=0.98,
        )
        # Seed a past attendance
        att_past = TeacherAttendance(
            teacher_id=teacher_rec.id,
            school_id=school.id,
            attendance_date=today - timedelta(days=1),
            in_time=datetime.now(timezone.utc).replace(hour=8, minute=20, second=0),
            out_time=datetime.now(timezone.utc).replace(hour=16, minute=10, second=0),
            status="Late",
            is_late=True,
            working_minutes=470,
            confidence=0.95,
        )
        db.session.add_all([att_today, att_past])
        db.session.commit()

        return school.id, teacher_user.id, teacher_rec.id


def test_teacher_portal_pages_render(app_client):
    """Teacher can access /portal and its subpath views."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    resp = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    assert resp.status_code == 200

    # /portal
    p_resp = client.get("/portal")
    assert p_resp.status_code == 200
    html = p_resp.get_data(as_text=True)
    assert "Teacher Portal" in html
    assert "Priya Sharma" in html

    # /portal subroutes
    for sub in ["attendance", "summary", "leave", "corrections", "profile"]:
        res = client.get(f"/portal/{sub}")
        assert res.status_code == 200
        assert "Teacher Portal" in res.get_data(as_text=True)


def test_teacher_dashboard_api(app_client):
    """Teacher dashboard API returns today status and quick stats for authenticated teacher."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Accept": "application/json"}

    res = client.get("/api/teacher/dashboard", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    payload = data["data"]
    assert payload["teacher_info"]["name"] == "Priya Sharma"
    assert payload["teacher_info"]["teacher_id"] == "TCH_101"
    assert payload["today"]["status"] == "Present"
    assert "month_stats" in payload
    assert "recent_7_days" in payload
    assert len(payload["recent_7_days"]) >= 2


def test_teacher_attendance_history_api(app_client):
    """Teacher can retrieve their attendance history."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Accept": "application/json"}

    res = client.get("/api/teacher/attendance-history", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    records = data["data"]["records"]
    assert len(records) >= 2
    dates = [r["date"] for r in records]
    assert date.today().isoformat() in dates


def test_teacher_monthly_summary_api(app_client):
    """Teacher monthly summary calculates accurate working days, present days, and rate."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Accept": "application/json"}

    res = client.get("/api/teacher/monthly-summary", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    summary = data["data"]
    assert "attendance_percentage" in summary
    assert "present_days" in summary
    assert summary["present_days"] >= 2


def test_teacher_leave_management_api(app_client):
    """Teacher can view leave balances and apply for leave."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json", "Accept": "application/json"}

    # 1. GET leaves
    res = client.get("/api/teacher/leaves", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "leave_balances" in data["data"]

    # 2. POST apply leave
    today = date.today()
    start_str = (today + timedelta(days=5)).isoformat()
    end_str = (today + timedelta(days=6)).isoformat()
    apply_res = client.post("/api/teacher/leaves", headers=headers, json={
        "leave_type": "Casual Leave",
        "start_date": start_str,
        "end_date": end_str,
        "reason": "Family function",
    })
    assert apply_res.status_code == 200
    apply_data = apply_res.get_json()
    assert apply_data["success"] is True

    # 3. Verify in leaves list
    res2 = client.get("/api/teacher/leaves", headers=headers)
    leaves = res2.get_json()["data"]["leave_requests"]
    assert len(leaves) >= 1
    assert leaves[0]["reason"] == "Family function"
    assert leaves[0]["status"] == "Pending"


def test_teacher_correction_request_api(app_client):
    """Teacher can submit an attendance correction request and see its status."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json", "Accept": "application/json"}

    # Submit correction request
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    post_res = client.post("/api/teacher/corrections", headers=headers, json={
        "attendance_date": yesterday,
        "issue_type": "Missing OUT Punch",
        "requested_time": "16:05",
        "reason": "Camera unreadable during exit rush",
        "note": "Left campus with Principal permission.",
    })
    assert post_res.status_code == 200
    post_data = post_res.get_json()
    assert post_data["success"] is True

    # Retrieve corrections list
    get_res = client.get("/api/teacher/corrections", headers=headers)
    assert get_res.status_code == 200
    corr_list = get_res.get_json()["data"]["corrections"]
    assert len(corr_list) == 1
    assert corr_list[0]["attendance_date"] == yesterday
    assert corr_list[0]["issue_type"] == "Missing OUT Punch"
    assert corr_list[0]["status"] == "Pending"


def test_teacher_profile_api(app_client):
    """Teacher profile API returns safe, non-sensitive profile details."""
    app, client = app_client
    seed_teacher_data(app)

    # Login
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_PORTAL_01",
        "email": "priya.sharma@portal.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Accept": "application/json"}

    res = client.get("/api/teacher/profile", headers=headers)
    assert res.status_code == 200
    p = res.get_json()["data"]
    assert p["name"] == "Priya Sharma"
    assert p["teacher_id"] == "TCH_101"
    assert p["department"] == "Mathematics"
    assert "password_hash" not in p
    assert "salary" not in p


def test_unauthenticated_api_blocked(app_client):
    """Unauthenticated access to /api/teacher/* returns 401."""
    app, client = app_client

    for ep in ["dashboard", "attendance-history", "monthly-summary", "leaves", "corrections", "profile"]:
        res = client.get(f"/api/teacher/{ep}")
        assert res.status_code == 401
