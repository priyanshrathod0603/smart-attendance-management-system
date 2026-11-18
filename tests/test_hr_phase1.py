"""Automated Test Suite for AttendanceOS Phase 1 — Advanced Employee Management & Organization Management.

Covers:
- Organization Master CRUD (Cost Centers, Job Levels, Job Grades, Employment Types, Teams)
- Hierarchical Departments & Designations with Level/Grade linking
- Employee 360 Profile creation with automatic initial history & timeline events
- Department transfer workflow & immutable history
- Designation change workflow & immutable history
- Manager change & Circular reporting hierarchy prevention (Loop detection)
- Status transitions (Active, Probation, Resigned, Terminated) and offboarding sync
- Promotion foundation (Designation, Grade, and Salary revision)
- Dependents and Emergency Contacts CRUD with primary contact guarantee
- Digital ID card generation & secure public QR verification endpoint
- Multi-tenant data isolation across all Phase 1 models and services
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest

from app import create_app
from database.db import db
from database.tenant_models import School
from database.auth_models import User
from database.models import Teacher
from hr.models import (
    Department, Designation, Team, JobLevel, JobGrade, CostCenter, EmploymentType,
    Employee, EmployeeEmergencyContact, EmployeeDependent,
    EmployeeDepartmentHistory, EmployeeDesignationHistory,
    EmployeeManagerHistory, EmployeeStatusHistory, EmployeePromotion,
    EmployeeTimelineEvent, EmployeeSalaryStructure
)
from hr.employee_service import (
    create_cost_center, get_cost_centers,
    create_job_level, get_job_levels,
    create_job_grade, get_job_grades,
    create_employment_type, get_employment_types,
    create_team, get_teams,
    create_department, get_departments,
    create_designation, get_designations,
    create_employee, get_employee, update_employee, get_employees,
    transfer_employee_department, get_department_history,
    change_employee_designation, get_designation_history,
    change_employee_manager, get_manager_history, validate_reporting_hierarchy,
    change_employee_status, get_status_history,
    record_employee_promotion, get_employee_promotions,
    create_timeline_event, get_employee_timeline,
    add_emergency_contact, get_emergency_contacts, delete_emergency_contact,
    add_dependent, get_dependents, delete_dependent,
    get_or_create_qr_identifier, verify_qr_identifier, get_employee_id_card_data
)


@pytest.fixture
def app_ctx():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
    })
    with app.test_client() as client:
        with app.app_context():
            db.drop_all()
            db.create_all()
            
            # Setup default school
            s1 = School(school_id="springfield", name="Springfield Academy", code="SPFD", is_active=True)
            s2 = School(school_id="shelbyville", name="Shelbyville High", code="SHBV", is_active=True)
            db.session.add_all([s1, s2])
            db.session.commit()

            yield {
                "app": app,
                "client": client,
                "school1_id": s1.id,
                "school2_id": s2.id
            }
            db.session.remove()
            db.drop_all()


# ============================================================================
# 1. ORGANIZATION MASTERS CRUD
# ============================================================================

def test_cost_centers_crud(app_ctx):
    s1_id = app_ctx["school1_id"]
    cc = create_cost_center({"code": "CC-ACAD", "name": "Academic Division", "description": "Academic budget"}, s1_id)
    assert cc.id is not None
    assert cc.code == "CC-ACAD"
    assert cc.school_id == s1_id

    # Duplicate code in same school should raise ValueError
    with pytest.raises(ValueError, match="already exists"):
        create_cost_center({"code": "CC-ACAD", "name": "Duplicate"}, s1_id)

    # Same code in different school should succeed
    s2_id = app_ctx["school2_id"]
    cc2 = create_cost_center({"code": "CC-ACAD", "name": "Shelbyville Academics"}, s2_id)
    assert cc2.id is not None
    assert cc2.school_id == s2_id

    # List cost centers
    centers = get_cost_centers(s1_id)
    assert len(centers) == 1
    assert centers[0]["code"] == "CC-ACAD"


def test_job_levels_crud(app_ctx):
    s1_id = app_ctx["school1_id"]
    l1 = create_job_level({"code": "L1", "name": "Junior", "rank": 1}, s1_id)
    l2 = create_job_level({"code": "L2", "name": "Mid-Level", "rank": 2}, s1_id)
    l3 = create_job_level({"code": "L3", "name": "Lead / HOD", "rank": 3}, s1_id)

    levels = get_job_levels(s1_id)
    assert len(levels) == 3
    assert [x["code"] for x in levels] == ["L1", "L2", "L3"]
    assert levels[2]["rank"] == 3


def test_job_grades_crud(app_ctx):
    s1_id = app_ctx["school1_id"]
    g1 = create_job_grade({
        "code": "GRADE-A",
        "name": "Executive Grade A",
        "min_salary": Decimal("40000.00"),
        "max_salary": Decimal("70000.00")
    }, s1_id)
    assert g1.id is not None
    assert g1.min_salary == Decimal("40000.00")

    # Invalid salary range (min > max) should raise ValueError
    with pytest.raises(ValueError, match="cannot be greater"):
        create_job_grade({
            "code": "GRADE-INV",
            "name": "Invalid Grade",
            "min_salary": Decimal("80000.00"),
            "max_salary": Decimal("50000.00")
        }, s1_id)


def test_employment_types_and_teams(app_ctx):
    s1_id = app_ctx["school1_id"]
    et = create_employment_type({"code": "visiting", "name": "Visiting Faculty"}, s1_id)
    assert et.id is not None
    assert et.code == "visiting"

    dept = create_department({"code": "SCI", "name": "Science Department"}, s1_id)
    team = create_team({"code": "PHYS-FAC", "name": "Physics Faculty Pod", "department_id": dept.id}, s1_id)
    assert team.id is not None
    assert team.department_id == dept.id
    assert team.code == "PHYS-FAC"


# ============================================================================
# 2. HIERARCHICAL DEPARTMENTS & DESIGNATIONS
# ============================================================================

def test_hierarchical_departments(app_ctx):
    s1_id = app_ctx["school1_id"]
    cc = create_cost_center({"code": "CC-MAIN", "name": "Main Campus Budget"}, s1_id)
    
    parent = create_department({
        "code": "ACAD",
        "name": "Academics",
        "campus": "North Campus",
        "cost_center_id": cc.id
    }, s1_id)

    child = create_department({
        "code": "MATH",
        "name": "Mathematics",
        "parent_department_id": parent.id,
        "campus": "North Campus",
        "cost_center_id": cc.id
    }, s1_id)

    depts = get_departments(s1_id)
    child_dict = next(d for d in depts if d["code"] == "MATH")
    assert child_dict["parent_department_id"] == parent.id
    assert child_dict["parent_dept_name"] == "Academics"
    assert child_dict["cost_center_name"] == "Main Campus Budget"


def test_designation_with_level_and_grade(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "ENG", "name": "English"}, s1_id)
    lvl = create_job_level({"code": "L2", "name": "Senior Teacher", "rank": 2}, s1_id)
    grd = create_job_grade({"code": "GRD-B", "name": "Pay Band B", "min_salary": 45000, "max_salary": 65000}, s1_id)

    desig = create_designation({
        "code": "SR-ENG",
        "name": "Senior English Teacher",
        "department_id": dept.id,
        "job_level_id": lvl.id,
        "job_grade_id": grd.id
    }, s1_id)

    desigs = get_designations(s1_id)
    d_dict = desigs[0]
    assert d_dict["job_level_name"] == "Senior Teacher"
    assert d_dict["job_grade_name"] == "Pay Band B"


# ============================================================================
# 3. EMPLOYEE CREATION, INITIAL HISTORY & TIMELINE
# ============================================================================

def test_employee_creation_and_timeline(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "CS", "name": "Computer Science"}, s1_id)
    desig = create_designation({"code": "PROF", "name": "Professor", "department_id": dept.id}, s1_id)
    team = create_team({"code": "AI-LAB", "name": "AI Research Lab", "department_id": dept.id}, s1_id)
    lvl = create_job_level({"code": "L4", "name": "Principal Researcher", "rank": 4}, s1_id)
    grd = create_job_grade({"code": "GRD-EXEC", "name": "Executive Grade", "min_salary": 90000, "max_salary": 150000}, s1_id)
    cc = create_cost_center({"code": "CC-RES", "name": "Research Budget"}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@lovelace.edu",
        "phone": "9876543210",
        "joining_date": date(2025, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
        "team_id": team.id,
        "job_level_id": lvl.id,
        "job_grade_id": grd.id,
        "cost_center_id": cc.id,
        "campus": "East Campus",
        "employment_type": "full_time",
        "employment_status": "probation",
        "probation_end_date": date(2025, 6, 30),
        "notice_period_days": 60,
        "emergency_contact_name": "Lord Byron",
        "emergency_contact_phone": "9876543211",
        "basic_salary": Decimal("95000.00")
    }, school_id=s1_id)

    assert emp.id is not None
    assert emp.full_name == "Ada Lovelace"
    assert emp.qr_identifier.startswith("EOS-QR-")
    assert emp.team_id == team.id
    assert emp.job_level_id == lvl.id
    assert emp.job_grade_id == grd.id
    assert emp.cost_center_id == cc.id
    assert emp.campus == "East Campus"
    assert emp.notice_period_days == 60

    # Verify initial department history was automatically created
    dept_hist = get_department_history(emp.id, s1_id)
    assert len(dept_hist) == 1
    assert dept_hist[0]["new_department_id"] == dept.id
    assert dept_hist[0]["reason"] == "Initial department assignment on employee onboarding"

    # Verify initial timeline event was created
    timeline = get_employee_timeline(emp.id, s1_id)
    assert len(timeline) >= 1
    event_types = [t["event_type"] for t in timeline]
    assert "employee_created" in event_types


# ============================================================================
# 4. LIFECYCLE WORKFLOWS: TRANSFERS, DESIGNATIONS, PROMOTIONS
# ============================================================================

def test_department_transfer_workflow(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept1 = create_department({"code": "MATH", "name": "Mathematics"}, s1_id)
    dept2 = create_department({"code": "DATA", "name": "Data Science"}, s1_id)
    desig = create_designation({"code": "LEC", "name": "Lecturer", "department_id": dept1.id}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-002",
        "first_name": "Carl",
        "last_name": "Gauss",
        "email": "gauss@math.edu",
        "phone": "9876543212",
        "joining_date": date(2025, 1, 1),
        "department_id": dept1.id,
        "designation_id": desig.id,
    }, school_id=s1_id)

    # Perform department transfer
    hist = transfer_employee_department(
        employee_id=emp.id,
        new_department_id=dept2.id,
        effective_date=date(2025, 3, 1),
        reason="Inter-disciplinary expansion",
        remarks="Approved by Academic Dean",
        changed_by_id=None,
        school_id=s1_id
    )
    assert hist.id is not None
    assert hist.previous_department_id == dept1.id
    assert hist.new_department_id == dept2.id

    # Verify employee current department updated
    updated_emp = get_employee(emp.id, s1_id)
    assert updated_emp.department_id == dept2.id

    # Verify timeline event added
    timeline = get_employee_timeline(emp.id, s1_id)
    event_types = [t["event_type"] for t in timeline]
    assert "department_transfer" in event_types


def test_designation_change_and_promotion(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "PHYS", "name": "Physics"}, s1_id)
    desig1 = create_designation({"code": "ASST", "name": "Assistant Professor", "department_id": dept.id}, s1_id)
    desig2 = create_designation({"code": "ASSOC", "name": "Associate Professor", "department_id": dept.id}, s1_id)
    grd1 = create_job_grade({"code": "G1", "name": "Grade 1", "min_salary": 50000, "max_salary": 70000}, s1_id)
    grd2 = create_job_grade({"code": "G2", "name": "Grade 2", "min_salary": 70000, "max_salary": 95000}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-003",
        "first_name": "Marie",
        "last_name": "Curie",
        "email": "curie@physics.edu",
        "phone": "9876543213",
        "joining_date": date(2025, 1, 1),
        "department_id": dept.id,
        "designation_id": desig1.id,
        "job_grade_id": grd1.id,
        "basic_salary": Decimal("60000.00")
    }, school_id=s1_id)

    # 1. Designation change workflow
    d_hist = change_employee_designation(
        employee_id=emp.id,
        new_designation_id=desig2.id,
        effective_date=date(2025, 4, 1),
        reason="Role reclassification",
        school_id=s1_id
    )
    assert d_hist.previous_designation_id == desig1.id
    assert d_hist.new_designation_id == desig2.id

    # 2. Promotion workflow
    promo = record_employee_promotion(
        employee_id=emp.id,
        new_designation_id=desig2.id,
        new_job_grade_id=grd2.id,
        new_basic_salary=Decimal("85000.00"),
        effective_date=date(2025, 5, 1),
        notes="Nobel prize recognition",
        school_id=s1_id
    )
    assert promo.id is not None
    assert promo.new_basic_salary == Decimal("85000.00")

    # Verify employee reflects new grade and new salary
    updated_emp = get_employee(emp.id, s1_id)
    assert updated_emp.job_grade_id == grd2.id
    
    # Active salary structure should reflect new basic salary
    sal = EmployeeSalaryStructure.query.filter_by(employee_id=emp.id, status="active").first()
    assert sal is not None
    assert sal.basic_salary == Decimal("85000.00")

    # Verify timeline contains promotion
    timeline = get_employee_timeline(emp.id, s1_id)
    event_types = [t["event_type"] for t in timeline]
    assert "promotion" in event_types


# ============================================================================
# 5. CIRCULAR REPORTING HIERARCHY PREVENTION
# ============================================================================

def test_circular_reporting_hierarchy_guard(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "MGMT", "name": "Management"}, s1_id)
    desig = create_designation({"code": "MGR", "name": "Manager", "department_id": dept.id}, s1_id)

    emp_a = create_employee({"employee_code": "EMP-A", "first_name": "Alice", "last_name": "Smith", "email": "alice@school.edu", "phone": "111", "joining_date": date(2025, 1, 1), "department_id": dept.id, "designation_id": desig.id}, school_id=s1_id)
    emp_b = create_employee({"employee_code": "EMP-B", "first_name": "Bob", "last_name": "Jones", "email": "bob@school.edu", "phone": "222", "joining_date": date(2025, 1, 1), "department_id": dept.id, "designation_id": desig.id}, school_id=s1_id)
    emp_c = create_employee({"employee_code": "EMP-C", "first_name": "Charlie", "last_name": "Brown", "email": "charlie@school.edu", "phone": "333", "joining_date": date(2025, 1, 1), "department_id": dept.id, "designation_id": desig.id}, school_id=s1_id)

    # 1. Self reporting should be rejected
    is_valid, msg = validate_reporting_hierarchy(emp_a.id, emp_a.id, s1_id)
    assert is_valid is False
    assert "cannot report to themselves" in msg

    with pytest.raises(ValueError, match="cannot report to themselves"):
        change_employee_manager(emp_a.id, emp_a.id, date(2025, 1, 1), "Self reporting", s1_id)

    # 2. Setup Chain: A reports to B, B reports to C (A -> B -> C)
    change_employee_manager(emp_a.id, emp_b.id, date(2025, 1, 1), "A to B", s1_id)
    change_employee_manager(emp_b.id, emp_c.id, date(2025, 1, 1), "B to C", s1_id)

    # 3. Direct Loop: Trying to make B report to A (B -> A -> B)
    is_valid, msg = validate_reporting_hierarchy(emp_b.id, emp_a.id, s1_id)
    assert is_valid is False
    assert "Circular reporting hierarchy detected" in msg

    # 4. Multi-Level Loop: Trying to make C report to A (C -> A -> B -> C)
    is_valid, msg = validate_reporting_hierarchy(emp_c.id, emp_a.id, s1_id)
    assert is_valid is False
    assert "Circular reporting hierarchy detected" in msg

    with pytest.raises(ValueError, match="Circular reporting hierarchy detected"):
        change_employee_manager(emp_c.id, emp_a.id, date(2025, 1, 1), "Looping C to A", s1_id)


# ============================================================================
# 6. STATUS TRANSITIONS & OFFBOARDING SYNC
# ============================================================================

def test_status_transitions_and_offboarding(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "HR", "name": "Human Resources"}, s1_id)
    desig = create_designation({"code": "OFFICER", "name": "HR Officer", "department_id": dept.id}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-004",
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "hopper@navy.edu",
        "phone": "9876543214",
        "joining_date": date(2025, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
        "employment_status": "active"
    }, school_id=s1_id)
    assert emp.is_active is True

    # Transition to Resigned (Offboarding)
    hist = change_employee_status(
        employee_id=emp.id,
        new_status="resigned",
        effective_date=date(2025, 6, 1),
        exit_date=date(2025, 6, 15),
        exit_reason="Pursuing advanced research",
        remarks="Completed handover",
        school_id=s1_id
    )
    assert hist.previous_status == "active"
    assert hist.new_status == "resigned"

    # Verify employee is_active synced to False and exit details recorded
    updated_emp = get_employee(emp.id, s1_id)
    assert updated_emp.employment_status == "resigned"
    assert updated_emp.is_active is False
    assert updated_emp.exit_date == date(2025, 6, 15)
    assert updated_emp.exit_reason == "Pursuing advanced research"

    # Re-activate employee
    change_employee_status(
        employee_id=emp.id,
        new_status="active",
        effective_date=date(2025, 9, 1),
        reason="Re-joined organization",
        school_id=s1_id
    )
    reactivated = get_employee(emp.id, s1_id)
    assert reactivated.employment_status == "active"
    assert reactivated.is_active is True
    assert reactivated.exit_date is None


# ============================================================================
# 7. DEPENDENTS & EMERGENCY CONTACTS
# ============================================================================

def test_emergency_contacts_and_dependents(app_ctx):
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "BIO", "name": "Biology"}, s1_id)
    desig = create_designation({"code": "DOC", "name": "Researcher", "department_id": dept.id}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-005",
        "first_name": "Rosalind",
        "last_name": "Franklin",
        "email": "franklin@bio.edu",
        "phone": "9876543215",
        "joining_date": date(2025, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
    }, school_id=s1_id)

    # 1. Emergency contacts with primary guarantee
    c1 = add_emergency_contact({
        "contact_name": "Ellis Franklin",
        "relationship": "Father",
        "phone_primary": "9112223334",
        "is_primary": True
    }, emp.id, s1_id)
    assert c1.is_primary is True

    c2 = add_emergency_contact({
        "contact_name": "Muriel Franklin",
        "relationship": "Sister",
        "phone_primary": "9112223335",
        "is_primary": True  # Setting as primary should unset c1 as primary
    }, emp.id, s1_id)
    
    contacts = get_emergency_contacts(emp.id, s1_id)
    assert len(contacts) == 2
    # Ensure c2 is primary, c1 is no longer primary
    c1_refreshed = next(c for c in contacts if c["id"] == c1.id)
    c2_refreshed = next(c for c in contacts if c["id"] == c2.id)
    assert c1_refreshed["is_primary"] is False
    assert c2_refreshed["is_primary"] is True

    # 2. Dependents CRUD
    d1 = add_dependent({
        "full_name": "Little Franklin",
        "relationship": "Child",
        "date_of_birth": date(2020, 5, 10),
        "gender": "male",
        "is_dependent": True
    }, emp.id, s1_id)
    assert d1.id is not None

    deps = get_dependents(emp.id, s1_id)
    assert len(deps) == 1
    assert deps[0]["full_name"] == "Little Franklin"

    delete_dependent(emp.id, d1.id, s1_id)
    assert len(get_dependents(emp.id, s1_id)) == 0


# ============================================================================
# 8. ID CARD DATA & PUBLIC QR VERIFICATION SECURITY
# ============================================================================

def test_id_card_and_public_qr_verification(app_ctx):
    client = app_ctx["client"]
    s1_id = app_ctx["school1_id"]
    dept = create_department({"code": "AERO", "name": "Aerospace"}, s1_id)
    desig = create_designation({"code": "ENG", "name": "Flight Engineer", "department_id": dept.id}, s1_id)

    emp = create_employee({
        "employee_code": "EMP-006",
        "first_name": "Neil",
        "last_name": "Armstrong",
        "email": "armstrong@nasa.edu",
        "phone": "9876543216",
        "campus": "Kennedy Space Center",
        "joining_date": date(2025, 1, 1),
        "department_id": dept.id,
        "designation_id": desig.id,
        "basic_salary": Decimal("120000.00"),
        "emergency_contact_phone": "9876543217"
    }, school_id=s1_id)

    id_card_data = get_employee_id_card_data(emp.id, s1_id)
    assert id_card_data["employee_code"] == "EMP-006"
    assert id_card_data["qr_identifier"].startswith("EOS-QR-")
    qr_token = id_card_data["qr_identifier"]

    # Test Public Verification Endpoint (No auth required)
    resp = client.get(f"/api/hr/verify/{qr_token}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["verified"] is True
    assert data["employee"]["employee_code"] == "EMP-006"
    assert data["employee"]["full_name"] == "Neil Armstrong"
    assert data["employee"]["campus"] == "Kennedy Space Center"
    assert data["employee"]["school_name"] == "Springfield Academy"

    # Verify sensitive data is NOT leaked
    assert "basic_salary" not in data["employee"]
    assert "salary" not in data["employee"]
    assert "bank_account" not in data["employee"]
    assert "pan" not in data["employee"]
    assert "password" not in data["employee"]

    # Test Invalid QR Token
    bad_resp = client.get("/api/hr/verify/EOS-QR-INVALID-TOKEN")
    assert bad_resp.status_code == 404
    bad_data = json.loads(bad_resp.data)
    assert bad_data["verified"] is False


# ============================================================================
# 9. MULTI-TENANT ISOLATION
# ============================================================================

def test_multitenant_isolation_phase1(app_ctx):
    s1_id = app_ctx["school1_id"]
    s2_id = app_ctx["school2_id"]

    dept1 = create_department({"code": "D1", "name": "School 1 Dept"}, s1_id)
    dept2 = create_department({"code": "D2", "name": "School 2 Dept"}, s2_id)
    desig1 = create_designation({"code": "T1", "name": "Teacher S1", "department_id": dept1.id}, s1_id)

    emp1 = create_employee({
        "employee_code": "EMP-S1",
        "first_name": "School1",
        "last_name": "Teacher",
        "email": "s1@school.edu",
        "phone": "1112223331",
        "joining_date": date(2025, 1, 1),
        "department_id": dept1.id,
        "designation_id": desig1.id,
    }, school_id=s1_id)

    # 1. School 2 cannot see School 1 employees
    s2_employees = get_employees(school_id=s2_id)
    assert len(s2_employees["employees"]) == 0

    # 2. School 2 cannot get School 1 employee by ID
    assert get_employee(emp1.id, school_id=s2_id) is None

    # 3. School 2 cannot transfer School 1 employee
    with pytest.raises(ValueError, match="Employee not found"):
        transfer_employee_department(emp1.id, dept2.id, date(2025, 1, 1), "Invalid cross-tenant transfer", None, school_id=s2_id)

    # 4. School 1 cannot transfer employee to School 2 department
    with pytest.raises(ValueError, match="Department not found"):
        transfer_employee_department(emp1.id, dept2.id, date(2025, 1, 1), "Cross-tenant dept transfer", None, school_id=s1_id)
