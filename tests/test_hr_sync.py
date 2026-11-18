"""
Tests for HR-EMPLOYEE-SYNC:
1. Existing teacher sync creates HR Employee.
2. Running sync twice is idempotent and does not create duplicate employees.
3. teacher_id correctly links Teacher -> Employee.
4. Manually edited HR fields are preserved when sync re-runs.
5. Teacher-linked employees can have independent salary structures (T1 vs T2).
6. Non-teaching staff (teacher_id=None) function cleanly.
"""

from datetime import date
import pytest
from app import create_app
from database.db import db
from database.models import Teacher, Attendance
from hr.models import Department, Designation, Employee, EmployeeSalaryStructure
from hr.employee_service import (
    sync_teachers_to_employees, create_employee, update_employee,
    create_salary_structure, get_employee
)
from hr.payroll_service import process_payroll_for_period, create_payroll_period


@pytest.fixture
def app_instance():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-secret"
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_teacher_sync_creates_employees_and_is_idempotent(app_instance):
    # 1. Add 2 Teachers
    t1 = Teacher(teacher_id="T001", name="Rahul Sharma", email="rahul@school.edu", mobile="9876543210", department="Mathematics")
    t2 = Teacher(teacher_id="T002", name="Priya Singh", email="priya@school.edu", mobile="9876543211", department="Science")
    db.session.add_all([t1, t2])
    db.session.commit()

    # 2. Run sync
    res1 = sync_teachers_to_employees()
    assert res1["created"] == 2
    assert res1["already_linked"] == 0

    # Verify employees created
    emp1 = Employee.query.filter_by(teacher_id=t1.id).first()
    emp2 = Employee.query.filter_by(teacher_id=t2.id).first()
    assert emp1 is not None
    assert emp2 is not None
    assert emp1.employee_code == "T001"
    assert emp1.first_name == "Rahul"
    assert emp1.last_name == "Sharma"
    assert emp1.phone == "9876543210"
    assert emp1.email == "rahul@school.edu"

    # 3. Run sync second time (Idempotency test)
    res2 = sync_teachers_to_employees()
    assert res2["created"] == 0
    assert res2["already_linked"] == 2
    assert Employee.query.count() == 2


def test_sync_preserves_manually_edited_hr_fields(app_instance):
    # 1. Create Teacher and sync
    t1 = Teacher(teacher_id="T101", name="Amit Verma", email="amit@school.edu", mobile="9000000000")
    db.session.add(t1)
    db.session.commit()

    sync_teachers_to_employees()
    emp = Employee.query.filter_by(teacher_id=t1.id).first()
    assert emp is not None

    # 2. Manually edit HR phone number
    update_employee(emp.id, {"phone": "9999999999", "address": "123 School St"})

    # 3. Re-run sync
    res = sync_teachers_to_employees()
    assert res["created"] == 0

    # 4. Verify HR edits preserved
    updated_emp = Employee.query.get(emp.id)
    assert updated_emp.phone == "9999999999"
    assert updated_emp.address == "123 School St"


def test_independent_salaries_per_teacher_and_non_teacher(app_instance):
    # 1. Setup Dept and Designation
    dept = Department(name="Academics", code="ACAD")
    desig = Designation(name="Teacher", code="TCH")
    db.session.add_all([dept, desig])
    db.session.commit()

    # 2. Teacher 1 & Teacher 2
    t1 = Teacher(teacher_id="T001", name="Rahul Sharma", email="r@sch.edu")
    t2 = Teacher(teacher_id="T002", name="Priya Singh", email="p@sch.edu")
    db.session.add_all([t1, t2])
    db.session.commit()
    sync_teachers_to_employees()

    emp1 = Employee.query.filter_by(teacher_id=t1.id).first()
    emp2 = Employee.query.filter_by(teacher_id=t2.id).first()

    # 3. Non-Teacher Staff (Accountant)
    emp_staff = create_employee({
        "employee_code": "EMP-ACC-01",
        "first_name": "Suresh",
        "last_name": "Kumar",
        "email": "suresh@school.edu",
        "phone": "9111111111",
        "joining_date": date(2026, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
        "employment_type": "full_time"
    })
    assert emp_staff.teacher_id is None

    # 4. Assign different salary structures
    create_salary_structure(emp1.id, {"basic_salary": 45000, "gross_salary": 45000, "effective_from": date(2026, 1, 1)})
    create_salary_structure(emp2.id, {"basic_salary": 32000, "gross_salary": 32000, "effective_from": date(2026, 1, 1)})
    create_salary_structure(emp_staff.id, {"basic_salary": 25000, "gross_salary": 25000, "effective_from": date(2026, 1, 1)})

    # Verify independent salaries
    ss1 = EmployeeSalaryStructure.query.filter_by(employee_id=emp1.id, status="active").first()
    ss2 = EmployeeSalaryStructure.query.filter_by(employee_id=emp2.id, status="active").first()
    ss_staff = EmployeeSalaryStructure.query.filter_by(employee_id=emp_staff.id, status="active").first()

    assert float(ss1.basic_salary) == 45000.0
    assert float(ss2.basic_salary) == 32000.0
    assert float(ss_staff.basic_salary) == 25000.0
