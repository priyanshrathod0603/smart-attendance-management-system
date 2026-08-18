"""Isolated identity, session and audit tables; no business-domain relationships."""
from datetime import datetime, timezone
from sqlalchemy.types import TypeDecorator, DateTime
from .db import db

def utcnow() -> datetime:
    """The sole clock used by the authentication subsystem."""
    return datetime.now(timezone.utc)

def as_utc(value: datetime | None) -> datetime | None:
    """Return an aware UTC instant, including for legacy SQLite rows."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

class UTCDateTime(TypeDecorator):
    """Store UTC portably and always materialise aware UTC datetimes."""
    impl = DateTime(timezone=True)
    cache_ok = True
    def process_bind_param(self, value, dialect):
        value = as_utc(value)
        return value.replace(tzinfo=None) if value is not None and dialect.name == "sqlite" else value
    def process_result_value(self, value, dialect):
        return as_utc(value)

class User(db.Model):
    __tablename__ = "auth_users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="employee")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    department = db.Column(db.String(120))
    avatar_url = db.Column(db.String(500))
    email_verified_at = db.Column(UTCDateTime())
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(UTCDateTime())
    last_login_at = db.Column(UTCDateTime())
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)

class AuthSession(db.Model):
    __tablename__ = "auth_sessions"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("auth_users.id"), nullable=False, index=True)
    user = db.relationship("User", backref=db.backref("sessions", lazy=True, cascade="all, delete-orphan"))
    device = db.Column(db.String(255))
    ip_address = db.Column(db.String(64))
    csrf_secret = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(UTCDateTime(), nullable=False)
    last_seen_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)
    revoked_at = db.Column(UTCDateTime())
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)

class AuthToken(db.Model):
    __tablename__ = "auth_tokens"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("auth_users.id"), nullable=False, index=True)
    purpose = db.Column(db.String(32), nullable=False, index=True)
    expires_at = db.Column(UTCDateTime(), nullable=False)
    used_at = db.Column(UTCDateTime())

class OAuthIdentity(db.Model):
    __tablename__ = "auth_oauth_identities"
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("auth_users.id"), nullable=False, index=True)
    __table_args__ = (db.UniqueConstraint("provider", "subject", name="uq_auth_provider_subject"),)

class LoginEvent(db.Model):
    __tablename__ = "auth_login_events"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("auth_users.id"), index=True)
    email = db.Column(db.String(254), nullable=False)
    event = db.Column(db.String(32), nullable=False)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(500))
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)
