"""Application configuration loaded from environment variables.

This module centralises every runtime setting so the rest of the codebase
never has to call ``os.getenv`` directly. Values come from ``.env`` (loaded
via python-dotenv) and fall back to sane development defaults.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

# Load .env from the project root (if present) before we read anything.
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _as_bool(value: str | None, default: bool = False) -> bool:
    """Parse common truthy strings into a proper bool."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_uri() -> str:
    """Build the SQLAlchemy URI.

    AttendanceOS / Krishna Payroll uses SQLite exclusively.
    The active database is: instance/krishna_erp.db

    The DATABASE_URL environment variable must be set in .env as:
        DATABASE_URL=sqlite:///krishna_erp.db

    Flask resolves this relative path to the instance/ folder automatically,
    giving the full path: <project>/instance/krishna_erp.db

    The fallback below also points explicitly to instance/ so the correct
    database is used even when .env is absent (e.g. bare ``python app.py``).
    """
    raw = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'krishna_erp.db')}",
    )
    # Safety rewrite for postgresql:// schemes (not used in production but
    # kept so the URI is valid if ever passed inadvertently).
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw[len("postgresql://"):]
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw[len("postgres://"):]
    return raw


class Config:
    """Flask + SQLAlchemy configuration."""

    # --- Flask ---
    DEBUG = _as_bool(os.getenv("FLASK_DEBUG"), True)
    SECRET_KEY: str = os.getenv("FLASK_SECRET", "dev-secret-change-me")
    SESSION_COOKIE_NAME = "krishna_payroll_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _as_bool(os.getenv("SESSION_COOKIE_SECURE"), not DEBUG)
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.getenv("AUTH_REMEMBER_DAYS", "30")))
    AUTH_SESSION_HOURS = int(os.getenv("AUTH_SESSION_HOURS", "12"))
    AUTH_REMEMBER_DAYS = int(os.getenv("AUTH_REMEMBER_DAYS", "30"))
    AUTH_MAX_LOGIN_ATTEMPTS = int(os.getenv("AUTH_MAX_LOGIN_ATTEMPTS", "5"))
    AUTH_VERIFICATION_HOURS = int(os.getenv("AUTH_VERIFICATION_HOURS", "24"))
    AUTH_VERIFICATION_CODE_TTL_MINUTES = int(os.getenv("AUTH_VERIFICATION_CODE_TTL_MINUTES", "10"))
    AUTH_MAX_VERIFICATION_ATTEMPTS = int(os.getenv("AUTH_MAX_VERIFICATION_ATTEMPTS", "5"))
    AUTH_VERIFICATION_RESEND_COOLDOWN_SECONDS = int(os.getenv("AUTH_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60"))
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.getenv("AUTH_SESSION_HOURS", "12")) + 1)
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM = os.getenv("SMTP_FROM")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID")
    MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET")
    MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "common")
    APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID")
    APPLE_TEAM_ID = os.getenv("APPLE_TEAM_ID")
    APPLE_KEY_ID = os.getenv("APPLE_KEY_ID")
    APPLE_PRIVATE_KEY = os.getenv("APPLE_PRIVATE_KEY")

    # --- Database ---
    # Single database: instance/krishna_erp.db (SQLite)
    # Configured via DATABASE_URL=sqlite:///krishna_erp.db in .env
    SQLALCHEMY_DATABASE_URI: str = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {"pool_pre_ping": True, "pool_recycle": 1800}

    # --- Camera ---
    CAMERA_SOURCE: str = os.getenv("CAMERA_SOURCE", "0")
    ATTENDANCE_COOLDOWN_SECONDS: int = int(
        os.getenv("ATTENDANCE_COOLDOWN_SECONDS", "300")
    )
    BIOMAX_DEVICE_TIMEZONE: str = os.getenv("BIOMAX_DEVICE_TIMEZONE", "UTC")
    BIOMAX_WEBHOOK_MAX_REQUESTS: int = int(os.getenv("BIOMAX_WEBHOOK_MAX_REQUESTS", "120"))
    BIOMAX_WEBHOOK_RATE_WINDOW_SECONDS: int = int(
        os.getenv("BIOMAX_WEBHOOK_RATE_WINDOW_SECONDS", "60")
    )

    # --- Face matching (InsightFace uses cosine similarity, 0..1) ---
    FACE_MATCH_THRESHOLD: float = float(os.getenv("FACE_MATCH_THRESHOLD", "0.35"))

    # --- Optional local SLM (Ollama) ---
    USE_OLLAMA: bool = _as_bool(os.getenv("USE_OLLAMA"), False)
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "phi3:mini")
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

    KNOWN_FACES_DIR: str = os.path.join(BASE_DIR, "static", "known_faces")
