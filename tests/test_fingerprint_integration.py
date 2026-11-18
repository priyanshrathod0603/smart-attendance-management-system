"""Automated software test suite for the Team Office Z305 adapter boundary.

Verifies:
1. Multi-modal Attendance Method configuration (FACE vs FINGERPRINT).
2. Identity Bridge between BioMax Device User ID and AttendanceOS Teacher ID.
3. Common Attendance Engine processing with 100% reused Time Rules and IN/OUT state machine.
4. Rejection of cross-method attempts (Face teacher using Fingerprint; Fingerprint teacher using Face).
5. Unknown / unmapped Teacher ID rejection and safe audit logging.
6. Inactive teacher and unenrolled fingerprint rejection.
7. Deduplication and cooldown behavior.
8. Single source of truth for attendance records and events.
9. Device adapter resilience, latency measurement, and socket error safety.
10. Device Management endpoints & RBAC enforcement (Admin/Principal permitted, Teacher forbidden).
11. Diagnostic Fingerprint Verification Test (NEVER creates attendance records).
12. Uniqueness of Teacher <-> Device User ID mappings and duplicate prevention.
"""
import json
import hashlib
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from database.db import db
from database.models import Teacher, Student
from database.enterprise_models import (
    TeacherAttendance,
    AttendanceEvent,
    AttendanceLog,
    TeacherTimeRule,
    FingerprintDevice,
    FingerprintDeviceAudit,
    FingerprintEventReceipt,
)
from attendance_service import record_recognition, process_attendance_identity
from biometrics.fingerprint_adapter import (
    BioMaxIndigoAdapter,
    MockFingerprintAdapter,
    set_fingerprint_adapter,
    get_fingerprint_adapter,
)
from biometrics.fingerprint_service import (
    verify_and_record_attendance,
    enroll_teacher_fingerprint,
    delete_teacher_fingerprint,
    get_fingerprint_device_status,
    get_fingerprint_audit_logs,
    save_fingerprint_device_config,
    save_teacher_fingerprint_mapping,
    test_fingerprint_verification as service_test_fingerprint_verification,
)


@pytest.fixture
def app_and_client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "AUTH_SESSION_HOURS": 12,
        "AUTH_VERIFICATION_HOURS": 24,
    })
    # Install test mock adapter
    mock_adapter = MockFingerprintAdapter(device_id="TEST_BIOMAX_INDIGO")
    set_fingerprint_adapter(mock_adapter)

    with app.test_client() as client:
        with app.app_context():
            db.drop_all()
            db.create_all()
            yield app, client, mock_adapter
            db.session.remove()
            db.drop_all()


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "expected a CSRF token in the form"
    return match.group(1)


def _authenticate(client, role: str = "super_admin", email: str = "admin@biomax.test"):
    import secrets
    from argon2 import PasswordHasher
    from database.auth_models import User, AuthSession, utcnow
    from auth import _hash
    from database.db import db as _db

    ph = PasswordHasher()
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            email=email,
            name=f"{role.title()} User",
            password_hash=ph.hash("SecurePassword123!"),
            role=role,
            email_verified_at=utcnow(),
            is_active=True,
        )
        _db.session.add(user)
        _db.session.flush()

    raw_sid = secrets.token_urlsafe(48)
    session_rec = AuthSession(
        token_hash=_hash(raw_sid),
        user=user,
        csrf_secret="test-csrf-token",
        device="pytest",
        ip_address="127.0.0.1",
        expires_at=utcnow() + timedelta(hours=12),
    )
    _db.session.add(session_rec)
    _db.session.commit()

    with client.session_transaction() as sess:
        sess["sid"] = raw_sid

    return "test-csrf-token"


def _register_webhook_device(device_id="Z305_TEST", token="test-device-token-0123456789-abcdefghijk"):
    device = FingerprintDevice(
        school_id=1,
        device_id=device_id,
        name="Test device",
        model="Test fixture only",
        connection_type="MOCK",
        webhook_token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        is_active=True,
    )
    db.session.add(device)
    db.session.commit()
    return device, token


def _webhook_payload(device_id="Z305_TEST", user_id="0007", timestamp=None, event_id=None):
    payload = {
        "device_id": device_id,
        "user_id": user_id,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "verify_type": "FINGERPRINT",
    }
    if event_id is not None:
        payload["event_id"] = event_id
    return payload


def _create_webhook_teacher(user_id="0007", active=True, school_id=1):
    teacher = Teacher(
        school_id=school_id,
        teacher_id=f"EMP-{user_id}",
        name="Webhook Test Teacher",
        attendance_method="FINGERPRINT",
        fingerprint_user_id=user_id,
        fingerprint_enrolled=True,
        is_active=active,
    )
    db.session.add(teacher)
    db.session.commit()
    return teacher


# ============================================================================
# 1. CORE ATTENDANCE ENGINE & METHOD ENFORCEMENT
# ============================================================================

def test_face_teacher_face_attendance_succeeds(app_and_client):
    """Face teacher using Face Recognition records attendance successfully."""
    _app, _client, _ = app_and_client
    t = Teacher(
        teacher_id="T-FACE-01",
        name="Prof. Face User",
        attendance_method="FACE",
        encoding=[0.1] * 512,
        is_active=True,
    )
    db.session.add(t)
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)
    res = record_recognition("teacher", t.id, camera_id=1, confidence=0.94, now=morning, auth_method="FACE")

    assert res["action"] == "inserted"
    assert res["event"] == "in"
    assert res["status"] == "present"

    att = TeacherAttendance.query.filter_by(teacher_id=t.id).first()
    assert att is not None
    assert att.in_time is not None

    evt = AttendanceEvent.query.filter_by(attendance_id=att.id).first()
    assert evt is not None
    assert evt.auth_method == "FACE"


def test_fingerprint_teacher_fingerprint_attendance_succeeds(app_and_client):
    """Fingerprint teacher using BioMax Fingerprint records attendance successfully."""
    _app, _client, _ = app_and_client
    t = Teacher(
        teacher_id="T-FP-01",
        name="Prof. Fingerprint User",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="T-FP-01",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add(t)
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)
    res = verify_and_record_attendance(device_user_id="T-FP-01", device_id="BIOMAX_INDIGO_01", event_time=morning)

    assert res["action"] == "inserted"
    assert res["event"] == "in"
    assert res["status"] == "present"

    att = TeacherAttendance.query.filter_by(teacher_id=t.id).first()
    assert att is not None
    assert att.in_time is not None

    evt = AttendanceEvent.query.filter_by(attendance_id=att.id).first()
    assert evt is not None
    assert evt.auth_method == "FINGERPRINT"
    assert evt.device_id == "BIOMAX_INDIGO_01"


def test_cross_method_rejections(app_and_client):
    """Teacher configured for FACE must be rejected on Fingerprint scan.
    Teacher configured for FINGERPRINT must be rejected on Face scan.
    """
    _app, _client, _ = app_and_client

    t_face = Teacher(
        teacher_id="T-ONLY-FACE",
        name="Face Only Teacher",
        attendance_method="FACE",
        fingerprint_user_id="T-ONLY-FACE",
        encoding=[0.2] * 512,
        is_active=True,
    )
    t_fp = Teacher(
        teacher_id="T-ONLY-FP",
        name="FP Only Teacher",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="T-ONLY-FP",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add_all([t_face, t_fp])
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)

    # 1. Face teacher attempts Fingerprint scan -> REJECTED
    res_fp_on_face = verify_and_record_attendance(device_user_id="T-ONLY-FACE", event_time=morning)
    assert res_fp_on_face["action"] == "rejected"
    assert "FACE" in res_fp_on_face["reason"]
    assert TeacherAttendance.query.filter_by(teacher_id=t_face.id).first() is None

    # 2. Fingerprint teacher attempts Face scan -> REJECTED
    res_face_on_fp = record_recognition("teacher", t_fp.id, camera_id=1, confidence=0.95, now=morning, auth_method="FACE")
    assert res_face_on_fp["action"] == "rejected"
    assert "FINGERPRINT" in res_face_on_fp["reason"]
    assert TeacherAttendance.query.filter_by(teacher_id=t_fp.id).first() is None


def test_unknown_device_user_id_rejected(app_and_client):
    """Scan from unmapped / unregistered Teacher ID is rejected and logged safely."""
    _app, _client, _ = app_and_client

    res = verify_and_record_attendance(device_user_id="UNKNOWN_9999", device_id="BIOMAX_INDIGO_01")
    assert res["action"] == "rejected"
    assert res["reason"] == "unknown teacher id"

    # Verify audit log recorded failure
    audit = FingerprintDeviceAudit.query.filter_by(teacher_id="UNKNOWN_9999").first()
    assert audit is not None
    assert audit.result == "rejected"
    assert audit.failure_reason == "unknown_teacher_id"


def test_inactive_teacher_rejected(app_and_client):
    """Inactive teacher is rejected and does not receive attendance."""
    _app, _client, _ = app_and_client

    t = Teacher(
        teacher_id="T-INACTIVE",
        name="Inactive Teacher",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="T-INACTIVE",
        fingerprint_enrolled=True,
        is_active=False,
    )
    db.session.add(t)
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)
    res = verify_and_record_attendance(device_user_id="T-INACTIVE", event_time=morning)
    assert res["action"] == "rejected"
    assert "inactive" in res["reason"]
    assert TeacherAttendance.query.filter_by(teacher_id=t.id).first() is None


def test_unenrolled_fingerprint_teacher_rejected(app_and_client):
    """Teacher configured for FINGERPRINT but not enrolled on device is rejected."""
    _app, _client, _ = app_and_client

    t = Teacher(
        teacher_id="T-NOT-ENROLLED",
        name="Not Enrolled Teacher",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="T-NOT-ENROLLED",
        fingerprint_enrolled=False,
        is_active=True,
    )
    db.session.add(t)
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0)
    res = verify_and_record_attendance(device_user_id="T-NOT-ENROLLED", event_time=morning)
    assert res["action"] == "rejected"
    assert "not enrolled" in res["reason"]


# ============================================================================
# 2. TIME RULES & IN/OUT STATE MACHINE REUSE
# ============================================================================

def test_fingerprint_time_rules_identical_to_face(app_and_client):
    """Fingerprint attendance applies the EXACT same Time Rules as Face recognition."""
    _app, _client, _ = app_and_client

    rule = TeacherTimeRule.query.first() or TeacherTimeRule()
    rule.office_start = "08:00"
    rule.late_time = "08:30"
    rule.half_day_time = "09:30"
    rule.absent_after = "11:00"
    db.session.add(rule)
    db.session.commit()

    t_ontime = Teacher(teacher_id="T-TIME-1", name="On Time", attendance_method="FINGERPRINT", fingerprint_user_id="T-TIME-1", fingerprint_enrolled=True, is_active=True)
    t_late = Teacher(teacher_id="T-TIME-2", name="Late Teacher", attendance_method="FINGERPRINT", fingerprint_user_id="T-TIME-2", fingerprint_enrolled=True, is_active=True)
    t_half = Teacher(teacher_id="T-TIME-3", name="Half Day Teacher", attendance_method="FINGERPRINT", fingerprint_user_id="T-TIME-3", fingerprint_enrolled=True, is_active=True)
    t_absent = Teacher(teacher_id="T-TIME-4", name="Absent Teacher", attendance_method="FINGERPRINT", fingerprint_user_id="T-TIME-4", fingerprint_enrolled=True, is_active=True)
    db.session.add_all([t_ontime, t_late, t_half, t_absent])
    db.session.commit()

    today_base = datetime.now().astimezone()

    # 1. 08:15 -> Present (on time)
    r1 = verify_and_record_attendance("T-TIME-1", event_time=today_base.replace(hour=8, minute=15, second=0, microsecond=0))
    assert r1["action"] == "inserted"
    assert r1["status"] == "present"

    # 2. 08:45 -> Late
    r2 = verify_and_record_attendance("T-TIME-2", event_time=today_base.replace(hour=8, minute=45, second=0, microsecond=0))
    assert r2["action"] == "inserted"
    assert r2["status"] == "late"

    # 3. 09:45 -> Half Day
    r3 = verify_and_record_attendance("T-TIME-3", event_time=today_base.replace(hour=9, minute=45, second=0, microsecond=0))
    assert r3["action"] == "inserted"
    assert r3["status"] == "half_day"

    # 4. 11:15 -> Absent after threshold
    r4 = verify_and_record_attendance("T-TIME-4", event_time=today_base.replace(hour=11, minute=15, second=0, microsecond=0))
    assert r4["action"] == "rejected"
    assert r4["reason"] == "absent after"
    att_absent = TeacherAttendance.query.filter_by(teacher_id=t_absent.id).first()
    assert att_absent.status == "absent"


def test_in_out_workflow_and_deduplication(app_and_client):
    """Test standard IN -> Duplicate Ignored -> OUT -> Completed Sequence."""
    _app, _client, _ = app_and_client

    rule = TeacherTimeRule.query.first() or TeacherTimeRule()
    rule.office_start = "08:00"
    rule.out_detection_start = "16:00"
    rule.early_exit_time = "16:30"
    rule.overtime_start = "17:00"
    rule.overtime_enabled = True
    db.session.add(rule)
    db.session.commit()

    t = Teacher(
        teacher_id="T-IN-OUT",
        name="Full Day Staff",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="T-IN-OUT",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add(t)
    db.session.commit()

    base_dt = datetime.now().astimezone()

    # 1. 08:10 IN scan
    in_res = verify_and_record_attendance("T-IN-OUT", event_time=base_dt.replace(hour=8, minute=10, second=0, microsecond=0))
    assert in_res["action"] == "inserted"
    assert in_res["event"] == "in"

    att = TeacherAttendance.query.filter_by(teacher_id=t.id).first()
    assert att.in_time is not None
    assert att.out_time is None

    # 2. 08:12 Duplicate scan immediately after -> Ignored (no second IN record)
    dup_res = verify_and_record_attendance("T-IN-OUT", event_time=base_dt.replace(hour=8, minute=12, second=0, microsecond=0))
    assert dup_res["action"] == "ignored"
    assert dup_res["reason"] == "before out detection start"

    # Still only 1 IN event in AttendanceEvent
    events_count = AttendanceEvent.query.filter_by(attendance_id=att.id).count()
    assert events_count == 1

    # 3. 17:15 OUT scan (after out_detection_start & overtime_start)
    out_res = verify_and_record_attendance("T-IN-OUT", event_time=base_dt.replace(hour=17, minute=15, second=0, microsecond=0))
    assert out_res["action"] == "updated"
    assert out_res["event"] == "out"

    db.session.refresh(att)
    assert att.out_time is not None
    assert att.working_minutes == 545  # 08:10 to 17:15 = 9h 5m = 545 mins
    assert att.overtime_minutes == 15  # 17:00 to 17:15 = 15 mins
    assert att.is_early_exit is False

    # 4. 17:20 Scan after day is already complete -> Rejected
    complete_res = verify_and_record_attendance("T-IN-OUT", event_time=base_dt.replace(hour=17, minute=20, second=0, microsecond=0))
    assert complete_res["action"] == "rejected"
    assert complete_res["reason"] == "daily attendance complete"


# ============================================================================
# 3. DEVICE RESILIENCE & FAILURE ISOLATION
# ============================================================================

def test_device_offline_does_not_break_face_attendance(app_and_client):
    """When BioMax Indigo device is offline/unreachable, Face Attendance remains 100% operational."""
    _app, _client, mock_adapter = app_and_client

    # Simulate BioMax device failure
    mock_adapter.set_connected(False)
    assert mock_adapter.is_connected() is False

    # Face Teacher records attendance
    t_face = Teacher(
        teacher_id="T-FACE-ISOLATION",
        name="Face Teacher Isolation",
        attendance_method="FACE",
        encoding=[0.3] * 512,
        is_active=True,
    )
    db.session.add(t_face)
    db.session.commit()

    morning = datetime.now().astimezone().replace(hour=8, minute=25, second=0, microsecond=0)
    res = record_recognition("teacher", t_face.id, camera_id=1, confidence=0.91, now=morning, auth_method="FACE")

    assert res["action"] == "inserted"
    assert res["event"] == "in"
    assert res["status"] == "present"


# ============================================================================
# 4. REST API & ENROLLMENT ENDPOINTS
# ============================================================================

def test_mock_adapter_teacher_api_fingerprint_lifecycle(app_and_client):
    """Mock integration test: simulated enrollment and event delivery via Flask Test Client."""
    _app, client, _ = app_and_client
    assert isinstance(get_fingerprint_adapter(), MockFingerprintAdapter)
    csrf = _authenticate(client)

    # Enrollment flags supplied by the generic teacher API must not assert hardware success.
    resp_fake_enroll = client.post(
        "/api/teachers",
        data=json.dumps({
            "teacher_id": "T-API-01",
            "name": "Prof. Alan Turing",
            "attendance_method": "FINGERPRINT",
            "fingerprint_user_id": "T-API-01",
            "fingerprint_enrolled": True,
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_fake_enroll.status_code == 400

    # 1. Create Teacher with FINGERPRINT attendance method
    resp_create = client.post(
        "/api/teachers",
        data=json.dumps({
            "teacher_id": "T-API-01",
            "name": "Prof. Alan Turing",
            "department": "Computer Science",
            "designation": "Head of Faculty",
            "attendance_method": "FINGERPRINT",
            "fingerprint_user_id": "T-API-01",
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_create.status_code == 201
    t_data = resp_create.get_json()
    assert t_data["attendance_method"] == "FINGERPRINT"
    assert t_data["fingerprint_enrolled"] is False
    tid = t_data["id"]

    resp_fake_update = client.put(
        f"/api/teachers/{tid}",
        data=json.dumps({"fingerprint_enrolled": True}),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_fake_update.status_code == 400

    # 2. Trigger Fingerprint Enrollment on BioMax Device
    resp_enroll = client.post(
        f"/api/teachers/{tid}/fingerprint/enroll",
        data=json.dumps({"device_user_id": "T-API-01"}),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_enroll.status_code == 200
    enroll_data = resp_enroll.get_json()
    assert enroll_data["ok"] is True
    assert enroll_data["simulated"] is True
    assert enroll_data["fingerprint_enrolled"] is True
    assert "no physical device was changed" in enroll_data["message"]

    # 3. Query Fingerprint Status
    resp_status = client.get(f"/api/teachers/{tid}/fingerprint/status")
    assert resp_status.status_code == 200
    status_data = resp_status.get_json()
    assert status_data["fingerprint_enrolled"] is True
    assert status_data["attendance_method"] == "FINGERPRINT"

    # 4. Register the simulator's test device. This is a mock integration test,
    #    not evidence of Team Office Z305 push compatibility.
    teacher = db.session.get(Teacher, tid)
    test_device = FingerprintDevice(
        school_id=teacher.school_id,
        device_id="BIOMAX_INDIGO_LOBBY",
        name="Test Simulator",
        model="Mock adapter test fixture",
        connection_type="MOCK",
        is_active=True,
    )
    db.session.add(test_device)
    db.session.commit()

    # 5. Post a normalized AttendanceOS event using the authenticated admin session.
    event_time = datetime.now().astimezone().replace(hour=8, minute=20, second=0, microsecond=0).isoformat()
    resp_event = client.post(
        "/api/devices/biomax/event",
        data=json.dumps({
            "user_id": "T-API-01",
            "device_id": "BIOMAX_INDIGO_LOBBY",
            "timestamp": event_time,
            "verify_type": "FINGERPRINT",
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_event.status_code == 200
    event_res = resp_event.get_json()
    assert event_res["action"] == "inserted"
    assert event_res["event"] == "in"
    assert event_res["method"] == "FINGERPRINT"

    # 6. Check Device Health Status Endpoint
    resp_dev_status = client.get("/api/devices/biomax/status")
    assert resp_dev_status.status_code == 200
    dev_info = resp_dev_status.get_json()
    assert dev_info["ok"] is True

    # 6. Check Device Audit Logs Endpoint
    resp_audit = client.get("/api/devices/biomax/audit")
    assert resp_audit.status_code == 200
    logs = resp_audit.get_json()
    assert len(logs) > 0
    assert any(log.get("teacher_id") == "T-API-01" for log in logs)

    # 7. Delete / Unenroll Fingerprint
    resp_delete = client.delete(
        f"/api/teachers/{tid}/fingerprint/enroll",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_delete.status_code == 200
    del_data = resp_delete.get_json()
    assert del_data["ok"] is True
    assert del_data["fingerprint_enrolled"] is False


# ============================================================================
# 5. DEVICE MANAGEMENT PAGE, CONFIGURATION & RBAC TESTS
# ============================================================================

def test_device_page_rbac_admin_allowed_teacher_forbidden(app_and_client):
    """Admin and Principal can access Fingerprint Device Page; Teacher role is forbidden (403)."""
    _app, client, _ = app_and_client

    # 1. Admin login -> 200 OK
    _authenticate(client, role="super_admin", email="admin@school.test")
    resp_admin = client.get("/admin/devices/fingerprint")
    assert resp_admin.status_code == 200
    assert "Team Office Z305" in resp_admin.get_data(as_text=True)

    # 2. Teacher login -> 403 Forbidden
    _authenticate(client, role="teacher", email="teacher@school.test")
    resp_teacher = client.get("/admin/devices/fingerprint")
    assert resp_teacher.status_code == 403


def test_device_config_lifecycle(app_and_client):
    """Test retrieving and persisting device configuration parameters."""
    _app, client, _ = app_and_client
    csrf = _authenticate(client, role="super_admin")

    # 1. Get initial config
    resp_get = client.get("/api/devices/fingerprint/config")
    assert resp_get.status_code == 200
    data_get = resp_get.get_json()
    assert data_get["ok"] is True
    assert "device" in data_get

    # 2. Update config
    resp_post = client.post(
        "/api/devices/fingerprint/config",
        data=json.dumps({
            "name": "Science Block BioMax Indigo",
            "device_id": "BIOMAX_SCI_02",
            "model": "BioMax Indigo Series",
            "connection_type": "TCP_IP",
            "ip_address": "192.168.10.150",
            "port": 4370,
            "comm_key": "SecretComm999",
            "timeout_seconds": 8.0,
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_post.status_code == 200
    data_post = resp_post.get_json()
    assert data_post["ok"] is True
    assert data_post["device"]["name"] == "Science Block BioMax Indigo"
    assert data_post["device"]["ip_address"] == "192.168.10.150"
    assert data_post["device"]["port"] == 4370


def test_device_connection_test_endpoint(app_and_client):
    """Test connection diagnostic endpoint with latency measurement."""
    _app, client, mock_adapter = app_and_client
    csrf = _authenticate(client, role="super_admin")

    # 1. When connected -> returns SUCCESS and latency
    mock_adapter.set_connected(True)
    resp_test = client.post("/api/devices/fingerprint/test-connection", headers={"X-CSRF-Token": csrf})
    assert resp_test.status_code == 200
    test_data = resp_test.get_json()
    assert test_data["ok"] is True
    assert test_data["status"] == "SUCCESS"
    assert "latency_ms" in test_data

    # 2. When simulated offline -> returns FAILED
    mock_adapter.set_connected(False)
    resp_test_off = client.post("/api/devices/fingerprint/test-connection", headers={"X-CSRF-Token": csrf})
    assert resp_test_off.status_code == 200
    off_data = resp_test_off.get_json()
    assert off_data["ok"] is False
    assert off_data["status"] == "FAILED"


def test_device_connect_and_disconnect_endpoints(app_and_client):
    """Test connecting and disconnecting the fingerprint device."""
    _app, client, mock_adapter = app_and_client
    csrf = _authenticate(client, role="super_admin")

    # Disconnect
    resp_disc = client.post("/api/devices/fingerprint/disconnect", headers={"X-CSRF-Token": csrf})
    assert resp_disc.status_code == 200
    assert resp_disc.get_json()["status"] == "disconnected"

    # Connect
    resp_conn = client.post("/api/devices/fingerprint/connect", headers={"X-CSRF-Token": csrf})
    assert resp_conn.status_code == 200
    assert resp_conn.get_json()["ok"] is True
    assert resp_conn.get_json()["status"] == "connected"


# ============================================================================
# 6. MAPPING UNIQUENESS & CONFLICT PREVENTION
# ============================================================================

def test_teacher_mapping_uniqueness_and_conflicts(app_and_client):
    """Prevent mapping one Device User ID to multiple Teachers."""
    _app, client, _ = app_and_client
    csrf = _authenticate(client, role="super_admin")

    t1 = Teacher(teacher_id="T-MAP-01", name="Prof. Alpha", attendance_method="FINGERPRINT", fingerprint_user_id="1001", is_active=True)
    t2 = Teacher(teacher_id="T-MAP-02", name="Prof. Beta", attendance_method="FINGERPRINT", fingerprint_user_id="1002", is_active=True)
    db.session.add_all([t1, t2])
    db.session.commit()

    # 1. List mappings
    resp_list = client.get("/api/devices/fingerprint/mappings")
    assert resp_list.status_code == 200
    assert resp_list.get_json()["total"] >= 2

    # 2. Try to map t2 to "1001" which is already assigned to t1 -> 409 Conflict
    resp_conflict = client.post(
        "/api/devices/fingerprint/mappings",
        data=json.dumps({
            "teacher_id": t2.id,
            "fingerprint_user_id": "1001",
            "attendance_method": "FINGERPRINT",
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_conflict.status_code == 409
    assert "already assigned" in resp_conflict.get_json()["error"]

    # 3. Map t2 to a fresh unique ID "2002" -> Succeeds
    resp_ok = client.post(
        "/api/devices/fingerprint/mappings",
        data=json.dumps({
            "teacher_id": t2.id,
            "fingerprint_user_id": "2002",
            "attendance_method": "FINGERPRINT",
        }),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_ok.status_code == 200
    assert resp_ok.get_json()["ok"] is True
    assert resp_ok.get_json()["teacher"]["fingerprint_user_id"] == "2002"


def test_teacher_mapping_rejects_duplicate_ids_when_school_is_null(app_and_client):
    _app, _client, _ = app_and_client
    first = Teacher(teacher_id="T-NULL-1", name="First")
    second = Teacher(teacher_id="T-NULL-2", name="Second")
    db.session.add_all([first, second])
    db.session.commit()
    assert first.to_dict()["fingerprint_user_id"] is None

    first_mapping = save_teacher_fingerprint_mapping(first.id, "0007")
    second_mapping = save_teacher_fingerprint_mapping(second.id, "0007")

    assert first_mapping["ok"] is True
    assert first.to_dict()["fingerprint_user_id"] == "0007"
    assert second_mapping["ok"] is False
    assert second_mapping["status"] == 409


# ============================================================================
# 7. SAFE DIAGNOSTIC VERIFICATION TEST (NO ATTENDANCE RECORDED)
# ============================================================================

def test_safe_fingerprint_verification_diagnostic_no_attendance(app_and_client):
    """Verification test diagnoses identity mapping and NEVER records attendance."""
    _app, client, _ = app_and_client
    csrf = _authenticate(client, role="super_admin")

    t = Teacher(
        teacher_id="T-DIAG-01",
        name="Prof. Diagnostic Test",
        attendance_method="FINGERPRINT",
        fingerprint_user_id="9001",
        fingerprint_enrolled=True,
        is_active=True,
    )
    db.session.add(t)
    db.session.commit()

    initial_att_count = TeacherAttendance.query.count()
    initial_evt_count = AttendanceEvent.query.count()

    # 1. Run diagnostic test for mapped user "9001"
    resp_test = client.post(
        "/api/devices/fingerprint/test-verify",
        data=json.dumps({"device_user_id": "9001"}),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_test.status_code == 200
    res_data = resp_test.get_json()
    assert res_data["ok"] is True
    assert res_data["status"] == "SUCCESS"
    assert res_data["attendance_marked"] is False
    assert res_data["teacher"]["name"] == "Prof. Diagnostic Test"

    # CRITICAL VERIFICATION: No attendance was created!
    assert TeacherAttendance.query.count() == initial_att_count
    assert AttendanceEvent.query.count() == initial_evt_count

    # 2. Test unknown user ID
    resp_unknown = client.post(
        "/api/devices/fingerprint/test-verify",
        data=json.dumps({"device_user_id": "UNKNOWN_RANDOM_UID"}),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_unknown.status_code == 200
    unk_data = resp_unknown.get_json()
    assert unk_data["ok"] is False
    assert unk_data["status"] == "UNKNOWN_USER"

    # Still zero attendance records created
    assert TeacherAttendance.query.count() == initial_att_count


def test_device_events_endpoint(app_and_client):
    """Test retrieving live hardware event feed and audit logs."""
    _app, client, _ = app_and_client
    _authenticate(client, role="super_admin")

    resp_events = client.get("/api/devices/fingerprint/events")
    assert resp_events.status_code == 200
    data = resp_events.get_json()
    assert data["ok"] is True
    assert "events" in data


def test_real_biomax_adapter_refuses_false_success():
    """Real adapter must not pretend a Team Office Z305 enrollment succeeded."""
    adapter = BioMaxIndigoAdapter(host="127.0.0.1", port=1, timeout=0.2)

    enroll_res = adapter.enroll_user("USER-42", "Test User")
    assert enroll_res["ok"] is False
    assert enroll_res["status"] in {"OFFLINE", "UNVERIFIED"}

    status = adapter.get_enrollment_status("USER-42")
    assert status["enrolled"] is False
    assert status["verified"] is False
    assert status["status"] == "UNVERIFIED"


def test_real_biomax_adapter_reports_unverified_operational_state():
    """Network reachability must not be mistaken for a confirmed operational device state."""
    adapter = BioMaxIndigoAdapter(host="127.0.0.1", port=1, timeout=0.2)
    info = adapter.get_device_info()

    assert info["connected"] is False
    assert info["protocol_verified"] is False
    assert info["operational"] is False
    assert "unverified" in info["sdk_status"]
    assert info["network_connected"] is False
    assert info["device_authenticated"] is False
    assert info["enrollment_supported"] is False
    assert info["attendance_sync_working"] is False


def test_status_api_exposes_distinct_device_readiness_fields(app_and_client):
    _app, client, _mock = app_and_client
    _authenticate(client, role="super_admin")
    set_fingerprint_adapter(BioMaxIndigoAdapter(host="192.168.1.25", port=5005))
    response = client.get("/api/devices/fingerprint/status")
    assert response.status_code == 200
    status = response.get_json()["integration_status"]
    assert set(status) >= {
        "network_connected", "protocol_verified", "device_authenticated",
        "enrollment_supported", "attendance_sync_working", "integration_ready",
    }
    assert status["protocol_verified"] is False
    assert status["device_authenticated"] is False
    assert status["enrollment_supported"] is False
    assert status["attendance_sync_working"] is False
    assert status["integration_ready"] is False


def test_tcp_reachability_never_implies_z305_protocol_or_integration(monkeypatch):
    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def connect_ex(self, _endpoint):
            return 0

        def close(self):
            pass

    monkeypatch.setattr("biometrics.fingerprint_adapter.socket.socket", lambda *_args: FakeSocket())
    adapter = BioMaxIndigoAdapter(host="192.168.1.25", port=5005)

    assert adapter.connect() is True  # TCP socket only
    info = adapter.get_device_info()
    assert info["network_connected"] is True
    assert info["protocol_verified"] is False
    assert info["device_authenticated"] is False
    assert info["enrollment_supported"] is False
    assert info["attendance_sync_working"] is False
    assert info["integration_ready"] is False
    assert "not verified" in info["port_role"]


def test_unauthorized_webhook_is_rejected(app_and_client):
    _app, client, _ = app_and_client
    _register_webhook_device()
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(),
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "Invalid device credentials."


def test_authenticated_admin_webhook_still_requires_csrf(app_and_client):
    _app, client, _ = app_and_client
    _authenticate(client, role="super_admin")
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(),
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid CSRF token"


def test_webhook_token_is_hashed_and_not_returned(app_and_client):
    _app, _client, _ = app_and_client
    secret = "device-secret-0123456789-abcdefghijk"
    result = save_fingerprint_device_config(
        {"device_id": "Z305_CONFIG_TEST", "webhook_token": secret},
        school_id=1,
    )
    assert result["ok"] is True
    device = FingerprintDevice.query.filter_by(device_id="Z305_CONFIG_TEST").one()
    assert device.webhook_token_hash == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert "webhook_token" not in result["device"]
    assert result["device"]["has_webhook_token"] is True


def test_webhook_rejects_unregistered_device_id(app_and_client):
    _app, client, _ = app_and_client
    _device, token = _register_webhook_device()
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(device_id="UNKNOWN_DEVICE"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_webhook_rejects_missing_employee_id(app_and_client):
    _app, client, _ = app_and_client
    _device, token = _register_webhook_device()
    payload = _webhook_payload()
    del payload["user_id"]
    response = client.post(
        "/api/devices/biomax/event",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_webhook_rejects_invalid_timestamp(app_and_client):
    _app, client, _ = app_and_client
    _device, token = _register_webhook_device()
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(timestamp="not-a-timestamp"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_webhook_rejects_malformed_json(app_and_client):
    _app, client, _ = app_and_client
    response = client.post(
        "/api/devices/biomax/event",
        data='{"device_id":',
        content_type="application/json",
    )
    assert response.status_code == 400


def test_webhook_rejects_unknown_employee(app_and_client):
    _app, client, _ = app_and_client
    _device, token = _register_webhook_device()
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(user_id="UNKNOWN"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert TeacherAttendance.query.count() == 0


def test_webhook_rejects_inactive_employee(app_and_client):
    _app, client, _ = app_and_client
    _register_webhook_device()
    _create_webhook_teacher(active=False)
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(),
        headers={"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"},
    )
    assert response.status_code == 422
    assert TeacherAttendance.query.count() == 0


def test_webhook_duplicate_event_and_replay_are_idempotently_rejected(app_and_client):
    _app, client, _ = app_and_client
    _register_webhook_device()
    _create_webhook_teacher()
    payload = _webhook_payload(event_id="device-event-1")
    headers = {"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"}

    first = client.post("/api/devices/biomax/event", json=payload, headers=headers)
    replay = client.post("/api/devices/biomax/event", json=payload, headers=headers)
    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.get_json()["reason"] == "duplicate event"
    assert FingerprintEventReceipt.query.count() == 1


def test_webhook_duplicate_without_vendor_event_id_is_rejected(app_and_client):
    """The normalized payload fingerprint still blocks identical log re-delivery."""
    _app, client, _ = app_and_client
    _register_webhook_device()
    _create_webhook_teacher()
    payload = _webhook_payload(timestamp="2025-01-02T08:30:00+05:30")
    headers = {"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"}
    assert client.post("/api/devices/biomax/event", json=payload, headers=headers).status_code == 200
    duplicate = client.post("/api/devices/biomax/event", json=payload, headers=headers)
    assert duplicate.status_code == 409
    assert FingerprintEventReceipt.query.count() == 1


def test_webhook_rate_limit_is_enforced(app_and_client):
    app, client, _ = app_and_client
    app.config["BIOMAX_WEBHOOK_MAX_REQUESTS"] = 1
    _register_webhook_device(device_id="Z305_RATE_LIMIT_TEST")
    _create_webhook_teacher()
    headers = {"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"}

    first = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(device_id="Z305_RATE_LIMIT_TEST", event_id="rate-limited-first"),
        headers=headers,
    )
    second = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(device_id="Z305_RATE_LIMIT_TEST", event_id="rate-limited-second"),
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 429


def test_webhook_normalizes_timezone_before_storing_receipt(app_and_client):
    app, client, _ = app_and_client
    app.config["BIOMAX_DEVICE_TIMEZONE"] = "Asia/Kolkata"
    _register_webhook_device()
    _create_webhook_teacher()
    local_event_time = datetime.now(ZoneInfo("Asia/Kolkata")).replace(second=0, microsecond=0) - timedelta(minutes=1)
    utc_event_time = local_event_time.astimezone(timezone.utc)
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(timestamp=utc_event_time.isoformat()),
        headers={"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"},
    )
    assert response.status_code == 200
    receipt = FingerprintEventReceipt.query.one()
    restored_utc = receipt.event_time.replace(
        tzinfo=ZoneInfo("Asia/Kolkata")
    ).astimezone(timezone.utc)
    assert restored_utc == utc_event_time


def test_webhook_database_failure_rolls_back_event_receipt(app_and_client, monkeypatch):
    _app, client, _ = app_and_client
    _register_webhook_device()
    _create_webhook_teacher()

    def fail_attendance(**_kwargs):
        raise SQLAlchemyError("simulated database failure")

    monkeypatch.setattr("biometrics.fingerprint_service.verify_and_record_attendance", fail_attendance)
    response = client.post(
        "/api/devices/biomax/event",
        json=_webhook_payload(),
        headers={"Authorization": "Bearer test-device-token-0123456789-abcdefghijk"},
    )
    assert response.status_code == 503
    assert FingerprintEventReceipt.query.count() == 0
    assert TeacherAttendance.query.count() == 0


def test_device_offline_and_connection_recovery_are_explicitly_simulated():
    adapter = MockFingerprintAdapter(device_id="MOCK-Z305", initially_connected=False)
    offline_result = adapter.fetch_attendance_logs()
    assert offline_result["status"] == "OFFLINE"
    assert offline_result["simulated"] is True
    assert adapter.connect() is True
    assert adapter.is_connected() is True


def test_mock_missed_logs_are_available_after_reconnection():
    adapter = MockFingerprintAdapter(device_id="MOCK-Z305", initially_connected=False)
    adapter.queue_event({"user_id": "0007", "timestamp": "2025-01-02T08:30:00+05:30"})

    assert adapter.fetch_attendance_logs()["status"] == "OFFLINE"
    adapter.connect()
    recovered = adapter.fetch_attendance_logs(cursor="0")
    assert recovered["ok"] is True
    assert recovered["simulated"] is True
    assert recovered["logs"][0]["user_id"] == "0007"
    assert recovered["cursor"] == "1"


def test_real_adapter_reports_sync_as_unsupported_and_enrollment_unverified():
    adapter = BioMaxIndigoAdapter(host="127.0.0.1", port=1, timeout=0.2)
    adapter._connected = True
    enrollment = adapter.enroll_user("0007", "Test User")
    logs = adapter.fetch_attendance_logs()

    assert enrollment["ok"] is False
    assert enrollment["status"] == "UNVERIFIED"
    assert logs["ok"] is False
    assert logs["status"] == "UNSUPPORTED"
    assert "model/firmware" in logs["error"]


def test_mock_enrollment_is_labeled_as_simulated():
    adapter = MockFingerprintAdapter()
    result = adapter.enroll_user("0007", "Test User")
    assert result["ok"] is True
    assert result["simulated"] is True
    assert result["status"] == "simulated"


def test_unconfigured_real_adapter_does_not_assume_default_port():
    adapter = BioMaxIndigoAdapter()
    assert adapter.port is None
    assert adapter.connect() is False
    assert "No port is assumed" in adapter.get_device_info()["last_error"]
