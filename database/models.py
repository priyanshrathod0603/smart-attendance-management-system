"""SQLAlchemy ORM models for the face attendance system.

Design notes
------------
* ``Student.encoding`` stores the 512-d InsightFace embedding as JSONB so
  we can keep using the same schema regardless of which Postgres column
  type pgAdmin exposes. JSONB is available in PostgreSQL 17.
* ``Attendance.timestamp`` defaults to a timezone-aware ``now()`` at the
  database level as well, so inserts from background threads always have
  a value even if the Python side forgets to populate it.
* ``datetime.utcnow()`` is deprecated in Python 3.12+; we use
  ``datetime.now(timezone.utc)`` here and everywhere else.
* ``Teacher`` and ``UnknownFace`` were added so the new School ERP
  modules (Teachers, Reports) have a real storage target. The
  attendance table is unchanged.
"""
from datetime import datetime, timezone

from sqlalchemy import Index, JSON, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from .db import db


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime`` (replacement for ``utcnow``)."""
    return datetime.now(timezone.utc)


class Student(db.Model):
    """A student enrolled in the attendance system."""

    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    roll_no = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    class_name = db.Column(db.String(80), index=True)
    section = db.Column(db.String(40), index=True)
    mobile = db.Column(db.String(20))
    email = db.Column(db.String(120))
    photo_path = db.Column(db.String(255))
    # 512-d InsightFace embedding (list[float]). Stored as JSONB.
    # SQLite cannot compile PostgreSQL's JSONB. Use portable JSON locally and
    # retain JSONB when a PostgreSQL database is selected.
    encoding = db.Column(JSON().with_variant(JSONB, "postgresql"))
    # Optional list of additional angle encodings captured during the
    # 3-step face registration flow. Each entry is ``{"angle": "left",
    # "encoding": [...], "photo_path": "known_faces/..."}``. The column
    # is nullable so existing rows stay valid without a migration.
    extra_encodings = db.Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    school = db.relationship("School")
    attendance_records = db.relationship(
        "Attendance", backref="student", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "roll_no": self.roll_no,
            "name": self.name,
            "class_name": self.class_name,
            "section": self.section,
            "mobile": self.mobile,
            "email": self.email,
            "photo_path": self.photo_path,
            "has_encoding": self.encoding is not None,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Camera(db.Model):
    """A camera (webcam index or RTSP URL)."""

    __tablename__ = "cameras"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    source = db.Column(db.String(255), nullable=False)
    location = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    school = db.relationship("School")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "name": self.name,
            "source": self.source,
            "location": self.location,
            "is_active": self.is_active,
        }


class Attendance(db.Model):
    """One attendance mark per student per day.

    The time-management module adds four additive columns
    (``event_type``, ``session_id``, ``manual_edit``, ``edit_reason``)
    that are nullable / have safe defaults. Existing rows and the
    existing ``status`` column are kept 100% intact for backward
    compatibility with the original recognizer pipeline.
    """

    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    student_id = db.Column(
        db.Integer, db.ForeignKey("students.id"), nullable=False, index=True
    )
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"))
    timestamp = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    confidence = db.Column(db.Float)
    status = db.Column(db.String(20), default="present", nullable=False)

    # ---- time-management additions (nullable, default safe) ----
    event_type = db.Column(db.String(8), default="in", nullable=False)  # 'in' | 'out'
    session_id = db.Column(db.Integer, index=True)
    manual_edit = db.Column(db.Boolean, default=False, nullable=False)
    edit_reason = db.Column(db.String(255))

    camera = db.relationship("Camera")
    school = db.relationship("School")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "student_id": self.student_id,
            "student_name": self.student.name if self.student else None,
            "roll_no": self.student.roll_no if self.student else None,
            "class_name": self.student.class_name if self.student else None,
            "section": self.student.section if self.student else None,
            "camera_id": self.camera_id,
            "camera_name": self.camera.name if self.camera else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "confidence": self.confidence,
            "status": self.status,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "manual_edit": self.manual_edit,
            "edit_reason": self.edit_reason,
        }


class Teacher(db.Model):
    """A teacher record. CRUD-only, no attendance logic yet."""

    __tablename__ = "teachers"
    __table_args__ = (
        Index(
            "uq_teachers_school_fingerprint_user_id_v2",
            text("COALESCE(school_id, 0)"),
            "fingerprint_user_id",
            unique=True,
            sqlite_where=text("fingerprint_user_id IS NOT NULL"),
            postgresql_where=text("fingerprint_user_id IS NOT NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    teacher_id = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(120))
    department = db.Column(db.String(120), index=True)
    designation = db.Column(db.String(120), index=True)
    assigned_classes = db.Column(db.String(255))  # comma-separated, e.g. "5A,6B"
    mobile = db.Column(db.String(20))
    email = db.Column(db.String(120))
    photo_path = db.Column(db.String(255))
    encoding = db.Column(JSON().with_variant(JSONB, "postgresql"))
    # Optional list of additional angle encodings captured during the
    # 3-step face registration flow. Each entry is a dict with
    # ``angle``, ``encoding`` and ``photo_path`` keys. Mirrors the
    # Student.extra_encodings column so the Teacher module behaves
    # identically to the Student module.
    extra_encodings = db.Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    # Multi-modal attendance configuration: FACE or FINGERPRINT
    attendance_method = db.Column(db.String(20), default="FACE", nullable=False)
    fingerprint_user_id = db.Column(db.String(50), nullable=True, index=True)
    fingerprint_enrolled = db.Column(db.Boolean, default=False, nullable=False)
    fingerprint_enrolled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    school = db.relationship("School")

    @property
    def linked_employee(self):
        try:
            from hr.models import Employee
            from sqlalchemy import or_
            emp = Employee.query.filter_by(teacher_id=self.id).first()
            if not emp and self.teacher_id:
                raw_tid = str(self.teacher_id).strip()
                emp = Employee.query.filter(
                    or_(
                        Employee.employee_code == raw_tid,
                        Employee.employee_code == f"EMP-{raw_tid}"
                    )
                ).first()
                if emp and not emp.teacher_id:
                    emp.teacher_id = self.id
            return emp
        except Exception:
            return None

    def to_dict(self) -> dict:
        emp = self.linked_employee
        return {
            "id": self.id,
            "school_id": self.school_id,
            "teacher_id": self.teacher_id,
            "name": self.name,
            "subject": self.subject,
            "department": self.department,
            "designation": self.designation,
            "assigned_classes": self.assigned_classes,
            "mobile": self.mobile,
            "email": self.email,
            "photo_path": self.photo_path,
            "has_encoding": self.encoding is not None,
            "extra_encodings": self.extra_encodings or [],
            "attendance_method": (self.attendance_method or "FACE").upper(),
            "fingerprint_user_id": self.fingerprint_user_id,
            "fingerprint_enrolled": bool(self.fingerprint_enrolled),
            "fingerprint_enrolled_at": self.fingerprint_enrolled_at.isoformat() if self.fingerprint_enrolled_at else None,
            "is_active": self.is_active,
            "linked_employee_id": emp.id if emp else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UnknownFace(db.Model):
    """A face that was detected but didn't match any known student.

    Used by the Reports module to surface unrecognised faces even though
    no attendance is recorded for them.

    The time-management module adds two additive nullable columns
    (``image_path``, ``location``) and an ``alerted`` flag. The original
    ``snapshot_path`` / ``camera_id`` / ``timestamp`` / ``confidence``
    columns are kept as-is.
    """

    __tablename__ = "unknown_faces"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("cameras.id"))
    timestamp = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    confidence = db.Column(db.Float)
    snapshot_path = db.Column(db.String(255))

    # ---- time-management additions ----
    image_path = db.Column(db.String(255))
    location = db.Column(db.String(120))
    alerted = db.Column(db.Boolean, default=False, nullable=False)

    camera = db.relationship("Camera")
    school = db.relationship("School")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "camera_id": self.camera_id,
            "camera_name": self.camera.name if self.camera else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "confidence": self.confidence,
            "snapshot_path": self.snapshot_path,
            "image_path": self.image_path,
            "location": self.location,
            "alerted": self.alerted,
        }
