"""Additive, normalized models for enterprise attendance management."""
from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint, Index
from sqlalchemy.sql import func

from .db import db


class StudentAttendance(db.Model):
    __tablename__ = "student_attendance"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    in_time = db.Column(db.DateTime(timezone=True))
    out_time = db.Column(db.DateTime(timezone=True))
    working_minutes = db.Column(db.Integer, default=0, nullable=False)
    working_seconds = db.Column(db.Integer, default=0, nullable=False)
    break_minutes = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(24), default="present", nullable=False)
    is_late = db.Column(db.Boolean, default=False, nullable=False)
    is_early_exit = db.Column(db.Boolean, default=False, nullable=False)
    overtime_minutes = db.Column(db.Integer, default=0, nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"))
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    student = db.relationship("Student")
    camera = db.relationship("Camera")
    school = db.relationship("School")
    __table_args__ = (UniqueConstraint("student_id", "attendance_date", name="uq_student_attendance_day"),)


class TeacherAttendance(db.Model):
    __tablename__ = "teacher_attendance"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    in_time = db.Column(db.DateTime(timezone=True))
    out_time = db.Column(db.DateTime(timezone=True))
    working_minutes = db.Column(db.Integer, default=0, nullable=False)
    working_seconds = db.Column(db.Integer, default=0, nullable=False)
    break_minutes = db.Column(db.Integer, default=0, nullable=False)
    overtime_minutes = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(24), default="present", nullable=False)
    is_late = db.Column(db.Boolean, default=False, nullable=False)
    is_early_exit = db.Column(db.Boolean, default=False, nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"))
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    teacher = db.relationship("Teacher")
    camera = db.relationship("Camera")
    school = db.relationship("School")
    __table_args__ = (UniqueConstraint("teacher_id", "attendance_date", name="uq_teacher_attendance_day"),)


class AttendanceEvent(db.Model):
    __tablename__ = "attendance_events"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    attendance_type = db.Column(db.String(12), nullable=False, index=True)  # student/teacher
    attendance_id = db.Column(db.Integer, nullable=False, index=True)
    event_type = db.Column(db.String(12), nullable=False)  # in/out/break_start/break_end
    auth_method = db.Column(db.String(20), default="FACE", nullable=False)  # FACE / FINGERPRINT
    device_id = db.Column(db.String(60), nullable=True)
    event_time = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"))
    confidence = db.Column(db.Float)
    camera = db.relationship("Camera")
    school = db.relationship("School")
    __table_args__ = (Index("ix_event_attendance", "attendance_type", "attendance_id"),)


class _TimeRuleBase:
    # IN and OUT boundaries are independent: a detection cannot become OUT
    # until out_detection_start, regardless of repeated camera detections.
    office_start = db.Column(db.String(5), default="08:00", nullable=False)
    in_end_time = db.Column(db.String(5), default="09:30", nullable=False)
    office_end = db.Column(db.String(5), default="17:00", nullable=False)
    late_time = db.Column(db.String(5), default="08:30", nullable=False)
    half_day_time = db.Column(db.String(5), default="09:30", nullable=False)
    absent_after = db.Column(db.String(5), default="11:00", nullable=False)
    out_detection_start = db.Column(db.String(5), default="16:00", nullable=False)
    early_exit_time = db.Column(db.String(5), default="16:30", nullable=False)
    overtime_start = db.Column(db.String(5), default="17:00", nullable=False)
    min_working_minutes = db.Column(db.Integer, default=480, nullable=False)
    overtime_enabled = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class TeacherTimeRule(db.Model, _TimeRuleBase):
    __tablename__ = "teacher_time_rules"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    name = db.Column(db.String(80), default="Default", nullable=False)
    school = db.relationship("School")


class StudentTimeRule(db.Model, _TimeRuleBase):
    __tablename__ = "student_time_rules"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    name = db.Column(db.String(80), default="Default", nullable=False)
    gate_close_time = db.Column(db.String(5), default="09:30", nullable=False)
    school = db.relationship("School")


class HolidayCalendar(db.Model):
    __tablename__ = "holiday_calendar"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    holiday_date = db.Column(db.Date, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    applies_to = db.Column(db.String(12), default="all", nullable=False)
    school = db.relationship("School")
    __table_args__ = (UniqueConstraint("holiday_date", "applies_to", name="uq_holiday_calendar_day"),)


class WeeklyOff(db.Model):
    __tablename__ = "weekly_off"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    applies_to = db.Column(db.String(12), nullable=False, index=True)
    weekday = db.Column(db.Integer, nullable=False)  # Monday=0
    school = db.relationship("School")
    __table_args__ = (UniqueConstraint("applies_to", "weekday", name="uq_weekly_off_scope_day"),)


class AttendanceSetting(db.Model):
    __tablename__ = "attendance_settings"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    key = db.Column(db.String(80), nullable=False)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    school = db.relationship("School")


class AttendanceLog(db.Model):
    __tablename__ = "attendance_logs"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    entity_type = db.Column(db.String(40), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    field = db.Column(db.String(80), nullable=False)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    edited_by = db.Column(db.String(80), default="admin", nullable=False)
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    school = db.relationship("School")


class AttendanceCorrection(db.Model):
    __tablename__ = "attendance_corrections"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    attendance_type = db.Column(db.String(12), default="teacher", nullable=False)
    attendance_id = db.Column(db.Integer, nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=True, index=True)
    issue_type = db.Column(db.String(64), nullable=True)  # missing_in, missing_out, wrong_time, wrong_status
    requested_time = db.Column(db.String(32), nullable=True)
    reason = db.Column(db.Text, nullable=False)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(24), default="pending", nullable=False)  # pending, approved, rejected
    in_time = db.Column(db.DateTime(timezone=True))
    out_time = db.Column(db.DateTime(timezone=True))
    admin_comment = db.Column(db.Text, nullable=True)
    approved_time = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    school = db.relationship("School")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "attendance_type": self.attendance_type,
            "attendance_id": self.attendance_id,
            "attendance_date": self.attendance_date.isoformat() if self.attendance_date else None,
            "issue_type": self.issue_type or "missing_in",
            "requested_time": self.requested_time,
            "reason": self.reason,
            "note": self.note,
            "status": self.status or "pending",
            "in_time": self.in_time.isoformat() if self.in_time else None,
            "out_time": self.out_time.isoformat() if self.out_time else None,
            "admin_comment": self.admin_comment,
            "approved_time": self.approved_time,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CameraSession(db.Model):
    __tablename__ = "camera_sessions"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"), nullable=False, index=True)
    started_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True))
    camera = db.relationship("Camera")
    school = db.relationship("School")


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    channel = db.Column(db.String(20), nullable=False)
    recipient = db.Column(db.String(160))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    school = db.relationship("School")


class FingerprintDeviceAudit(db.Model):
    """Lightweight audit log for fingerprint device integration events.

    Security & Privacy:
    Strictly records identification metadata, event timestamps, and outcomes.
    Biometric templates and raw fingerprint images are NEVER stored in this table
    or anywhere in AttendanceOS.
    """
    __tablename__ = "fingerprint_device_audit"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    device_id = db.Column(db.String(60), nullable=False, default="TEAM_OFFICE_Z305_01")
    teacher_id = db.Column(db.String(50), nullable=True, index=True)
    event_time = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    event_type = db.Column(db.String(30), nullable=False)  # verify / enroll / delete_enrollment / sync / status
    result = db.Column(db.String(30), nullable=False)  # success / rejected / ignored / error / inserted / updated
    failure_reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    school = db.relationship("School")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "device_id": self.device_id,
            "teacher_id": self.teacher_id,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "event_type": self.event_type,
            "result": self.result,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FingerprintEventReceipt(db.Model):
    """Idempotency receipt for authenticated fingerprint webhook events."""
    __tablename__ = "fingerprint_event_receipts"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(60), nullable=False, index=True)
    event_key = db.Column(db.String(64), nullable=False, unique=True)
    payload_hash = db.Column(db.String(64), nullable=False)
    device_user_id = db.Column(db.String(50), nullable=False)
    event_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    result = db.Column(db.String(30), nullable=False, default="processing")
    response_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)


class FingerprintDevice(db.Model):
    """Configuration and state record for a fingerprint terminal."""
    __tablename__ = "fingerprint_devices"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    device_id = db.Column(db.String(60), nullable=False, default="TEAM_OFFICE_Z305_01", index=True)
    name = db.Column(db.String(120), nullable=False, default="Main Lobby Team Office Z305")
    model = db.Column(db.String(80), nullable=False, default="Team Office Z305 (variant/firmware unverified)")
    connection_type = db.Column(db.String(30), nullable=False, default="TCP_IP")  # TCP_IP / PUSH_WEBHOOK / USB_SERIAL / MOCK
    ip_address = db.Column(db.String(64), nullable=True)
    port = db.Column(db.Integer, nullable=True)
    comm_key = db.Column(db.String(64), nullable=True, default="0")
    webhook_token_hash = db.Column(db.String(64), nullable=True)
    timeout_seconds = db.Column(db.Float, default=5.0, nullable=False)
    serial_number = db.Column(db.String(80), nullable=True)
    firmware_version = db.Column(db.String(80), nullable=True)
    status = db.Column(db.String(30), default="disconnected", nullable=False)  # network_reachable / disconnected / connecting / error / not_configured
    last_connected_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_activity_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    school = db.relationship("School")

    def to_dict(self, include_secrets: bool = False) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "device_id": self.device_id,
            "name": self.name,
            "model": self.model,
            "connection_type": self.connection_type,
            "ip_address": self.ip_address,
            "port": self.port,
            "comm_key": self.comm_key if include_secrets else ("••••••" if self.comm_key else ""),
            "has_comm_key": bool(self.comm_key and self.comm_key != "0"),
            "has_webhook_token": bool(self.webhook_token_hash),
            "timeout_seconds": self.timeout_seconds,
            "serial_number": self.serial_number or "Not Available",
            "firmware_version": self.firmware_version or "Not Available",
            "status": self.status,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "last_error": self.last_error,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
