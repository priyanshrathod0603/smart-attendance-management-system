"""Biometric device integration package for AttendanceOS.

Provides clean device adapters and unified biometric services for fingerprint
and other hardware identification methods without storing biometric templates
in the application database.
"""
from .fingerprint_adapter import (
    FingerprintDeviceAdapter,
    BioMaxIndigoAdapter,
    TeamOfficeZ305Adapter,
    MockFingerprintAdapter,
    get_fingerprint_adapter,
)
from .fingerprint_service import (
    verify_and_record_attendance,
    enroll_teacher_fingerprint,
    delete_teacher_fingerprint,
    get_fingerprint_device_status,
    get_fingerprint_audit_logs,
)

__all__ = [
    "FingerprintDeviceAdapter",
    "BioMaxIndigoAdapter",
    "TeamOfficeZ305Adapter",
    "MockFingerprintAdapter",
    "get_fingerprint_adapter",
    "verify_and_record_attendance",
    "enroll_teacher_fingerprint",
    "delete_teacher_fingerprint",
    "get_fingerprint_device_status",
    "get_fingerprint_audit_logs",
]
