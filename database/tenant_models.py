"""Tenant entity model for Multi-Tenant School ERP."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.sql import func
from .db import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class School(db.Model):
    """A School / Educational Institution tenant entity.
    
    Each institution has a unique public identifier (``school_id``) and
    owns its isolated dataset of users, students, teachers, cameras,
    attendance, HR, payroll, and configuration records.
    """

    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(80), nullable=True)
    contact_email = db.Column(db.String(120), nullable=True)
    contact_phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.Text, nullable=True)
    logo_url = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "name": self.name,
            "code": self.code,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "address": self.address,
            "logo_url": self.logo_url,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
