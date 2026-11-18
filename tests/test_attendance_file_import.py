import csv
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from database.db import db
from database.models import Teacher
from database.enterprise_models import AttendanceEvent, FingerprintEventReceipt
from database.tenant_models import School
from database.auth_models import AuthSession, User, utcnow
from auth import _hash
from biometrics.attendance_file_import import import_attendance_export, read_attendance_export


@pytest.fixture
def import_app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.drop_all()
        db.create_all()
        school = School(school_id="IMPORT-TEST", name="Import test school")
        db.session.add(school)
        db.session.commit()
        yield app, school.id
        db.session.remove()
        db.drop_all()


def test_reads_csv_preserving_device_ids_and_local_timestamp(tmp_path):
    path = tmp_path / "attlog.csv"
    path.write_text("User ID,Date,Time\n0007,03/11/2026,09:15:00\n", encoding="utf-8")
    rows = read_attendance_export(path)
    assert rows[0]["user_id"] == "0007"
    assert rows[0]["timestamp"] == datetime(2026, 11, 3, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_reads_xlsx_export(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "attendance.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Employee ID", "Punch Time"])
    sheet.append(["0007", "2026-11-03T09:15:00+05:30"])
    workbook.save(path)
    rows = read_attendance_export(path)
    assert rows[0]["user_id"] == "0007"
    assert rows[0]["timestamp"].hour == 9


def test_import_records_attendance_once_without_claiming_enrollment(import_app, tmp_path):
    app, school_id = import_app
    path = tmp_path / "attendance.txt"
    event_time = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=9, minute=20, second=0, microsecond=0)
    path.write_text(f"PIN\tDateTime\n0007\t{event_time.isoformat()}\n", encoding="utf-8")
    with app.app_context():
        teacher = Teacher(teacher_id="T-7", name="Teacher Seven", school_id=school_id,
                          fingerprint_user_id="0007", attendance_method="FINGERPRINT",
                          fingerprint_enrolled=False, is_active=True)
        db.session.add(teacher)
        db.session.commit()
        first = import_attendance_export(path, device_id="Z305-USB", school_id=school_id)
        replay = import_attendance_export(path, device_id="Z305-USB", school_id=school_id)
        assert first["imported"] == 1
        assert replay["duplicates"] == 1
        assert teacher.fingerprint_enrolled is False
        assert AttendanceEvent.query.count() == 1
        assert FingerprintEventReceipt.query.count() == 1


def test_unknown_mapping_is_not_receipted_so_it_can_be_retried(import_app, tmp_path):
    app, school_id = import_app
    path = tmp_path / "attendance.csv"
    path.write_text("User ID,DateTime\n0007,2026-11-03T09:15:00+05:30\n", encoding="utf-8")
    with app.app_context():
        rejected = import_attendance_export(path, device_id="Z305-USB", school_id=school_id)
        assert rejected["rejected"] == 1
        assert FingerprintEventReceipt.query.count() == 0
        teacher = Teacher(teacher_id="T-7", name="Teacher Seven", school_id=school_id,
                          fingerprint_user_id="0007", attendance_method="FINGERPRINT",
                          is_active=True)
        db.session.add(teacher)
        db.session.commit()
        retried = import_attendance_export(path, device_id="Z305-USB", school_id=school_id)
        assert retried["imported"] == 1


def test_export_without_required_columns_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Name,Department\nPerson,Science\n", encoding="utf-8")
    with pytest.raises(ValueError, match="employee ID"):
        read_attendance_export(path)


def test_admin_upload_endpoint_imports_export(import_app, tmp_path):
    app, school_id = import_app
    app.config["WTF_CSRF_ENABLED"] = False
    path = tmp_path / "attendance.csv"
    path.write_text("User ID,DateTime\n0007,2026-11-03T09:15:00+05:30\n", encoding="utf-8")
    with app.app_context():
        teacher = Teacher(teacher_id="T-7", name="Teacher Seven", school_id=school_id,
                          fingerprint_user_id="0007", attendance_method="FINGERPRINT",
                          is_active=True)
        user = User(email="device-admin@test.local", name="Device Admin",
                    password_hash="unused-test-hash", role="super_admin", school_id=school_id,
                    email_verified_at=utcnow(), is_active=True)
        db.session.add_all([teacher, user])
        db.session.flush()
        raw_sid = secrets.token_urlsafe(48)
        db.session.add(AuthSession(token_hash=_hash(raw_sid), user=user,
                                   csrf_secret="test-csrf-token", device="pytest",
                                   ip_address="127.0.0.1", expires_at=utcnow().replace(year=utcnow().year + 1)))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["sid"] = raw_sid
    with path.open("rb") as handle:
        response = client.post("/api/devices/fingerprint/import",
                               data={"file": (handle, "attendance.csv")},
                               content_type="multipart/form-data",
                               headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == 200
    assert response.get_json()["result"]["imported"] == 1
