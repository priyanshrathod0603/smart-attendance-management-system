"""Automated regression test suite for Attendance Time-Rule resolution hierarchy.

Verifies:
1. Prefer an applicable rule belonging to the teacher's school_id.
2. If no school-specific rule exists, fall back to a global rule where school_id IS NULL.
3. Never silently override an existing school-specific rule with a global rule.
4. Database pollution prevention: querying fallback rule does not create redundant school rows.
5. Absence of any matching rule (neither school nor global) instantiates and persists a documented default rule.
6. Shift timing, timezone handling, IN/OUT state machine, deduplication, and late/half-day flags are preserved.
7. Verification across both teacher and student time rules.
"""
from datetime import datetime, timezone
import pytest

from app import create_app
from database.db import db
from database.models import Teacher, Student
from database.enterprise_models import (
    TeacherTimeRule,
    StudentTimeRule,
    TeacherAttendance,
    StudentAttendance,
    AttendanceEvent,
)
from attendance_service import _rules_for, record_recognition


@pytest.fixture
def app_and_client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "AUTH_SESSION_HOURS": 12,
        "AUTH_VERIFICATION_HOURS": 24,
    })
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_school_specific_rule_preferred_over_global(app_and_client):
    """When a school-specific rule exists, it must be preferred over the global rule."""
    # Global rule: starts 08:00, late at 08:30
    global_rule = TeacherTimeRule(
        school_id=None,
        name="Global Default Rule",
        office_start="08:00",
        late_time="08:30",
        half_day_time="09:30",
    )
    # School 101 specific rule: starts 09:00, late at 09:30
    school_101_rule = TeacherTimeRule(
        school_id=101,
        name="School 101 Shift",
        office_start="09:00",
        late_time="09:30",
        half_day_time="10:30",
    )
    db.session.add_all([global_rule, school_101_rule])
    db.session.commit()

    # Query for school 101
    resolved = _rules_for("teacher", school_id=101)
    assert resolved.id == school_101_rule.id
    assert resolved.school_id == 101
    assert resolved.office_start == "09:00"
    assert resolved.late_time == "09:30"


def test_global_fallback_when_no_school_specific_rule(app_and_client):
    """When no school-specific rule exists, must fall back to global rule (school_id IS NULL) without creating a duplicate."""
    # Global rule only
    global_rule = TeacherTimeRule(
        school_id=None,
        name="Global Default Rule",
        office_start="07:30",
        late_time="08:00",
        half_day_time="09:00",
    )
    db.session.add(global_rule)
    db.session.commit()

    initial_rule_count = TeacherTimeRule.query.count()
    assert initial_rule_count == 1

    # Query for school 202 which has NO rule
    resolved = _rules_for("teacher", school_id=202)
    assert resolved.id == global_rule.id
    assert resolved.school_id is None
    assert resolved.office_start == "07:30"
    assert resolved.late_time == "08:00"

    # Verify NO new row was created or polluted in teacher_time_rules
    assert TeacherTimeRule.query.count() == initial_rule_count


def test_never_override_school_specific_with_global(app_and_client):
    """An existing school-specific rule must never be overridden or ignored in presence of multiple rules."""
    # Global rule
    global_rule = TeacherTimeRule(
        school_id=None,
        name="Global Rule",
        office_start="08:00",
        late_time="08:30",
    )
    # School 1 rule
    school_1_rule = TeacherTimeRule(
        school_id=1,
        name="School 1 Custom Rule",
        office_start="07:00",
        late_time="07:30",
    )
    # School 2 rule
    school_2_rule = TeacherTimeRule(
        school_id=2,
        name="School 2 Custom Rule",
        office_start="09:30",
        late_time="10:00",
    )
    db.session.add_all([global_rule, school_1_rule, school_2_rule])
    db.session.commit()

    assert _rules_for("teacher", school_id=1).office_start == "07:00"
    assert _rules_for("teacher", school_id=2).office_start == "09:30"
    assert _rules_for("teacher", school_id=None).office_start == "08:00"
    # An unconfigured school 999 must fall back to global 08:00
    assert _rules_for("teacher", school_id=999).office_start == "08:00"


def test_absence_of_any_matching_rule_persists_default(app_and_client):
    """When the table is completely empty, it instantiates and flushes a documented default rule."""
    # Ensure table is empty
    TeacherTimeRule.query.delete()
    db.session.commit()
    assert TeacherTimeRule.query.count() == 0

    resolved = _rules_for("teacher", school_id=505)
    assert resolved is not None
    assert resolved.id is not None
    assert resolved.school_id == 505
    assert resolved.office_start == "08:00"
    assert resolved.late_time == "08:30"
    assert resolved.min_working_minutes == 480
    assert TeacherTimeRule.query.count() == 1


def test_live_attendance_evaluates_with_school_specific_rule(app_and_client):
    """End-to-end attendance evaluation honours school-specific shift hours."""
    # Global rule: starts 07:30, late at 08:00
    global_rule = TeacherTimeRule(
        school_id=None,
        name="Global",
        office_start="07:30",
        late_time="08:00",
    )
    # School 10 rule: starts 07:30, late at 09:00
    school_10_rule = TeacherTimeRule(
        school_id=10,
        name="School 10 Shift",
        office_start="07:30",
        late_time="09:00",
    )
    db.session.add_all([global_rule, school_10_rule])

    # Teacher belonging to School 10
    teacher = Teacher(
        name="Alice Walker",
        teacher_id="T1001",
        school_id=10,
        attendance_method="FACE",
        is_active=True,
    )
    # Teacher belonging to School 20 (no custom rule -> global fallback)
    teacher_global = Teacher(
        name="Bob Smith",
        teacher_id="T2001",
        school_id=20,
        attendance_method="FACE",
        is_active=True,
    )
    db.session.add_all([teacher, teacher_global])
    db.session.commit()

    # Time of punch: 08:30 AM local time
    # For School 10 (late_time 09:00): 08:30 is on-time (status="present", is_late=False)
    # For School 20 (global late_time 08:00): 08:30 is late (status="late", is_late=True)
    punch_time = datetime.now().astimezone().replace(hour=8, minute=30, second=0, microsecond=0)

    # 1. School 10 teacher punches
    res_10 = record_recognition("teacher", teacher.id, now=punch_time, school_id=10)
    assert res_10["action"] == "inserted"
    assert res_10["status"] == "present"

    att_10 = TeacherAttendance.query.filter_by(teacher_id=teacher.id).first()
    assert att_10.status == "present"
    assert att_10.is_late is False

    # 2. School 20 teacher punches at the exact same minute
    res_20 = record_recognition("teacher", teacher_global.id, now=punch_time, school_id=20)
    assert res_20["action"] == "inserted"
    assert res_20["status"] == "late"

    att_20 = TeacherAttendance.query.filter_by(teacher_id=teacher_global.id).first()
    assert att_20.status == "late"
    assert att_20.is_late is True


def test_in_out_workflow_and_deduplication_preserved(app_and_client):
    """Verifies that IN/OUT boundaries, duplicate rejection, and state transitions remain intact."""
    rule = TeacherTimeRule(
        school_id=None,
        office_start="08:00",
        late_time="08:30",
        out_detection_start="16:00",
        office_end="17:00",
        early_exit_time="16:30",
        min_working_minutes=480,
    )
    teacher = Teacher(
        name="Charlie Brown",
        teacher_id="T3001",
        attendance_method="FACE",
        is_active=True,
    )
    db.session.add_all([rule, teacher])
    db.session.commit()

    # 1. Reject punch before office_start (07:45 < 08:00)
    early_time = datetime.now().astimezone().replace(hour=7, minute=45, second=0, microsecond=0)
    res_early = record_recognition("teacher", teacher.id, now=early_time)
    assert res_early["action"] == "rejected"
    assert res_early["reason"] == "before in start"

    # 2. Valid IN punch at 08:15 (present)
    in_time = datetime.now().astimezone().replace(hour=8, minute=15, second=0, microsecond=0)
    res_in = record_recognition("teacher", teacher.id, now=in_time)
    assert res_in["action"] == "inserted"
    assert res_in["status"] == "present"

    # 3. Deduplication: second punch during the day before out_detection_start (10:00 < 16:00)
    mid_time = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    res_mid = record_recognition("teacher", teacher.id, now=mid_time)
    assert res_mid["action"] == "ignored"
    assert res_mid["reason"] == "before out detection start"

    # 4. Valid OUT punch at 16:45 (after 16:00 and after early_exit 16:30)
    out_time = datetime.now().astimezone().replace(hour=16, minute=45, second=0, microsecond=0)
    res_out = record_recognition("teacher", teacher.id, now=out_time)
    assert res_out["action"] == "updated"
    assert res_out["event"] == "out"

    # 5. Subsequent punch after OUT already complete is rejected
    after_time = datetime.now().astimezone().replace(hour=17, minute=15, second=0, microsecond=0)
    res_after = record_recognition("teacher", teacher.id, now=after_time)
    assert res_after["action"] == "rejected"
    assert res_after["reason"] == "daily attendance complete"


def test_student_time_rules_resolution_parity(app_and_client):
    """Verifies that StudentTimeRule follows identical school-specific vs global fallback logic."""
    global_student_rule = StudentTimeRule(
        school_id=None,
        name="Global Student Rule",
        office_start="07:45",
        late_time="08:15",
    )
    school_student_rule = StudentTimeRule(
        school_id=42,
        name="School 42 Student Rule",
        office_start="08:15",
        late_time="08:45",
    )
    db.session.add_all([global_student_rule, school_student_rule])
    db.session.commit()

    # School 42 gets school-specific
    res_42 = _rules_for("student", school_id=42)
    assert res_42.id == school_student_rule.id
    assert res_42.office_start == "08:15"

    # School 99 falls back to global
    res_99 = _rules_for("student", school_id=99)
    assert res_99.id == global_student_rule.id
    assert res_99.office_start == "07:45"


def test_real_event_processing_and_database_persistence(app_and_client):
    """End-to-end event reception: extracts employee ID, maps to teacher, invokes attendance engine, and commits all audit rows."""
    from database.enterprise_models import FingerprintDeviceAudit, AttendanceLog
    from biometrics.fingerprint_service import verify_and_record_attendance

    rule = TeacherTimeRule(
        school_id=None,
        office_start="08:00",
        late_time="08:30",
        out_detection_start="16:00",
        office_end="17:00",
    )
    teacher = Teacher(
        name="Diana Prince",
        teacher_id="T4001",
        fingerprint_user_id="DEV_USER_4001",
        attendance_method="FINGERPRINT",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add_all([rule, teacher])
    db.session.commit()

    punch_time = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)
    result = verify_and_record_attendance(
        device_user_id="DEV_USER_4001",
        device_id="BIOMAX_INDIGO_01",
        event_time=punch_time,
    )

    # Verify returned outcome
    assert result["action"] == "inserted"
    assert result["event"] == "in"
    assert result["status"] == "present"
    assert result["teacher_id"] == "T4001"

    # Verify TeacherAttendance committed
    att = TeacherAttendance.query.filter_by(teacher_id=teacher.id).first()
    assert att is not None
    assert att.status == "present"
    assert att.is_late is False
    assert att.in_time is not None

    # Verify AttendanceEvent committed
    event = AttendanceEvent.query.filter_by(attendance_id=att.id, attendance_type="teacher").first()
    assert event is not None
    assert event.auth_method == "FINGERPRINT"
    assert event.device_id == "BIOMAX_INDIGO_01"
    assert event.event_type == "in"

    # Verify AttendanceLog committed
    log = AttendanceLog.query.filter_by(entity_id=att.id, entity_type="teacher_attendance").first()
    assert log is not None
    assert log.field == "in_time"
    assert "FINGERPRINT" in log.reason

    # Verify FingerprintDeviceAudit committed
    audit = FingerprintDeviceAudit.query.filter_by(teacher_id="T4001").first()
    assert audit is not None
    assert audit.event_type == "verify"
    assert audit.result == "inserted"


def test_unknown_employee_id_rejected_and_audit_logged(app_and_client):
    """An event for an unknown device employee ID must be safely rejected with audit trail and 0 attendance records."""
    from database.enterprise_models import FingerprintDeviceAudit
    from biometrics.fingerprint_service import verify_and_record_attendance

    TeacherAttendance.query.delete()
    AttendanceEvent.query.delete()
    db.session.commit()

    result = verify_and_record_attendance(
        device_user_id="UNKNOWN_99999",
        device_id="BIOMAX_INDIGO_01",
    )
    assert result["action"] == "rejected"
    assert result["reason"] == "unknown teacher id"

    # Ensure zero attendance records created
    assert TeacherAttendance.query.count() == 0
    assert AttendanceEvent.query.count() == 0

    # Ensure audit log recorded the security event
    audit = FingerprintDeviceAudit.query.filter_by(teacher_id="UNKNOWN_99999").first()
    assert audit is not None
    assert audit.result == "rejected"
    assert audit.failure_reason == "unknown_teacher_id"


def test_duplicate_event_rejection_and_state_machine_flow(app_and_client):
    """Multiple punches throughout the day are properly filtered by the state machine with zero duplicate rows."""
    from biometrics.fingerprint_service import verify_and_record_attendance

    rule = TeacherTimeRule(
        school_id=None,
        office_start="08:00",
        late_time="08:30",
        out_detection_start="16:00",
        office_end="17:00",
        early_exit_time="16:30",
        min_working_minutes=480,
    )
    teacher = Teacher(
        name="Evan Wright",
        teacher_id="T5001",
        fingerprint_user_id="DEV_5001",
        attendance_method="FINGERPRINT",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add_all([rule, teacher])
    db.session.commit()

    # 1. First valid IN punch
    in_time = datetime.now().astimezone().replace(hour=8, minute=10, second=0, microsecond=0)
    res1 = verify_and_record_attendance("DEV_5001", event_time=in_time)
    assert res1["action"] == "inserted"
    assert res1["event"] == "in"

    # 2. Duplicate punch 15 minutes later before OUT window
    dup_time = datetime.now().astimezone().replace(hour=8, minute=25, second=0, microsecond=0)
    res2 = verify_and_record_attendance("DEV_5001", event_time=dup_time)
    assert res2["action"] == "ignored"
    assert res2["reason"] == "before out detection start"
    # Still exactly 1 attendance row
    assert TeacherAttendance.query.filter_by(teacher_id=teacher.id).count() == 1

    # 3. Valid OUT punch at 16:45
    out_time = datetime.now().astimezone().replace(hour=16, minute=45, second=0, microsecond=0)
    res3 = verify_and_record_attendance("DEV_5001", event_time=out_time)
    assert res3["action"] == "updated"
    assert res3["event"] == "out"

    # 4. Subsequent punch after day is completed
    after_time = datetime.now().astimezone().replace(hour=17, minute=0, second=0, microsecond=0)
    res4 = verify_and_record_attendance("DEV_5001", event_time=after_time)
    assert res4["action"] == "rejected"
    assert res4["reason"] == "daily attendance complete"

    # Total events: exactly 2 events (1 IN, 1 OUT)
    events = AttendanceEvent.query.filter_by(attendance_type="teacher").all()
    assert len(events) == 2
    assert {e.event_type for e in events} == {"in", "out"}

