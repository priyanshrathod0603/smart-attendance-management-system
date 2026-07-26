import os
import json
import re
from datetime import date, datetime, timezone
import pytest

from app import create_app
from database.db import db
from database.models import Student, Teacher, UnknownFace
from database.enterprise_models import (
    TeacherAttendance, AttendanceEvent, TeacherTimeRule, AttendanceLog
)
from attendance_service import record_recognition
from enterprise_query import compute_dashboard_summary, build_report


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


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "expected a CSRF token in the form"
    return match.group(1)


def _authenticate(client):
    signup = client.get("/signup")
    response = client.post("/signup", data={
        "csrf_token": _csrf(signup),
        "name": "Test Administrator",
        "email": "admin@example.test",
        "password": "CorrectHorse1",
    })
    assert response.status_code == 302
    dashboard = client.get("/")
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.get_data(as_text=True)).group(1)


def test_teacher_crud_and_profile(client):
    csrf = _authenticate(client)
    # 1. Create Teacher
    resp = client.post(
        "/api/teachers",
        data=json.dumps({
            "teacher_id": "T-1001",
            "name": "Dr. Sarah Connor",
            "department": "Science",
            "designation": "HOD",
            "subject": "Physics",
            "assigned_classes": "10A, 10B",
            "mobile": "9876543210",
            "email": "sarah@school.com"
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code in (200, 201)
    t_data = resp.get_json()
    assert t_data["teacher_id"] == "T-1001"
    tid = t_data["id"]

    # 2. Get Teacher Profile by PK and by teacher_id string
    prof_resp = client.get(f"/api/teachers/{tid}/profile")
    assert prof_resp.status_code == 200
    prof = prof_resp.get_json()
    assert prof["name"] == "Dr. Sarah Connor"
    assert prof["face_registered"] is False

    prof_by_code = client.get("/api/teachers/T-1001/profile")
    assert prof_by_code.status_code == 200
    assert prof_by_code.get_json()["id"] == tid


def test_teacher_recognition_and_attendance(client):
    # Create Teacher
    t = Teacher(
        teacher_id="T-2002",
        name="Prof. John Doe",
        department="Mathematics",
        designation="Senior Teacher",
        subject="Algebra",
        encoding=[0.1] * 512,
        extra_encodings=[{"angle": "left", "encoding": [0.1] * 512, "photo_path": "known_faces/t2_left.jpg"}]
    )
    db.session.add(t)
    db.session.commit()

    # Record recognition (IN event at 08:30 AM local time)
    morning_time = datetime.now().astimezone().replace(hour=8, minute=30, second=0, microsecond=0)
    rec_in = record_recognition("teacher", t.id, camera_id=1, confidence=0.92, now=morning_time)
    assert rec_in["action"] == "inserted"
    assert rec_in["event"] == "in"

    # Verify TeacherAttendance record
    att = TeacherAttendance.query.filter_by(teacher_id=t.id).first()
    assert att is not None
    assert att.status in ("present", "late")

    # Verify AttendanceEvent record
    evt = AttendanceEvent.query.filter_by(attendance_type="teacher", attendance_id=att.id).first()
    assert evt is not None
    assert evt.event_type == "in"
    assert evt.confidence == 0.92

    # Verify AttendanceLog record
    log = AttendanceLog.query.filter_by(entity_type="teacher_attendance", entity_id=att.id).first()
    assert log is not None
    assert log.field == "in_time"


def test_dashboard_summary_and_reports(client):
    # Create Student & Teacher
    s = Student(roll_no="S-001", name="Alice", class_name="Class 5", section="A")
    t = Teacher(teacher_id="T-3003", name="Mary Jane", department="English")
    db.session.add_all([s, t])
    db.session.commit()

    morning_time = datetime.now().astimezone().replace(hour=8, minute=30, second=0, microsecond=0)
    record_recognition("teacher", t.id, camera_id=1, confidence=0.95, now=morning_time)

    summary = compute_dashboard_summary(d=morning_time.date())
    assert summary["total_teachers"] == 1
    assert summary["teachers_present"] == 1
    assert summary["teachers_absent"] == 0
    assert "recognition_accuracy" in summary

    # Build report
    rows, headers, title = build_report("daily", d=morning_time.date())
    assert any(r.get("employee_id") == "T-3003" for r in rows)
