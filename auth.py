"""Authentication, server-side session validation, CSRF protection and Multi-Tenant RBAC."""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from datetime import timedelta
from email.mime.text import MIMEText
from functools import wraps
from smtplib import SMTP
from urllib.parse import urljoin, urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from authlib.integrations.flask_client import OAuth
from authlib.jose import jwt
from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func

from database.auth_models import AuthSession, AuthToken, LoginEvent, OAuthIdentity, User, utcnow
from database.db import db
from database.tenant_models import School
from database.models import Camera

logger = logging.getLogger(__name__)

# Configuration constants for verification code
VERIFICATION_CODE_LENGTH = 6

bp = Blueprint("auth", __name__)
oauth = OAuth()
ph = PasswordHasher()

# ---------------------------------------------------------------------------
# Multi-Tenant Institutional Roles
# ---------------------------------------------------------------------------
ROLES = {
    "super_admin",
    "institution_admin",
    "principal",
    "trustee",
    "dean",
    "hod",
    "hr_admin",
    "payroll_manager",
    "finance_manager",
    "attendance_manager",
    "receptionist",
    "teacher",
    "employee",
    "viewer",
}

ADMIN_ROLES = {"super_admin", "institution_admin", "principal", "hr_admin"}

# ---------------------------------------------------------------------------
# Permissions — role → set of granular permission strings
# ---------------------------------------------------------------------------
_ALL_PERMISSIONS = {
    "dashboard.view", "dashboard.manage",
    "students.view", "students.create", "students.update", "students.delete",
    "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
    "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
    "cameras.view", "cameras.create", "cameras.update", "cameras.delete", "cameras.start", "cameras.stop",
    "reports.view", "reports.export",
    "settings.view", "settings.update",
    "users.view", "users.create", "users.update", "users.disable", "users.roles.update", "users.delete",
    "employees.view", "employees.create", "employees.update", "employees.delete",
    "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
    "departments.manage", "designations.manage",
    "teams.view", "teams.manage",
    "grades.view", "grades.manage",
    "job_levels.view", "job_levels.manage",
    "cost_centers.view", "cost_centers.manage",
    "employment_types.view", "employment_types.manage",
    "leave.view", "leave.create", "leave.approve",
    "payroll.view", "payroll.process", "payroll.finalize",
    "enterprise.view", "enterprise.manage",
    "time_rules.view", "time_rules.update",
    "unknown_faces.view", "unknown_faces.action",
    "institutions.view", "institutions.manage",
    "devices.view", "devices.manage", "devices.enroll", "devices.test",
    "chat.view",
    "teacher_portal.view",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": _ALL_PERMISSIONS.copy(),
    "institution_admin": {
        "dashboard.view", "dashboard.manage",
        "students.view", "students.create", "students.update", "students.delete",
        "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
        "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
        "cameras.view", "cameras.create", "cameras.update", "cameras.delete", "cameras.start", "cameras.stop",
        "reports.view", "reports.export",
        "settings.view", "settings.update",
        "users.view", "users.create", "users.update", "users.disable", "users.roles.update", "users.delete",
        "employees.view", "employees.create", "employees.update", "employees.delete",
        "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
        "departments.manage", "designations.manage",
        "teams.view", "teams.manage",
        "grades.view", "grades.manage",
        "job_levels.view", "job_levels.manage",
        "cost_centers.view", "cost_centers.manage",
        "employment_types.view", "employment_types.manage",
        "leave.view", "leave.create", "leave.approve",
        "payroll.view", "payroll.process", "payroll.finalize",
        "enterprise.view", "enterprise.manage",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "devices.view", "devices.manage", "devices.enroll", "devices.test",
        "chat.view",
        "teacher_portal.view",
    },
    "principal": {
        "dashboard.view", "dashboard.manage",
        "students.view", "students.create", "students.update", "students.delete",
        "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
        "attendance.view", "attendance.mark", "attendance.update",
        "cameras.view", "cameras.start", "cameras.stop",
        "reports.view", "reports.export",
        "settings.view",
        "users.view",
        "employees.view", "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
        "teams.view", "grades.view", "job_levels.view", "cost_centers.view", "employment_types.view",
        "leave.view", "leave.approve",
        "payroll.view",
        "enterprise.view", "enterprise.manage",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "devices.view", "devices.manage", "devices.enroll", "devices.test",
        "chat.view",
        "teacher_portal.view",
    },
    "trustee": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view",
        "cameras.view",
        "reports.view", "reports.export",
        "employees.view", "employees.timeline.view",
        "payroll.view",
        "enterprise.view",
        "chat.view",
    },
    "dean": {
        "dashboard.view",
        "students.view", "students.update",
        "teachers.view", "teachers.update",
        "attendance.view", "attendance.mark",
        "cameras.view",
        "reports.view", "reports.export",
        "employees.view", "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
        "teams.view", "grades.view", "job_levels.view",
        "leave.view", "leave.approve",
        "enterprise.view",
        "time_rules.view",
        "unknown_faces.view",
        "chat.view",
    },
    "hod": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view", "attendance.mark",
        "cameras.view",
        "reports.view",
        "employees.view", "employees.timeline.view", "employees.id_card.generate",
        "teams.view",
        "leave.view", "leave.create", "leave.approve",
        "enterprise.view",
        "time_rules.view",
        "chat.view",
    },
    "hr_admin": {
        "dashboard.view", "dashboard.manage",
        "students.view", "students.create", "students.update", "students.delete",
        "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
        "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
        "cameras.view", "cameras.create", "cameras.update", "cameras.delete", "cameras.start", "cameras.stop",
        "reports.view", "reports.export",
        "employees.view", "employees.create", "employees.update", "employees.delete",
        "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
        "departments.manage", "designations.manage",
        "teams.view", "teams.manage",
        "grades.view", "grades.manage",
        "job_levels.view", "job_levels.manage",
        "cost_centers.view", "cost_centers.manage",
        "employment_types.view", "employment_types.manage",
        "leave.view", "leave.create", "leave.approve",
        "payroll.view", "payroll.process", "payroll.finalize",
        "enterprise.view", "enterprise.manage",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "users.view", "users.create", "users.update", "users.roles.update",
        "devices.view", "devices.manage", "devices.enroll", "devices.test",
        "chat.view",
        "teacher_portal.view",
    },
    "payroll_manager": {
        "dashboard.view",
        "employees.view", "employees.update",
        "leave.view", "leave.approve",
        "payroll.view", "payroll.process", "payroll.finalize",
        "reports.view", "reports.export",
        "chat.view",
    },
    "finance_manager": {
        "dashboard.view",
        "employees.view",
        "payroll.view", "payroll.process", "payroll.finalize",
        "cost_centers.view", "cost_centers.manage",
        "reports.view", "reports.export",
        "chat.view",
    },
    "attendance_manager": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
        "cameras.view", "cameras.create", "cameras.update", "cameras.start", "cameras.stop",
        "reports.view", "reports.export",
        "enterprise.view", "enterprise.manage",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "devices.view", "devices.test",
        "chat.view",
        "teacher_portal.view",
    },
    "receptionist": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view", "attendance.mark",
        "cameras.view",
        "reports.view",
        "unknown_faces.view", "unknown_faces.action",
        "chat.view",
    },
    "manager": {
        "dashboard.view",
        "students.view", "students.create", "students.update", "students.delete",
        "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
        "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
        "cameras.view", "cameras.start", "cameras.stop",
        "reports.view",
        "employees.view", "employees.create", "employees.update",
        "employees.history.view", "employees.timeline.view", "employees.id_card.generate",
        "teams.view",
        "leave.view", "leave.create", "leave.approve",
        "enterprise.view",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "chat.view",
        "teacher_portal.view",
    },
    "teacher": {
        "dashboard.view",
        "chat.view",
        "teacher_portal.view",
    },
    "employee": {
        "dashboard.view",
        "students.view", "students.create", "students.update",
        "teachers.view",
        "attendance.view", "attendance.mark", "attendance.update",
        "cameras.view", "cameras.start", "cameras.stop",
        "reports.view",
        "employees.view",
        "employees.id_card.generate",
        "settings.view",
        "enterprise.view",
        "time_rules.view",
        "unknown_faces.view", "unknown_faces.action",
        "chat.view",
        "teacher_portal.view",
    },
    "viewer": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view",
        "cameras.view",
        "reports.view",
        "employees.view",
        "payroll.view",
        "leave.view",
        "settings.view",
        "enterprise.view",
        "time_rules.view",
        "chat.view",
    },
}

# ---------------------------------------------------------------------------
# Endpoint → required permission (method-aware)
# ---------------------------------------------------------------------------
_ENDPOINT_PERMS: dict[str, dict[str, str | None]] = {
    # --- Page routes ---
    "page_dashboard": {"GET": "dashboard.view"},
    "page_teacher_portal": {"GET": "dashboard.view"},
    "page_teacher_portal_sub": {"GET": "dashboard.view"},
    "page_attendance": {"GET": "attendance.view"},
    "page_students": {"GET": "students.view"},
    "page_students_class": {"GET": "students.view"},
    "page_teachers": {"GET": "teachers.view"},
    "page_cameras": {"GET": "cameras.view"},
    "page_reports": {"GET": "reports.view"},
    "page_settings": {"GET": "settings.view"},
    "chat_page": {"GET": "chat.view"},
    "page_users": {"GET": "users.view"},
    "page_hr_dashboard": {"GET": "enterprise.view"},
    "page_hr_employees": {"GET": "employees.view"},
    "page_hr_employee_profile": {"GET": "employees.view"},
    "page_teacher_360_profile": {"GET": "employees.view"},
    "page_hr_payroll": {"GET": "payroll.view"},
    "page_hr_reports": {"GET": "reports.view"},
    "page_hr_settings": {"GET": "settings.update"},
    "page_time_rules": {"GET": "time_rules.view"},
    "page_dashboard_enterprise": {"GET": "enterprise.view"},
    "page_reports_enterprise": {"GET": "reports.view"},
    "page_attendance_extended": {"GET": "attendance.view"},
    "page_fingerprint_device": {"GET": "devices.view"},
    "stream": {"GET": "cameras.view"},
    # --- Teacher Portal API routes ---
    "api_teacher_portal_dashboard": {"GET": "teacher_portal.view"},
    "api_teacher_portal_history": {"GET": "teacher_portal.view"},
    "api_teacher_portal_summary": {"GET": "teacher_portal.view"},
    "api_teacher_portal_leaves": {"GET": "teacher_portal.view", "POST": "teacher_portal.view"},
    "api_teacher_portal_single_leave": {"GET": "teacher_portal.view"},
    "api_teacher_portal_corrections": {"GET": "teacher_portal.view", "POST": "teacher_portal.view"},
    "api_teacher_portal_single_correction": {"GET": "teacher_portal.view"},
    "api_teacher_portal_single_attendance": {"GET": "teacher_portal.view"},
    "api_teacher_portal_profile": {"GET": "teacher_portal.view"},
    # --- API routes ---
    "api_meta": {"GET": None},
    "api_stats": {"GET": "dashboard.view"},
    "api_students": {"GET": "students.view", "POST": "students.create"},
    "api_students_sid": {"GET": "students.view", "PUT": "students.update", "DELETE": "students.delete"},
    "api_student_detail": {"GET": "students.view", "PUT": "students.update", "DELETE": "students.delete"},
    "api_student_photo": {"POST": "students.update"},
    "api_teachers": {"GET": "teachers.view", "POST": "teachers.create"},
    "api_teacher_detail": {"PUT": "teachers.update", "DELETE": "teachers.delete"},
    "api_teacher_photo": {"POST": "teachers.update"},
    "api_teacher_profile": {"GET": "teachers.view"},
    "api_teacher_attendance": {"GET": "attendance.view"},
    "api_teacher_fingerprint_enroll": {"POST": "devices.enroll", "DELETE": "devices.enroll"},
    "api_teacher_fingerprint_status": {"GET": "teachers.view"},
    "api_fingerprint_device_config": {"GET": "devices.view", "POST": "devices.manage"},
    "api_fingerprint_device_connect": {"POST": "devices.manage"},
    "api_fingerprint_device_disconnect": {"POST": "devices.manage"},
    "api_fingerprint_device_test_conn": {"POST": "devices.test"},
    "api_fingerprint_device_status": {"GET": "devices.view"},
    "api_fingerprint_device_mappings": {"GET": "devices.view", "POST": "devices.manage"},
    "api_fingerprint_device_mapping_delete": {"DELETE": "devices.manage"},
    "api_fingerprint_device_enroll": {"POST": "devices.enroll"},
    "api_fingerprint_device_test_verify": {"POST": "devices.test"},
    "api_fingerprint_device_events": {"GET": "devices.view"},
    "api_fingerprint_device_import": {"POST": "devices.manage"},
    "api_biomax_status": {"GET": "devices.view"},
    "api_biomax_audit": {"GET": "devices.view"},
    "api_biomax_event": {"POST": "devices.manage"},
    "api_attendance": {"GET": "attendance.view", "POST": "attendance.mark"},
    "api_attendance_sid": {"PUT": "attendance.update", "DELETE": "attendance.delete"},
    "api_attendance_detail": {"PUT": "attendance.update", "DELETE": "attendance.delete"},
    "api_attendance_today": {"GET": "attendance.view"},
    "api_attendance_export": {"GET": "reports.export"},
    "api_cameras": {"GET": "cameras.view", "POST": "cameras.create"},
    "api_camera_detail": {"PUT": "cameras.update", "DELETE": "cameras.delete"},
    "api_camera_start": {"POST": "cameras.start"},
    "api_camera_stop": {"POST": "cameras.stop"},
    "api_camera_test": {"POST": "cameras.start"},
    "api_cameras_status": {"GET": "cameras.view"},
    "api_reports_export": {"GET": "reports.export"},
    "api_reports_preview": {"GET": "reports.view"},
    "api_unknown_faces": {"GET": "unknown_faces.view"},
    "api_unknown_face_action": {"POST": "unknown_faces.action", "DELETE": "unknown_faces.action"},
    "api_ask": {"POST": "chat.view"},
    # --- User Management API ---
    "auth.api_users": {"GET": "users.view", "POST": "users.create"},
    "auth.api_user_detail": {"GET": "users.view", "PUT": "users.update", "DELETE": "users.delete"},
    "auth.api_settings_school": {"GET": "settings.view", "PUT": "settings.update", "POST": "settings.update"},
    # --- Enterprise / HR API (blueprint prefixes) ---
    "hr.departments": {"GET": "employees.view", "POST": "departments.manage"},
    "hr.department_detail": {"GET": "employees.view", "PUT": "departments.manage", "DELETE": "departments.manage"},
    "hr.designations": {"GET": "employees.view", "POST": "designations.manage"},
    "hr.designation_detail": {"GET": "employees.view", "PUT": "designations.manage", "DELETE": "designations.manage"},
    "hr.cost_centers": {"GET": "cost_centers.view", "POST": "cost_centers.manage"},
    "hr.cost_center_detail": {"GET": "cost_centers.view", "PUT": "cost_centers.manage", "DELETE": "cost_centers.manage"},
    "hr.job_levels": {"GET": "job_levels.view", "POST": "job_levels.manage"},
    "hr.job_level_detail": {"GET": "job_levels.view", "PUT": "job_levels.manage", "DELETE": "job_levels.manage"},
    "hr.job_grades": {"GET": "grades.view", "POST": "grades.manage"},
    "hr.job_grade_detail": {"GET": "grades.view", "PUT": "grades.manage", "DELETE": "grades.manage"},
    "hr.employment_types": {"GET": "employment_types.view", "POST": "employment_types.manage"},
    "hr.employment_type_detail": {"GET": "employment_types.view", "PUT": "employment_types.manage", "DELETE": "employment_types.manage"},
    "hr.teams": {"GET": "teams.view", "POST": "teams.manage"},
    "hr.team_detail": {"GET": "teams.view", "PUT": "teams.manage", "DELETE": "teams.manage"},
    "hr.salary_components": {"GET": "payroll.view", "POST": "payroll.view"},
    "hr.salary_component_detail": {"GET": "payroll.view", "PUT": "payroll.view"},
    "hr.sync_teachers": {"POST": "employees.create"},
    "hr.employees": {"GET": "employees.view", "POST": "employees.create"},
    "hr.employee_detail": {"GET": "employees.view", "PUT": "employees.update", "DELETE": "employees.delete"},
    "hr.employee_by_teacher": {"GET": "employees.view"},
    "hr.employee_salary": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_salary_structure": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_salary_history": {"GET": "employees.view"},
    "hr.shifts": {"GET": "employees.view", "POST": "employees.update"},
    "hr.shift_detail": {"GET": "employees.view", "PUT": "employees.update"},
    "hr.employee_leaves": {"POST": "leave.create"},
    "hr.employee_overtime": {"POST": "employees.update"},
    "hr.employee_bonuses": {"POST": "employees.update"},
    "hr.employee_loans": {"GET": "employees.view", "POST": "employees.update"},
    "hr.loan_repayments": {"POST": "employees.update"},
    "hr.employee_reimbursements": {"POST": "employees.update"},
    "hr.employee_transfer_department": {"POST": "employees.update"},
    "hr.employee_change_designation": {"POST": "employees.update"},
    "hr.employee_change_manager": {"POST": "employees.update"},
    "hr.employee_change_status": {"POST": "employees.update"},
    "hr.employee_promotions": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_timeline": {"GET": "employees.timeline.view", "POST": "employees.update"},
    "hr.employee_department_history": {"GET": "employees.history.view"},
    "hr.employee_designation_history": {"GET": "employees.history.view"},
    "hr.employee_manager_history": {"GET": "employees.history.view"},
    "hr.employee_status_history": {"GET": "employees.history.view"},
    "hr.employee_emergency_contacts": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_emergency_contact_detail": {"PUT": "employees.update", "DELETE": "employees.update"},
    "hr.employee_dependents": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_dependent_detail": {"PUT": "employees.update", "DELETE": "employees.update"},
    "hr.employee_360_profile": {"GET": "employees.view"},
    "hr.employee_documents": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_document_delete": {"DELETE": "employees.update"},
    "hr.employee_leave_balances": {"GET": "leave.view"},
    "hr.employee_id_card": {"GET": "employees.id_card.generate"},
    "hr.verify_id_card": {"GET": None},
    "hr.leave_types": {"GET": "leave.view", "POST": "leave.create"},
    "hr.leaves": {"GET": "leave.view"},
    "hr.leave_approve": {"PUT": "leave.approve"},
    "hr.leave_reject": {"PUT": "leave.approve"},
    "hr.leave_cancel": {"PUT": "leave.create"},
    "hr.overtime": {"GET": "leave.view"},
    "hr.overtime_approve": {"PUT": "leave.approve"},
    "hr.reimbursements": {"GET": "leave.view"},
    "hr.reimbursement_approve": {"PUT": "leave.approve"},
    "hr.payroll_periods": {"GET": "payroll.view", "POST": "payroll.process"},
    "hr.payroll_period_detail": {"GET": "payroll.view"},
    "hr.payroll_process": {"POST": "payroll.process"},
    "hr.payroll_finalize": {"POST": "payroll.finalize"},
    "hr.payroll_records": {"GET": "payroll.view"},
    "hr.payroll_record_detail": {"GET": "payroll.view"},
    "hr.payslips": {"GET": "payroll.view", "POST": "payroll.view"},
    "hr.dashboard_summary": {"GET": "enterprise.view"},
    "hr.audit_log": {"GET": "settings.view"},
    "api_enterprise_attendance": {"GET": "enterprise.view", "POST": "attendance.mark"},
    "api_enterprise_dashboard": {"GET": "enterprise.view"},
    "api_enterprise_summary": {"GET": "enterprise.view"},
    "api_enterprise_widgets": {"GET": "enterprise.view"},
    "api_enterprise_sessions": {"GET": "attendance.view"},
    "api_enterprise_events": {"GET": "attendance.view"},
    "api_enterprise_settings": {"GET": "settings.view", "PUT": "settings.update"},
    "api_enterprise_time_rules": {"GET": "time_rules.view", "PUT": "time_rules.update"},
    # --- Auth account pages (always allowed if authenticated) ---
    "auth.profile": {"GET": None},
    "auth.security": {"GET": None},
    "auth.active_sessions": {"GET": None},
    "auth.change_password": {"GET": None, "POST": None},
}


def _required_permission(endpoint: str | None, method: str) -> str | None:
    """Return the permission required for *endpoint* + *method*, or None."""
    if not endpoint:
        return None
    rule = _ENDPOINT_PERMS.get(endpoint)
    if rule:
        return rule.get(method.upper())
    if "." in endpoint:
        rule = _ENDPOINT_PERMS.get(endpoint)
        if rule:
            return rule.get(method.upper())
    return None


def user_has_permission(user: User, permission: str) -> bool:
    if not user or not user.role:
        return False
    if user.role == "super_admin":
        return True
    return permission in ROLE_PERMISSIONS.get(user.role, set())


# ---------------------------------------------------------------------------
# Tenant Context Helpers
# ---------------------------------------------------------------------------

def current_user() -> User | None:
    return getattr(g, "user", None)


def current_school() -> School | None:
    user = current_user()
    return user.school if user else None


def current_school_id() -> int | None:
    user = current_user()
    if not user:
        return None
    return user.school_id


def is_tenant_permitted(entity_school_id, user: User | None = None) -> bool:
    """Check if the current authenticated user has permission to access an entity
    belonging to entity_school_id.

    * super_admin has platform-wide access across all schools.
    * If either entity_school_id or current_school_id is None, access is permitted (backwards compatible).
    * Compares values flexibly after string-normalization so int vs string SQLite anomalies don't cause false mismatches.
    """
    if user is None:
        user = current_user()
    if user and getattr(user, "role", None) == "super_admin":
        return True
    cur_sid = current_school_id()
    if cur_sid is None or entity_school_id is None:
        return True
    return str(entity_school_id).strip() == str(cur_sid).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_error(message: str, code: int):
    return jsonify({"error": message}), code


def _event(email: str, event: str, user: User | None = None) -> None:
    db.session.add(LoginEvent(
        user_id=user.id if user else None, email=email, event=event,
        ip_address=request.remote_addr, user_agent=request.user_agent.string[:500],
    ))


def _form_csrf_token() -> str:
    """Return a CSRF token for authenticated and anonymous HTML forms."""
    if getattr(g, "auth_session", None):
        return g.auth_session.csrf_secret
    token = session.get("form_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["form_csrf"] = token
    return token


def _valid_csrf(token: str | None = None) -> bool:
    supplied = (
        token
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-Csrf-Token")
        or request.form.get("csrf_token", "")
    )
    return bool(supplied) and secrets.compare_digest(supplied, _form_csrf_token())


def _clear_login(response=None):
    """Clear the signed browser session and explicitly expire its cookie."""
    session.clear()
    if response is not None:
        response.delete_cookie(
            current_app.config["SESSION_COOKIE_NAME"],
            path=current_app.config.get("SESSION_COOKIE_PATH", "/"),
            domain=current_app.config.get("SESSION_COOKIE_DOMAIN"),
            secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
            httponly=True,
            samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        )
    return response


def _safe_next(value: str | None) -> str | None:
    if not value:
        return None
    target = urlparse(urljoin(request.host_url, value))
    return value if target.scheme in {"http", "https"} and target.netloc == request.host else None


def _issue(user: User, remember: bool = False) -> None:
    """Rotate the browser/session identifier and create a hashed server record."""
    raw = secrets.token_urlsafe(48)
    hours = current_app.config["AUTH_SESSION_HOURS"]
    row = AuthSession(
        token_hash=_hash(raw), user=user, csrf_secret=secrets.token_urlsafe(32),
        device=request.user_agent.string[:255], ip_address=request.remote_addr,
        expires_at=utcnow() + timedelta(hours=hours),
    )
    db.session.add(row)
    user.last_login_at = utcnow()
    db.session.commit()
    session.clear()  # session fixation defense: preserve no pre-auth keys/state
    session["sid"] = raw
    session.permanent = remember


def csrf_token() -> str:
    return _form_csrf_token()


def require_roles(*roles: str):
    """Optional endpoint decorator for sensitive admin-only operations."""
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user or g.user.role not in roles:
                return _json_error("insufficient permission", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def _too_many_failures(email: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Global before-request: authentication + authorization (GET & Non-GET)
# ---------------------------------------------------------------------------

@bp.before_app_request
def load_and_protect():
    g.user = g.auth_session = None
    raw = session.get("sid")
    if raw:
        row = AuthSession.query.filter_by(token_hash=_hash(raw), revoked_at=None).first()
        if row and row.expires_at > utcnow() and row.user.role in ROLES and row.user.is_active:
            g.auth_session, g.user = row, row.user
            row.last_seen_at = utcnow()
            db.session.commit()
        else:
            _clear_login()

    endpoint = request.endpoint or ""
    public = {
        "auth.login", "auth.signup", "auth.forgot_password", "auth.reset_password",
        "auth.verify_email", "auth.resend_verification", "auth.verify_code",
        "auth.oauth_google", "auth.oauth_google_callback",
        "auth.oauth_microsoft", "auth.oauth_microsoft_callback",
        "auth.oauth_apple", "auth.oauth_apple_callback",
        "page_landing", "page_landing_alias",
        "static",
        "hr.verify_id_card",
    }
    if (
        endpoint in public
        or request.path.startswith("/static/")
        or request.path.startswith("/api/hr/verify/")
    ):
        return None

    if request.path in {"/api/devices/biomax/event", "/api/devices/fingerprint/verify"} and not g.user:
        # The route performs per-device token validation before accepting any event.
        return None


    if not g.user:
        return _json_error("authentication required", 401) if request.path.startswith("/api/") else redirect(url_for("auth.login", next=request.full_path))
    
    # Check email verification - unverified users cannot access protected pages
    if not g.user.email_verified_at:
        _event(g.user.email, "unverified_access_blocked", g.user)
        db.session.commit()
        _clear_login()
        return _json_error("email verification required", 403) if request.path.startswith("/api/") else redirect(url_for("auth.verify_code", email=g.user.email))

    # CSRF protection on mutating requests
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not _valid_csrf():
            return _json_error("invalid CSRF token", 400)

    # Granular RBAC Permission enforcement across ALL methods (including GET)
    perm = _required_permission(endpoint, request.method)
    if perm and not user_has_permission(g.user, perm):
        if request.path.startswith("/api/") or request.is_json:
            return _json_error("insufficient permission", 403)
        return render_template("404.html", error="Forbidden: You do not have permission to view this page."), 403


@bp.app_context_processor
def auth_context():
    return {
        "current_user": current_user(),
        "current_school": current_school(),
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

def _password_ok(password: str) -> bool:
    """Validate password: at least 6 characters."""
    return len(password) >= 6


# ---------------------------------------------------------------------------
# Email verification helpers
# ---------------------------------------------------------------------------

def _send_verification_email(user: User, raw_token: str) -> None:
    """Send a verification email via SMTP, or log the link in DEBUG mode."""
    verify_url = url_for("auth.verify_email", token=raw_token, _external=True)
    if current_app.config.get("SMTP_HOST") and current_app.config.get("SMTP_USERNAME"):
        try:
            msg = MIMEText(
                render_template("auth/verify_email.txt", name=user.name, url=verify_url),
                "plain",
            )
            msg["Subject"] = "Verify your email — KRISHNA PAYROLL"
            msg["From"] = current_app.config.get("SMTP_FROM", current_app.config["SMTP_USERNAME"])
            msg["To"] = user.email
            with SMTP(current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"]) as s:
                s.starttls()
                s.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
                s.send_message(msg)
            logger.info("Verification email sent to %s", user.email)
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)
    elif current_app.debug:
        logger.info("DEBUG verification link for %s: %s", user.email, verify_url)
    else:
        logger.warning("SMTP not configured; verification email for %s not sent", user.email)


def _create_verification_token(user: User) -> str:
    """Create a single-use verification token and send the email. Returns the raw token."""
    AuthToken.query.filter_by(user_id=user.id, purpose="verify", used_at=None).update({"used_at": utcnow()})
    raw = secrets.token_urlsafe(48)
    hours = current_app.config["AUTH_VERIFICATION_HOURS"]
    db.session.add(AuthToken(token_hash=_hash(raw), user_id=user.id, purpose="verify", expires_at=utcnow() + timedelta(hours=hours)))
    db.session.commit()
    _send_verification_email(user, raw)
    return raw


def _generate_verification_code() -> str:
    """Generate a cryptographically secure 6-digit numeric code."""
    return "".join(str(secrets.randbelow(10)) for _ in range(VERIFICATION_CODE_LENGTH))


def _send_verification_email_with_code(user: User, raw_token: str, code: str) -> None:
    """Send a verification email with both link and 6-digit code via SMTP."""
    verify_url = url_for("auth.verify_email", token=raw_token, _external=True)
    code_expiry_minutes = current_app.config["AUTH_VERIFICATION_CODE_TTL_MINUTES"]
    
    if current_app.config.get("SMTP_HOST") and current_app.config.get("SMTP_USERNAME"):
        try:
            html_body = render_template(
                "auth/verify_email.html",
                name=user.name,
                url=verify_url,
                code=code,
                code_expiry_minutes=code_expiry_minutes,
            )
            msg = MIMEText(html_body, "html")
            msg["Subject"] = "Verify your email — KRISHNA PAYROLL"
            msg["From"] = current_app.config.get("SMTP_FROM", current_app.config["SMTP_USERNAME"])
            msg["To"] = user.email
            
            with SMTP(current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"], timeout=5) as s:
                s.starttls()
                s.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
                s.send_message(msg)
            logger.info("Verification email sent to %s", user.email)
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)
    elif current_app.debug:
        logger.info("DEBUG verification link for %s: %s", user.email, verify_url)
        logger.info("DEBUG verification code for %s: %s", user.email, code)
    else:
        logger.warning("SMTP not configured; verification email for %s not sent", user.email)


def _create_verification_token_and_code(user: User) -> tuple[str, str]:
    """Create a single-use verification token and 6-digit code, store both, send email."""
    AuthToken.query.filter_by(user_id=user.id, purpose="verify", used_at=None).update({"used_at": utcnow()})
    
    raw_token = secrets.token_urlsafe(48)
    code = _generate_verification_code()
    code_hash = _hash(code)
    
    hours = current_app.config["AUTH_VERIFICATION_HOURS"]
    code_expires_at = utcnow() + timedelta(minutes=current_app.config["AUTH_VERIFICATION_CODE_TTL_MINUTES"])
    
    token = AuthToken(
        token_hash=_hash(raw_token),
        user_id=user.id,
        purpose="verify",
        expires_at=utcnow() + timedelta(hours=hours),
        verification_code_hash=code_hash,
        verification_code_expires_at=code_expires_at,
        verification_attempts=0,
    )
    db.session.add(token)
    db.session.commit()
    
    _send_verification_email_with_code(user, raw_token, code)
    return raw_token, code


def _verify_code(user: User, code: str) -> tuple[bool, str]:
    """Verify a 6-digit code for a user."""
    token = AuthToken.query.filter_by(
        user_id=user.id, purpose="verify", used_at=None
    ).order_by(AuthToken.id.desc()).first()
    
    if not token:
        return False, "No verification pending. Please request a new code."
    
    if user.email_verified_at:
        return True, "Email already verified."
    
    if token.verification_code_expires_at and token.verification_code_expires_at <= utcnow():
        return False, "Verification code has expired. Please request a new code."
    
    max_attempts = current_app.config["AUTH_MAX_VERIFICATION_ATTEMPTS"]
    if token.verification_attempts >= max_attempts:
        return False, "Too many failed attempts. Please request a new code."
    
    if not token.verification_code_hash or not secrets.compare_digest(token.verification_code_hash, _hash(code)):
        token.verification_attempts += 1
        db.session.commit()
        remaining = max_attempts - token.verification_attempts
        if remaining > 0:
            return False, f"Invalid verification code. {remaining} attempts remaining."
        return False, "Too many failed attempts. Please request a new code."
    
    token.verification_code_used_at = utcnow()
    token.used_at = utcnow()
    user.email_verified_at = utcnow()
    db.session.commit()
    
    return True, "Email verified successfully."


def _resend_verification(user: User) -> tuple[bool, str]:
    cooldown = current_app.config["AUTH_VERIFICATION_RESEND_COOLDOWN_SECONDS"]
    recent_resend = LoginEvent.query.filter(
        LoginEvent.event == "verification_resent",
        LoginEvent.user_id == user.id,
        LoginEvent.created_at >= utcnow() - timedelta(seconds=cooldown),
    ).first()
    
    if recent_resend:
        return False, f"Please wait {cooldown} seconds before requesting a new code."
    
    _create_verification_token_and_code(user)
    _event(user.email, "verification_resent", user)
    db.session.commit()
    return True, "Verification email sent."


# ---------------------------------------------------------------------------
# Routes — Email verification
# ---------------------------------------------------------------------------

@bp.route("/verify-email/<token>", methods=["GET"])
def verify_email(token: str):
    row = AuthToken.query.filter_by(token_hash=_hash(token), purpose="verify", used_at=None).first()
    if not row or row.expires_at <= utcnow():
        return render_template("auth/login.html", error="This verification link is invalid or has expired.")
    user = db.session.get(User, row.user_id)
    if not user:
        return render_template("auth/login.html", error="This verification link is invalid or has expired.")
    
    if user.email_verified_at:
        return render_template("auth/login.html", verified="Email already verified. You may now sign in.")
    
    row.used_at = utcnow()
    if hasattr(row, "verification_code_used_at"):
        row.verification_code_used_at = utcnow()
    user.email_verified_at = utcnow()
    db.session.commit()
    return render_template("auth/login.html", verified="Your email has been verified. You may now sign in.")


@bp.route("/verify-code", methods=["GET", "POST"])
def verify_code():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/verify_code.html", error="Your form expired. Please try again."), 400
        
        email = (request.form.get("email") or "").strip().lower()
        code = (request.form.get("code") or "").strip()
        
        if not email or not code:
            return render_template("auth/verify_code.html", error="Please enter the verification code."), 400
        
        if not re.fullmatch(r"\d{6}", code):
            return render_template("auth/verify_code.html", error="Please enter a valid 6-digit code."), 400
        
        user = User.query.filter_by(email=email).first()
        if not user:
            return render_template("auth/verify_code.html", error="Invalid verification code."), 400
        
        if user.email_verified_at:
            return render_template("auth/login.html", verified="Email already verified. You may now sign in.")
        
        success, message = _verify_code(user, code)
        if success:
            return render_template("auth/login.html", verified=message)
        return render_template("auth/verify_code.html", error=message, email=email), 400
    
    email = request.args.get("email", "")
    return render_template("auth/verify_code.html", email=email)


@bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/login.html", error="Your form expired. Please try again."), 400
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and not user.email_verified_at:
            success, message = _resend_verification(user)
            if not success:
                return render_template("auth/login.html", verified=message)
        return render_template("auth/login.html", verified="If an account exists with that email, a new verification email has been sent.")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Routes — Multi-Tenant School Login
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("page_dashboard"))
    
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/login.html", error="Your form expired. Please try again."), 400
        
        school_id_input = (request.form.get("school_id") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = None
        if school_id_input:
            school = School.query.filter(
                func.lower(School.school_id) == func.lower(school_id_input)
            ).first()
            if not school or not school.is_active:
                _event(email, "login_denied")
                db.session.commit()
                return render_template(
                    "auth/login.html",
                    error="Invalid School ID or inactive institution.",
                    school_id=school_id_input,
                    email=email
                ), 401
            user = User.query.filter_by(school_id=school.id, email=email).first()
            if not user:
                _event(email, "login_denied")
                db.session.commit()
                return render_template(
                    "auth/login.html",
                    error=f"No account found with email '{email}' for School ID '{school_id_input}'.",
                    school_id=school_id_input,
                    email=email
                ), 401
        else:
            # Look up matching users by email
            matching_users = User.query.filter_by(email=email).all()
            if len(matching_users) == 1:
                user = matching_users[0]
            elif len(matching_users) > 1:
                # Multiple schools have this email, prompt for school_id
                return render_template(
                    "auth/login.html",
                    error="Multiple institutions found for this email. Please specify School ID.",
                    email=email
                ), 400
            else:
                user = None

        if not user or not user.password_hash:
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template(
                "auth/login.html",
                error="Invalid email or password.",
                school_id=school_id_input,
                email=email
            ), 401

        if not user.is_active:
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template(
                "auth/login.html",
                error="This account is deactivated. Please contact an administrator.",
                school_id=school_id_input,
                email=email
            ), 403

        if not user.email_verified_at:
            user.email_verified_at = utcnow()
            db.session.commit()

        try:
            valid = ph.verify(user.password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False

        if not valid:
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template(
                "auth/login.html",
                error="Invalid email or password.",
                school_id=school_id_input,
                email=email
            ), 401

        if ph.check_needs_rehash(user.password_hash):
            user.password_hash = ph.hash(password)

        user.failed_attempts, user.locked_until = 0, None
        _event(email, "login", user)
        _issue(user, bool(request.form.get("remember")))
        return redirect(_safe_next(request.args.get("next")) or url_for("page_dashboard"))

    return render_template("auth/login.html")


# ---------------------------------------------------------------------------
# Routes — Institution Onboarding & Registration
# ---------------------------------------------------------------------------

@bp.route("/signup", methods=["GET", "POST"])
@bp.route("/register-school", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/signup.html", error="Your form expired. Please try again."), 400

        school_name = (request.form.get("school_name") or request.form.get("institution_name") or "").strip()
        school_id_raw = (request.form.get("school_id") or "").strip()
        admin_name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip() or None
        password = request.form.get("password") or ""

        # Auto-generate unique school_id if not explicitly provided
        is_custom_school_id = bool(school_id_raw)
        if not school_id_raw:
            school_id_raw = f"SCH_{uuid.uuid4().hex[:6].upper()}"

        existing_school = School.query.filter(func.lower(School.school_id) == func.lower(school_id_raw)).first()
        if existing_school:
            if is_custom_school_id:
                return render_template("auth/signup.html", error=f"School ID '{school_id_raw}' is already registered. Please choose a different School ID."), 409
            while existing_school:
                school_id_raw = f"SCH_{uuid.uuid4().hex[:6].upper()}"
                existing_school = School.query.filter(func.lower(School.school_id) == func.lower(school_id_raw)).first()

        if not school_name:
            school_name = f"{admin_name}'s Institution" if admin_name else f"School {school_id_raw}"

        if not admin_name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not _password_ok(password):
            return render_template("auth/signup.html", error="Use a valid email and a password between 6 and 12 characters with upper-, lower-case letters and a number."), 400

        if User.query.filter_by(email=email).first():
            return render_template("auth/signup.html", error="An account already exists for this email."), 409

        # Determine assigned role: first user is super_admin; explicit school onboarding gets institution_admin; otherwise employee
        is_first_user = User.query.count() == 0
        if is_first_user:
            assigned_role = "super_admin"
        elif is_custom_school_id or request.form.get("school_name") or request.form.get("institution_name"):
            assigned_role = "institution_admin"
        else:
            assigned_role = "employee"

        # Create Institution
        school = School(
            school_id=school_id_raw,
            name=school_name,
            is_active=True,
        )
        db.session.add(school)
        db.session.flush()

        # Create Administrator / User
        user = User(
            school_id=school.id,
            email=email,
            name=admin_name,
            phone=phone,
            password_hash=ph.hash(password),
            role=assigned_role,
            email_verified_at=utcnow(),
        )
        db.session.add(user)
        db.session.flush()

        _event(email, "institution_registered", user)
        db.session.commit()

        _issue(user)
        return redirect(url_for("page_dashboard"))

    return render_template("auth/signup.html")


# ---------------------------------------------------------------------------
# Routes — Forgot / Reset password
# ---------------------------------------------------------------------------

def _send_reset_email(user: User, raw_token: str) -> None:
    """Send a password reset email via SMTP, or log the link in DEBUG mode."""
    reset_url = url_for("auth.reset_password", token=raw_token, _external=True)
    if current_app.config.get("SMTP_HOST") and current_app.config.get("SMTP_USERNAME"):
        try:
            body = (
                f"Hello {user.name},\n\n"
                f"You requested a password reset for Krishna Payroll.\n"
                f"Click the link below to set a new password:\n{reset_url}\n\n"
                f"This link will expire in 30 minutes.\n"
                f"If you did not request this, you can safely ignore this email.\n"
            )
            msg = MIMEText(body, "plain")
            msg["Subject"] = "Reset your password — KRISHNA PAYROLL"
            msg["From"] = current_app.config.get("SMTP_FROM", current_app.config["SMTP_USERNAME"])
            msg["To"] = user.email
            with SMTP(current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"], timeout=5) as s:
                s.starttls()
                s.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
                s.send_message(msg)
            logger.info("Password reset email sent to %s", user.email)
        except Exception:
            logger.exception("Failed to send password reset email to %s", user.email)
    elif current_app.debug:
        logger.info("DEBUG reset password link for %s: %s", user.email, reset_url)
    else:
        logger.warning("SMTP not configured; password reset email for %s not sent", user.email)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/forgot_password.html", error="Your form expired. Please try again."), 400
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            AuthToken.query.filter_by(user_id=user.id, purpose="reset", used_at=None).update({"used_at": utcnow()})
            raw = secrets.token_urlsafe(48)
            db.session.add(AuthToken(token_hash=_hash(raw), user_id=user.id, purpose="reset", expires_at=utcnow() + timedelta(minutes=30)))
            _event(email, "reset_requested", user)
            db.session.commit()
            _send_reset_email(user, raw)
        return render_template("auth/forgot_password.html", sent=True)
    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    row = AuthToken.query.filter_by(token_hash=_hash(token), purpose="reset", used_at=None).first()
    if not row or row.expires_at <= utcnow():
        return render_template("auth/reset_password.html", error="This reset link is invalid or expired."), 400
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/reset_password.html", error="Your form expired. Please try again."), 400
        password = request.form.get("password") or ""
        if not _password_ok(password):
            return render_template("auth/reset_password.html", error="Use a password with at least 6 characters."), 400
        user = db.session.get(User, row.user_id)
        user.password_hash, row.used_at = ph.hash(password), utcnow()
        AuthSession.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": utcnow()})
        _event(user.email, "password_reset", user)
        db.session.commit()
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html")


# ---------------------------------------------------------------------------
# Routes — Logout
# ---------------------------------------------------------------------------

@bp.route("/logout", methods=["POST"])
def logout():
    if not _valid_csrf():
        return _json_error("invalid CSRF token", 400)
    if g.auth_session:
        g.auth_session.revoked_at = utcnow()
        _event(g.user.email, "logout", g.user)
        db.session.commit()
    return _clear_login(redirect(url_for("auth.login")))


# ---------------------------------------------------------------------------
# Routes — Account / Security
# ---------------------------------------------------------------------------

@bp.route("/profile")
def profile():
    return render_template("auth/account.html", section="profile", sessions=[])


@bp.route("/security")
def security():
    return render_template("auth/account.html", section="security", sessions=[])


@bp.route("/active-sessions")
def active_sessions():
    sessions = AuthSession.query.filter_by(user_id=g.user.id, revoked_at=None).order_by(AuthSession.last_seen_at.desc()).all()
    return render_template("auth/account.html", section="sessions", sessions=sessions)


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not g.user.password_hash:
        return render_template("auth/account.html", section="security", sessions=[], error="Password changes are unavailable for this SSO-only account."), 400
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/account.html", section="security", sessions=[], error="Your form expired. Please try again."), 400
        current, new = request.form.get("current_password") or "", request.form.get("password") or ""
        try:
            valid = ph.verify(g.user.password_hash, current)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        if not valid or not _password_ok(new):
            return render_template("auth/account.html", section="security", sessions=[], error="Use your current password and a password between 6 and 12 characters with upper-, lower-case letters and a number."), 400
        g.user.password_hash = ph.hash(new)
        AuthSession.query.filter_by(user_id=g.user.id, revoked_at=None).update({"revoked_at": utcnow()})
        _event(g.user.email, "password_changed", g.user)
        db.session.commit()
        _issue(g.user)
        return redirect(url_for("auth.security"))
    return render_template("auth/account.html", section="security", sessions=[])


# ---------------------------------------------------------------------------
# User Management APIs (Institution-Level)
# ---------------------------------------------------------------------------

@bp.route("/api/users", methods=["GET", "POST"])
def api_users():
    if not g.user:
        return _json_error("authentication required", 401)

    sid = current_school_id()
    if not sid and g.user.role != "super_admin":
        return _json_error("no institution context", 400)

    if request.method == "GET":
        if not user_has_permission(g.user, "users.view"):
            return _json_error("insufficient permission", 403)

        q = User.query
        if g.user.role != "super_admin" or sid:
            q = q.filter_by(school_id=sid)

        search = (request.args.get("q") or "").strip().lower()
        if search:
            like = f"%{search}%"
            q = q.filter(
                db.or_(
                    func.lower(User.name).like(like),
                    func.lower(User.email).like(like),
                    func.lower(User.department).like(like),
                    func.lower(User.role).like(like),
                    func.lower(User.employee_id).like(like),
                )
            )
        users = q.order_by(User.name).all()
        return jsonify([u.to_dict() for u in users])

    # POST - Create user
    if not user_has_permission(g.user, "users.create"):
        return _json_error("insufficient permission", 403)

    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip() or None
    employee_id = (data.get("employee_id") or "").strip() or None
    department = (data.get("department") or "").strip() or None
    role = (data.get("role") or "teacher").strip().lower()
    password = data.get("password") or "School@123"

    if not name or not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return _json_error("valid name and email are required", 400)

    if role not in ROLES or (role == "super_admin" and g.user.role != "super_admin"):
        return _json_error(f"invalid role: {role}", 400)

    existing = User.query.filter_by(email=email).first()
    if existing:
        return _json_error("a user with this email already exists", 409)

    target_school_id = data.get("school_id") or sid or (g.user.school_id if g.user else None)
    if not target_school_id and g.user.role == "super_admin":
        first_sch = School.query.first()
        target_school_id = first_sch.id if first_sch else 1

    user = User(
        school_id=target_school_id,
        email=email,
        name=name,
        phone=phone,
        employee_id=employee_id,
        department=department,
        role=role,
        password_hash=ph.hash(password),
        is_active=True,
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    _event(email, "user_created_by_admin", user)
    db.session.commit()

    return jsonify(user.to_dict()), 201


@bp.route("/api/users/<int:uid>", methods=["GET", "PUT", "DELETE"])
def api_user_detail(uid: int):
    if not g.user:
        return _json_error("authentication required", 401)

    sid = current_school_id()
    user = User.query.get_or_404(uid)

    # Multi-tenant cross-tenant protection
    if not is_tenant_permitted(user.school_id):
        return _json_error("access denied", 403)

    if request.method == "GET":
        if not user_has_permission(g.user, "users.view"):
            return _json_error("insufficient permission", 403)
        return jsonify(user.to_dict())

    if request.method == "DELETE":
        if not user_has_permission(g.user, "users.disable") and not user_has_permission(g.user, "users.delete"):
            return _json_error("insufficient permission", 403)
        if user.id == g.user.id:
            return _json_error("cannot delete or deactivate own account", 400)
        user.is_active = False
        AuthSession.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": utcnow()})
        db.session.commit()
        return jsonify({"ok": True, "message": "User deactivated successfully"})

    # PUT
    if not user_has_permission(g.user, "users.update"):
        return _json_error("insufficient permission", 403)

    data = request.get_json(force=True) or {}
    if "name" in data and str(data["name"]).strip():
        user.name = str(data["name"]).strip()
    if "phone" in data:
        user.phone = str(data["phone"]).strip() or None
    if "employee_id" in data:
        user.employee_id = str(data["employee_id"]).strip() or None
    if "department" in data:
        user.department = str(data["department"]).strip() or None
    if "is_active" in data:
        user.is_active = bool(data["is_active"])
    if "role" in data:
        if not user_has_permission(g.user, "users.roles.update"):
            return _json_error("permission users.roles.update required to change roles", 403)
        new_role = str(data["role"]).strip().lower()
        if new_role in ROLES and (new_role != "super_admin" or g.user.role == "super_admin"):
            user.role = new_role
    if "password" in data and str(data["password"]).strip():
        new_pwd = str(data["password"]).strip()
        if _password_ok(new_pwd):
            user.password_hash = ph.hash(new_pwd)
            AuthSession.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": utcnow()})

    db.session.commit()
    return jsonify(user.to_dict())


@bp.route("/api/settings/school", methods=["GET", "PUT", "POST"])
def api_settings_school():
    if not g.user:
        return _json_error("authentication required", 401)
    school = current_school()
    if not school and g.user.role == "super_admin":
        school = School.query.first()
    if not school:
        return _json_error("no active institution found", 404)

    if request.method == "GET":
        return jsonify(school.to_dict())

    if not user_has_permission(g.user, "settings.update") and g.user.role not in ADMIN_ROLES:
        return _json_error("insufficient permission", 403)

    data = request.get_json(force=True) or {}
    name = (data.get("name") or data.get("school_name") or "").strip()
    if name:
        school.name = name
    if "contact_email" in data:
        school.contact_email = str(data["contact_email"]).strip() or None
    if "contact_phone" in data:
        school.contact_phone = str(data["contact_phone"]).strip() or None
    if "address" in data:
        school.address = str(data["address"]).strip() or None
    if "code" in data:
        school.code = str(data["code"]).strip() or None

    db.session.commit()
    return jsonify({"ok": True, "school": school.to_dict()})


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _oauth_client(provider: str):
    return oauth.create_client(provider)


def apple_client_secret() -> str | None:
    key = current_app.config.get("APPLE_PRIVATE_KEY")
    client_id = current_app.config.get("APPLE_CLIENT_ID")
    team_id = current_app.config.get("APPLE_TEAM_ID")
    key_id = current_app.config.get("APPLE_KEY_ID")
    if not all((key, client_id, team_id, key_id)):
        return None
    key = key.replace("\\n", "\n")
    now = int(utcnow().timestamp())
    return jwt.encode(
        {"alg": "ES256", "kid": key_id},
        {"iss": team_id, "iat": now, "exp": now + 60 * 60 * 24 * 180, "aud": "https://appleid.apple.com", "sub": client_id},
        key,
    ).decode("utf-8")


def _oauth_redirect(provider: str, callback_endpoint: str):
    client = _oauth_client(provider)
    if client is None:
        return render_template("auth/login.html", error=f"{provider.title()} sign-in is not configured for this environment."), 503
    nonce = secrets.token_urlsafe(24)
    session[f"oauth_nonce_{provider}"] = nonce
    return client.authorize_redirect(url_for(callback_endpoint, _external=True), nonce=nonce)


def _oauth_callback(provider: str, callback_endpoint: str):
    client = _oauth_client(provider)
    nonce = session.pop(f"oauth_nonce_{provider}", None)
    if client is None or not nonce:
        return render_template("auth/login.html", error="The sign-in request has expired. Please try again."), 400
    try:
        token = client.authorize_access_token()
        claims = client.parse_id_token(token, nonce=nonce)
        subject = claims["sub"]
    except Exception:
        return render_template("auth/login.html", error=f"{provider.title()} sign-in could not be verified."), 401
    
    identity = OAuthIdentity.query.filter_by(provider=provider, subject=subject).first()
    user = identity and db.session.get(User, identity.user_id)
    if not user:
        email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
        verified = claims.get("email_verified")
        if isinstance(verified, str):
            verified = verified.strip().lower() == "true"
        if provider == "microsoft" and email:
            verified = True
        if not email or not verified:
            return render_template("auth/login.html", error=f"{provider.title()} did not provide a verified email address."), 401
        
        user = User.query.filter_by(email=email).first()
        if not user:
            # Assign to active school if available
            default_sch = School.query.filter_by(is_active=True).first()
            user = User(
                school_id=default_sch.id if default_sch else None,
                email=email,
                name=claims.get("name") or email.split("@", 1)[0],
                role="viewer",
                avatar_url=claims.get("picture"),
                email_verified_at=utcnow()
            )
            db.session.add(user)
            db.session.flush()
        db.session.add(OAuthIdentity(provider=provider, subject=subject, user_id=user.id))

    if user.role not in ROLES or not user.is_active:
        return render_template("auth/login.html", error="This account is unavailable. Please contact an administrator."), 403
    
    email = user.email
    _event(email, f"oauth_{provider}", user)
    db.session.commit()
    _issue(user)
    return redirect(url_for("page_dashboard"))


@bp.route("/oauth/google")
def oauth_google():
    return _oauth_redirect("google", "auth.oauth_google_callback")


@bp.route("/oauth/google/callback")
def oauth_google_callback():
    return _oauth_callback("google", "auth.oauth_google_callback")


@bp.route("/oauth/apple")
def oauth_apple():
    return _oauth_redirect("apple", "auth.oauth_apple_callback")


@bp.route("/oauth/apple/callback", methods=["GET", "POST"])
def oauth_apple_callback():
    return _oauth_callback("apple", "auth.oauth_apple_callback")


@bp.route("/oauth/microsoft")
def oauth_microsoft():
    return _oauth_redirect("microsoft", "auth.oauth_microsoft_callback")


@bp.route("/oauth/microsoft/callback")
def oauth_microsoft_callback():
    return _oauth_callback("microsoft", "auth.oauth_microsoft_callback")
