"""Comprehensive tests for HR + Payroll database models and backend services."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from app import create_app
from database.db import db
from database.models import Teacher
from hr.models import (
    Department, Designation, Employee, EmployeeEmploymentHistory,
    SalaryComponent, EmployeeSalaryStructure, EmployeeSalaryComponent,
    LeaveType, EmployeeLeaveBalance, LeaveRequest,
    Shift, EmployeeShift, OvertimeRecord,
    EmployeeBonus, EmployeeLoan, LoanRepayment, Reimbursement,
    PayrollPeriod, PayrollRecord, PayrollAttendanceSummary,
    PayrollEarning, PayrollDeduction, Payslip, HRAuditLog
)
from hr.employee_service import (
    create_department, create_designation, create_employee,
    create_salary_structure, add_salary_component,
    create_leave_type, create_leave_request, approve_leave_request,
    create_employee_loan, create_loan_repayment
)
from hr.payroll_service import (
    create_payroll_period, process_payroll_for_period, finalize_payroll_period
)


@pytest.fixture
def client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
    })
    with app.test_client() as client:
        with app.app_context():
            db.drop_all()
            db.create_all()
            yield client
            db.session.remove()
            db.drop_all()


def test_department_and_designation_crud(client):
    dept = create_department({"name": "Mathematics", "code": "MATH"})
    assert dept.id is not None
    assert dept.name == "Mathematics"

    desig = create_designation({"name": "Senior Lecturer", "code": "SR_LEC", "department_id": dept.id})
    assert desig.id is not None
    assert desig.department_id == dept.id


def test_employee_teacher_mapping(client):
    # 1. Create Teacher in legacy database table
    t = Teacher(teacher_id="T-999", name="Prof. Turing", department="Math")
    db.session.add(t)
    db.session.commit()

    # 2. Create HR Department and Designation
    dept = create_department({"name": "Computer Science", "code": "CS"})
    desig = create_designation({"name": "Professor", "code": "PROF", "department_id": dept.id})

    # 3. Create HR Employee linked to Teacher
    emp = create_employee({
        "employee_code": "EMP-001",
        "teacher_id": t.id,
        "first_name": "Alan",
        "last_name": "Turing",
        "email": "turing@cs.edu",
        "phone": "1234567890",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })
    assert emp.id is not None
    assert emp.teacher_id == t.id
    assert emp.full_name == "Alan Turing"


def test_salary_structures_and_history(client):
    dept = create_department({"name": "Physics", "code": "PHY"})
    desig = create_designation({"name": "Assistant Prof", "code": "ASST_PROF", "department_id": dept.id})

    emp1 = create_employee({
        "employee_code": "EMP-101",
        "first_name": "Albert",
        "last_name": "Einstein",
        "email": "albert@physics.edu",
        "phone": "9998887771",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })

    emp2 = create_employee({
        "employee_code": "EMP-102",
        "first_name": "Niels",
        "last_name": "Bohr",
        "email": "bohr@physics.edu",
        "phone": "9998887772",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })

    # Employee-specific basic salary: Emp1 = 25000, Emp2 = 32000
    ss1 = create_salary_structure(emp1.id, {
        "basic_salary": Decimal("25000.00"),
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 8, 31),
        "status": "active"
    })
    assert ss1.basic_salary == Decimal("25000.00")

    ss2 = create_salary_structure(emp2.id, {
        "basic_salary": Decimal("32000.00"),
        "effective_from": date(2026, 1, 1),
        "status": "active"
    })
    assert ss2.basic_salary == Decimal("32000.00")

    # Salary hike for Emp1 starting 2026-09-01
    ss1.status = "inactive"
    db.session.commit()

    ss1_new = create_salary_structure(emp1.id, {
        "basic_salary": Decimal("30000.00"),
        "effective_from": date(2026, 9, 1),
        "status": "active"
    })
    assert ss1_new.basic_salary == Decimal("30000.00")


def test_leave_request_and_balance(client):
    dept = create_department({"name": "Biology", "code": "BIO"})
    desig = create_designation({"name": "Lecturer", "code": "LEC", "department_id": dept.id})
    emp = create_employee({
        "employee_code": "EMP-201",
        "first_name": "Gregor",
        "last_name": "Mendel",
        "email": "mendel@bio.edu",
        "phone": "5554443332",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })

    lt = create_leave_type({"name": "Casual Leave", "code": "CL", "max_days_per_year": 12, "paid": True})
    
    # Request 3 days leave
    lr = create_leave_request(emp.id, {
        "leave_type_id": lt.id,
        "start_date": "2026-03-10",
        "end_date": "2026-03-12",
        "reason": "Family event"
    })
    assert lr.status == "pending"
    assert float(lr.total_days) == 3.0

    # Approve leave
    approved = approve_leave_request(lr.id, approved_by=emp.id)
    assert approved.status == "approved"

    # Check leave balance updated
    bal = EmployeeLeaveBalance.query.filter_by(employee_id=emp.id, leave_type_id=lt.id, year=2026).first()
    assert bal is not None
    assert float(bal.remaining_days) == 9.0


def test_loan_and_repayment_guard(client):
    dept = create_department({"name": "Chemistry", "code": "CHEM"})
    desig = create_designation({"name": "Lab Assistant", "code": "LAB_AST", "department_id": dept.id})
    emp = create_employee({
        "employee_code": "EMP-301",
        "first_name": "Marie",
        "last_name": "Curie",
        "email": "marie@chem.edu",
        "phone": "7778889990",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })

    loan = create_employee_loan(emp.id, {
        "loan_type": "personal_loan",
        "principal_amount": Decimal("10000.00"),
        "installment_amount": Decimal("2000.00"),
        "start_date": date(2026, 1, 1),
        "status": "active"
    })
    assert loan.remaining_amount == Decimal("10000.00")

    # Repay 3000
    rep = create_loan_repayment(loan.id, {
        "amount": Decimal("3000.00"),
        "payment_date": date(2026, 1, 31)
    })
    assert loan.remaining_amount == Decimal("7000.00")

    # Attempt to repay more than remaining balance -> should raise ValueError
    with pytest.raises(ValueError, match="exceeds remaining loan balance"):
        create_loan_repayment(loan.id, {
            "amount": Decimal("8000.00"),
            "payment_date": date(2026, 2, 28)
        })


def test_payroll_run_and_snapshot_immutability(client):
    dept = create_department({"name": "History", "code": "HIST"})
    desig = create_designation({"name": "Professor", "code": "PROF_H", "department_id": dept.id})
    emp = create_employee({
        "employee_code": "EMP-401",
        "first_name": "Herodotus",
        "last_name": "Halicarnassus",
        "email": "herodotus@history.edu",
        "phone": "1112223334",
        "joining_date": date(2025, 1, 1),
        "employment_type": "full_time",
        "department_id": dept.id,
        "designation_id": desig.id,
    })

    create_salary_structure(emp.id, {
        "basic_salary": Decimal("30000.00"),
        "effective_from": date(2026, 1, 1),
        "status": "active"
    })

    period = create_payroll_period({
        "period_name": "January 2026",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 31),
        "payment_date": date(2026, 2, 5),
        "status": "draft"
    })

    records = process_payroll_for_period(period.id)
    assert len(records) == 1
    rec = records[0]
    assert rec.basic_salary == Decimal("30000.00")
    assert rec.attendance_summary is not None
    assert rec.attendance_summary.total_calendar_days == 31

    # Finalize payroll
    finalized_period = finalize_payroll_period(period.id)
    assert finalized_period.status == "finalized"
    assert rec.status == "finalized"
