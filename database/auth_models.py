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
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True, index=True)
    email = db.Column(db.String(254), nullable=False, index=True)
    password_hash = db.Column(db.String(255))
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    employee_id = db.Column(db.String(50), nullable=True)
    role = db.Column(db.String(32), nullable=False, default="employee")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    department = db.Column(db.String(120))
    avatar_url = db.Column(db.String(500))
    email_verified_at = db.Column(UTCDateTime())
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(UTCDateTime())
    last_login_at = db.Column(UTCDateTime())
    created_at = db.Column(UTCDateTime(), nullable=False, default=utcnow)

    school = db.relationship("School", backref=db.backref("users", lazy=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "school_name": self.school.name if self.school else None,
            "school_identifier": self.school.school_id if self.school else None,
            "email": self.email,
            "name": self.name,
            "phone": self.phone,
            "employee_id": self.employee_id,
            "role": self.role,
            "department": self.department,
            "is_active": self.is_active,
            "avatar_url": self.avatar_url,
            "email_verified": self.email_verified_at is not None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

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
    # New fields for 6-digit verification code
    verification_code_hash = db.Column(db.String(64), nullable=True, index=True)
    verification_code_expires_at = db.Column(UTCDateTime(), nullable=True)
    verification_attempts = db.Column(db.Integer, nullable=False, default=0)
    verification_code_used_at = db.Column(UTCDateTime(), nullable=True)

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