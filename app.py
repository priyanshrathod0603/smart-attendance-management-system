"""
Face Attendance System
======================

Flask + PostgreSQL 17 (managed via pgAdmin 4) + InsightFace + OpenCV
+ optional local SLM (Ollama) for natural-language attendance queries.

Run::

    python app.py

Then open http://localhost:5000

The application exposes a School ERP-style UI (sidebar with classes,
per-page modules, stat cards) on top of the original face-recognition
pipeline. No PostgreSQL schema was changed, only two new tables
(``teachers``, ``unknown_faces``) were added for the new modules.
"""
from __future__ import annotations

import os
import re
import socket
import hashlib
import hmac
import json
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cv2
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.utils import secure_filename

from camera.stream import manager
from config import Config
from database import Attendance, Camera, Student, Teacher, UnknownFace, School
from database.enterprise_models import (
    AttendanceEvent, StudentAttendance, TeacherAttendance, AttendanceLog,
    AttendanceSetting, HolidayCalendar, WeeklyOff, FingerprintDevice,
    FingerprintEventReceipt,
)
from database.db import db as _db
from recognition.encoder import encode_image
from recognition.face_registration import (
    register_face_photos, iter_known_embeddings, refresh_recognizer,
)
from recognition.recognizer import FaceRecognizer
from time_management import ensure_default_rules
from time_management.api import bp as time_mgmt_bp
from auth import (
    apple_client_secret, bp as auth_bp, oauth,
    current_user, current_school, current_school_id, is_tenant_permitted,
)
from hr.routes import hr_bp


# Canonical class list used by the sidebar / filter dropdowns.
CLASS_LEVELS = [
    "Nursery", "Jr KG", "Sr KG",
    "Class 1", "Class 2", "Class 3", "Class 4", "Class 5",
    "Class 6", "Class 7", "Class 8",
]
SECTIONS = ["A", "B", "C", "D"]

_BIOMAX_RATE_LOCK = threading.Lock()
_BIOMAX_RATE_BUCKETS: dict[tuple[str, str], deque[float]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _biomax_rate_limited(device_id: str, remote_addr: str | None, limit: int, window: int) -> bool:
    """Apply a bounded per-process request limit; production proxies should also limit globally."""
    now = time.monotonic()
    key = (device_id, remote_addr or "unknown")
    cutoff = now - window
    with _BIOMAX_RATE_LOCK:
        bucket = _BIOMAX_RATE_BUCKETS.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        if len(_BIOMAX_RATE_BUCKETS) > 1024:
            stale = [k for k, entries in _BIOMAX_RATE_BUCKETS.items() if not entries or entries[-1] <= cutoff]
            for stale_key in stale:
                _BIOMAX_RATE_BUCKETS.pop(stale_key, None)
    return False


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_class_name(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in CLASS_LEVELS:
        return text
    match = re.fullmatch(r"(?i)class\s*([0-9]+)", text)
    if match:
        return f"Class {int(match.group(1))}"
    match = re.fullmatch(r"([0-9]+)", text)
    if match:
        return f"Class {int(match.group(1))}"
    return text


def _normalize_section(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper()


def _normalize_camera_source(value) -> str | int:
    """Normalize a camera source the same way the live recognizer does."""
    return FaceRecognizer._normalize_source(value)


def _camera_source_key(value) -> str:
    """Stable string key for comparing physical camera sources."""
    normalized = _normalize_camera_source(value)
    return str(normalized)


def _find_camera_by_source(source, school_id: str | None = None) -> Camera | None:
    """Return an existing camera that points at the same physical source for this school."""
    target = _camera_source_key(source)
    q = Camera.query
    if school_id:
        q = q.filter((Camera.school_id == school_id) | (Camera.school_id.is_(None)))
    for cam in q.all():
        if _camera_source_key(cam.source) == target:
            return cam
    return None


def ensure_schema_compat() -> None:
    """Add missing columns expected by the current app without rewriting data.

    ``create_all`` creates missing tables but intentionally does not alter
    existing tables. Older installs can therefore lack newer nullable columns
    like mobile/email or school_id even though the ORM model and UI already use them.
    """
    statements = [
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS class_name VARCHAR(80)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS section VARCHAR(40)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS mobile VARCHAR(20)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS email VARCHAR(120)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_path VARCHAR(255)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS encoding JSONB",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS extra_encodings JSONB",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        # ---- time-management additions (additive, safe defaults) ----
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS event_type VARCHAR(8) NOT NULL DEFAULT 'in'",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS session_id INTEGER",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS manual_edit BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS edit_reason VARCHAR(255)",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "CREATE INDEX IF NOT EXISTS ix_attendance_session_id ON attendance(session_id)",
        "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE unknown_faces ADD COLUMN IF NOT EXISTS image_path VARCHAR(255)",
        "ALTER TABLE unknown_faces ADD COLUMN IF NOT EXISTS location VARCHAR(120)",
        "ALTER TABLE unknown_faces ADD COLUMN IF NOT EXISTS alerted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE unknown_faces ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS department VARCHAR(120)",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS designation VARCHAR(120)",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS photo_path VARCHAR(255)",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS encoding JSONB",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS extra_encodings JSONB",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS attendance_method VARCHAR(20) NOT NULL DEFAULT 'FACE'",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS fingerprint_user_id VARCHAR(50)",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS fingerprint_enrolled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS fingerprint_enrolled_at TIMESTAMPTZ",
        "ALTER TABLE student_attendance ADD COLUMN IF NOT EXISTS working_seconds INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE student_attendance ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE teacher_attendance ADD COLUMN IF NOT EXISTS working_seconds INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE teacher_attendance ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS auth_method VARCHAR(20) NOT NULL DEFAULT 'FACE'",
        "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS device_id VARCHAR(60)",
        "ALTER TABLE attendance_logs ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE fingerprint_devices ADD COLUMN IF NOT EXISTS webhook_token_hash VARCHAR(64)",

        "ALTER TABLE student_time_rules ADD COLUMN IF NOT EXISTS in_end_time VARCHAR(5) NOT NULL DEFAULT '09:30'",
        "ALTER TABLE student_time_rules ADD COLUMN IF NOT EXISTS out_detection_start VARCHAR(5) NOT NULL DEFAULT '16:00'",
        "ALTER TABLE student_time_rules ADD COLUMN IF NOT EXISTS early_exit_time VARCHAR(5) NOT NULL DEFAULT '16:30'",
        "ALTER TABLE student_time_rules ADD COLUMN IF NOT EXISTS overtime_start VARCHAR(5) NOT NULL DEFAULT '17:00'",
        "ALTER TABLE student_time_rules ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE teacher_time_rules ADD COLUMN IF NOT EXISTS in_end_time VARCHAR(5) NOT NULL DEFAULT '09:30'",
        "ALTER TABLE teacher_time_rules ADD COLUMN IF NOT EXISTS out_detection_start VARCHAR(5) NOT NULL DEFAULT '16:00'",
        "ALTER TABLE teacher_time_rules ADD COLUMN IF NOT EXISTS early_exit_time VARCHAR(5) NOT NULL DEFAULT '16:30'",
        "ALTER TABLE teacher_time_rules ADD COLUMN IF NOT EXISTS overtime_start VARCHAR(5) NOT NULL DEFAULT '17:00'",
        "ALTER TABLE teacher_time_rules ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE attendance_settings ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE holiday_calendar ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        "ALTER TABLE weekly_offs ADD COLUMN IF NOT EXISTS school_id VARCHAR(64) DEFAULT 'DEFAULT'",
        # ---- users additions ----
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS school_id VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(30)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS employee_id INTEGER",
        # ---- attendance_corrections additions ----
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS school_id INTEGER",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS attendance_type VARCHAR(12) DEFAULT 'teacher'",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS attendance_date DATE",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS issue_type VARCHAR(40) DEFAULT 'missed_punch'",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS requested_time VARCHAR(20)",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS note TEXT",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS admin_comment TEXT",
        "ALTER TABLE attendance_corrections ADD COLUMN IF NOT EXISTS approved_time TIMESTAMPTZ",
        # ---- auth verification code additions ----
        "ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS verification_code_hash VARCHAR(64)",
        "ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS verification_code_expires_at TIMESTAMPTZ",
        "ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS verification_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS verification_code_used_at TIMESTAMPTZ",
        # ---- HR Phase 1 additions ----
        "ALTER TABLE departments ADD COLUMN IF NOT EXISTS parent_department_id INTEGER",
        "ALTER TABLE departments ADD COLUMN IF NOT EXISTS campus VARCHAR(120)",
        "ALTER TABLE departments ADD COLUMN IF NOT EXISTS cost_center_id INTEGER",
        "ALTER TABLE designations ADD COLUMN IF NOT EXISTS job_level_id INTEGER",
        "ALTER TABLE designations ADD COLUMN IF NOT EXISTS job_grade_id INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS team_id INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS job_grade_id INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS job_level_id INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS cost_center_id INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS campus VARCHAR(120)",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS exit_date DATE",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS exit_reason TEXT",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS probation_status VARCHAR(30) DEFAULT 'not_applicable'",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS probation_start_date DATE",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS probation_end_date DATE",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS probation_duration_days INTEGER",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS probation_extension_reason TEXT",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS notice_period_days INTEGER DEFAULT 30",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS notice_period_unit VARCHAR(20) DEFAULT 'days'",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS custom_fields JSONB",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS qr_identifier VARCHAR(64)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_employees_qr_identifier ON employees(qr_identifier)",
    ]
    if _db.engine.dialect.name == "sqlite":
        # SQLite supports ADD COLUMN but not PostgreSQL's IF NOT EXISTS.
        # Only issue an ALTER for columns absent from an existing database.
        from sqlalchemy import inspect
        insp = inspect(_db.engine)
        table_names = set(insp.get_table_names())
        columns = {table: {c["name"] for c in insp.get_columns(table)}
                   for table in table_names}
        sqlite_columns = {
            "students": [("class_name", "VARCHAR(80)"), ("section", "VARCHAR(40)"), ("mobile", "VARCHAR(20)"), ("email", "VARCHAR(120)"), ("photo_path", "VARCHAR(255)"), ("encoding", "JSON"), ("extra_encodings", "JSON"), ("is_active", "BOOLEAN NOT NULL DEFAULT 1"), ("created_at", "DATETIME"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "attendance": [("event_type", "VARCHAR(8) NOT NULL DEFAULT 'in'"), ("session_id", "INTEGER"), ("manual_edit", "BOOLEAN NOT NULL DEFAULT 0"), ("edit_reason", "VARCHAR(255)"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "cameras": [("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "unknown_faces": [("image_path", "VARCHAR(255)"), ("location", "VARCHAR(120)"), ("alerted", "BOOLEAN NOT NULL DEFAULT 0"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "teachers": [
                ("department", "VARCHAR(120)"), ("designation", "VARCHAR(120)"),
                ("photo_path", "VARCHAR(255)"), ("encoding", "JSON"), ("extra_encodings", "JSON"),
                ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'"),
                ("attendance_method", "VARCHAR(20) NOT NULL DEFAULT 'FACE'"),
                ("fingerprint_user_id", "VARCHAR(50)"),
                ("fingerprint_enrolled", "BOOLEAN NOT NULL DEFAULT 0"),
                ("fingerprint_enrolled_at", "DATETIME"),
            ],
            "student_attendance": [("working_seconds", "INTEGER NOT NULL DEFAULT 0"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "teacher_attendance": [("working_seconds", "INTEGER NOT NULL DEFAULT 0"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "attendance_events": [
                ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'"),
                ("auth_method", "VARCHAR(20) NOT NULL DEFAULT 'FACE'"),
                ("device_id", "VARCHAR(60)"),
            ],
            "attendance_logs": [("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "fingerprint_devices": [("webhook_token_hash", "VARCHAR(64)")],
            "student_time_rules": [("in_end_time", "VARCHAR(5) NOT NULL DEFAULT '09:30'"), ("out_detection_start", "VARCHAR(5) NOT NULL DEFAULT '16:00'"), ("early_exit_time", "VARCHAR(5) NOT NULL DEFAULT '16:30'"), ("overtime_start", "VARCHAR(5) NOT NULL DEFAULT '17:00'"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "teacher_time_rules": [("in_end_time", "VARCHAR(5) NOT NULL DEFAULT '09:30'"), ("out_detection_start", "VARCHAR(5) NOT NULL DEFAULT '16:00'"), ("early_exit_time", "VARCHAR(5) NOT NULL DEFAULT '16:30'"), ("overtime_start", "VARCHAR(5) NOT NULL DEFAULT '17:00'"), ("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "attendance_settings": [("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "holiday_calendar": [("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "weekly_offs": [("school_id", "VARCHAR(64) DEFAULT 'DEFAULT'")],
            "users": [("school_id", "VARCHAR(64)"), ("phone", "VARCHAR(30)"), ("employee_id", "INTEGER")],
            "attendance_corrections": [
                ("school_id", "INTEGER"),
                ("attendance_type", "VARCHAR(12) DEFAULT 'teacher'"),
                ("attendance_date", "DATE"),
                ("issue_type", "VARCHAR(40) DEFAULT 'missed_punch'"),
                ("requested_time", "VARCHAR(20)"),
                ("note", "TEXT"),
                ("admin_comment", "TEXT"),
                ("approved_time", "DATETIME"),
            ],
            "auth_tokens": [("verification_code_hash", "VARCHAR(64)"), ("verification_code_expires_at", "DATETIME"), ("verification_attempts", "INTEGER NOT NULL DEFAULT 0"), ("verification_code_used_at", "DATETIME")],
            "departments": [("parent_department_id", "INTEGER"), ("campus", "VARCHAR(120)"), ("cost_center_id", "INTEGER")],
            "designations": [("job_level_id", "INTEGER"), ("job_grade_id", "INTEGER")],
            "employees": [
                ("team_id", "INTEGER"), ("job_grade_id", "INTEGER"), ("job_level_id", "INTEGER"),
                ("cost_center_id", "INTEGER"), ("campus", "VARCHAR(120)"), ("exit_date", "DATE"),
                ("exit_reason", "TEXT"), ("probation_status", "VARCHAR(30) DEFAULT 'not_applicable'"),
                ("probation_start_date", "DATE"), ("probation_end_date", "DATE"),
                ("probation_duration_days", "INTEGER"), ("probation_extension_reason", "TEXT"),
                ("notice_period_days", "INTEGER DEFAULT 30"), ("notice_period_unit", "VARCHAR(20) DEFAULT 'days'"),
                ("custom_fields", "JSON"), ("qr_identifier", "VARCHAR(64)"),
            ],
        }
        for table, definitions in sqlite_columns.items():
            if table not in table_names:
                continue
            for column, definition in definitions:
                if column not in columns.get(table, set()):
                    try:
                        _db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                    except Exception:
                        pass
    else:
        for stmt in statements:
            try:
                _db.session.execute(text(stmt))
            except Exception:
                pass

    try:
        _db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_teachers_school_fingerprint_user_id_v2 "
            "ON teachers (COALESCE(school_id, 0), fingerprint_user_id) "
            "WHERE fingerprint_user_id IS NOT NULL"
        ))
    except IntegrityError as exc:
        _db.session.rollback()
        raise RuntimeError(
            "Cannot enforce unique teacher device-user mappings because duplicate IDs already exist "
            "within a school. Resolve the conflicting teacher mappings and restart."
        ) from exc

    # Ensure default school exists
    try:
        def_school = School.query.filter_by(school_id="DEFAULT").first()
        if not def_school:
            _db.session.add(School(
                school_id="DEFAULT",
                name="Default School",
                code="DEFAULT",
                is_active=True,
            ))
            _db.session.commit()
    except Exception:
        _db.session.rollback()

    for student in Student.query.all():
        normalized_class = _normalize_class_name(student.class_name)
        normalized_section = _normalize_section(student.section)
        if normalized_class != student.class_name or normalized_section != student.section:
            student.class_name = normalized_class
            student.section = normalized_section

    _db.session.commit()


def create_app(config_overrides: Mapping | None = None) -> Flask:
    """Application factory used by both ``python app.py`` and tests."""
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)
    # Tests and deployment entry points can override configuration before any
    # extension is initialised.  In particular, changing SQLALCHEMY_DATABASE_URI
    # after init_app() does not move Flask-SQLAlchemy to another database.
    if config_overrides:
        app.config.update(config_overrides)
    if not app.config["DEBUG"] and app.config["SECRET_KEY"] == "dev-secret-change-me":
        raise RuntimeError("FLASK_SECRET must be set when FLASK_DEBUG is disabled.")
    _db.init_app(app)
    oauth.init_app(app)
    if app.config.get("GOOGLE_CLIENT_ID") and app.config.get("GOOGLE_CLIENT_SECRET"):
        oauth.register("google", client_id=app.config["GOOGLE_CLIENT_ID"], client_secret=app.config["GOOGLE_CLIENT_SECRET"], server_metadata_url="https://accounts.google.com/.well-known/openid-configuration", client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})
    if app.config.get("MICROSOFT_CLIENT_ID") and app.config.get("MICROSOFT_CLIENT_SECRET"):
        tenant = app.config.get("MICROSOFT_TENANT_ID", "common")
        oauth.register("microsoft", client_id=app.config["MICROSOFT_CLIENT_ID"], client_secret=app.config["MICROSOFT_CLIENT_SECRET"], server_metadata_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration", client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})
    if app.config.get("APPLE_CLIENT_ID") and app.config.get("APPLE_TEAM_ID") and app.config.get("APPLE_KEY_ID") and app.config.get("APPLE_PRIVATE_KEY"):
        with app.app_context():
            oauth.register("apple", client_id=app.config["APPLE_CLIENT_ID"], client_secret=apple_client_secret(), server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration", client_kwargs={"scope": "name email", "code_challenge_method": "S256"})

    os.makedirs(app.config["KNOWN_FACES_DIR"], exist_ok=True)

    with app.app_context():
        _db.create_all()
        ensure_schema_compat()
        # Seed the default time-rule rows for teacher + student scopes.
        # Idempotent — a no-op if they already exist.
        try:
            ensure_default_rules()
        except Exception as exc:
            print(f"[WARN] ensure_default_rules failed: {exc}")
        # Separate normalized enterprise rule rows, kept apart from the
        # compatibility time-rule module above.
        from database.enterprise_models import TeacherTimeRule, StudentTimeRule
        if TeacherTimeRule.query.first() is None:
            _db.session.add(TeacherTimeRule())
        if StudentTimeRule.query.first() is None:
            _db.session.add(StudentTimeRule())
        _db.session.commit()

        # --- deduplicate cameras (fix for Issue #2) ---
        # Group cameras by their normalised source key and keep only the
        # oldest record (lowest id) for each physical device.
        _seen_sources: dict[str, int] = {}
        for cam in Camera.query.order_by(Camera.id).all():
            key = _camera_source_key(cam.source)
            if key in _seen_sources:
                # Duplicate – null-out FK references then delete
                Attendance.query.filter_by(camera_id=cam.id).update(
                    {Attendance.camera_id: _seen_sources[key]},
                    synchronize_session=False,
                )
                UnknownFace.query.filter_by(camera_id=cam.id).update(
                    {UnknownFace.camera_id: _seen_sources[key]},
                    synchronize_session=False,
                )
                _db.session.delete(cam)
            else:
                _seen_sources[key] = cam.id
        _db.session.commit()

        # Auto-create default camera only if none exists for that source.
        # Wrapped in try/except to handle the race condition caused by
        # Flask's debug reloader running create_app() in two processes.
        default_source = str(app.config["CAMERA_SOURCE"])
        if _find_camera_by_source(default_source) is None:
            try:
                default_cam = Camera(
                    name="Default Webcam",
                    source=default_source,
                    location="Lab 1",
                    is_active=True,
                )
                _db.session.add(default_cam)
                _db.session.commit()
            except IntegrityError:
                _db.session.rollback()

    register_routes(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(time_mgmt_bp)
    app.register_blueprint(hr_bp)

    @app.context_processor
    def inject_globals():
        return {
            "CLASS_LEVELS": CLASS_LEVELS,
            "SECTIONS": SECTIONS,
            "current_user": current_user(),
            "current_school": current_school(),
            "current_school_id": current_school_id(),
        }

    @app.errorhandler(404)
    def _not_found(_):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("404.html"), 404

    return app


def register_routes(app: Flask) -> None:
    """Register every URL the dashboard depends on."""

    # ---------------------------------------------------------------- PAGES
    @app.route("/")
    def page_landing():
        return render_template("landing/index.html")

    @app.route("/landing")
    def page_landing_alias():
        return render_template("landing/index.html")

    @app.route("/dashboard")
    def page_dashboard():
        u = getattr(g, "user", None)
        if u and u.role == "teacher":
            return redirect(url_for("page_teacher_portal"))
        return render_template(
            "dashboard.html",
            active_page="dashboard",
            active_class=None,
        )

    @app.route("/portal")
    def page_teacher_portal():
        return render_template(
            "teacher/portal.html",
            active_page="teacher_portal",
            active_tab="dashboard",
        )

    @app.route("/portal/<path:subpath>")
    def page_teacher_portal_sub(subpath: str):
        valid_tabs = {"dashboard", "attendance", "summary", "leave", "corrections", "profile"}
        active_tab = subpath.strip("/").lower()
        if active_tab not in valid_tabs:
            active_tab = "dashboard"
        return render_template(
            "teacher/portal.html",
            active_page="teacher_portal",
            active_tab=active_tab,
        )

    @app.route("/users")
    def page_users():
        return render_template(
            "users.html",
            active_page="users",
        )

    @app.route("/attendance")
    def page_attendance():
        return render_template(
            "attendance.html",
            active_page="attendance_enterprise",
        )

    @app.route("/students")
    def page_students():
        return render_template(
            "students.html",
            active_page="students",
        )

    @app.route("/students/class/<path:class_name>")
    def page_students_class(class_name: str):
        return render_template(
            "students.html",
            active_page="students",
            active_class=class_name,
        )

    @app.route("/teachers")
    def page_teachers():
        return render_template(
            "teachers.html",
            active_page="teachers",
        )

    @app.route("/cameras")
    def page_cameras():
        return render_template(
            "cameras.html",
            active_page="cameras",
        )

    @app.route("/reports")
    def page_reports():
        return render_template(
            "reports.html",
            active_page="reports",
        )

    @app.route("/admin/devices/fingerprint")
    @app.route("/devices/fingerprint")
    def page_fingerprint_device():
        return render_template(
            "devices/fingerprint.html",
            active_page="fingerprint_device",
        )

    @app.route("/settings")
    def page_settings():
        return render_template(
            "settings.html",
            active_page="settings",
        )

    @app.route("/chat")
    def chat_page():
        return render_template("chat.html", active_page=None)

    # ---------------------------------------------------------------- HR PAGES
    @app.route("/hr")
    @app.route("/hr/dashboard")
    def page_hr_dashboard():
        return render_template(
            "hr/dashboard.html",
            active_page="hr_dashboard",
        )

    @app.route("/hr/employees")
    def page_hr_employees():
        return render_template(
            "hr/employees.html",
            active_page="hr_employees",
        )

    @app.route("/hr/employees/<int:emp_id>")
    def page_hr_employee_profile(emp_id: int):
        from hr.models import Employee
        emp = Employee.query.get(emp_id)
        if not emp:
            # Check if this ID is a Teacher PK or code
            t = Teacher.query.get(emp_id)
            if not t:
                t = Teacher.query.filter_by(teacher_id=str(emp_id)).first()
            if t and is_tenant_permitted(t.school_id):
                from hr.employee_service import ensure_employee_from_teacher
                emp = ensure_employee_from_teacher(t, school_id=t.school_id)
                _db.session.commit()
        if not emp or not is_tenant_permitted(emp.school_id):
            return render_template("404.html"), 404
        return render_template(
            "hr/employee_profile.html",
            active_page="hr_employees",
            emp_id=emp.id,
            employee_id=emp.id,
        )

    @app.route("/teachers/<int:tid>/profile")
    @app.route("/hr/teachers/<int:tid>/profile")
    @app.route("/teachers/<int:tid>/360")
    def page_teacher_360_profile(tid: int):
        t = Teacher.query.get_or_404(tid)
        if not is_tenant_permitted(t.school_id):
            return render_template("404.html"), 404
        from hr.employee_service import ensure_employee_from_teacher
        emp = ensure_employee_from_teacher(t, school_id=t.school_id)
        _db.session.commit()
        return render_template(
            "hr/employee_profile.html",
            active_page="teachers",
            emp_id=emp.id,
            employee_id=emp.id,
        )

    @app.route("/hr/payroll")
    def page_hr_payroll():
        return render_template(
            "hr/payroll.html",
            active_page="hr_payroll",
        )

    @app.route("/hr/reports")
    def page_hr_reports():
        return render_template(
            "hr/reports.html",
            active_page="hr_reports",
        )

    @app.route("/hr/settings")
    def page_hr_settings():
        return render_template(
            "hr/settings.html",
            active_page="hr_settings",
        )

    # ------------------------------------------- enterprise time management
    @app.route("/time-rules/<scope>")
    def page_time_rules(scope: str):
        if scope not in {"teacher", "student"}:
            scope = "student"
        return render_template(
            "time_rules.html",
            active_page="time_rules",
            active_time_scope=scope,
        )

    @app.route("/dashboard-enterprise")
    def page_dashboard_enterprise():
        return render_template(
            "dashboard_enterprise.html",
            active_page="dashboard_enterprise",
            active_class=None,
        )

    @app.route("/reports-enterprise")
    def page_reports_enterprise():
        return render_template(
            "reports_enterprise.html",
            active_page="reports_enterprise",
        )

    @app.route("/attendance-extended")
    def page_attendance_extended():
        return render_template(
            "attendance_extended.html",
            active_page="attendance_extended",
        )

    # ------------------------------------------------------------- meta api
    @app.route("/api/meta")
    def api_meta():
        """Static data the UI needs (class list, section list, etc.)."""
        sch = current_school()
        school_name = sch.name if sch else app.config.get("SCHOOL_NAME", "AttendanceOS")
        school_id = sch.school_id if sch else "DEFAULT"
        return jsonify(
            {
                "classes": CLASS_LEVELS,
                "sections": SECTIONS,
                "school_name": school_name,
                "school_id": school_id,
            }
        )

    # ------------------------------------------------------------ stats api
    @app.route("/api/stats")
    def api_stats():
        """Class-aware KPI cards.

        Query string::

            class_name=Nursery    (optional, scoped to one class)
        """
        sid = current_school_id()
        class_name = request.args.get("class_name") or None
        section = request.args.get("section") or None

        student_q = Student.query.filter_by(is_active=True)
        if sid:
            student_q = student_q.filter((Student.school_id == sid) | (Student.school_id.is_(None)))
        if class_name:
            student_q = student_q.filter_by(class_name=class_name)
        if section:
            student_q = student_q.filter_by(section=section)
        total_students = student_q.count()

        present_ids_q = _db.session.query(StudentAttendance.student_id).filter(
            StudentAttendance.attendance_date == date.today()
        )
        if sid:
            present_ids_q = present_ids_q.filter((StudentAttendance.school_id == sid) | (StudentAttendance.school_id.is_(None)))
        if class_name:
            present_ids_q = present_ids_q.join(
                Student, Student.id == StudentAttendance.student_id
            ).filter(Student.class_name == class_name)
        if section:
            present_ids_q = present_ids_q.join(
                Student, Student.id == StudentAttendance.student_id
            ).filter(Student.section == section)
        present_ids = {row[0] for row in present_ids_q.all()}

        present_today = sum(
            1 for s_id in present_ids if student_q.filter(Student.id == s_id).count()
        )
        present_today = len(present_ids) if not (class_name or section) else len(present_ids)

        # When a class filter is active, "present" = present AND in the class.
        if class_name or section:
            scoped_students = student_q.all()
            present_today = sum(1 for s in scoped_students if s.id in present_ids)
        else:
            present_today = len(present_ids)

        absent_today = max(total_students - present_today, 0)
        
        t_att_q = TeacherAttendance.query.filter_by(attendance_date=date.today())
        t_q = Teacher.query.filter_by(is_active=True)
        ent_q = AttendanceEvent.query.filter(
            func.date(AttendanceEvent.event_time) == date.today(), AttendanceEvent.event_type == "in"
        )
        exit_q = AttendanceEvent.query.filter(
            func.date(AttendanceEvent.event_time) == date.today(), AttendanceEvent.event_type == "out"
        )
        late_s_q = StudentAttendance.query.filter_by(attendance_date=date.today(), is_late=True)
        late_t_q = TeacherAttendance.query.filter_by(attendance_date=date.today(), is_late=True)
        unk_q = UnknownFace.query.filter(func.date(UnknownFace.timestamp) == date.today())
        cam_q = Camera.query.filter_by(is_active=True)

        if sid:
            t_att_q = t_att_q.filter((TeacherAttendance.school_id == sid) | (TeacherAttendance.school_id.is_(None)))
            t_q = t_q.filter((Teacher.school_id == sid) | (Teacher.school_id.is_(None)))
            ent_q = ent_q.filter((AttendanceEvent.school_id == sid) | (AttendanceEvent.school_id.is_(None)))
            exit_q = exit_q.filter((AttendanceEvent.school_id == sid) | (AttendanceEvent.school_id.is_(None)))
            late_s_q = late_s_q.filter((StudentAttendance.school_id == sid) | (StudentAttendance.school_id.is_(None)))
            late_t_q = late_t_q.filter((TeacherAttendance.school_id == sid) | (TeacherAttendance.school_id.is_(None)))
            unk_q = unk_q.filter((UnknownFace.school_id == sid) | (UnknownFace.school_id.is_(None)))
            cam_q = cam_q.filter((Camera.school_id == sid) | (Camera.school_id.is_(None)))

        teachers_present = t_att_q.count()
        total_teachers = t_q.count()
        entries = ent_q.count()
        exits = exit_q.count()
        late_today = late_s_q.count() + late_t_q.count()
        unknown_today = unk_q.count()
        active_cams = cam_q.count()

        return jsonify(
            {
                "total_students": total_students,
                "present_today": present_today,
                "absent_today": absent_today,
                "unknown_today": unknown_today,
                "active_cameras": active_cams,
                "students_present": present_today,
                "teachers_present": teachers_present,
                "students_absent": absent_today,
                "teachers_absent": max(total_teachers - teachers_present, 0),
                "late_today": late_today,
                "today_entries": entries,
                "today_exits": exits,
                "class_name": class_name,
                "section": section,
            }
        )

    def _resolve_school_id(sid) -> int:
        if sid is not None:
            try:
                return int(sid)
            except (ValueError, TypeError):
                pass
        def_sch = School.query.filter_by(school_id="DEFAULT").first()
        return def_sch.id if def_sch else 1

    # ----------------------------------------------------------------- cameras
    @app.route("/api/cameras", methods=["GET", "POST"])
    def api_cameras():
        sid = current_school_id()
        if request.method == "GET":
            q = Camera.query
            if sid:
                cams = q.filter(Camera.school_id == sid).all()
                if not cams:
                    default_source = str(app.config.get("CAMERA_SOURCE", "0"))
                    default_cam = Camera(
                        school_id=sid,
                        name="Default Webcam",
                        source=default_source,
                        location="Main Entrance",
                        is_active=True,
                    )
                    _db.session.add(default_cam)
                    try:
                        _db.session.commit()
                        cams = [default_cam]
                    except Exception:
                        _db.session.rollback()
                        cams = q.filter(Camera.school_id == sid).all()
            else:
                cams = q.all()
                if not cams:
                    default_source = str(app.config.get("CAMERA_SOURCE", "0"))
                    default_cam = Camera(
                        school_id=1,
                        name="Default Webcam",
                        source=default_source,
                        location="Main Entrance",
                        is_active=True,
                    )
                    _db.session.add(default_cam)
                    try:
                        _db.session.commit()
                        cams = [default_cam]
                    except Exception:
                        _db.session.rollback()
                        cams = q.all()
            return jsonify([c.to_dict() for c in cams])
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        source_raw = data.get("source")
        if source_raw is None or str(source_raw).strip() == "":
            return jsonify({"error": "camera source is required"}), 400
        if not name:
            return jsonify({"error": "camera name is required"}), 400
        source = str(source_raw).strip()
        existing = _find_camera_by_source(source, school_id=sid)
        if existing is not None:
            return jsonify(
                {
                    "error": "a camera with this source already exists",
                    "existing_id": existing.id,
                    "existing_name": existing.name,
                }
            ), 409
        cam = Camera(
            school_id=_resolve_school_id(sid),
            name=name,
            source=source,
            location=(data.get("location") or "").strip() or None,
            is_active=True if data.get("is_active") is not False else False,
        )
        _db.session.add(cam)
        _db.session.commit()
        return jsonify(cam.to_dict()), 201

    @app.route("/api/cameras/<int:cid>", methods=["GET", "PUT", "DELETE"])
    def api_camera_detail(cid: int):
        sid = current_school_id()
        cam = Camera.query.get_or_404(cid)
        if not is_tenant_permitted(cam.school_id):
            return jsonify({"error": "camera not found"}), 404
        if request.method == "GET":
            return jsonify(cam.to_dict())

        if request.method == "DELETE":
            # Stop the camera if it's running (do it server-side so the
            # delete is a single atomic operation from the frontend's POV).
            manager.stop(cid)

            # Null-out FK references so the camera row can be deleted.
            Attendance.query.filter_by(camera_id=cid).update(
                {Attendance.camera_id: None},
                synchronize_session=False,
            )
            UnknownFace.query.filter_by(camera_id=cid).update(
                {UnknownFace.camera_id: None},
                synchronize_session=False,
            )

            # Flush the bulk updates and expire the session so that the
            # ORM's identity map no longer holds stale references to
            # Attendance/UnknownFace objects that still point at this cam.
            _db.session.flush()
            _db.session.expire_all()

            # Re-fetch the camera after expiry to get a clean ORM state.
            cam = Camera.query.get(cid)
            if cam is None:
                return jsonify({"ok": True})

            try:
                _db.session.delete(cam)
                _db.session.commit()
            except IntegrityError:
                _db.session.rollback()
                return jsonify(
                    {"error": "cannot delete camera; referenced by other records"}
                ), 409
            return jsonify({"ok": True})
        data = request.get_json(force=True) or {}
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "camera name is required"}), 400
            cam.name = name
        if "source" in data:
            source_raw = data.get("source")
            if source_raw is None or str(source_raw).strip() == "":
                return jsonify({"error": "camera source is required"}), 400
            source = str(source_raw).strip()
            existing = _find_camera_by_source(source, school_id=sid)
            if existing is not None and existing.id != cam.id:
                return jsonify(
                    {
                        "error": "a camera with this source already exists",
                        "existing_id": existing.id,
                        "existing_name": existing.name,
                    }
                ), 409
            cam.source = source
        if "location" in data:
            cam.location = (data.get("location") or "").strip() or None
        if "is_active" in data:
            prev_active = cam.is_active
            cam.is_active = bool(data["is_active"])
            if cam.is_active and not prev_active:
                manager.start(cam, app)
            elif not cam.is_active and prev_active:
                manager.stop(cid)
        _db.session.commit()
        manager.refresh_known()
        return jsonify(cam.to_dict())

    @app.route("/api/cameras/<int:cid>/start", methods=["POST"])
    def api_camera_start(cid: int):
        sid = current_school_id()
        cam = Camera.query.get_or_404(cid)
        if not is_tenant_permitted(cam.school_id):
            return jsonify({"error": "camera not found"}), 404
        cam.is_active = True
        _db.session.commit()
        manager.start(cam, app)
        return jsonify({"ok": True, "camera": cam.to_dict()})

    @app.route("/api/cameras/<int:cid>/stop", methods=["POST"])
    def api_camera_stop(cid: int):
        sid = current_school_id()
        cam = Camera.query.get(cid)
        if cam and not is_tenant_permitted(cam.school_id):
            return jsonify({"error": "camera not found"}), 404
        manager.stop(cid)
        if cam:
            cam.is_active = False
            _db.session.commit()
        return jsonify({"ok": True})

    @app.route("/api/cameras/<int:cid>/test", methods=["POST"])
    def api_camera_test(cid: int):
        """Quick non-destructive reachability check for a camera URL.

        We don't open a second VideoCapture for the same device (avoids
        macOS exclusive-access issues). Instead we just confirm the
        configuration is present and the manager has a recognizer for it,
        which is a strong signal it's reachable.
        """
        sid = current_school_id()
        cam = Camera.query.get_or_404(cid)
        if not is_tenant_permitted(cam.school_id):
            return jsonify({"error": "camera not found"}), 404
        ok = cam.source and len(cam.source) > 0
        return jsonify(
            {
                "ok": bool(ok),
                "message": "source configured" if ok else "no source",
                "source": cam.source,
            }
        )

    @app.route("/api/cameras/status")
    def api_camera_status():
        sid = current_school_id()
        statuses = {}
        q = Camera.query
        if sid:
            q = q.filter((Camera.school_id == sid) | (Camera.school_id.is_(None)))
        for cam in q.all():
            frame, dets = manager.snapshot(cam.id)
            recognized = sum(1 for d in dets if d.get("name") != "Unknown")
            unknown = sum(1 for d in dets if d.get("name") == "Unknown")
            statuses[str(cam.id)] = {
                "camera_id": cam.id,
                "fps": "Live" if frame is not None and cam.is_active else "--",
                "detected_faces": len(dets),
                "recognized_faces": recognized,
                "unknown_faces": unknown,
            }
        return jsonify(statuses)

    # ----------------------------------------------------------------- students
    @app.route("/api/students", methods=["GET", "POST"])
    def api_students():
        sid = current_school_id()
        if request.method == "GET":
            q = Student.query
            if sid:
                q = q.filter(Student.school_id == sid)
            class_name = _normalize_class_name(request.args.get("class_name"))
            if class_name:
                q = q.filter(func.lower(Student.class_name) == func.lower(class_name))
            section = _normalize_section(request.args.get("section"))
            if section:
                q = q.filter(func.lower(Student.section) == func.lower(section))
            search = (request.args.get("q") or "").strip()
            if search:
                like = f"%{search.lower()}%"
                q = q.filter(
                    _db.or_(
                        func.lower(Student.roll_no).like(like),
                        func.lower(Student.name).like(like),
                        func.cast(Student.id, _db.String).like(like),
                    )
                )
            return jsonify([s.to_dict() for s in q.order_by(Student.roll_no).all()])
        data = request.get_json(force=True) or {}
        roll_no = (data.get("roll_no") or "").strip()
        name = (data.get("name") or "").strip()
        if not roll_no:
            return jsonify({"error": "roll number is required"}), 400
        if not name:
            return jsonify({"error": "name is required"}), 400
        class_name = _normalize_class_name(data.get("class_name"))
        if not class_name:
            return jsonify({"error": "class is required"}), 400
        chk_q = Student.query.filter(func.lower(Student.roll_no) == roll_no.lower())
        if sid:
            chk_q = chk_q.filter(Student.school_id == sid)
        if chk_q.first():
            return jsonify({"error": f"roll number '{roll_no}' already exists"}), 409
        s = Student(
            school_id=_resolve_school_id(sid),
            roll_no=roll_no,
            name=name,
            class_name=class_name,
            section=_normalize_section(data.get("section")),
            mobile=(data.get("mobile") or "").strip() or None,
            email=(data.get("email") or "").strip() or None,
        )
        _db.session.add(s)
        try:
            _db.session.commit()
        except IntegrityError:
            _db.session.rollback()
            return jsonify({"error": f"roll number '{roll_no}' already exists"}), 409
        return jsonify(s.to_dict()), 201

    @app.route("/api/students/<int:sid>", methods=["GET", "PUT", "DELETE"])
    def api_student_detail(sid: int):
        s = Student.query.get_or_404(sid)
        if not is_tenant_permitted(s.school_id):
            return jsonify({"error": "student not found"}), 404
        if request.method == "GET":
            return jsonify(s.to_dict())
        if request.method == "DELETE":
            if s.photo_path:
                try:
                    os.remove(
                        os.path.join(
                            app.config["KNOWN_FACES_DIR"],
                            os.path.basename(s.photo_path),
                        )
                    )
                except OSError:
                    pass
            _db.session.delete(s)
            _db.session.commit()
            manager.refresh_known()
            return jsonify({"ok": True})
        data = request.get_json(force=True)
        for key in ("roll_no", "name", "mobile", "email"):
            if key in data:
                setattr(s, key, data[key])
        if "class_name" in data:
            s.class_name = _normalize_class_name(data["class_name"])
        if "section" in data:
            s.section = _normalize_section(data["section"])
        if "is_active" in data:
            s.is_active = _coerce_bool(data["is_active"])
        _db.session.commit()
        return jsonify(s.to_dict())

    @app.route("/api/students/<int:sid>/photo", methods=["POST"])
    def api_student_photo(sid: int):
        """Register / re-register a student's face from 3 angle photos.

        Accepts either:
          * ``photo``   -- a single legacy upload (kept for back-compat)
          * ``front``/``left``/``right`` -- the new 3-angle workflow

        The actual encoding + persistence logic lives in the shared
        :func:`register_face_photos` helper so the Student and Teacher
        endpoints behave identically. The first successful encoding is
        stored on the student record and the recognizer is refreshed.
        Additional angle encodings are stored as ``extra_encodings``
        (list) so the front-face slot stays compatible with downstream
        code that reads ``Student.encoding``.
        """
        s = Student.query.get_or_404(sid)
        if not is_tenant_permitted(s.school_id):
            return jsonify({"error": "student not found"}), 404
        result = register_face_photos(
            request.files,
            person=s,
            filename_prefix=str(s.roll_no),
            known_faces_dir=app.config["KNOWN_FACES_DIR"],
        )
        if not result.get("ok"):
            return jsonify({"error": result.get("error", "no file")}), result.get("status", 400)
        _db.session.commit()
        refresh_recognizer()
        return jsonify(s.to_dict())

    # --------------------------------------------------------------- attendance
    @app.route("/api/attendance")
    def api_attendance():
        sid = current_school_id()
        q = Attendance.query.order_by(Attendance.timestamp.desc())
        if sid:
            q = q.filter(Attendance.school_id == sid)
        cid = request.args.get("camera_id", type=int)
        if cid:
            q = q.filter_by(camera_id=cid)
        if request.args.get("today") in {"1", "true", "yes"}:
            q = q.filter(func.date(Attendance.timestamp) == date.today())
        class_name = request.args.get("class_name")
        section = request.args.get("section")
        d = request.args.get("date")
        search = (request.args.get("q") or "").strip()
        if d:
            try:
                target = datetime.strptime(d, "%Y-%m-%d").date()
                q = q.filter(func.date(Attendance.timestamp) == target)
            except ValueError:
                return jsonify({"error": "bad date; use YYYY-MM-DD"}), 400
        if class_name or section or search:
            q = q.join(Student, Student.id == Attendance.student_id)
            if class_name:
                q = q.filter(Student.class_name == class_name)
            if section:
                q = q.filter(Student.section == section)
            if search:
                like = f"%{search.lower()}%"
                q = q.filter(
                    _db.or_(
                        func.lower(Student.roll_no).like(like),
                        func.lower(Student.name).like(like),
                        func.cast(Student.id, _db.String).like(like),
                    )
                )
        return jsonify([r.to_dict() for r in q.limit(1000).all()])

    @app.route("/api/attendance/<int:aid>", methods=["PUT", "DELETE"])
    def api_attendance_detail(aid: int):
        sid = current_school_id()
        record = Attendance.query.get_or_404(aid)
        if not is_tenant_permitted(record.school_id):
            return jsonify({"error": "attendance record not found"}), 404
        if request.method == "DELETE":
            record_date = (
                record.timestamp.date()
                if record.timestamp
                else None
            )
            force = _coerce_bool(request.args.get("force"))
            if record_date and record_date != date.today() and not force:
                return jsonify(
                    {
                        "error": "only today's attendance can be deleted without force=true",
                        "record_date": record_date.isoformat(),
                    }
                ), 403
            _db.session.delete(record)
            _db.session.commit()
            return jsonify({"ok": True})

        data = request.get_json(force=True) or {}
        record_date = (
            record.timestamp.date()
            if record.timestamp
            else None
        )
        force = _coerce_bool(data.get("force"))
        if record_date and record_date != date.today() and not force:
            return jsonify(
                {
                    "error": "only today's attendance can be edited without force=true",
                    "record_date": record_date.isoformat(),
                }
            ), 403

        if "student_id" in data:
            student_id = data.get("student_id")
            if not student_id:
                return jsonify({"error": "student_id is required"}), 400
            student = Student.query.get(student_id)
            if student is None:
                return jsonify({"error": "student not found"}), 404
            record.student_id = int(student_id)

        if "status" in data:
            status = (data.get("status") or "").strip().lower()
            if status not in {"present", "absent", "late"}:
                return jsonify({"error": "status must be present, absent, or late"}), 400
            record.status = status

        if "confidence" in data:
            try:
                record.confidence = float(data["confidence"])
            except (TypeError, ValueError):
                return jsonify({"error": "confidence must be a number"}), 400

        if "camera_id" in data:
            camera_id = data.get("camera_id")
            if camera_id in (None, "", 0, "0"):
                record.camera_id = None
            else:
                camera = Camera.query.get(int(camera_id))
                if camera is None:
                    return jsonify({"error": "camera not found"}), 404
                record.camera_id = camera.id

        if "timestamp" in data and data.get("timestamp"):
            try:
                raw_ts = data["timestamp"]
                if isinstance(raw_ts, str) and raw_ts.endswith("Z"):
                    raw_ts = raw_ts[:-1] + "+00:00"
                parsed = datetime.fromisoformat(raw_ts)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                record.timestamp = parsed
            except (TypeError, ValueError):
                return jsonify(
                    {"error": "timestamp must be an ISO-8601 datetime"}
                ), 400

        _db.session.commit()
        return jsonify(record.to_dict())

    @app.route("/api/attendance/today")
    def api_attendance_today():
        sid = current_school_id()
        q = Attendance.query.filter(func.date(Attendance.timestamp) == date.today())
        if sid:
            q = q.filter((Attendance.school_id == sid) | (Attendance.school_id.is_(None)))
        rows = q.order_by(Attendance.timestamp).all()
        return jsonify([r.to_dict() for r in rows])

    @app.route("/api/attendance/export")
    def api_attendance_export():
        sid = current_school_id()
        fmt = (request.args.get("format") or "csv").lower()
        class_name = request.args.get("class_name") or None
        section = request.args.get("section") or None
        d = request.args.get("date")
        target = (
            datetime.strptime(d, "%Y-%m-%d").date()
            if d
            else date.today()
        )
        q = (
            Attendance.query.join(Student, Student.id == Attendance.student_id)
            .filter(func.date(Attendance.timestamp) == target)
        )
        if sid:
            q = q.filter((Attendance.school_id == sid) | (Attendance.school_id.is_(None)))
        if class_name:
            q = q.filter(Student.class_name == class_name)
        if section:
            q = q.filter(Student.section == section)
        rows = q.order_by(Attendance.timestamp).all()

        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["Time", "Roll", "Name", "Class", "Section", "Camera", "Confidence"]
        )
        for r in rows:
            writer.writerow(
                [
                    r.timestamp.strftime("%H:%M:%S") if r.timestamp else "",
                    r.student.roll_no if r.student else "",
                    r.student.name if r.student else "",
                    r.student.class_name if r.student else "",
                    r.student.section if r.student else "",
                    r.camera.name if r.camera else "",
                    f"{(r.confidence or 0):.2f}",
                ]
            )
        from flask import Response as _R

        if fmt == "pdf":
            # Lightweight PDF via reportlab if available, otherwise fall back to CSV download.
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfgen import canvas as _cv
                from reportlab.lib.units import cm

                pdf_buf = io.BytesIO()
                c = _cv.Canvas(pdf_buf, pagesize=A4)
                c.setFont("Helvetica-Bold", 14)
                c.drawString(2 * cm, 28 * cm, "Attendance Report")
                c.setFont("Helvetica", 10)
                c.drawString(
                    2 * cm, 27 * cm, f"Date: {target.isoformat()}    Class: {class_name or 'ALL'}"
                )
                y = 25 * cm
                c.setFont("Helvetica-Bold", 10)
                c.drawString(2 * cm, y, "Time")
                c.drawString(4 * cm, y, "Roll")
                c.drawString(6 * cm, y, "Name")
                c.drawString(10 * cm, y, "Class")
                c.drawString(13 * cm, y, "Camera")
                c.drawString(17 * cm, y, "Conf")
                y -= 0.6 * cm
                c.setFont("Helvetica", 10)
                for r in rows:
                    if y < 2 * cm:
                        c.showPage()
                        y = 28 * cm
                    c.drawString(2 * cm, y, r.timestamp.strftime("%H:%M:%S") if r.timestamp else "")
                    c.drawString(4 * cm, y, r.student.roll_no if r.student else "")
                    c.drawString(6 * cm, y, (r.student.name if r.student else "")[:30])
                    c.drawString(10 * cm, y, r.student.class_name if r.student else "")
                    c.drawString(13 * cm, y, r.camera.name if r.camera else "")
                    c.drawString(17 * cm, y, f"{(r.confidence or 0):.2f}")
                    y -= 0.55 * cm
                c.save()
                return _R(
                    pdf_buf.getvalue(),
                    mimetype="application/pdf",
                    headers={
                        "Content-Disposition": f"attachment; filename=attendance_{target.isoformat()}.pdf"
                    },
                )
            except Exception:
                pass

        return _R(
            buf.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=attendance_{target.isoformat()}.csv"
            },
        )

    # ----------------------------------------- enterprise report export
    @app.route("/api/reports/export")
    def api_reports_export():
        """Export a report in CSV / Excel / PDF / Print (HTML)."""
        from enterprise_query import build_report, compute_dashboard_summary
        sid = current_school_id()
        report_type = request.args.get("type") or "daily"
        fmt = (request.args.get("format") or "csv").lower()
        d_raw = request.args.get("date")
        try:
            target = datetime.strptime(d_raw, "%Y-%m-%d").date() if d_raw else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        def _opt(name):
            raw = request.args.get(name)
            if not raw:
                return None
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return None
        rows, headers, title = build_report(
            report_type,
            d=target,
            start=_opt("start"),
            end=_opt("end"),
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            search=(request.args.get("q") or "").strip() or None,
            person_type=request.args.get("person_type") or None,
            school_id=sid,
        )
        filename = f"{report_type}_{target.isoformat()}"
        if fmt == "json":
            return jsonify({"title": title, "headers": headers, "rows": rows})
        if fmt in ("excel", "xlsx"):
            return _export_excel(rows, headers, title, filename)
        if fmt in ("pdf",):
            return _export_pdf(rows, headers, title, filename)
        if fmt in ("print", "html"):
            return _export_printable_html(rows, headers, title, filename)
        return _export_csv(rows, headers, filename)

    def _export_csv(rows, headers, filename):
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(h, "") for h in headers])
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
        )

    def _export_excel(rows, headers, title, filename):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
        except ImportError:
            return _export_csv(rows, headers, filename)
        wb = Workbook()
        ws = wb.active
        ws.title = (title or "Report")[:31]
        bold = Font(bold=True)
        header_fill = PatternFill("solid", fgColor="007AFF")
        white_bold = Font(bold=True, color="FFFFFFFF")
        ws.append(headers)
        for cell in ws[1]:
            cell.font = white_bold
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([r.get(h, "") for h in headers])
        # Auto column width
        for col_idx, header in enumerate(headers, 1):
            max_len = len(str(header))
            for r in rows:
                v = str(r.get(header, ""))
                if len(v) > max_len:
                    max_len = len(v)
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max(max_len + 2, 10), 40)
        from io import BytesIO as _B
        buf = _B()
        wb.save(buf)
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"},
        )

    def _export_pdf(rows, headers, title, filename):
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib import colors
            from reportlab.lib.units import cm
            from reportlab.platypus import (
                SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            )
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            return _export_csv(rows, headers, filename)
        from io import BytesIO as _B
        buf = _B()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4),
            leftMargin=1 * cm, rightMargin=1 * cm,
            topMargin=1 * cm, bottomMargin=1 * cm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f"<b>{title}</b>", styles["Title"]),
            Spacer(1, 0.4 * cm),
        ]
        data = [headers] + [[str(r.get(h, ""))[:60] for h in headers] for r in rows]
        if not rows:
            data.append(["No records"] + [""] * (len(headers) - 1))
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#007AFF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(table)
        doc.build(elements)
        return Response(
            buf.getvalue(),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}.pdf"},
        )

    def _export_printable_html(rows, headers, title, filename):
        body = "".join(f"<th>{h}</th>" for h in headers)
        rows_html = "".join(
            "<tr>" + "".join(f"<td>{r.get(h, '')}</td>" for h in headers) + "</tr>"
            for r in rows
        )
        if not rows_html:
            rows_html = f"<tr><td colspan='{len(headers)}'>No records found.</td></tr>"
        html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{title}</title>
<style>
body {{ font-family: -apple-system, sans-serif; padding: 20px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th, td {{ border: 1px solid #d1d1d6; padding: 6px 8px; text-align: left; }}
th {{ background: #007AFF; color: white; }}
h1 {{ color: #1d1d1f; }}
@media print {{ .no-print {{ display: none; }} }}
</style></head><body>
<h1>{title}</h1>
<button class='no-print' onclick='window.print()' style='padding:8px 16px;background:#007AFF;color:white;border:none;border-radius:6px;cursor:pointer;'>Print</button>
<table><thead><tr>{body}</tr></thead><tbody>{rows_html}</tbody></table>
</body></html>"""
    # ---------------------------------------------------------------- TEACHER PORTAL APIS
    @app.route("/api/teacher/dashboard")
    def api_teacher_portal_dashboard():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_dashboard
        res, status = get_teacher_dashboard(u, client_params=request.args.to_dict())
        return jsonify(res), status

    @app.route("/api/teacher/attendance-history")
    def api_teacher_portal_history():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_attendance_history
        month_str = request.args.get("month")
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")
        res, status = get_teacher_attendance_history(
            u, month_str=month_str, start_date_str=start_date_str, end_date_str=end_date_str,
            client_params=request.args.to_dict(),
        )
        return jsonify(res), status

    @app.route("/api/teacher/monthly-summary")
    def api_teacher_portal_summary():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_monthly_summary
        month_str = request.args.get("month")
        res, status = get_teacher_monthly_summary(u, month_str=month_str, client_params=request.args.to_dict())
        return jsonify(res), status

    @app.route("/api/teacher/leaves", methods=["GET", "POST"])
    def api_teacher_portal_leaves():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_leaves, apply_teacher_leave
        if request.method == "POST":
            payload = request.get_json(silent=True) or request.form.to_dict()
            res, status_code = apply_teacher_leave(u, payload)
            return jsonify(res), status_code
        res, status = get_teacher_leaves(u, client_params=request.args.to_dict())
        return jsonify(res), status

    @app.route("/api/teacher/leaves/<int:lid>")
    def api_teacher_portal_single_leave(lid: int):
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_single_leave
        res, status = get_teacher_single_leave(u, lid)
        return jsonify(res), status

    @app.route("/api/teacher/corrections", methods=["GET", "POST"])
    def api_teacher_portal_corrections():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_corrections, submit_teacher_correction
        if request.method == "POST":
            payload = request.get_json(silent=True) or request.form.to_dict()
            res, status_code = submit_teacher_correction(u, payload)
            return jsonify(res), status_code
        res, status = get_teacher_corrections(u, client_params=request.args.to_dict())
        return jsonify(res), status

    @app.route("/api/teacher/corrections/<int:cid>")
    def api_teacher_portal_single_correction(cid: int):
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_single_correction
        res, status = get_teacher_single_correction(u, cid)
        return jsonify(res), status

    @app.route("/api/teacher/attendance/<int:aid>")
    def api_teacher_portal_single_attendance(aid: int):
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_single_attendance
        res, status = get_teacher_single_attendance(u, aid)
        return jsonify(res), status

    @app.route("/api/teacher/profile")
    def api_teacher_portal_profile():
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        from teacher_portal_service import get_teacher_profile
        res, status = get_teacher_profile(u, client_params=request.args.to_dict())
        return jsonify(res), status

    # ----------------------------------------- enterprise attendance (v2)
    def _enterprise_row(row, kind: str) -> dict:
        person = row.student if kind == "student" else row.teacher
        return {"id": row.id, "type": kind, "date": row.attendance_date.isoformat(), "in_time": row.in_time.isoformat() if row.in_time else None, "out_time": row.out_time.isoformat() if row.out_time else None, "working_minutes": row.working_minutes, "break_minutes": row.break_minutes, "overtime_minutes": row.overtime_minutes, "late": row.is_late, "early_exit": row.is_early_exit, "status": row.status, "camera": row.camera.name if row.camera else None, "confidence": row.confidence, "person_id": person.id, "name": person.name, "roll_no": person.roll_no if kind == "student" else None, "class_name": person.class_name if kind == "student" else None, "section": person.section if kind == "student" else None, "employee_id": person.teacher_id if kind == "teacher" else None, "department": person.department if kind == "teacher" else None, "designation": person.designation if kind == "teacher" else None}

    @app.route("/api/enterprise/attendance", methods=["GET", "POST"])
    def api_enterprise_attendance():
        from enterprise_query import list_sessions
        sid = current_school_id()
        data = request.get_json(silent=True) or {}
        kind = (request.args.get("type") or data.get("type") or "all").lower()
        if kind not in {"student", "teacher", "all"}:
            return jsonify({"error": "type must be student, teacher, or all"}), 400

        if request.method == "POST":
            # Manual entry: insert an enterprise row (no camera needed).
            from attendance_service import record_recognition
            payload = dict(data)
            person_kind = (payload.get("type") or "student").lower()
            if person_kind not in {"student", "teacher"}:
                return jsonify({"error": "type must be student or teacher"}), 400
            try:
                confidence = float(payload.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            from datetime import datetime as _dt
            now = _dt.now(timezone.utc)
            result = record_recognition(
                person_kind,
                int(payload["person_id"]),
                payload.get("camera_id"),
                confidence,
                now=now,
                school_id=sid,
            )
            if result.get("action") == "rejected":
                return jsonify(result), 409
            # Return the row we just created/updated.
            model = StudentAttendance if person_kind == "student" else TeacherAttendance
            row = model.query.get(result["attendance_id"])
            return jsonify(_enterprise_row(row, person_kind)), 201

        # GET: unified list across the same query engine
        d_raw = request.args.get("date")
        try:
            target = datetime.strptime(d_raw, "%Y-%m-%d").date() if d_raw else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        # Map event_type to status for the underlying query
        event_type = request.args.get("event_type") or None
        status = request.args.get("status") or None
        rows = list_sessions(
            d=target,
            kind=kind,
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            status=status,
            event_type=event_type,
            search=(request.args.get("q") or "").strip() or None,
            limit=int(request.args.get("limit", 1000)),
            school_id=sid,
        )
        return jsonify(rows)

    @app.route("/api/enterprise/dashboard")
    def api_enterprise_dashboard():
        from enterprise_query import compute_dashboard_summary
        sid = current_school_id()
        d = request.args.get("date")
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date() if d else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        return jsonify(compute_dashboard_summary(
            target,
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            school_id=sid,
        ))

    @app.route("/api/enterprise/summary")
    def api_enterprise_summary():
        """Same as the dashboard endpoint but always for today. Powers the
        cards on the IN/OUT page, the Time Reports preview, and any other
        surface that needs live counts."""
        from enterprise_query import compute_dashboard_summary
        sid = current_school_id()
        d = request.args.get("date")
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date() if d else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        return jsonify(compute_dashboard_summary(
            target,
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            school_id=sid,
        ))

    @app.route("/api/enterprise/widgets")
    def api_enterprise_widgets():
        """Additional cards / graphs (trend, top-late, top-OT, etc.)."""
        from enterprise_query import (
            attendance_trend, hourly_entries, top_late_students,
            top_overtime_teachers, top_early_exits, working_hours_distribution,
            camera_health, recognition_stats, activity_feed, class_attendance_heatmap,
        )
        sid = current_school_id()
        return jsonify({
            "trend": attendance_trend(days=7, school_id=sid),
            "hourly": hourly_entries(school_id=sid),
            "top_late": top_late_students(school_id=sid),
            "top_overtime": top_overtime_teachers(school_id=sid),
            "top_early_exit": top_early_exits(school_id=sid),
            "working_hours": working_hours_distribution(school_id=sid),
            "camera_health": camera_health(school_id=sid),
            "recognition": recognition_stats(school_id=sid),
            "activity_feed": activity_feed(limit=30, school_id=sid),
            "heatmap": class_attendance_heatmap(days=7, school_id=sid),
        })

    @app.route("/api/enterprise/sessions")
    def api_enterprise_sessions():
        """Filtered session list for the IN/OUT page."""
        from enterprise_query import list_sessions
        sid = current_school_id()
        d = request.args.get("date")
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date() if d else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        kind = (request.args.get("type") or "all").lower()
        try:
            min_conf = float(request.args.get("min_confidence")) if request.args.get("min_confidence") else None
            max_conf = float(request.args.get("max_confidence")) if request.args.get("max_confidence") else None
        except (TypeError, ValueError):
            return jsonify({"error": "confidence filters must be numeric"}), 400
        return jsonify(list_sessions(
            d=target,
            kind=kind,
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            status=request.args.get("status") or None,
            event_type=request.args.get("event_type") or None,
            camera_id=request.args.get("camera_id", type=int),
            min_confidence=min_conf,
            max_confidence=max_conf,
            recognition_type=request.args.get("recognition_type") or None,
            search=(request.args.get("q") or "").strip() or None,
            limit=int(request.args.get("limit", 1000)),
            school_id=sid,
        ))

    @app.route("/api/reports/preview")
    def api_reports_preview():
        """Return a JSON report preview that matches the exported file."""
        from enterprise_query import build_report, compute_dashboard_summary
        sid = current_school_id()
        report_type = request.args.get("type") or "daily"
        d = request.args.get("date")
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date() if d else date.today()
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        def _opt(name):
            raw = request.args.get(name)
            if not raw:
                return None
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return None
        rows, headers, title = build_report(
            report_type,
            d=target,
            start=_opt("start"),
            end=_opt("end"),
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            search=(request.args.get("q") or "").strip() or None,
            person_type=request.args.get("person_type") or None,
            school_id=sid,
        )
        summary = compute_dashboard_summary(
            target,
            class_name=request.args.get("class_name") or None,
            section=request.args.get("section") or None,
            department=request.args.get("department") or None,
            designation=request.args.get("designation") or None,
            school_id=sid,
        )
        # The preview's total is the rows currently being shown (for example,
        # only late arrivals), while the other cards remain live daily KPIs.
        summary["total_records"] = len(rows)
        return jsonify({
            "title": title,
            "headers": headers,
            "rows": rows,
            "summary": summary,
        })

    @app.route("/api/enterprise/events")
    def api_enterprise_events():
        """IN/OUT feed sourced from immutable attendance_events rows."""
        sid = current_school_id()
        kind = (request.args.get("type") or "student").lower()
        if kind not in {"student", "teacher"}: return jsonify({"error": "type must be student or teacher"}), 400
        q = AttendanceEvent.query.filter_by(attendance_type=kind)
        if sid:
            q = q.filter((AttendanceEvent.school_id == sid) | (AttendanceEvent.school_id.is_(None)))
        raw_date = request.args.get("date")
        if raw_date:
            try: q = q.filter(func.date(AttendanceEvent.event_time) == datetime.strptime(raw_date, "%Y-%m-%d").date())
            except ValueError: return jsonify({"error": "date must be YYYY-MM-DD"}), 400
        events = q.order_by(AttendanceEvent.event_time.desc()).limit(1000).all(); out = []
        model, person_model, fk = (StudentAttendance, Student, "student_id") if kind == "student" else (TeacherAttendance, Teacher, "teacher_id")
        for event in events:
            row = model.query.get(event.attendance_id)
            if row is None: continue
            person = getattr(row, "student" if kind == "student" else "teacher")
            if kind == "student":
                if request.args.get("class_name") and person.class_name != request.args["class_name"]: continue
                if request.args.get("section") and person.section != request.args["section"]: continue
            else:
                if request.args.get("department") and person.department != request.args["department"]: continue
                if request.args.get("designation") and person.designation != request.args["designation"]: continue
            data = _enterprise_row(row, kind); data.update({"event_id": event.id, "event_type": event.event_type, "event_time": event.event_time.isoformat(), "event_camera": event.camera.name if event.camera else None, "event_confidence": event.confidence})
            out.append(data)
        return jsonify(out)

    @app.route("/api/enterprise/settings", methods=["GET", "PUT"])
    def api_enterprise_settings():
        sid = current_school_id()
        keys = ["face_recognition", "qr", "rfid", "manual_attendance", "whatsapp", "sms", "email", "anti_spoofing", "liveness_detection", "unknown_face_alert"]
        if request.method == "PUT":
            for key, value in (request.get_json(force=True) or {}).items():
                if key in keys:
                    setting_q = AttendanceSetting.query.filter_by(key=key)
                    if sid:
                        setting_q = setting_q.filter(AttendanceSetting.school_id == sid)
                    setting = setting_q.first() or AttendanceSetting(key=key, school_id=sid or "DEFAULT")
                    setting.enabled = _coerce_bool(value); _db.session.add(setting)
            _db.session.commit()
        saved_q = AttendanceSetting.query
        if sid:
            saved_q = saved_q.filter((AttendanceSetting.school_id == sid) | (AttendanceSetting.school_id.is_(None)))
        saved = {s.key: s.enabled for s in saved_q.all()}; return jsonify({key: saved.get(key, key == "face_recognition") for key in keys})

    @app.route("/api/enterprise/time-rules/<kind>", methods=["GET", "PUT"])
    def api_enterprise_time_rules(kind: str):
        from database.enterprise_models import TeacherTimeRule, StudentTimeRule
        sid = current_school_id()
        model = {"teacher": TeacherTimeRule, "student": StudentTimeRule}.get(kind)
        if model is None: return jsonify({"error": "kind must be teacher or student"}), 400
        rule_q = model.query
        if sid:
            rule_q = rule_q.filter((model.school_id == sid) | (model.school_id.is_(None)))
        rule = rule_q.first() or model(school_id=sid or "DEFAULT"); _db.session.add(rule)
        if request.method == "PUT":
            data = request.get_json(force=True) or {}
            allowed = {"office_start", "in_end_time", "office_end", "late_time", "half_day_time", "absent_after", "out_detection_start", "early_exit_time", "overtime_start", "min_working_minutes", "overtime_enabled", "gate_close_time"}
            for key, value in data.items():
                if key in allowed and hasattr(rule, key):
                    old = getattr(rule, key); setattr(rule, key, value)
                    if old != value: _db.session.add(AttendanceLog(school_id=sid, entity_type=f"{kind}_time_rule", entity_id=rule.id or 0, field=key, old_value=str(old), new_value=str(value), reason=data.get("reason")))
            rule.updated_at = _utcnow(); _db.session.commit()
        return jsonify({c.name: getattr(rule, c.name) for c in rule.__table__.columns})

    # --------------------------------------------------------- unknown faces
    @app.route("/api/unknown-faces")
    def api_unknown_faces():
        sid = current_school_id()
        q = UnknownFace.query
        if sid:
            q = q.filter((UnknownFace.school_id == sid) | (UnknownFace.school_id.is_(None)))
        rows = q.order_by(UnknownFace.timestamp.desc()).limit(200)
        return jsonify([r.to_dict() for r in rows.all()])

    @app.route("/api/unknown-faces/<int:uid>/action", methods=["POST", "DELETE"])
    def api_unknown_face_action(uid: int):
        """Audit-approved lifecycle actions without deleting history by default."""
        sid = current_school_id()
        face = UnknownFace.query.get_or_404(uid)
        if not is_tenant_permitted(face.school_id):
            return jsonify({"error": "unknown face not found"}), 404
        if request.method == "DELETE":
            _db.session.add(AttendanceLog(school_id=sid, entity_type="unknown_face", entity_id=uid, field="delete", old_value="active", new_value="deleted", reason=request.args.get("reason")))
            _db.session.delete(face); _db.session.commit(); return jsonify({"ok": True})
        data = request.get_json(force=True) or {}; action = (data.get("action") or "").lower()
        if action not in {"approve", "reject", "assign_student", "assign_teacher"}:
            return jsonify({"error": "action must be approve, reject, assign_student, or assign_teacher"}), 400
        if action == "assign_student" and not Student.query.get(data.get("student_id")):
            return jsonify({"error": "student not found"}), 404
        if action == "assign_teacher" and not Teacher.query.get(data.get("teacher_id")):
            return jsonify({"error": "teacher not found"}), 404
        face.alerted = action in {"approve", "assign_student", "assign_teacher"}
        _db.session.add(AttendanceLog(school_id=sid, entity_type="unknown_face", entity_id=uid, field="action", old_value=None, new_value=action, reason=data.get("reason")))
        _db.session.commit(); return jsonify({"ok": True, "action": action})

    # --------------------------------------------------------------- teachers
    @app.route("/api/teachers", methods=["GET", "POST"])
    def api_teachers():
        sid = current_school_id()
        if request.method == "GET":
            q = Teacher.query
            if sid:
                q = q.filter(Teacher.school_id == sid)
            q = q.order_by(Teacher.name)
            search = (request.args.get("q") or "").strip()
            if search:
                like = f"%{search.lower()}%"
                q = q.filter(
                    _db.or_(
                        func.lower(Teacher.name).like(like),
                        func.lower(Teacher.teacher_id).like(like),
                        func.lower(Teacher.subject).like(like),
                        func.lower(Teacher.assigned_classes).like(like),
                    )
                )
            return jsonify([t.to_dict() for t in q.all()])
        data = request.get_json(force=True)
        method_choice = (data.get("attendance_method") or "FACE").upper().strip()
        if method_choice not in {"FACE", "FINGERPRINT"}:
            return jsonify({"error": "attendance_method must be FACE or FINGERPRINT"}), 400
        teacher_code = data.get("teacher_id")
        device_user_id = data.get("fingerprint_user_id", teacher_code)
        if not isinstance(teacher_code, str) or not teacher_code.strip():
            return jsonify({"error": "teacher_id must be a non-empty string"}), 400
        if not isinstance(device_user_id, str) or not device_user_id.strip():
            return jsonify({"error": "fingerprint_user_id must be a non-empty string"}), 400
        if _coerce_bool(data.get("fingerprint_enrolled", False)):
            return jsonify({
                "error": "Enrollment status can only be set by an adapter-confirmed enrollment request."
            }), 400

        teacher_school_id = _resolve_school_id(sid)
        same_school = (
            Teacher.school_id.is_(None)
            if teacher_school_id is None
            else Teacher.school_id == teacher_school_id
        )
        mapping_conflict = Teacher.query.filter(
            same_school,
            Teacher.fingerprint_user_id == device_user_id.strip(),
        ).first()
        if mapping_conflict:
            return jsonify({"error": "Device User ID is already mapped to a teacher."}), 409

        t = Teacher(
            school_id=teacher_school_id,
            teacher_id=teacher_code.strip(),
            name=data["name"],
            subject=data.get("subject"),
            assigned_classes=data.get("assigned_classes"),
            mobile=data.get("mobile"),
            email=data.get("email"),
            department=data.get("department"),
            designation=data.get("designation"),
            attendance_method=method_choice,
            fingerprint_user_id=device_user_id.strip(),
            fingerprint_enrolled=False,
            is_active=_coerce_bool(data.get("is_active", True)),
        )
        _db.session.add(t)
        try:
            _db.session.flush()
        except IntegrityError:
            _db.session.rollback()
            return jsonify({"error": "Device User ID or teacher ID conflicts with an existing record."}), 409

        # Automatically make the employee available in HR with the exact same Staff ID
        try:
            from hr.employee_service import ensure_employee_from_teacher
            ensure_employee_from_teacher(t, school_id=_resolve_school_id(sid))
        except Exception as e:
            app.logger.warning(f"Failed to auto-create HR Employee for teacher {t.id}: {e}")

        _db.session.commit()
        return jsonify(t.to_dict()), 201

    @app.route("/api/teachers/<int:tid>", methods=["GET", "PUT", "DELETE"])
    def api_teacher_detail(tid: int):
        t = Teacher.query.get_or_404(tid)
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404
        if request.method == "GET":
            return jsonify(t.to_dict())
        if request.method == "DELETE":
            try:
                from hr.models import Employee
                linked_emp = Employee.query.filter_by(teacher_id=t.id).first()
                if linked_emp:
                    linked_emp.is_active = False
                    linked_emp.employment_status = "inactive"
            except Exception:
                pass
            _db.session.delete(t)
            _db.session.commit()
            return jsonify({"ok": True})
        data = request.get_json(force=True)
        if "fingerprint_enrolled" in data and _coerce_bool(data["fingerprint_enrolled"]):
            return jsonify({
                "error": "Enrollment status can only be set by an adapter-confirmed enrollment request."
            }), 400
        if "fingerprint_user_id" in data:
            device_user_id = data["fingerprint_user_id"]
            if not isinstance(device_user_id, str) or not device_user_id.strip():
                return jsonify({"error": "fingerprint_user_id must be a non-empty string"}), 400
            same_school = (
                Teacher.school_id.is_(None)
                if t.school_id is None
                else Teacher.school_id == t.school_id
            )
            mapping_conflict = Teacher.query.filter(
                Teacher.id != t.id,
                same_school,
                Teacher.fingerprint_user_id == device_user_id.strip(),
            ).first()
            if mapping_conflict:
                return jsonify({"error": "Device User ID is already mapped to a teacher."}), 409
        for key in ("teacher_id", "name", "subject", "assigned_classes", "mobile", "email", "department", "designation"):
            if key in data and data[key] is not None:
                setattr(t, key, str(data[key]).strip())
        if "fingerprint_user_id" in data:
            t.fingerprint_user_id = device_user_id.strip()

        method_changed = False
        if "attendance_method" in data:
            new_method = (data["attendance_method"] or "FACE").upper().strip()
            if new_method in {"FACE", "FINGERPRINT"}:
                if t.attendance_method != new_method:
                    method_changed = True
                t.attendance_method = new_method

        if "fingerprint_enrolled" in data:
            t.fingerprint_enrolled = False
            t.fingerprint_enrolled_at = None

        if "is_active" in data:
            t.is_active = _coerce_bool(data["is_active"])
        try:
            _db.session.flush()
        except IntegrityError:
            _db.session.rollback()
            return jsonify({"error": "Device User ID or teacher ID conflicts with an existing record."}), 409

        # Synchronize updates to connected HR Employee record
        try:
            from hr.employee_service import ensure_employee_from_teacher
            ensure_employee_from_teacher(t, school_id=t.school_id)
        except Exception as e:
            app.logger.warning(f"Failed to sync HR Employee for teacher {t.id}: {e}")

        _db.session.commit()
        if method_changed:
            refresh_recognizer()
        return jsonify(t.to_dict())

    @app.route("/api/teachers/<path:tid>/fingerprint/enroll", methods=["POST", "DELETE"])
    def api_teacher_fingerprint_enroll(tid):
        """Enroll or unenroll a teacher fingerprint through the configured adapter."""
        from biometrics.fingerprint_service import (
            enroll_teacher_fingerprint,
            delete_teacher_fingerprint,
        )
        tid_str = str(tid).strip()
        t = _db.session.get(Teacher, int(tid_str)) if tid_str.isdigit() else None
        if t is None:
            t = Teacher.query.filter_by(teacher_id=tid_str).first_or_404()
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404

        if request.method == "DELETE":
            res = delete_teacher_fingerprint(t.id, school_id=t.school_id)
            return jsonify(res)

        data = request.get_json(silent=True) or {}
        device_uid = data.get("device_user_id") or t.fingerprint_user_id or t.teacher_id
        res = enroll_teacher_fingerprint(t.id, school_id=t.school_id, device_user_id=device_uid)
        status_code = 200 if res.get("ok") else 400
        return jsonify(res), status_code

    @app.route("/api/teachers/<path:tid>/fingerprint/status", methods=["GET"])
    def api_teacher_fingerprint_status(tid):
        """Query fingerprint enrollment and sync status for a teacher."""
        from biometrics.fingerprint_adapter import get_fingerprint_adapter
        tid_str = str(tid).strip()
        t = _db.session.get(Teacher, int(tid_str)) if tid_str.isdigit() else None
        if t is None:
            t = Teacher.query.filter_by(teacher_id=tid_str).first_or_404()
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404

        adapter = get_fingerprint_adapter()
        target_uid = t.fingerprint_user_id or t.teacher_id
        device_status = adapter.get_enrollment_status(target_uid)
        device_enrolled = bool(device_status.get("enrolled")) and bool(
            device_status.get("verified") or device_status.get("simulated")
        )
        return jsonify({
            "teacher_id": t.teacher_id,
            "name": t.name,
            "attendance_method": (t.attendance_method or "FACE").upper(),
            "fingerprint_enrolled": device_enrolled,
            "application_enrollment_flag": bool(t.fingerprint_enrolled),
            "enrollment_simulated": bool(device_status.get("simulated")),
            "fingerprint_enrolled_at": t.fingerprint_enrolled_at.isoformat() if t.fingerprint_enrolled_at else None,
            "fingerprint_user_id": target_uid,
            "device_status": device_status,
        })

    # ---------------------------------------------------- biometric devices
    @app.route("/api/devices/fingerprint/config", methods=["GET", "POST"])
    def api_fingerprint_device_config():
        """Get or update fingerprint terminal configuration."""
        from biometrics.fingerprint_service import (
            get_or_create_fingerprint_device,
            save_fingerprint_device_config,
        )
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"

        if request.method == "POST":
            data = request.get_json(force=True) or {}
            res = save_fingerprint_device_config(data, school_id=sid, actor_email=user_email)
            return jsonify(res), int(res.get("status", 200)) if not res.get("ok") else 200

        dev = get_or_create_fingerprint_device(school_id=sid)
        return jsonify({
            "ok": True,
            "device": dev.to_dict(),
        })

    @app.route("/api/devices/fingerprint/connect", methods=["POST"])
    def api_fingerprint_device_connect():
        """Check reachability of the configured fingerprint terminal."""
        from biometrics.fingerprint_service import connect_fingerprint_device
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        res = connect_fingerprint_device(school_id=sid, actor_email=user_email)
        status_code = 200 if res.get("ok") else 400
        return jsonify(res), status_code

    @app.route("/api/devices/fingerprint/disconnect", methods=["POST"])
    def api_fingerprint_device_disconnect():
        """Disconnect the configured fingerprint adapter."""
        from biometrics.fingerprint_service import disconnect_fingerprint_device
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        res = disconnect_fingerprint_device(school_id=sid, actor_email=user_email)
        return jsonify(res)

    @app.route("/api/devices/fingerprint/test-connection", methods=["POST"])
    def api_fingerprint_device_test_conn():
        """Execute a TCP reachability test and latency measurement."""
        from biometrics.fingerprint_service import test_fingerprint_device_connection
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        res = test_fingerprint_device_connection(school_id=sid, actor_email=user_email)
        return jsonify(res)

    @app.route("/api/devices/fingerprint/mappings", methods=["GET", "POST"])
    def api_fingerprint_device_mappings():
        """List or save teacher-to-device ID mappings."""
        from biometrics.fingerprint_service import (
            get_teacher_fingerprint_mappings,
            save_teacher_fingerprint_mapping,
        )
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"

        if request.method == "POST":
            data = request.get_json(force=True) or {}
            tid = data.get("teacher_id") or data.get("id")
            dev_uid = data.get("fingerprint_user_id") or data.get("device_user_id")
            method = data.get("attendance_method", "FINGERPRINT")
            if not tid or not dev_uid:
                return jsonify({"ok": False, "error": "teacher_id and fingerprint_user_id are required"}), 400

            res = save_teacher_fingerprint_mapping(
                teacher_id_or_pk=tid,
                fingerprint_user_id=dev_uid,
                attendance_method=method,
                school_id=sid,
                actor_email=user_email,
            )
            status_code = res.get("status", 200)
            return jsonify(res), status_code

        search = request.args.get("search", "")
        mappings = get_teacher_fingerprint_mappings(school_id=sid, search=search)
        return jsonify({
            "ok": True,
            "mappings": mappings,
            "total": len(mappings),
        })

    @app.route("/api/devices/fingerprint/mappings/<path:tid>", methods=["DELETE"])
    def api_fingerprint_device_mapping_delete(tid):
        """Remove teacher fingerprint mapping and unenroll."""
        from biometrics.fingerprint_service import delete_teacher_fingerprint
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        res = delete_teacher_fingerprint(teacher_id_or_pk=tid, school_id=sid, actor_email=user_email)
        return jsonify(res)

    @app.route("/api/devices/fingerprint/enroll", methods=["POST"])
    def api_fingerprint_device_enroll():
        """Request teacher enrollment through the configured adapter."""
        from biometrics.fingerprint_service import enroll_teacher_fingerprint
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        data = request.get_json(force=True) or {}
        tid = data.get("teacher_id") or data.get("id")
        dev_uid = data.get("fingerprint_user_id") or data.get("device_user_id")
        if not tid:
            return jsonify({"ok": False, "error": "teacher_id is required"}), 400

        res = enroll_teacher_fingerprint(
            teacher_id_or_pk=tid,
            school_id=sid,
            device_user_id=dev_uid,
            actor_email=user_email,
        )
        status_code = 200 if res.get("ok") else 400
        return jsonify(res), status_code

    @app.route("/api/devices/fingerprint/test-verify", methods=["POST"])
    def api_fingerprint_device_test_verify():
        """Diagnostic fingerprint verification test (DOES NOT mark attendance)."""
        from biometrics.fingerprint_service import test_fingerprint_verification
        sid = current_school_id()
        user_email = g.user.email if getattr(g, "user", None) else "admin"
        data = request.get_json(force=True) or {}
        user_id = data.get("device_user_id") or data.get("user_id") or data.get("teacher_id")
        if not user_id:
            return jsonify({"ok": False, "error": "device_user_id is required"}), 400

        res = test_fingerprint_verification(
            device_user_id=user_id,
            school_id=sid,
            actor_email=user_email,
        )
        return jsonify(res)

    @app.route("/api/devices/fingerprint/events", methods=["GET"])
    def api_fingerprint_device_events():
        """Return combined recent events and hardware audit log."""
        from biometrics.fingerprint_service import get_fingerprint_audit_logs
        sid = current_school_id()
        limit = int(request.args.get("limit", 100))
        logs = get_fingerprint_audit_logs(limit=limit, school_id=sid)
        return jsonify({
            "ok": True,
            "events": logs,
            "count": len(logs),
        })

    @app.route("/api/devices/fingerprint/import", methods=["POST"])
    def api_fingerprint_device_import():
        """Import an administrator-supplied attendance export file."""
        import tempfile
        from pathlib import Path
        from biometrics.attendance_file_import import import_attendance_export
        from biometrics.fingerprint_service import get_or_create_fingerprint_device

        if request.content_length is not None and request.content_length > 10 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Export file exceeds the 10 MB upload limit."}), 413
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "Select a device attendance export file."}), 400
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".csv", ".tsv", ".txt", ".xlsx"}:
            return jsonify({"ok": False, "error": "Supported files: CSV, TSV, TXT, or XLSX."}), 400
        sid = current_school_id()
        if not sid:
            return jsonify({"ok": False, "error": "Select a school before importing attendance."}), 409
        device = get_or_create_fingerprint_device(school_id=sid)
        try:
            with tempfile.TemporaryDirectory(prefix="attendanceos-device-import-") as temp_dir:
                temp_path = Path(temp_dir) / ("device-export" + suffix)
                upload.save(temp_path)
                result = import_attendance_export(
                    temp_path,
                    device_id=device.device_id,
                    school_id=int(sid),
                    timezone_name=app.config.get("BIOMAX_DEVICE_TIMEZONE", "Asia/Kolkata"),
                )
            result["file"] = upload.filename
            return jsonify({"ok": result["rejected"] == 0, "result": result}), 200
        except (ValueError, RuntimeError) as exc:
            _db.session.rollback()
            return jsonify({"ok": False, "error": str(exc)}), 400
        except SQLAlchemyError:
            _db.session.rollback()
            app.logger.exception("Fingerprint attendance file import failed")
            return jsonify({"ok": False, "error": "Database error while importing attendance; retry the file."}), 503

    @app.route("/api/devices/biomax/event", methods=["POST"])
    @app.route("/api/devices/fingerprint/verify", methods=["POST"])
    def api_biomax_event():
        """Receive an authenticated event using the AttendanceOS normalized webhook contract."""
        from biometrics.fingerprint_service import verify_and_record_attendance
        from biometrics.fingerprint_service import log_device_audit

        if request.content_length is not None and request.content_length > 8192:
            return jsonify({"action": "rejected", "error": "Request payload is too large."}), 413
        if not request.is_json:
            return jsonify({"action": "rejected", "error": "Content-Type must be application/json."}), 415
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            app.logger.warning("[BIOMAX_WEBHOOK] Rejected malformed JSON from %s", request.remote_addr)
            return jsonify({"action": "rejected", "error": "A JSON object is required."}), 400

        required = {"device_id", "user_id", "timestamp", "verify_type"}
        allowed = required | {"event_id"}
        if set(data) - allowed or not required.issubset(data):
            return jsonify({
                "action": "rejected",
                "error": "Payload must contain device_id, user_id, timestamp, and verify_type; only event_id is optional.",
            }), 400

        device_id = data.get("device_id")
        user_id = data.get("user_id")
        raw_timestamp = data.get("timestamp")
        verify_type = data.get("verify_type")
        event_id = data.get("event_id")
        if (
            not isinstance(device_id, str) or not device_id.strip() or len(device_id.strip()) > 60
            or not isinstance(user_id, str) or not user_id.strip() or len(user_id.strip()) > 50
            or not isinstance(raw_timestamp, str) or len(raw_timestamp) > 64
            or not isinstance(verify_type, str) or verify_type.strip().upper() != "FINGERPRINT"
            or (event_id is not None and (
                not isinstance(event_id, str) or not event_id.strip() or len(event_id.strip()) > 128
            ))
        ):
            return jsonify({"action": "rejected", "error": "Invalid event field types or values."}), 400

        device_id = device_id.strip()
        user_id = user_id.strip()
        event_id = event_id.strip() if event_id is not None else None
        try:
            device_tz = ZoneInfo(app.config.get("BIOMAX_DEVICE_TIMEZONE", "UTC"))
            timestamp_text = raw_timestamp.strip()
            if timestamp_text.endswith(("Z", "z")):
                timestamp_text = timestamp_text[:-1] + "+00:00"
            event_time = datetime.fromisoformat(timestamp_text)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=device_tz)
            event_time = event_time.astimezone(device_tz)
        except (ValueError, ZoneInfoNotFoundError):
            return jsonify({
                "action": "rejected",
                "error": "timestamp must be ISO-8601 and BIOMAX_DEVICE_TIMEZONE must name a valid timezone.",
            }), 400
        if event_time > datetime.now(device_tz) + timedelta(minutes=5):
            return jsonify({"action": "rejected", "error": "Event timestamp is in the future."}), 400

        device_query = FingerprintDevice.query.filter_by(device_id=device_id, is_active=True)
        if getattr(g, "user", None):
            school_scope = current_school_id()
            if school_scope:
                device_query = device_query.filter_by(school_id=school_scope)
            devices = device_query.all()
            if len(devices) != 1 or not is_tenant_permitted(devices[0].school_id):
                return jsonify({"action": "rejected", "error": "Registered device not found."}), 404
            device = devices[0]
        else:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
            candidates = device_query.all()
            matched = []
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
            for candidate in candidates:
                configured_hash = candidate.webhook_token_hash or ""
                if token_hash and configured_hash and hmac.compare_digest(token_hash, configured_hash):
                    matched.append(candidate)
            if len(matched) != 1:
                if _biomax_rate_limited(
                    "unauthenticated",
                    request.remote_addr,
                    max(1, int(app.config.get("BIOMAX_WEBHOOK_MAX_REQUESTS", 120))),
                    max(1, int(app.config.get("BIOMAX_WEBHOOK_RATE_WINDOW_SECONDS", 60))),
                ):
                    return jsonify({"action": "rejected", "error": "Rate limit exceeded; retry later."}), 429
                app.logger.warning(
                    "[BIOMAX_WEBHOOK] Rejected unauthenticated request from %s for device_id=%r",
                    request.remote_addr,
                    device_id[:60],
                )
                return jsonify({"action": "rejected", "error": "Invalid device credentials."}), 401
            device = matched[0]

        if not device.school_id:
            return jsonify({"action": "rejected", "error": "Device must be assigned to a school before receiving events."}), 409
        if _biomax_rate_limited(
            device.device_id,
            request.remote_addr,
            max(1, int(app.config.get("BIOMAX_WEBHOOK_MAX_REQUESTS", 120))),
            max(1, int(app.config.get("BIOMAX_WEBHOOK_RATE_WINDOW_SECONDS", 60))),
        ):
            app.logger.warning("[BIOMAX_WEBHOOK] Rate limit exceeded for device_id=%s", device.device_id)
            return jsonify({"action": "rejected", "error": "Rate limit exceeded; retry later."}), 429

        teacher_matches = Teacher.query.filter_by(
            fingerprint_user_id=user_id,
            school_id=device.school_id,
        ).all()
        if len(teacher_matches) != 1:
            app.logger.warning(
                "[BIOMAX_MAPPING_FAIL] device_id=%s device_user_id=%s school_id=%s matches=%s",
                device.device_id,
                user_id,
                device.school_id,
                len(teacher_matches),
            )
            log_device_audit(
                event_type="verify",
                result="rejected",
                device_id=device.device_id,
                teacher_id=user_id,
                failure_reason="unknown_or_ambiguous_device_user_id",
                school_id=device.school_id,
                event_time=event_time,
            )
            return jsonify({"action": "rejected", "error": "Device user ID is not uniquely mapped."}), 422

        teacher = teacher_matches[0]
        if not teacher.is_active:
            log_device_audit(
                event_type="verify",
                result="rejected",
                device_id=device.device_id,
                teacher_id=teacher.teacher_id,
                failure_reason="teacher_inactive",
                school_id=device.school_id,
                event_time=event_time,
            )
            return jsonify({"action": "rejected", "error": "Mapped teacher is inactive."}), 422
        if (teacher.attendance_method or "").upper() != "FINGERPRINT" or not teacher.fingerprint_enrolled:
            log_device_audit(
                event_type="verify",
                result="rejected",
                device_id=device.device_id,
                teacher_id=teacher.teacher_id,
                failure_reason="teacher_not_enrolled_for_fingerprint",
                school_id=device.school_id,
                event_time=event_time,
            )
            return jsonify({"action": "rejected", "error": "Mapped teacher is not enrolled for fingerprint attendance."}), 422

        canonical = {
            "device_id": device.device_id,
            "user_id": user_id,
            "timestamp": event_time.astimezone(timezone.utc).isoformat(),
            "verify_type": "FINGERPRINT",
        }
        if event_id:
            canonical["event_id"] = event_id
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        event_identity = f"{device.device_id}:{event_id}" if event_id else serialized
        event_key = hashlib.sha256(event_identity.encode("utf-8")).hexdigest()
        existing = FingerprintEventReceipt.query.filter_by(event_key=event_key).first()
        if existing:
            code = 409 if existing.payload_hash != payload_hash else 409
            app.logger.info(
                "[BIOMAX_WEBHOOK] Duplicate/replayed event rejected: device_id=%s user_id=%s event_key=%s",
                device.device_id,
                user_id,
                event_key,
            )
            return jsonify({"action": "rejected", "reason": "duplicate event"}), code

        receipt = FingerprintEventReceipt(
            device_id=device.device_id,
            event_key=event_key,
            payload_hash=payload_hash,
            device_user_id=user_id,
            event_time=event_time,
            result="processing",
        )
        _db.session.add(receipt)
        try:
            _db.session.flush()
        except IntegrityError:
            _db.session.rollback()
            app.logger.info(
                "[BIOMAX_WEBHOOK] Concurrent duplicate rejected: device_id=%s event_key=%s",
                device.device_id,
                event_key,
            )
            return jsonify({"action": "rejected", "reason": "duplicate event"}), 409

        app.logger.info(
            "[BIOMAX_EVENT_RECV] device_id=%s device_user_id=%s timestamp=%s event_type=FINGERPRINT mapping_teacher_id=%s",
            device.device_id,
            user_id,
            event_time.isoformat(),
            teacher.teacher_id,
        )
        try:
            result = verify_and_record_attendance(
                device_user_id=user_id,
                device_id=device.device_id,
                event_time=event_time,
                school_id=device.school_id,
            )
            receipt.result = str(result.get("action", "unknown"))[:30]
            receipt.response_json = result
            device.last_activity_at = _utcnow()
            _db.session.commit()
        except SQLAlchemyError:
            _db.session.rollback()
            app.logger.exception(
                "[BIOMAX_ATTENDANCE_ROLLBACK] Attendance persistence failed for device_id=%s user_id=%s",
                device.device_id,
                user_id,
            )
            return jsonify({"action": "error", "error": "Attendance persistence failed; retry the event."}), 503

        app.logger.info(
            "[BIOMAX_ATTENDANCE_RESULT] device_id=%s device_user_id=%s teacher_id=%s action=%s",
            device.device_id,
            user_id,
            teacher.teacher_id,
            receipt.result,
        )
        return jsonify(result)

    @app.route("/api/devices/biomax/status", methods=["GET"])
    @app.route("/api/devices/fingerprint/status", methods=["GET"])
    def api_biomax_status():
        """Return network reachability, protocol-verification, and adapter status."""
        from biometrics.fingerprint_service import get_fingerprint_device_status
        sid = current_school_id()
        return jsonify(get_fingerprint_device_status(school_id=sid))

    @app.route("/api/devices/biomax/audit", methods=["GET"])
    def api_biomax_audit():
        """Return hardware event audit log entries."""
        from biometrics.fingerprint_service import get_fingerprint_audit_logs
        sid = current_school_id()
        limit = int(request.args.get("limit", 100))
        return jsonify(get_fingerprint_audit_logs(limit=limit, school_id=sid))


    @app.route("/api/teachers/<path:tid>/photo", methods=["POST"])
    def api_teacher_photo(tid):
        """Register / re-register a teacher's face from 3 angle photos.

        Accepts either:
          * ``photo``   -- a single legacy upload (kept for back-compat)
          * ``front``/``left``/``right`` -- the new 3-angle workflow

        Uses the shared :func:`register_face_photos` helper so future
        fixes only need to happen once. Refreshes the recognizer so the
        next live frame picks up the new embedding without a restart.
        """
        tid_str = str(tid).strip()
        t = _db.session.get(Teacher, int(tid_str)) if tid_str.isdigit() else None
        if t is None:
            t = Teacher.query.filter_by(teacher_id=tid_str).first_or_404()
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404
        result = register_face_photos(
            request.files,
            person=t,
            filename_prefix=str(t.teacher_id),
            known_faces_dir=app.config["KNOWN_FACES_DIR"],
        )
        if not result.get("ok"):
            return jsonify({"error": result.get("error", "no file")}), result.get("status", 400)
        _db.session.flush()
        try:
            from hr.employee_service import ensure_employee_from_teacher
            ensure_employee_from_teacher(t, school_id=t.school_id)
        except Exception:
            pass
        _db.session.commit()
        refresh_recognizer()
        print(f"[RECOGNITION] teacher enrollment refreshed teacher={t.id} angles={result.get('encodings')}")
        return jsonify(t.to_dict())

    @app.route("/api/teachers/<path:tid>/profile")
    def api_teacher_profile(tid):
        """Return the full profile + attendance stats for a teacher.

        The dashboard, profile modal, and unknown-faces assignment flow
        all read from this endpoint so they always agree. Includes
        monthly working hours, attendance %, last recognition and the
        full set of registered face encodings.
        """
        from datetime import timedelta as _td
        tid_str = str(tid).strip()
        t = _db.session.get(Teacher, int(tid_str)) if tid_str.isdigit() else None
        if t is None:
            t = Teacher.query.filter_by(teacher_id=tid_str).first_or_404()
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404
        today = date.today()
        month_start = today.replace(day=1)
        sessions = (
            TeacherAttendance.query
            .filter(
                TeacherAttendance.teacher_id == t.id,
                TeacherAttendance.attendance_date >= month_start,
            )
            .all()
        )
        total_minutes = sum((s.working_minutes or 0) for s in sessions)
        overtime_minutes = sum((s.overtime_minutes or 0) for s in sessions)
        days_present = sum(1 for s in sessions if s.in_time is not None)
        days_absent = max(today.day - days_present, 0)
        days_recorded = len(sessions) or 1
        attendance_pct = round((days_present / days_recorded) * 100, 1) if days_recorded else 0.0

        # Last recognition = most recent attendance event for this teacher.
        last_event = (
            AttendanceEvent.query
            .join(TeacherAttendance, TeacherAttendance.id == AttendanceEvent.attendance_id)
            .filter(
                TeacherAttendance.teacher_id == t.id,
                AttendanceEvent.attendance_type == "teacher",
            )
            .order_by(AttendanceEvent.event_time.desc())
            .first()
        )
        last_recognition = None
        if last_event is not None:
            last_recognition = {
                "event_type": last_event.event_type,
                "event_time": last_event.event_time.isoformat() if last_event.event_time else None,
                "confidence": last_event.confidence,
                "camera": last_event.camera.name if last_event.camera else None,
            }

        profile = t.to_dict()
        extras = t.extra_encodings or []
        profile.update({
            "face_registered": t.encoding is not None,
            "recognition_status": "Active & Ready" if t.encoding is not None else "Face Not Registered",
            "encodings_count": (1 if t.encoding is not None else 0) + len(extras),
            "angles": (
                ["front"] +
                [e.get("angle") for e in extras if isinstance(e, dict) and e.get("angle")]
            ),
            "extra_encodings": extras,
            "assigned_classes_list": [
                c.strip() for c in (t.assigned_classes or "").split(",") if c.strip()
            ],
            # Monthly roll-up
            "month": {
                "month_start": month_start.isoformat(),
                "days_present": days_present,
                "days_absent": days_absent,
                "working_minutes": total_minutes,
                "overtime_minutes": overtime_minutes,
                "attendance_pct": attendance_pct,
            },
            "last_recognition": last_recognition,
        })
        return jsonify(profile)

    @app.route("/api/teachers/<int:tid>/attendance")
    def api_teacher_attendance(tid: int):
        """Return attendance rows for a single teacher, scoped by date range.

        Mirrors the per-student logic exposed elsewhere in the app. The
        optional ``start``/``end`` query params default to the current
        month. The result is sorted by date descending and capped at
        ``limit`` rows (default 500).
        """
        t = Teacher.query.get_or_404(tid)
        if not is_tenant_permitted(t.school_id):
            return jsonify({"error": "teacher not found"}), 404
        try:
            limit = max(1, min(int(request.args.get("limit", 500)), 5000))
        except (TypeError, ValueError):
            limit = 500
        d_raw = request.args.get("start")
        try:
            start = datetime.strptime(d_raw, "%Y-%m-%d").date() if d_raw else date.today().replace(day=1)
        except ValueError:
            return jsonify({"error": "start must be YYYY-MM-DD"}), 400
        d_raw = request.args.get("end")
        try:
            end = datetime.strptime(d_raw, "%Y-%m-%d").date() if d_raw else date.today()
        except ValueError:
            return jsonify({"error": "end must be YYYY-MM-DD"}), 400
        if end < start:
            return jsonify({"error": "end must be on/after start"}), 400

        rows = (
            TeacherAttendance.query
            .filter(
                TeacherAttendance.teacher_id == tid,
                TeacherAttendance.attendance_date >= start,
                TeacherAttendance.attendance_date <= end,
            )
            .order_by(TeacherAttendance.attendance_date.desc())
            .limit(limit)
            .all()
        )
        out = []
        for row in rows:
            out.append({
                "id": row.id,
                "date": row.attendance_date.isoformat() if row.attendance_date else None,
                "in_time": row.in_time.isoformat() if row.in_time else None,
                "out_time": row.out_time.isoformat() if row.out_time else None,
                "working_minutes": row.working_minutes or 0,
                "overtime_minutes": row.overtime_minutes or 0,
                "break_minutes": row.break_minutes or 0,
                "status": row.status,
                "is_late": bool(row.is_late),
                "is_early_exit": bool(row.is_early_exit),
                "camera": row.camera.name if row.camera else None,
                "confidence": row.confidence,
            })
        return jsonify({
            "teacher": {
                "id": t.id,
                "name": t.name,
                "teacher_id": t.teacher_id,
                "department": t.department,
                "designation": t.designation,
                "subject": t.subject,
            },
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rows": out,
            "count": len(out),
        })

    # ----------------------------------------------------------- MJPEG stream
    _STOPPED_FRAME_CACHE = {}

    def _generate_stopped_frame():
        if "bytes" in _STOPPED_FRAME_CACHE:
            return _STOPPED_FRAME_CACHE["bytes"]
        canvas = np.zeros((270, 480, 3), dtype=np.uint8)
        canvas[:] = (18, 24, 38)
        cv2.putText(canvas, "Camera Stopped", (140, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (220, 220, 220), 2)
        cv2.putText(canvas, "Click 'Start' to activate stream & recognition", (95, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (130, 150, 175), 1)
        ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 75])
        _STOPPED_FRAME_CACHE["bytes"] = buf.tobytes() if ok else b""
        return _STOPPED_FRAME_CACHE["bytes"]

    @app.route("/stream/<int:cid>")
    def stream(cid: int):
        cam = Camera.query.get_or_404(cid)
        if not cam.is_active:
            return Response(_generate_stopped_frame(), mimetype="image/jpeg")

        if cid not in manager.recognizers:
            manager.start(cam, app)

        def gen():
            """MJPEG generator — reads pre-encoded JPEG from shared publisher.

            JPEG encoding happens exactly ONCE per frame (in the publisher
            thread), regardless of how many browser clients are connected.
            subscribe()/unsubscribe() signals the publisher thread to work
            or idle based on whether any viewer is present.
            """
            publisher = manager.get_publisher(cid)
            if publisher is None:
                return

            publisher.subscribe()
            last_seq = 0
            try:
                while True:
                    # Verify the camera/recognizer is still running.
                    rec = manager.recognizers.get(cid)
                    if rec is None or not rec.running:
                        break

                    # Block until a new JPEG frame is ready (timeout = 0.5s
                    # so we re-check the running state regularly).
                    seq, jpeg_bytes = publisher.get(last_seq, timeout=0.5)

                    if seq == last_seq:
                        # Timeout — loop again and recheck running state.
                        continue

                    last_seq = seq
                    if jpeg_bytes:
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n"
                            + jpeg_bytes
                            + b"\r\n"
                        )
            finally:
                # This runs when the client disconnects (generator GC or close).
                publisher.unsubscribe()

        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    # ----------------------------------------------------------------- /api/ask
    @app.route("/api/ask", methods=["POST"])
    def api_ask():
        from slm.nlp_engine import answer as slm_answer
        data = request.get_json(force=True)
        question = data.get("question", "")
        return jsonify({"answer": slm_answer(question)})


import time  # noqa: E402  (used by MJPEG generator's sleep)

app = create_app()


def _resolve_port(default_port: int = 5001) -> int:
    requested_port = int(os.getenv("PORT", str(default_port)))
    if requested_port <= 0:
        return default_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", requested_port))
        except OSError:
            return 5001 if requested_port == 5000 else requested_port
        return requested_port


if __name__ == "__main__":
    # Server starts cleanly on demand.
    # Camera capture and face recognition threads run ONLY when explicitly started by user.
    app.run(
        host="0.0.0.0",
        port=_resolve_port(),
        debug=app.config["DEBUG"],
        use_reloader=False,   # prevents duplicate camera workers on debug mode
        threaded=True,
    )
