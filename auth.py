"""Authentication, server-side session validation, CSRF protection and RBAC."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urljoin, urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from authlib.integrations.flask_client import OAuth
from authlib.jose import jwt
from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for

from database.auth_models import AuthSession, AuthToken, LoginEvent, OAuthIdentity, User, utcnow
from database.db import db

bp = Blueprint("auth", __name__)
oauth = OAuth()
ph = PasswordHasher()

ROLES = {"super_admin", "hr_admin", "payroll_manager", "finance_manager", "manager", "employee", "viewer"}
WRITE_ROLES = {"super_admin", "hr_admin", "payroll_manager", "finance_manager", "manager"}
ADMIN_ROLES = {"super_admin", "hr_admin"}


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
    hours = current_app.config["AUTH_REMEMBER_DAYS"] * 24 if remember else current_app.config["AUTH_SESSION_HOURS"]
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


@bp.before_app_request
def load_and_protect():
    g.user = g.auth_session = None
    raw = session.get("sid")
    if raw:
        row = AuthSession.query.filter_by(token_hash=_hash(raw), revoked_at=None).first()
        if row and row.expires_at > utcnow() and row.user.role in ROLES:
            g.auth_session, g.user = row, row.user
            row.last_seen_at = utcnow()
            db.session.commit()
        else:
            _clear_login()

    endpoint = request.endpoint or ""
    public = {
        "auth.login", "auth.signup", "auth.forgot_password", "auth.reset_password",
        "auth.oauth_google", "auth.oauth_google_callback", "auth.oauth_microsoft", "auth.oauth_microsoft_callback", "auth.oauth_apple", "auth.oauth_apple_callback",
        "static",
    }
    if endpoint in public or request.path.startswith("/static/"):
        return None
    if not g.user:
        return _json_error("authentication required", 401) if request.path.startswith("/api/") else redirect(url_for("auth.login", next=request.full_path))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not _valid_csrf():
            return _json_error("invalid CSRF token", 400)
        if endpoint not in {"auth.logout", "auth.change_password"} and g.user.role not in WRITE_ROLES:
            return _json_error("insufficient permission", 403)


@bp.app_context_processor
def auth_context():
    return {"current_user": current_user(), "csrf_token": csrf_token}


def _password_ok(password: str) -> bool:
    return len(password) >= 12 and all(re.search(pattern, password) for pattern in (r"[a-z]", r"[A-Z]", r"\d"))


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
        if not user or not user.password_hash or (user.locked_until and user.locked_until > utcnow()):
            _event(email, "login_denied", user)
            db.session.commit()
            return render_template("auth/login.html", error="Invalid email or password."), 401
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
        _issue(user)
        return redirect(url_for("page_dashboard"))
    return render_template("auth/signup.html")


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


@bp.route("/logout", methods=["POST"])
def logout():
    if not _valid_csrf():
        return _json_error("invalid CSRF token", 400)
    if g.auth_session:
        g.auth_session.revoked_at = utcnow()
        _event(g.user.email, "logout", g.user)
        db.session.commit()
    return _clear_login(redirect(url_for("auth.login")))


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
        # Apple only returns the email claim on the first consent.  Existing
        # identities must therefore be resolved by provider subject before we
        # require an email address.  New account linking still requires an
        # explicitly verified email claim.
        email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
        verified = claims.get("email_verified")
        if isinstance(verified, str):
            verified = verified.strip().lower() == "true"
        # A Microsoft-issued ID token is itself the verified identity proof;
        # Microsoft commonly exposes preferred_username but no email_verified.
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
    if user.role not in ROLES or (user.locked_until and user.locked_until > utcnow()):
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
