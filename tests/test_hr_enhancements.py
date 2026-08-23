import datetime
import secrets
import pytest
from argon2 import PasswordHasher
from app import create_app
from database.db import db
from database.auth_models import User, AuthSession, utcnow
from auth import _hash
from hr.models import Department, Designation, Employee, EmployeeSalaryStructure, Shift
from hr.employee_service import create_employee, create_salary_structure, get_employee_salary_history


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
        # Seed user and session
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
            expires_at=utcnow() + datetime.timedelta(hours=12),
        )
        db.session.add(session_rec)

        # Seed test dept and desig
        dept = Department(name="Academics", code="ACAD", status="active")
        db.session.add(dept)
        db.session.flush()
        desig = Designation(name="Teacher", code="TCH", department_id=dept.id, status="active")
        db.session.add(desig)
        db.session.commit()

        client = app.test_client()
        # Set session cookie
        with client.session_transaction() as sess:
            sess["sid"] = raw_sid

        yield app, client, dept, desig
        db.session.remove()
        db.drop_all()


def test_employee_creation_with_salary(app_and_client):
    app, client, dept, desig = app_and_client
    headers = {"X-CSRF-Token": "test-csrf-token"}
    payload = {
        "employee_code": "EMP-9001",
        "first_name": "Rohan",
        "last_name": "Sharma",
        "email": "rohan.sharma@school.edu",
        "phone": "9898989898",
        "joining_date": "2026-01-15",
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
        "basic_salary": 45000.0,
        "overtime_rate": 250.0,
        "pay_frequency": "monthly"
    }
    res = client.post("/api/hr/employees", json=payload, headers=headers)
    assert res.status_code == 201
    emp_data = res.get_json()
    emp_id = emp_data["id"]

    # Verify active salary structure
    sal_res = client.get(f"/api/hr/employees/{emp_id}/salary-structure", headers=headers)
    assert sal_res.status_code == 200
    sal_data = sal_res.get_json()
    assert sal_data["basic_salary"] == 45000.0
    assert sal_data["overtime_rate"] == 250.0
    assert sal_data["status"] == "active"


def test_salary_revision_and_history(app_and_client):
    app, client, dept, desig = app_and_client
    headers = {"X-CSRF-Token": "test-csrf-token"}
    # Create employee
    emp_payload = {
        "employee_code": "EMP-9002",
        "first_name": "Pooja",
        "last_name": "Patel",
        "email": "pooja.patel@school.edu",
        "phone": "9898989897",
        "joining_date": "2025-01-01",
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
        "basic_salary": 30000.0
    }
    emp_res = client.post("/api/hr/employees", json=emp_payload, headers=headers)
    assert emp_res.status_code == 201
    emp_id = emp_res.get_json()["id"]

    # Revise salary
    rev_payload = {
        "basic_salary": 38000.0,
        "effective_from": "2026-01-01",
        "overtime_rate": 200.0,
        "pay_frequency": "monthly",
        "status": "active"
    }
    rev_res = client.post(f"/api/hr/employees/{emp_id}/salary-structure", json=rev_payload, headers=headers)
    assert rev_res.status_code == 201

    # Check history endpoint
    hist_res = client.get(f"/api/hr/employees/{emp_id}/salary-history", headers=headers)
    assert hist_res.status_code == 200
    history = hist_res.get_json()
    assert len(history) == 2
    # Verify latest is first
    assert history[0]["basic_salary"] == 38000.0
    assert history[0]["status"] == "active"
    assert history[1]["basic_salary"] == 30000.0
    assert history[1]["status"] == "inactive"
    assert history[1]["effective_to"] is not None


def test_shifts_api_crud(app_and_client):
    app, client, dept, desig = app_and_client
    headers = {"X-CSRF-Token": "test-csrf-token"}
    # Create shift
    shift_payload = {
        "name": "Morning Shift",
        "code": "MORN",
        "start_time": "08:00",
        "end_time": "16:00",
        "grace_minutes": 10,
        "is_overnight": False
    }
    create_res = client.post("/api/hr/shifts", json=shift_payload, headers=headers)
    assert create_res.status_code == 201
    shift_id = create_res.get_json()["id"]

    # List shifts
    list_res = client.get("/api/hr/shifts", headers=headers)
    assert list_res.status_code == 200
    shifts = list_res.get_json()
    assert len(shifts) >= 1
    assert any(s["code"] == "MORN" for s in shifts)

    # Get shift detail
    detail_res = client.get(f"/api/hr/shifts/{shift_id}", headers=headers)
    assert detail_res.status_code == 200
    assert detail_res.get_json()["name"] == "Morning Shift"

    # Update shift
    update_res = client.put(f"/api/hr/shifts/{shift_id}", json={"grace_minutes": 20}, headers=headers)
    assert update_res.status_code == 200
    assert update_res.get_json()["grace_minutes"] == 20
