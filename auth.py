"""Authentication, server-side session validation, CSRF protection and RBAC."""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
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

from database.auth_models import AuthSession, AuthToken, LoginEvent, OAuthIdentity, User, utcnow
from database.db import db

logger = logging.getLogger(__name__)

# Configuration constants for verification code
VERIFICATION_CODE_LENGTH = 6

bp = Blueprint("auth", __name__)
oauth = OAuth()
ph = PasswordHasher()

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLES = {"super_admin", "hr_admin", "payroll_manager", "finance_manager", "manager", "employee", "viewer"}
ADMIN_ROLES = {"super_admin", "hr_admin"}

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
    "users.view", "users.create", "users.update", "users.disable", "users.roles.update",
    "employees.view", "employees.create", "employees.update", "employees.delete",
    "leave.view", "leave.create", "leave.approve",
    "payroll.view", "payroll.process", "payroll.finalize",
    "enterprise.view", "enterprise.manage",
    "time_rules.view", "time_rules.update",
    "unknown_faces.view", "unknown_faces.action",
    "chat.view",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": _ALL_PERMISSIONS.copy(),
    "hr_admin": {
        "dashboard.view", "dashboard.manage",
        "students.view", "students.create", "students.update", "students.delete",
        "teachers.view", "teachers.create", "teachers.update", "teachers.delete",
        "attendance.view", "attendance.mark", "attendance.update", "attendance.delete",
        "cameras.view", "cameras.create", "cameras.update", "cameras.delete", "cameras.start", "cameras.stop",
        "reports.view", "reports.export",
        "employees.view", "employees.create", "employees.update", "employees.delete",
        "leave.view", "leave.create", "leave.approve",
        "payroll.view", "payroll.process", "payroll.finalize",
        "enterprise.view", "enterprise.manage",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "users.view", "users.update", "users.roles.update",
        "chat.view",
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
        "reports.view", "reports.export",
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
        "leave.view", "leave.create", "leave.approve",
        "enterprise.view",
        "time_rules.view", "time_rules.update",
        "unknown_faces.view", "unknown_faces.action",
        "chat.view",
    },
    "employee": {
        "dashboard.view",
        "students.view", "students.create", "students.update",
        "teachers.view",
        "attendance.view", "attendance.mark", "attendance.update",
        "cameras.view", "cameras.start", "cameras.stop",
        "reports.view",
        "enterprise.view",
        "time_rules.view",
        "unknown_faces.view", "unknown_faces.action",
        "chat.view",
    },
    "viewer": {
        "dashboard.view",
        "students.view",
        "teachers.view",
        "attendance.view",
        "cameras.view",
        "reports.view",
        "enterprise.view",
        "time_rules.view",
        "chat.view",
    },
}

# ---------------------------------------------------------------------------
# Endpoint → required permission (method-aware)
# ---------------------------------------------------------------------------
# Keys are endpoint names (as Flask registers them); methods in uppercase.
# "GET" permission is enforced only for page/API endpoints that need it.
# Wild-card prefix matches: "api.students" matches "api.students" and
# "api.students_class" etc.  Specific endpoint names win over prefixes.
_ENDPOINT_PERMS: dict[str, dict[str, str | None]] = {
    # --- Page routes ---
    "page_dashboard": {"GET": "dashboard.view"},
    "page_attendance": {"GET": "attendance.view"},
    "page_students": {"GET": "students.view"},
    "page_students_class": {"GET": "students.view"},
    "page_teachers": {"GET": "teachers.view"},
    "page_cameras": {"GET": "cameras.view"},
    "page_reports": {"GET": "reports.view"},
    "page_settings": {"GET": "settings.view"},
    "chat_page": {"GET": "chat.view"},
    "page_hr_dashboard": {"GET": "enterprise.view"},
    "page_hr_employees": {"GET": "employees.view"},
    "page_hr_employee_profile": {"GET": "employees.view"},
    "page_hr_payroll": {"GET": "payroll.view"},
    "page_hr_reports": {"GET": "reports.view"},
    "page_hr_settings": {"GET": "settings.update"},
    "page_time_rules": {"GET": "time_rules.view"},
    "page_dashboard_enterprise": {"GET": "enterprise.view"},
    "page_reports_enterprise": {"GET": "reports.view"},
    "page_attendance_extended": {"GET": "attendance.view"},
    # --- API routes ---
    "api_meta": {"GET": None},
    "api_stats": {"GET": "dashboard.view"},
    "api_students": {"GET": "students.view", "POST": "students.create"},
    "api_students_sid": {"GET": "students.view", "PUT": "students.update", "DELETE": "students.delete"},
    "api_student_photo": {"POST": "students.update"},
    "api_teachers": {"GET": "teachers.view", "POST": "teachers.create"},
    "api_teacher_sid": {"PUT": "teachers.update", "DELETE": "teachers.delete"},
    "api_teacher_photo": {"POST": "teachers.update"},
    "api_teacher_profile": {"GET": "teachers.view"},
    "api_teacher_attendance": {"GET": "attendance.view"},
    "api_attendance": {"GET": "attendance.view", "POST": "attendance.mark"},
    "api_attendance_sid": {"PUT": "attendance.update", "DELETE": "attendance.delete"},
    "api_attendance_today": {"GET": "attendance.view"},
    "api_attendance_export": {"GET": "reports.export"},
    "api_cameras": {"GET": "cameras.view", "POST": "cameras.create"},
    "api_camera_sid": {"PUT": "cameras.update", "DELETE": "cameras.delete"},
    "api_camera_start": {"POST": "cameras.start"},
    "api_camera_stop": {"POST": "cameras.stop"},
    "api_camera_test": {"POST": "cameras.start"},
    "api_cameras_status": {"GET": "cameras.view"},
    "api_reports_export": {"GET": "reports.export"},
    "api_reports_preview": {"GET": "reports.view"},
    "api_unknown_faces": {"GET": "unknown_faces.view"},
    "api_unknown_face_action": {"POST": "unknown_faces.action", "DELETE": "unknown_faces.action"},
    "api_ask": {"POST": "chat.view"},
    # --- Enterprise / HR API (blueprint prefixes) ---
    "hr.departments": {"GET": "employees.view", "POST": "employees.create"},
    "hr.department_id": {"GET": "employees.view", "PUT": "employees.update"},
    "hr.designations": {"GET": "employees.view", "POST": "employees.create"},
    "hr.designation_id": {"GET": "employees.view", "PUT": "employees.update"},
    "hr.salary_components": {"GET": "payroll.view", "POST": "payroll.view"},
    "hr.salary_component_id": {"GET": "payroll.view", "PUT": "payroll.view"},
    "hr.sync_teachers": {"POST": "employees.create"},
    "hr.employees": {"GET": "employees.view", "POST": "employees.create"},
    "hr.employee_id": {"GET": "employees.view", "PUT": "employees.update", "DELETE": "employees.delete"},
    "hr.employee_by_teacher": {"GET": "employees.view"},
    "hr.employee_salary": {"GET": "employees.view", "POST": "employees.update"},
    "hr.employee_leaves": {"POST": "leave.create"},
    "hr.employee_overtime": {"POST": "employees.update"},
    "hr.employee_bonuses": {"POST": "employees.update"},
    "hr.employee_loans": {"GET": "employees.view", "POST": "employees.update"},
    "hr.loan_repayments": {"POST": "employees.update"},
    "hr.employee_reimbursements": {"POST": "employees.update"},
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
    "hr.payroll_period_id": {"GET": "payroll.view"},
    "hr.payroll_process": {"POST": "payroll.process"},
    "hr.payroll_finalize": {"POST": "payroll.finalize"},
    "hr.payroll_records": {"GET": "payroll.view"},
    "hr.payroll_record_id": {"GET": "payroll.view"},
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
    # Exact match first
    rule = _ENDPOINT_PERMS.get(endpoint)
    if rule:
        return rule.get(method.upper())
    # HR blueprint endpoints use "hr.<name>" pattern
    if "." in endpoint:
        rule = _ENDPOINT_PERMS.get(endpoint)
        if rule:
            return rule.get(method.upper())
    return None


def user_has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, set())


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
    supplied = token or request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
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


def current_user() -> User | None:
    return getattr(g, "user", None)


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
    cutoff = utcnow() - timedelta(minutes=15)
    failures = LoginEvent.query.filter(
        LoginEvent.event == "login_denied", LoginEvent.created_at >= cutoff,
        (LoginEvent.email == email) | (LoginEvent.ip_address == request.remote_addr),
    ).count()
    return failures >= current_app.config["AUTH_MAX_LOGIN_ATTEMPTS"]


# ---------------------------------------------------------------------------
# Global before-request: authentication + authorization
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
    }
    if endpoint in public or request.path.startswith("/static/"):
        return None
    if not g.user:
        return _json_error("authentication required", 401) if request.path.startswith("/api/") else redirect(url_for("auth.login", next=request.full_path))
    
    # Check email verification - unverified users cannot access protected pages
    if not g.user.email_verified_at:
        # Log the blocked access attempt
        _event(g.user.email, "unverified_access_blocked", g.user)
        db.session.commit()
        # Clear the session since unverified users shouldn't have authenticated access
        _clear_login()
        return _json_error("email verification required", 403) if request.path.startswith("/api/") else redirect(url_for("auth.verify_code", email=g.user.email))

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not _valid_csrf():
            return _json_error("invalid CSRF token", 400)
        perm = _required_permission(endpoint, request.method)
        if perm and not user_has_permission(g.user, perm):
            return _json_error("insufficient permission", 403)


@bp.app_context_processor
def auth_context():
    return {"current_user": current_user(), "csrf_token": csrf_token}


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

def _password_ok(password: str) -> bool:
    return len(password) >= 12 and all(re.search(pattern, password) for pattern in (r"[a-z]", r"[A-Z]", r"\d"))


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
    # Invalidate previous unused tokens
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
            # Render both text and HTML versions
            text_body = render_template(
                "auth/verify_email.txt",
                name=user.name,
                url=verify_url,
                code=code,
                code_expiry_minutes=code_expiry_minutes,
            )
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
            
            with SMTP(current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"]) as s:
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
    """Create a single-use verification token and 6-digit code, store both, send email. Returns (raw_token, code)."""
    # Invalidate previous unused tokens
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
    """
    Verify a 6-digit code for a user.
    Returns (success, message).
    """
    # Find the latest unused verification token for this user
    token = AuthToken.query.filter_by(
        user_id=user.id, purpose="verify", used_at=None
    ).order_by(AuthToken.id.desc()).first()
    
    if not token:
        return False, "No verification pending. Please request a new code."
    
    if user.email_verified_at:
        return True, "Email already verified."
    
    # Check if code has expired
    if token.verification_code_expires_at and token.verification_code_expires_at <= utcnow():
        return False, "Verification code has expired. Please request a new code."
    
    # Check max attempts
    max_attempts = current_app.config["AUTH_MAX_VERIFICATION_ATTEMPTS"]
    if token.verification_attempts >= max_attempts:
        return False, "Too many failed attempts. Please request a new code."
    
    # Verify code
    if not token.verification_code_hash or not secrets.compare_digest(token.verification_code_hash, _hash(code)):
        token.verification_attempts += 1
        db.session.commit()
        remaining = max_attempts - token.verification_attempts
        if remaining > 0:
            return False, f"Invalid verification code. {remaining} attempts remaining."
        else:
            return False, "Too many failed attempts. Please request a new code."
    
    # Code is valid - mark as used
    token.verification_code_used_at = utcnow()
    token.used_at = utcnow()  # Also invalidate the token
    user.email_verified_at = utcnow()
    db.session.commit()
    
    return True, "Email verified successfully."


def _resend_verification(user: User) -> tuple[bool, str]:
    """
    Resend verification email with new token and code.
    Returns (success, message).
    """
    # Check cooldown
    cooldown = current_app.config["AUTH_VERIFICATION_RESEND_COOLDOWN_SECONDS"]
    recent_resend = LoginEvent.query.filter(
        LoginEvent.event == "verification_resent",
        LoginEvent.user_id == user.id,
        LoginEvent.created_at >= utcnow() - timedelta(seconds=cooldown),
    ).first()
    
    if recent_resend:
        return False, f"Please wait {cooldown} seconds before requesting a new code."
    
    # Create new token and code (invalidates old ones)
    _create_verification_token_and_code(user)
    
    # Log the resend event
    _event(user.email, "verification_resent", user)
    db.session.commit()
    
    return True, "Verification email sent."


# ---------------------------------------------------------------------------
# Routes — Email verification (Link)
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
    # Also invalidate the code
    row.verification_code_used_at = utcnow()
    user.email_verified_at = utcnow()
    db.session.commit()
    return render_template("auth/login.html", verified="Your email has been verified. You may now sign in.")


# ---------------------------------------------------------------------------
# Routes — Email verification (Code)
# ---------------------------------------------------------------------------

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
            # Don't reveal if user exists
            return render_template("auth/verify_code.html", error="Invalid verification code."), 400
        
        if user.email_verified_at:
            return render_template("auth/login.html", verified="Email already verified. You may now sign in.")
        
        success, message = _verify_code(user, code)
        if success:
            return render_template("auth/login.html", verified=message)
        else:
            return render_template("auth/verify_code.html", error=message, email=email), 400
    
    # GET request - show the code entry page
    email = request.args.get("email", "")
    return render_template("auth/verify_code.html", email=email)


# ---------------------------------------------------------------------------
# Routes — Resend verification
# ---------------------------------------------------------------------------

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
        # Always return the same message to avoid leaking account existence
        return render_template("auth/login.html", verified="If an account exists with that email, a new verification email has been sent.")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Routes — Login
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("page_dashboard"))
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/login.html", error="Your form expired. Please try again."), 400
        email = (request.form.get("email") or "").strip().lower()
        if _too_many_failures(email):
            _event(email, "login_denied")
            db.session.commit()
            return render_template("auth/login.html", error="Too many attempts. Please try again later."), 429
        user = User.query.filter_by(email=email).first()
        if not user or not user.password_hash:
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template("auth/login.html", error="Invalid email or password."), 401
        if not user.is_active:
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template("auth/login.html", error="This account is deactivated. Please contact an administrator."), 403
        if (user.locked_until and user.locked_until > utcnow()):
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template("auth/login.html", error="Too many attempts. Please try again later."), 429
        if not user.email_verified_at:
            _event(email, "login_denied", user)
            # Create/send verification token and code if not already sent
            _create_verification_token_and_code(user)
            db.session.commit()
            return redirect(url_for("auth.verify_code", email=email))
        try:
            valid = ph.verify(user.password_hash, request.form.get("password") or "")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        if not valid:
            user.failed_attempts += 1
            if user.failed_attempts >= current_app.config["AUTH_MAX_LOGIN_ATTEMPTS"]:
                user.locked_until = utcnow() + timedelta(minutes=15)
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template("auth/login.html", error="Invalid email or password."), 401
        if ph.check_needs_rehash(user.password_hash):
            user.password_hash = ph.hash(request.form.get("password") or "")
        user.failed_attempts, user.locked_until = 0, None
        _event(email, "login", user)
        _issue(user, bool(request.form.get("remember")))
        return redirect(_safe_next(request.args.get("next")) or url_for("page_dashboard"))
    return render_template("auth/login.html")


# ---------------------------------------------------------------------------
# Routes — Signup
# ---------------------------------------------------------------------------

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/signup.html", error="Your form expired. Please try again."), 400
        email, name = (request.form.get("email") or "").strip().lower(), (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not _password_ok(password):
            return render_template("auth/signup.html", error="Use a valid email and a 12+ character password with upper-, lower-case letters and a number."), 400
        if User.query.filter_by(email=email).first():
            return render_template("auth/signup.html", error="An account already exists for this email."), 409
        user = User(email=email, name=name, password_hash=ph.hash(password), role="super_admin" if User.query.count() == 0 else "employee")
        db.session.add(user)
        db.session.flush()
        _event(email, "signup", user)
        db.session.commit()
        # Send verification email with both link and code — do NOT issue session
        _create_verification_token_and_code(user)
        return render_template("auth/verify_required.html", email=email)
    return render_template("auth/signup.html")


# ---------------------------------------------------------------------------
# Routes — Email verification
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Forgot / Reset password
# ---------------------------------------------------------------------------

@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if not _valid_csrf():
            return render_template("auth/forgot_password.html", error="Your form expired. Please try again."), 400
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            # Invalidate earlier reset links; only a one-time hashed token is retained.
            AuthToken.query.filter_by(user_id=user.id, purpose="reset", used_at=None).update({"used_at": utcnow()})
            raw = secrets.token_urlsafe(48)
            db.session.add(AuthToken(token_hash=_hash(raw), user_id=user.id, purpose="reset", expires_at=utcnow() + timedelta(minutes=30)))
            _event(email, "reset_requested", user)
            db.session.commit()
            # Delivery is delegated to the configured transactional email provider; never expose a token in HTTP/logs.
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
            return render_template("auth/reset_password.html", error="Use a 12+ character password with upper-, lower-case letters and a number."), 400
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
            return render_template("auth/account.html", section="security", sessions=[], error="Use your current password and a 12+ character password with upper-, lower-case letters and a number."), 400
        g.user.password_hash = ph.hash(new)
        AuthSession.query.filter_by(user_id=g.user.id, revoked_at=None).update({"revoked_at": utcnow()})
        _event(g.user.email, "password_changed", g.user)
        db.session.commit()
        _issue(g.user)
        return redirect(url_for("auth.security"))
    return render_template("auth/account.html", section="security", sessions=[])


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _oauth_client(provider: str):
    return oauth.create_client(provider)


def apple_client_secret() -> str | None:
    """Create Apple's short-lived ES256 client secret only from configured keys."""
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
            user = User(email=email, name=claims.get("name") or email.split("@", 1)[0], role="employee", avatar_url=claims.get("picture"), email_verified_at=utcnow())
            db.session.add(user)
            db.session.flush()
        db.session.add(OAuthIdentity(provider=provider, subject=subject, user_id=user.id))
    if user.role not in ROLES or not user.is_active or (user.locked_until and user.locked_until > utcnow()):
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
