"""Targeted test suite for Phase 2: Teacher Role-Based Access Control (RBAC)."""
from __future__ import annotations

import re
import pytest
from app import create_app
from database.auth_models import User
from database.db import db
from database.tenant_models import School
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


def seed_school_admin_and_teacher(app):
    """Seed test school, admin, principal and teacher users with verified status."""
    with app.app_context():
        from auth import utcnow
        school = School(school_id="SCH_TEST_01", name="Test Academy", contact_email="admin@testacademy.com")
        db.session.add(school)
        db.session.commit()

        admin = User(
            school_id=school.id,
            email="admin@testacademy.com",
            name="Admin User",
            role="institution_admin",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )
        principal = User(
            school_id=school.id,
            email="principal@testacademy.com",
            name="Principal User",
            role="principal",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )
        teacher = User(
            school_id=school.id,
            email="teacher@testacademy.com",
            name="Teacher One",
            role="teacher",
            employee_id="TCH_001",
            password_hash=ph.hash(PASSWORD),
            is_active=True,
            email_verified_at=utcnow(),
        )
        db.session.add_all([admin, principal, teacher])
        db.session.commit()
        return school.id, admin.id, teacher.id


def test_teacher_authentication_and_role_recognition(app_client):
    """Teacher can log in with existing auth system and is recognized with role='teacher'."""
    app, client = app_client
    seed_school_admin_and_teacher(app)

    # 1. Login page GET
    login_page = client.get("/login")
    csrf = form_csrf(login_page)

    # 2. Login as teacher
    resp = client.post("/login", data={
        "csrf_token": csrf,
        "school_id": "SCH_TEST_01",
        "email": "teacher@testacademy.com",
        "password": PASSWORD,
    }, follow_redirects=False)

    assert resp.status_code in (302, 303), "Login should succeed and redirect"

    # 3. Access dashboard as authenticated teacher (redirects to /portal)
    dash_resp = client.get("/dashboard", follow_redirects=True)
    assert dash_resp.status_code == 200, "Teacher should access dashboard/portal"
    html = dash_resp.get_data(as_text=True)
    assert "Teacher One" in html
    assert "Teacher" in html


def test_teacher_blocked_from_admin_pages(app_client):
    """Teacher is rejected (403) from accessing admin-only pages by URL navigation."""
    app, client = app_client
    seed_school_admin_and_teacher(app)

    # Login as teacher
    login_page = client.get("/login")
    client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_TEST_01",
        "email": "teacher@testacademy.com",
        "password": PASSWORD,
    })

    # Try navigating to admin-only pages
    admin_pages = [
        "/settings",
        "/cameras",
        "/devices/fingerprint",
        "/admin/devices/fingerprint",
        "/users",
        "/hr/payroll",
        "/hr/employees",
        "/hr/settings",
        "/time-rules/teacher",
        "/time-rules/student",
    ]

    for page in admin_pages:
        res = client.get(page)
        assert res.status_code == 403, f"Teacher should be rejected from {page} with 403 Forbidden, got {res.status_code}"


def test_teacher_blocked_from_admin_apis(app_client):
    """Teacher is rejected (403) from calling admin-only APIs."""
    app, client = app_client
    seed_school_admin_and_teacher(app)

    # Login as teacher
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_TEST_01",
        "email": "teacher@testacademy.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    csrf = page_csrf(dash)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

    # Try calling protected APIs
    res = client.get("/api/users", headers=headers)
    assert res.status_code == 403, f"GET /api/users should be 403, got {res.status_code}"

    res = client.get("/api/cameras", headers=headers)
    assert res.status_code == 403, f"GET /api/cameras should be 403, got {res.status_code}"

    res = client.get("/api/devices/fingerprint/config", headers=headers)
    assert res.status_code == 403, f"GET /api/devices/fingerprint/config should be 403, got {res.status_code}"

    res = client.post("/api/devices/fingerprint/config", headers=headers, json={"device_name": "Hack"})
    assert res.status_code == 403, f"POST /api/devices/fingerprint/config should be 403, got {res.status_code}"

    res = client.get("/api/hr/employees", headers=headers)
    assert res.status_code == 403, f"GET /api/hr/employees should be 403, got {res.status_code}"


def test_teacher_allowed_actions(app_client):
    """Teacher can access their own profile, security settings, and AI chat assistant."""
    app, client = app_client
    seed_school_admin_and_teacher(app)

    login_page = client.get("/login")
    client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_TEST_01",
        "email": "teacher@testacademy.com",
        "password": PASSWORD,
    })

    # Allowed routes
    res = client.get("/profile")
    assert res.status_code == 200

    res = client.get("/security")
    assert res.status_code == 200

    res = client.get("/active-sessions")
    assert res.status_code == 200

    res = client.get("/chat")
    assert res.status_code == 200


def test_admin_and_principal_access_remains_intact(app_client):
    """Existing Admin and Principal roles retain their full permissions."""
    app, client = app_client
    seed_school_admin_and_teacher(app)

    # Login as Admin
    login_page = client.get("/login")
    dash = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_TEST_01",
        "email": "admin@testacademy.com",
        "password": PASSWORD,
    }, follow_redirects=True)

    assert client.get("/settings").status_code == 200
    assert client.get("/cameras").status_code == 200
    assert client.get("/devices/fingerprint").status_code == 200
    assert client.get("/users").status_code == 200
    assert client.get("/hr/payroll").status_code == 200

    # Logout with CSRF
    csrf = page_csrf(dash)
    client.post("/logout", data={"csrf_token": csrf})

    # Login as Principal
    login_page = client.get("/login")
    client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_TEST_01",
        "email": "principal@testacademy.com",
        "password": PASSWORD,
    })

    assert client.get("/dashboard").status_code == 200
    assert client.get("/cameras").status_code == 200
    assert client.get("/devices/fingerprint").status_code == 200
