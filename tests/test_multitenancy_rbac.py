"""Comprehensive Multi-Tenant Isolation & Role-Based Access Control (RBAC) Test Suite."""
from __future__ import annotations

import json
import re
import pytest

from app import create_app
from auth import _hash, _issue, utcnow
from database.auth_models import User
from database.db import db
from database.models import Attendance, Camera, Student, Teacher
from database.tenant_models import School
from hr.models import Department, Designation, Employee, PayrollPeriod, PayrollRecord

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
    assert token, "the HTML form must include a CSRF token"
    return token.group(1)


def page_csrf(response):
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert token, "authenticated pages must publish the CSRF token for API calls"
    return token.group(1)


def register_school_and_admin(client, school_id, school_name, admin_email, role="institution_admin"):
    """Register a new institution with an admin, verify and log in."""
    signup_page = client.get("/signup")
    resp = client.post("/signup", data={
        "csrf_token": form_csrf(signup_page),
        "school_id": school_id,
        "school_name": school_name,
        "name": f"{school_name} Admin",
        "email": admin_email,
        "password": PASSWORD,
    })
    assert resp.status_code == 200

    app = client.application
    with app.app_context():
        user = User.query.filter_by(email=admin_email).one()
        user.email_verified_at = utcnow()
        user.role = role
        db.session.commit()

    login_page = client.get("/login")
    login_resp = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": school_id,
        "email": admin_email,
        "password": PASSWORD,
    })
    assert login_resp.status_code == 302
    dashboard_resp = client.get("/dashboard")
    return page_csrf(dashboard_resp)


# =========================================================================
# 1. Institution Onboarding & Registration Tests
# =========================================================================

def test_multi_school_onboarding(app_client):
    app, client = app_client

    # Onboard School A
    csrf_a = register_school_and_admin(client, "SCH_ALPHA", "Alpha Academy", "admin@alpha.test")
    assert csrf_a

    # Logout
    client.post("/logout", data={"csrf_token": csrf_a})

    # Onboard School B
    csrf_b = register_school_and_admin(client, "SCH_BETA", "Beta High", "admin@beta.test")
    assert csrf_b

    with app.app_context():
        school_a = School.query.filter_by(school_id="SCH_ALPHA").first()
        school_b = School.query.filter_by(school_id="SCH_BETA").first()
        assert school_a is not None and school_a.name == "Alpha Academy"
        assert school_b is not None and school_b.name == "Beta High"
        assert school_a.id != school_b.id


def test_duplicate_school_id_registration_rejected(app_client):
    _, client = app_client
    register_school_and_admin(client, "SCH_DUP", "Original School", "orig@school.test")
    client.get("/logout")

    signup_page = client.get("/signup")
    dup_resp = client.post("/signup", data={
        "csrf_token": form_csrf(signup_page),
        "school_id": "SCH_DUP",
        "school_name": "Duplicate School",
        "name": "Another Admin",
        "email": "another@school.test",
        "password": PASSWORD,
    })
    assert dup_resp.status_code == 409
    assert "already registered" in dup_resp.get_data(as_text=True).lower()


# =========================================================================
# 2. Tenant Data Isolation Tests (Students, Teachers, Cameras, Attendance)
# =========================================================================

def test_tenant_isolation_students_and_teachers(app_client):
    app, client = app_client

    # 1. School A creates Student 101 and Teacher TCH-01
    csrf_a = register_school_and_admin(client, "SCH_A", "School A", "admin_a@a.test")
    resp_s_a = client.post("/api/students", json={
        "roll_no": "101",
        "name": "Student Alpha",
        "class_name": "Class 1",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_s_a.status_code in (200, 201)
    s_a_id = resp_s_a.get_json()["id"]

    resp_t_a = client.post("/api/teachers", json={
        "teacher_id": "TCH-01",
        "name": "Teacher Alpha",
        "department": "Science",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_t_a.status_code in (200, 201)
    t_a_id = resp_t_a.get_json()["id"]

    client.post("/logout", data={"csrf_token": csrf_a})

    # 2. School B creates Student 101 and Teacher TCH-01 (same codes, scoped by school_id!)
    csrf_b = register_school_and_admin(client, "SCH_B", "School B", "admin_b@b.test")
    resp_s_b = client.post("/api/students", json={
        "roll_no": "101",
        "name": "Student Beta",
        "class_name": "Class 1",
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_s_b.status_code in (200, 201)
    s_b_id = resp_s_b.get_json()["id"]

    resp_t_b = client.post("/api/teachers", json={
        "teacher_id": "TCH-01",
        "name": "Teacher Beta",
        "department": "Maths",
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_t_b.status_code in (200, 201)
    t_b_id = resp_t_b.get_json()["id"]

    # Verify School B list only shows School B items
    students_b = client.get("/api/students").get_json()
    assert len(students_b) == 1
    assert students_b[0]["name"] == "Student Beta"

    teachers_b = client.get("/api/teachers").get_json()
    assert len(teachers_b) == 1
    assert teachers_b[0]["name"] == "Teacher Beta"

    # Cross-tenant ID access from School B to School A items MUST return 404
    assert client.get(f"/api/students/{s_a_id}").status_code == 404
    assert client.get(f"/api/teachers/{t_a_id}").status_code == 404
    assert client.put(f"/api/students/{s_a_id}", json={"name": "Hacked"}, headers={"X-CSRF-Token": csrf_b}).status_code == 404
    assert client.delete(f"/api/students/{s_a_id}", headers={"X-CSRF-Token": csrf_b}).status_code == 404

    client.post("/logout", data={"csrf_token": csrf_b})

    # 3. Log back into School A and check lists
    login_page = client.get("/login")
    client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_A",
        "email": "admin_a@a.test",
        "password": PASSWORD,
    })
    csrf_a_2 = page_csrf(client.get("/dashboard"))

    students_a = client.get("/api/students").get_json()
    assert len(students_a) == 1
    assert students_a[0]["name"] == "Student Alpha"

    teachers_a = client.get("/api/teachers").get_json()
    assert len(teachers_a) == 1
    assert teachers_a[0]["name"] == "Teacher Alpha"

    # Cross-tenant ID access from School A to School B items MUST return 404
    assert client.get(f"/api/students/{s_b_id}").status_code == 404
    assert client.get(f"/api/teachers/{t_b_id}").status_code == 404


def test_tenant_isolation_cameras_and_attendance(app_client):
    app, client = app_client

    # School A Camera
    csrf_a = register_school_and_admin(client, "SCH_CAM_A", "Camera School A", "cam_a@a.test")
    resp_c_a = client.post("/api/cameras", json={
        "name": "Main Gate A",
        "source": "0",
        "location": "Entrance A",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_c_a.status_code in (200, 201)
    c_a_id = resp_c_a.get_json()["id"]

    client.post("/logout", data={"csrf_token": csrf_a})

    # School B Camera
    csrf_b = register_school_and_admin(client, "SCH_CAM_B", "Camera School B", "cam_b@b.test")
    resp_c_b = client.post("/api/cameras", json={
        "name": "Main Gate B",
        "source": "1",
        "location": "Entrance B",
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_c_b.status_code in (200, 201)
    c_b_id = resp_c_b.get_json()["id"]

    # List cameras in B
    cameras_b = client.get("/api/cameras").get_json()
    assert len(cameras_b) == 1
    assert cameras_b[0]["id"] == c_b_id

    # Cross-tenant camera manipulation returns 404
    assert client.get(f"/api/cameras/{c_a_id}").status_code == 404
    assert client.put(f"/api/cameras/{c_a_id}", json={"name": "Hacked Camera"}, headers={"X-CSRF-Token": csrf_b}).status_code == 404
    assert client.delete(f"/api/cameras/{c_a_id}", headers={"X-CSRF-Token": csrf_b}).status_code == 404


# =========================================================================
# 3. Tenant Data Isolation Tests (HR, Employees, Payroll)
# =========================================================================

def test_tenant_isolation_hr_and_payroll(app_client):
    app, client = app_client

    # School A HR Setup
    csrf_a = register_school_and_admin(client, "SCH_HR_A", "School HR A", "hr_a@a.test")
    
    # Department in A
    resp_d_a = client.post("/api/hr/departments", json={
        "name": "Computer Science",
        "code": "CS",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_d_a.status_code in (200, 201)
    dept_a_id = resp_d_a.get_json()["id"]

    # Designation in A
    resp_des_a = client.post("/api/hr/designations", json={
        "name": "Lecturer",
        "code": "LEC",
        "department_id": dept_a_id,
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_des_a.status_code in (200, 201)
    des_a_id = resp_des_a.get_json()["id"]

    # Employee in A
    resp_emp_a = client.post("/api/hr/employees", json={
        "first_name": "Alice",
        "last_name": "Smith",
        "email": "alice@hra.test",
        "phone": "+91 9876543210",
        "employee_code": "EMP-001",
        "employment_type": "full_time",
        "department_id": dept_a_id,
        "designation_id": des_a_id,
        "joining_date": "2024-01-01",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_emp_a.status_code in (200, 201)
    emp_a_id = resp_emp_a.get_json()["id"]

    client.post("/logout", data={"csrf_token": csrf_a})

    # School B HR Setup (same codes: CS, LEC, EMP-001)
    csrf_b = register_school_and_admin(client, "SCH_HR_B", "School HR B", "hr_b@b.test")
    
    resp_d_b = client.post("/api/hr/departments", json={
        "name": "Civic Studies",
        "code": "CS",
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_d_b.status_code in (200, 201)
    dept_b_id = resp_d_b.get_json()["id"]

    resp_des_b = client.post("/api/hr/designations", json={
        "name": "Lead Educator",
        "code": "LEC",
        "department_id": dept_b_id,
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_des_b.status_code in (200, 201)
    des_b_id = resp_des_b.get_json()["id"]

    resp_emp_b = client.post("/api/hr/employees", json={
        "first_name": "Bob",
        "last_name": "Jones",
        "email": "bob@hrb.test",
        "phone": "+91 9876543211",
        "employee_code": "EMP-001",
        "employment_type": "full_time",
        "department_id": dept_b_id,
        "designation_id": des_b_id,
        "joining_date": "2024-01-01",
    }, headers={"X-CSRF-Token": csrf_b})
    assert resp_emp_b.status_code in (200, 201)
    emp_b_id = resp_emp_b.get_json()["id"]

    # Verify School B list isolation
    employees_b_data = client.get("/api/hr/employees").get_json()
    employees_b = employees_b_data["employees"]
    assert len(employees_b) == 1
    assert employees_b[0]["first_name"] == "Bob"

    # Cross-tenant employee lookup & mutation rejected with 404
    assert client.get(f"/api/hr/employees/{emp_a_id}").status_code == 404
    assert client.put(f"/api/hr/employees/{emp_a_id}", json={"first_name": "Hacked"}, headers={"X-CSRF-Token": csrf_b}).status_code == 404
    assert client.delete(f"/api/hr/employees/{emp_a_id}", headers={"X-CSRF-Token": csrf_b}).status_code == 404


# =========================================================================
# 4. RBAC & User Management API Scoping Tests
# =========================================================================

def test_user_management_api_tenant_scoping(app_client):
    app, client = app_client

    # School A Admin creates two users in School A
    csrf_a = register_school_and_admin(client, "SCH_USR_A", "User School A", "admin@usra.test")

    resp_u1 = client.post("/api/users", json={
        "name": "Teacher User A",
        "email": "teacher1@usra.test",
        "password": "Password123",
        "role": "teacher",
    }, headers={"X-CSRF-Token": csrf_a})
    assert resp_u1.status_code in (200, 201)
    u1_id = resp_u1.get_json()["id"]

    # List users in A
    users_a = client.get("/api/users").get_json()
    assert any(u["email"] == "teacher1@usra.test" for u in users_a)

    client.post("/logout", data={"csrf_token": csrf_a})

    # School B Admin lists users
    csrf_b = register_school_and_admin(client, "SCH_USR_B", "User School B", "admin@usrb.test")
    users_b = client.get("/api/users").get_json()
    # Should only see School B's admin, NOT teacher1@usra.test
    assert not any(u["email"] == "teacher1@usra.test" for u in users_b)

    # School B Admin cannot update or delete School A's user
    assert client.put(f"/api/users/{u1_id}", json={"role": "institution_admin"}, headers={"X-CSRF-Token": csrf_b}).status_code in (403, 404)
    assert client.delete(f"/api/users/{u1_id}", headers={"X-CSRF-Token": csrf_b}).status_code in (403, 404)


def test_rbac_method_enforcement(app_client):
    app, client = app_client

    # Create School
    csrf_admin = register_school_and_admin(client, "SCH_RBAC", "RBAC School", "admin@rbac.test")

    # Create Viewer user in DB directly
    with app.app_context():
        school = School.query.filter_by(school_id="SCH_RBAC").one()
        from argon2 import PasswordHasher
        _ph = PasswordHasher()
        viewer_user = User(
            school_id=school.id,
            email="viewer@rbac.test",
            name="Viewer User",
            password_hash=_ph.hash("ViewerPass123"),
            role="viewer",
            email_verified_at=utcnow(),
            is_active=True,
        )
        db.session.add(viewer_user)
        db.session.commit()

    client.post("/logout", data={"csrf_token": csrf_admin})

    # Log in as Viewer
    login_page = client.get("/login")
    login_resp = client.post("/login", data={
        "csrf_token": form_csrf(login_page),
        "school_id": "SCH_RBAC",
        "email": "viewer@rbac.test",
        "password": "ViewerPass123",
    })
    assert login_resp.status_code == 302
    csrf_viewer = page_csrf(client.get("/dashboard"))

    # Viewer CAN read
    assert client.get("/api/students").status_code == 200
    assert client.get("/api/teachers").status_code == 200

    # Viewer CANNOT write (POST, PUT, DELETE) -> 403 Forbidden
    assert client.post("/api/students", json={
        "roll_no": "999",
        "name": "No Access",
        "class_name": "Class 1",
    }, headers={"X-CSRF-Token": csrf_viewer}).status_code == 403

    assert client.post("/api/cameras", json={
        "name": "No Access",
        "source": "0",
    }, headers={"X-CSRF-Token": csrf_viewer}).status_code == 403

    assert client.post("/api/users", json={
        "name": "No Access",
        "email": "noaccess@test.com",
    }, headers={"X-CSRF-Token": csrf_viewer}).status_code == 403
