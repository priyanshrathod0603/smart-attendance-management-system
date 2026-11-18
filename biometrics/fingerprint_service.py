"""High-level service coordinating normalized fingerprint events and AttendanceOS.

Key principles:
1. An explicit Device User ID mapping is the identity bridge to an AttendanceOS teacher.
2. No biometric template is processed by AttendanceOS.
3. Every verified event flows into the SAME common attendance engine (Time Rules,
   IN/OUT logic, duplicate prevention, and logs).
4. Device testing and diagnostic verification workflows NEVER alter attendance records.
5. All administrative actions (configuration, connection tests, enrollment, unenrollment)
   are audit-logged safely without recording biometric data.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("biometrics.fingerprint_service")

from database.db import db
from database.enterprise_models import FingerprintDevice, FingerprintDeviceAudit, AttendanceEvent
from database.models import Teacher
from database.tenant_models import School
from attendance_service import record_recognition

from .fingerprint_adapter import (
    BioMaxIndigoAdapter,
    FingerprintDeviceAdapter,
    get_fingerprint_adapter,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _find_teacher(teacher_id_or_pk: str | int, school_id: Optional[int | str] = None) -> Optional[Teacher]:
    """Resolve PKs without treating a digit-only teacher code as an integer."""
    teacher = db.session.get(Teacher, teacher_id_or_pk) if isinstance(teacher_id_or_pk, int) else None
    if teacher is None:
        raw_id = str(teacher_id_or_pk).strip()
        if not raw_id:
            return None
        teacher = Teacher.query.filter_by(teacher_id=raw_id).first()
        if teacher is None and raw_id.isdigit():
            teacher = db.session.get(Teacher, int(raw_id))
    if teacher is not None and school_id is not None:
        try:
            if teacher.school_id != int(school_id):
                return None
        except (TypeError, ValueError):
            return None
    return teacher


def log_device_audit(
    event_type: str,
    result: str,
    device_id: str = "TEAM_OFFICE_Z305_01",
    teacher_id: Optional[str] = None,
    failure_reason: Optional[str] = None,
    school_id: Optional[int | str] = None,
    event_time: Optional[datetime] = None,
) -> Optional[FingerprintDeviceAudit]:
    """Safely append a lightweight audit log entry for hardware biometric and administrative events."""
    try:
        clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
        if clean_sid is None:
            default_school = School.query.filter_by(school_id="DEFAULT").first()
            clean_sid = default_school.id if default_school else None
        audit = FingerprintDeviceAudit(
            school_id=clean_sid,
            device_id=device_id or "TEAM_OFFICE_Z305_01",
            teacher_id=str(teacher_id).strip() if teacher_id else None,
            event_type=event_type,
            result=result,
            failure_reason=failure_reason[:250] if failure_reason else None,
            event_time=event_time or _utcnow(),
        )
        db.session.add(audit)
        db.session.commit()
        return audit
    except Exception as exc:
        print(f"[BIOMAX_AUDIT_ERROR] Failed to save audit log: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Device Configuration & Lifecycle Management
# ---------------------------------------------------------------------------

def get_or_create_fingerprint_device(school_id: Optional[int | str] = None) -> FingerprintDevice:
    """Fetch existing device config or initialize a sensible default for the school."""
    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    if clean_sid is None:
        default_school = School.query.filter_by(school_id="DEFAULT").first()
        clean_sid = default_school.id if default_school else None
    device = None
    if clean_sid:
        device = FingerprintDevice.query.filter_by(school_id=clean_sid).first()
    if not device:
        device = FingerprintDevice.query.first()

    if not device:
        device = FingerprintDevice(
            school_id=clean_sid,
            device_id="TEAM_OFFICE_Z305_01",
            name="Main Entrance Team Office Z305",
            model="Team Office Z305 (variant/firmware unverified)",
            connection_type="TCP_IP",
            ip_address=None,
            port=None,
            comm_key="0",
            timeout_seconds=5.0,
            status="disconnected",
            is_active=True,
        )
        db.session.add(device)
        db.session.commit()

    return device


def save_fingerprint_device_config(
    data: dict[str, Any],
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Validate and persist fingerprint terminal connection settings."""
    device = get_or_create_fingerprint_device(school_id)

    name = str(data.get("name") or device.name).strip()
    model = str(data.get("model") or device.model).strip()
    conn_type = str(data.get("connection_type") or device.connection_type).strip().upper()
    ip_address = str(data.get("ip_address") or device.ip_address).strip() if data.get("ip_address") is not None else device.ip_address
    
    port_val = data.get("port")
    if port_val is not None:
        if not str(port_val).isdigit() or not 1 <= int(port_val) <= 65535:
            return {"ok": False, "error": "Port must be an integer between 1 and 65535.", "status": 400}
        port = int(port_val)
    else:
        port = device.port

    timeout_val = data.get("timeout_seconds")
    if timeout_val is not None:
        try:
            timeout_sec = float(timeout_val)
        except (ValueError, TypeError):
            timeout_sec = 5.0
    else:
        timeout_sec = device.timeout_seconds or 5.0

    comm_key = data.get("comm_key")
    if comm_key is not None and str(comm_key).strip() and str(comm_key).strip() != "••••••":
        device.comm_key = str(comm_key).strip()

    webhook_token = data.get("webhook_token")
    if webhook_token is not None:
        clean_token = str(webhook_token).strip()
        if clean_token and len(clean_token) < 32:
            return {
                "ok": False,
                "error": "Webhook token must be at least 32 characters.",
                "status": 400,
            }
        if clean_token:
            device.webhook_token_hash = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()

    device_id = str(data.get("device_id") or device.device_id).strip()

    device.name = name
    device.model = model
    device.connection_type = conn_type
    device.ip_address = ip_address
    device.port = port
    device.timeout_seconds = max(1.0, min(60.0, timeout_sec))
    device.device_id = device_id
    if "is_active" in data:
        device.is_active = bool(data["is_active"])

    db.session.commit()

    # Sync adapter instance if it's a BioMax adapter
    adapter = get_fingerprint_adapter()
    if isinstance(adapter, BioMaxIndigoAdapter):
        adapter.reconfigure(
            host=device.ip_address,
            port=device.port,
            device_id=device.device_id,
            comm_key=device.comm_key,
            protocol=device.connection_type,
            timeout=device.timeout_seconds,
        )

    log_device_audit(
        event_type="config_change",
        result="success",
        device_id=device.device_id,
        failure_reason=f"Config updated by {actor_email or 'admin'}",
        school_id=school_id,
    )

    return {
        "ok": True,
        "message": "Fingerprint device configuration updated successfully.",
        "device": device.to_dict(),
    }


def connect_fingerprint_device(
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Check TCP reachability to the configured fingerprint terminal."""
    device = get_or_create_fingerprint_device(school_id)
    adapter = get_fingerprint_adapter()

    if isinstance(adapter, BioMaxIndigoAdapter):
        adapter.reconfigure(
            host=device.ip_address,
            port=device.port,
            device_id=device.device_id,
            comm_key=device.comm_key,
            protocol=device.connection_type,
            timeout=device.timeout_seconds,
        )

    device.status = "connecting"
    db.session.commit()

    success = adapter.connect()
    device_info = adapter.get_device_info()

    if success:
        real_adapter = isinstance(adapter, BioMaxIndigoAdapter)
        operational = device_info.get("operational")
        if real_adapter and operational is False:
            device.status = "network_reachable"
            device.last_error = "Socket reachable; protocol/device verification pending."
        else:
            device.status = "connected"
            device.last_error = None
        device.last_connected_at = _utcnow()
        db.session.commit()

        log_device_audit(
            event_type="connect",
            result="network_reachable" if real_adapter and operational is False else "success",
            device_id=device.device_id,
            failure_reason=f"Connected by {actor_email or 'admin'} (network_reachable={bool(device_info.get('network_reachable'))}, protocol_verified={bool(device_info.get('protocol_verified'))})",
            school_id=school_id,
        )
        return {
            "ok": True,
            "status": device.status,
            "network_connected": bool(device_info.get("network_connected", success)),
            "protocol_verified": bool(device_info.get("protocol_verified", False)),
            "device_authenticated": bool(device_info.get("device_authenticated", False)),
            "enrollment_supported": bool(device_info.get("enrollment_supported", False)),
            "attendance_sync_working": bool(device_info.get("attendance_sync_working", False)),
            "integration_ready": bool(operational),
            "connection_scope": "NETWORK_ONLY" if real_adapter and not operational else "DEVICE_PROTOCOL",
            "message": (
                f"Network socket reachable at {device.ip_address}:{device.port}. "
                "This confirms only TCP reachability; Z305 protocol, authentication, enrollment, and attendance sync remain unverified."
                if real_adapter and not operational
                else f"Connection check completed for {device.name}."
            ),
            "device": device.to_dict(),
            "device_info": device_info,
        }
    else:
        err_msg = device_info.get("last_error") or f"Unable to reach device at {device.ip_address}:{device.port}"
        device.status = "error"
        device.last_error = err_msg
        db.session.commit()

        log_device_audit(
            event_type="connect",
            result="error",
            device_id=device.device_id,
            failure_reason=err_msg,
            school_id=school_id,
        )
        return {
            "ok": False,
            "status": "error",
            "message": f"✕ Unable to connect to device. Reason: {err_msg}",
            "device": device.to_dict(),
            "device_info": device_info,
        }


def disconnect_fingerprint_device(
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Disconnect active device connection."""
    device = get_or_create_fingerprint_device(school_id)
    adapter = get_fingerprint_adapter()

    adapter.disconnect()
    device.status = "disconnected"
    db.session.commit()

    log_device_audit(
        event_type="disconnect",
        result="success",
        device_id=device.device_id,
        failure_reason=f"Disconnected by {actor_email or 'admin'}",
        school_id=school_id,
    )

    return {
        "ok": True,
        "status": "disconnected",
        "message": f"Device {device.name} disconnected.",
        "device": device.to_dict(),
    }


def test_fingerprint_device_connection(
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Perform a TCP reachability test and latency measurement."""
    device = get_or_create_fingerprint_device(school_id)
    adapter = get_fingerprint_adapter()

    if isinstance(adapter, BioMaxIndigoAdapter):
        adapter.reconfigure(
            host=device.ip_address,
            port=device.port,
            device_id=device.device_id,
            comm_key=device.comm_key,
            protocol=device.connection_type,
            timeout=device.timeout_seconds,
        )

    test_res = adapter.test_connection()

    device_info = test_res.get("device_info") or {}
    test_res.update({
        "network_connected": bool(device_info.get("network_connected", device_info.get("network_reachable"))),
        "protocol_verified": bool(device_info.get("protocol_verified")),
        "device_authenticated": bool(device_info.get("device_authenticated")),
        "enrollment_supported": bool(device_info.get("enrollment_supported", device_info.get("fingerprint_enrollment_supported"))),
        "attendance_sync_working": bool(device_info.get("attendance_sync_working", device_info.get("event_sync_active"))),
        "integration_ready": bool(device_info.get("integration_ready", device_info.get("operational"))),
        "connection_scope": "NETWORK_ONLY" if device_info.get("network_reachable") and not device_info.get("protocol_verified") else "DEVICE_PROTOCOL",
    })

    if test_res.get("ok"):
        if isinstance(adapter, BioMaxIndigoAdapter) and not bool(test_res.get("device_info", {}).get("protocol_verified", True)):
            device.status = "network_reachable"
            device.last_error = "TCP reachability confirmed; protocol verification pending."
        else:
            device.status = "connected"
            device.last_error = None
        device.last_connected_at = _utcnow()
        db.session.commit()

        log_device_audit(
            event_type="test_connection",
            result=(
                "network_reachable"
                if isinstance(adapter, BioMaxIndigoAdapter)
                and not bool(test_res.get("protocol_verified"))
                else "success"
            ),
            device_id=device.device_id,
            failure_reason=f"Latency: {test_res.get('latency_ms')}ms (protocol_verified={bool(test_res.get('device_info', {}).get('protocol_verified', True))})",
            school_id=school_id,
        )
    else:
        device.status = "error"
        device.last_error = test_res.get("message")
        db.session.commit()

        log_device_audit(
            event_type="test_connection",
            result="failed",
            device_id=device.device_id,
            failure_reason=test_res.get("message"),
            school_id=school_id,
        )

    test_res["device_record"] = device.to_dict()
    return test_res


def get_fingerprint_device_status(school_id: Optional[int | str] = None) -> dict[str, Any]:
    """Return aggregated device status, hardware metadata, and teacher mapping statistics."""
    device = get_or_create_fingerprint_device(school_id)
    adapter = get_fingerprint_adapter()
    if isinstance(adapter, BioMaxIndigoAdapter) and (
        adapter.host != device.ip_address
        or adapter.port != device.port
        or adapter.device_id != device.device_id
    ):
        adapter.reconfigure(
            host=device.ip_address,
            port=device.port,
            device_id=device.device_id,
            comm_key=device.comm_key,
            protocol=device.connection_type,
            timeout=device.timeout_seconds,
        )
    info = adapter.get_device_info()

    # Mapping statistics
    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    t_query = Teacher.query
    if clean_sid:
        t_query = t_query.filter((Teacher.school_id == clean_sid) | (Teacher.school_id.is_(None)))

    all_teachers = t_query.all()
    total_teachers = len(all_teachers)
    fp_teachers = [t for t in all_teachers if (t.attendance_method or "FACE").upper() == "FINGERPRINT"]
    enrolled_count = sum(1 for t in fp_teachers if t.fingerprint_enrolled)
    mapped_count = sum(1 for t in fp_teachers if t.fingerprint_user_id)

    # Sync device DB status with adapter state
    is_conn = adapter.is_connected()
    if isinstance(adapter, BioMaxIndigoAdapter) and info.get("network_reachable"):
        next_status = "operational" if info.get("integration_ready") else "network_reachable"
    elif info.get("is_mock") and is_conn:
        next_status = "simulated"
    elif info.get("operational"):
        next_status = "operational"
    else:
        next_status = "disconnected"
    if device.status != next_status:
        device.status = next_status
        db.session.commit()

    return {
        "ok": True,
        "device": device.to_dict(),
        "adapter_info": info,
        "integration_status": {
            "network_connected": bool(info.get("network_connected", info.get("network_reachable"))),
            "protocol_verified": bool(info.get("protocol_verified")),
            "device_authenticated": bool(info.get("device_authenticated")),
            "enrollment_supported": bool(info.get("enrollment_supported", info.get("fingerprint_enrollment_supported"))),
            "attendance_sync_working": bool(info.get("attendance_sync_working", info.get("event_sync_active"))),
            "integration_ready": bool(info.get("integration_ready", info.get("operational"))),
            "port_role": info.get("port_role", "not confirmed"),
        },
        "statistics": {
            "total_teachers": total_teachers,
            "fingerprint_method_count": len(fp_teachers),
            "mapped_teachers_count": mapped_count,
            "enrolled_teachers_count": enrolled_count,
            "application_enrollment_flags_count": enrolled_count,
        },
    }


# ---------------------------------------------------------------------------
# Teacher Fingerprint User ID Mapping
# ---------------------------------------------------------------------------

def get_teacher_fingerprint_mappings(
    school_id: Optional[int | str] = None,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return all teachers with their fingerprint mapping and enrollment details."""
    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    q = Teacher.query
    if clean_sid:
        q = q.filter((Teacher.school_id == clean_sid) | (Teacher.school_id.is_(None)))

    if search:
        s = f"%{search.strip()}%"
        q = q.filter(
            db.or_(
                Teacher.name.ilike(s),
                Teacher.teacher_id.ilike(s),
                Teacher.fingerprint_user_id.ilike(s),
                Teacher.department.ilike(s),
            )
        )

    teachers = q.order_by(Teacher.name.asc()).all()
    results = []
    for t in teachers:
        d = t.to_dict()
        results.append(d)
    return results


def save_teacher_fingerprint_mapping(
    teacher_id_or_pk: str | int,
    fingerprint_user_id: str,
    attendance_method: str = "FINGERPRINT",
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Set or update the BioMax Device User ID mapping for a teacher.

    Enforces uniqueness:
    - No two teachers may share the same BioMax Device User ID within the school.
    """
    if not isinstance(fingerprint_user_id, str):
        return {"ok": False, "error": "Device User ID must be provided as a string to preserve leading zeros.", "status": 400}
    clean_uid = fingerprint_user_id.strip()
    if not clean_uid:
        return {"ok": False, "error": "Device User ID cannot be blank.", "status": 400}

    teacher = _find_teacher(teacher_id_or_pk, school_id=school_id)

    if not teacher:
        return {"ok": False, "error": "Teacher not found.", "status": 404}

    # Check for duplicate device user ID among OTHER teachers
    same_school = (
        Teacher.school_id.is_(None)
        if teacher.school_id is None
        else Teacher.school_id == teacher.school_id
    )
    conflict_q = Teacher.query.filter(
        Teacher.id != teacher.id,
        Teacher.fingerprint_user_id == clean_uid,
        same_school,
    )

    conflict = conflict_q.first()
    if conflict:
        return {
            "ok": False,
            "error": f"Device User ID '{clean_uid}' is already assigned to {conflict.name} ({conflict.teacher_id}).",
            "status": 409,
        }

    # Apply mapping
    old_uid = teacher.fingerprint_user_id
    teacher.fingerprint_user_id = clean_uid
    teacher.attendance_method = (attendance_method or "FINGERPRINT").upper().strip()
    db.session.commit()

    log_device_audit(
        event_type="mapping_change",
        result="success",
        teacher_id=teacher.teacher_id,
        failure_reason=f"Mapped Device User ID '{old_uid}' -> '{clean_uid}' by {actor_email or 'admin'}",
        school_id=teacher.school_id,
    )

    return {
        "ok": True,
        "message": f"Device User ID '{clean_uid}' successfully mapped to {teacher.name}.",
        "teacher": teacher.to_dict(),
    }


# ---------------------------------------------------------------------------
# Teacher Enrollment & Deletion Workflows
# ---------------------------------------------------------------------------

def enroll_teacher_fingerprint(
    teacher_id_or_pk: str | int,
    school_id: Optional[int | str] = None,
    device_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Trigger real or explicitly simulated fingerprint enrollment for a teacher.

    The biometric data is stored on the physical device; AttendanceOS marks
    the teacher profile as enrolled upon successful hardware registration.
    """
    teacher = _find_teacher(teacher_id_or_pk, school_id=school_id)

    if not teacher:
        return {"ok": False, "error": "Teacher not found", "status": 404}
    if not teacher.is_active:
        return {"ok": False, "error": "Inactive teachers cannot be enrolled.", "status": 409}

    adapter = get_fingerprint_adapter()
    if device_user_id is not None and not isinstance(device_user_id, str):
        return {"ok": False, "error": "Device User ID must be provided as a string to preserve leading zeros.", "status": 400}
    target_device_uid = (
        device_user_id
        if device_user_id is not None
        else teacher.fingerprint_user_id or teacher.teacher_id
    ).strip()
    if not target_device_uid:
        return {"ok": False, "error": "Device User ID cannot be blank.", "status": 400}
    if len(target_device_uid) > 50:
        return {"ok": False, "error": "Device User ID must be 50 characters or fewer.", "status": 400}

    same_school = (
        Teacher.school_id.is_(None)
        if teacher.school_id is None
        else Teacher.school_id == teacher.school_id
    )
    conflict = Teacher.query.filter(
        Teacher.id != teacher.id,
        same_school,
        Teacher.fingerprint_user_id == target_device_uid,
    ).first()
    if conflict:
        return {"ok": False, "error": "Device User ID is already mapped to another teacher.", "status": 409}

    enroll_res = adapter.enroll_user(user_id=target_device_uid, name=teacher.name)
    if not enroll_res.get("ok", False) or (
        not enroll_res.get("simulated", False)
        and not enroll_res.get("device_verified", False)
    ):
        failure_message = enroll_res.get("error") or enroll_res.get("message") or (
            "The adapter did not confirm device-side enrollment."
        )
        log_device_audit(
            event_type="enroll",
            result="error",
            device_id=adapter.get_device_info().get("device_id", "BIOMAX"),
            teacher_id=teacher.teacher_id,
            failure_reason=failure_message,
            school_id=teacher.school_id,
        )
        return {
            "ok": False,
            "error": failure_message,
            "device_info": adapter.get_device_info(),
            "simulated": bool(enroll_res.get("simulated", False)),
        }

    # Update teacher profile in AttendanceOS database
    teacher.fingerprint_user_id = target_device_uid
    teacher.fingerprint_enrolled = True
    teacher.fingerprint_enrolled_at = _utcnow()
    teacher.attendance_method = "FINGERPRINT"
    db.session.commit()

    log_device_audit(
        event_type="enroll",
        result="success",
        device_id=adapter.get_device_info().get("device_id", "BIOMAX"),
        teacher_id=teacher.teacher_id,
        failure_reason=f"Enrolled by {actor_email or 'admin'}",
        school_id=teacher.school_id,
    )

    return {
        "ok": True,
        "teacher_id": teacher.teacher_id,
        "name": teacher.name,
        "fingerprint_user_id": teacher.fingerprint_user_id,
        "fingerprint_enrolled": True,
        "simulated": bool(enroll_res.get("simulated", False)),
        "fingerprint_enrolled_at": teacher.fingerprint_enrolled_at.isoformat(),
        "device_info": adapter.get_device_info(),
        "message": (
            f"Simulated fingerprint enrollment for {teacher.name} ({teacher.teacher_id}); "
            "no physical device was changed."
            if enroll_res.get("simulated")
            else f"Fingerprint enrollment registered for {teacher.name} ({teacher.teacher_id})."
        ),
    }


def delete_teacher_fingerprint(
    teacher_id_or_pk: str | int,
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Delete a real or explicitly simulated fingerprint association for a teacher."""
    teacher = _find_teacher(teacher_id_or_pk, school_id=school_id)

    if not teacher:
        return {"ok": False, "error": "Teacher not found", "status": 404}

    adapter = get_fingerprint_adapter()
    target_device_uid = (teacher.fingerprint_user_id or teacher.teacher_id).strip()

    delete_res = adapter.delete_user(target_device_uid)
    if not delete_res.get("ok", False) or (
        not delete_res.get("simulated", False)
        and not delete_res.get("device_verified", False)
    ):
        return {
            "ok": False,
            "error": delete_res.get("error") or delete_res.get("message") or "Device deletion was not verified.",
            "simulated": bool(delete_res.get("simulated", False)),
        }

    teacher.fingerprint_enrolled = False
    teacher.fingerprint_enrolled_at = None
    db.session.commit()

    log_device_audit(
        event_type="delete_enrollment",
        result="success",
        device_id=adapter.get_device_info().get("device_id", "BIOMAX"),
        teacher_id=teacher.teacher_id,
        failure_reason=f"Unenrolled by {actor_email or 'admin'}",
        school_id=teacher.school_id,
    )

    return {
        "ok": True,
        "teacher_id": teacher.teacher_id,
        "fingerprint_enrolled": False,
        "simulated": bool(delete_res.get("simulated", False)),
        "message": f"Fingerprint enrollment removed for {teacher.name}.",
    }


# ---------------------------------------------------------------------------
# Fingerprint Verification Diagnostic Test (Does NOT Mark Attendance)
# ---------------------------------------------------------------------------

def test_fingerprint_verification(
    device_user_id: str,
    school_id: Optional[int | str] = None,
    actor_email: Optional[str] = None,
) -> dict[str, Any]:
    """Verify that the device/mapping correctly identifies a registered teacher.

    CRITICAL SAFETY REQUIREMENT:
    This diagnostic test NEVER alters or creates attendance records!
    """
    if not isinstance(device_user_id, str):
        return {
            "ok": False,
            "status": "INVALID_INPUT",
            "message": "Device User ID must be a string to preserve leading zeros.",
        }
    clean_uid = device_user_id.strip()
    if not clean_uid:
        return {
            "ok": False,
            "status": "INVALID_INPUT",
            "message": "Device User ID cannot be blank.",
        }

    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    q = Teacher.query.filter(Teacher.fingerprint_user_id == clean_uid)
    if clean_sid:
        q = q.filter((Teacher.school_id == clean_sid) | (Teacher.school_id.is_(None)))

    teacher = q.first()

    if not teacher:
        log_device_audit(
            event_type="test_verify",
            result="rejected",
            teacher_id=clean_uid,
            failure_reason="unknown_device_user_id",
            school_id=school_id,
        )
        return {
            "ok": False,
            "status": "UNKNOWN_USER",
            "device_user_id": clean_uid,
            "message": f"Device User ID '{clean_uid}' is not mapped to any registered teacher in AttendanceOS.",
        }

    method = (teacher.attendance_method or "FACE").upper()
    is_enrolled = bool(teacher.fingerprint_enrolled)
    is_active = bool(teacher.is_active)

    status = "SUCCESS"
    notes = []

    if not is_active:
        status = "INACTIVE_TEACHER"
        notes.append("Teacher profile is currently deactivated.")
    if method != "FINGERPRINT":
        status = "METHOD_MISMATCH"
        notes.append(f"Teacher is configured for '{method}' attendance, not FINGERPRINT.")
    if not is_enrolled:
        status = "NOT_ENROLLED"
        notes.append("Teacher has not completed biometric fingerprint enrollment.")

    log_device_audit(
        event_type="test_verify",
        result="success" if status == "SUCCESS" else "warning",
        teacher_id=teacher.teacher_id,
        failure_reason=f"Diagnostic verify result: {status} (Tested by {actor_email or 'admin'})",
        school_id=teacher.school_id,
    )

    return {
        "ok": status == "SUCCESS",
        "status": status,
        "device_user_id": clean_uid,
        "teacher": {
            "id": teacher.id,
            "teacher_id": teacher.teacher_id,
            "name": teacher.name,
            "department": teacher.department or "General",
            "designation": teacher.designation or "Faculty",
            "attendance_method": method,
            "fingerprint_enrolled": is_enrolled,
            "fingerprint_enrolled_at": teacher.fingerprint_enrolled_at.isoformat() if teacher.fingerprint_enrolled_at else None,
            "is_active": is_active,
        },
        "attendance_marked": False,  # Explicit safety confirmation
        "notes": notes,
        "message": f"✓ Teacher recognized: {teacher.name} ({teacher.teacher_id})." if status == "SUCCESS" else f"Teacher found ({teacher.name}), but: {', '.join(notes)}",
    }


# ---------------------------------------------------------------------------
# Attendance Engine Integration (Production Verification Events)
# ---------------------------------------------------------------------------

def verify_and_record_attendance(
    device_user_id: str,
    device_id: str = "TEAM_OFFICE_Z305_01",
    event_time: Optional[datetime] = None,
    school_id: Optional[int | str] = None,
    allow_unenrolled_fingerprint: bool = False,
) -> dict[str, Any]:
    """Process a device event received through an authenticated integration channel.

    Parameters
    ----------
    device_user_id : str
        Teacher identifier returned by the fingerprint device.
    device_id : str
        Physical hardware device identifier.
    event_time : datetime | None
        Timestamp of the verification. Defaults to UTC now.
    school_id : int | str | None
        Tenant identifier.
    """
    if not isinstance(device_user_id, str):
        logger.warning("[BIOMAX_SERVICE] Rejected non-string device_user_id from device='%s'", device_id)
        return {"action": "rejected", "reason": "device user id must be a string"}
    clean_uid = device_user_id.strip()
    if not clean_uid:
        logger.warning(f"[BIOMAX_SERVICE] Rejected empty device_user_id from device='{device_id}'")
        log_device_audit(
            event_type="verify",
            result="rejected",
            device_id=device_id,
            failure_reason="empty_user_id",
            school_id=school_id,
            event_time=event_time,
        )
        return {"action": "rejected", "reason": "empty user id"}

    event_time = event_time or _utcnow()
    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    logger.info(f"[BIOMAX_EVENT_RECV] Received biometric punch: device_user_id='{clean_uid}', device_id='{device_id}', event_time='{event_time.isoformat()}', school_id={clean_sid}")

    # Device punches must resolve through an explicit device mapping, never a name/code fallback.
    q = Teacher.query.filter(Teacher.fingerprint_user_id == clean_uid)
    if clean_sid is not None:
        q = q.filter(Teacher.school_id == clean_sid)
    matches = q.all()
    teacher = matches[0] if len(matches) == 1 else None

    # 2. Unknown / unmapped Teacher ID
    if not teacher:
        reason = "ambiguous_teacher_mapping" if len(matches) > 1 else "unknown_teacher_id"
        logger.warning(f"[BIOMAX_MAPPING_FAIL] device_user_id='{clean_uid}' mapping_count={len(matches)} (school_id={clean_sid})")
        log_device_audit(
            event_type="verify",
            result="rejected",
            device_id=device_id,
            teacher_id=clean_uid,
            failure_reason=reason,
            school_id=school_id,
            event_time=event_time,
        )
        return {
            "action": "rejected",
            "reason": "unknown teacher id",
            "device_user_id": clean_uid,
        }

    teacher_code = teacher.teacher_id
    logger.info(f"[BIOMAX_MAPPING_OK] Mapped device_user_id='{clean_uid}' -> Teacher '{teacher.name}' (PK={teacher.id}, Code='{teacher_code}', School={teacher.school_id})")

    # 3. Inactive teacher
    if not teacher.is_active:
        logger.warning(f"[BIOMAX_REJECT] Inactive teacher: '{teacher_code}' (PK={teacher.id})")
        log_device_audit(
            event_type="verify",
            result="rejected",
            device_id=device_id,
            teacher_id=teacher_code,
            failure_reason="teacher_inactive",
            school_id=teacher.school_id,
            event_time=event_time,
        )
        return {
            "action": "rejected",
            "reason": "teacher inactive",
            "teacher_id": teacher_code,
        }

    # 4. Teacher attendance method check (Must be FINGERPRINT)
    current_method = (teacher.attendance_method or "FACE").upper()
    if current_method != "FINGERPRINT":
        logger.warning(
            f"[BIOMAX_REJECT] Method mismatch for teacher '{teacher_code}': "
            f"configured for '{current_method}', received FINGERPRINT"
        )
        log_device_audit(
            event_type="verify",
            result="rejected",
            device_id=device_id,
            teacher_id=teacher_code,
            failure_reason=f"wrong_method_configured_{current_method}",
            school_id=teacher.school_id,
            event_time=event_time,
        )
        return {
            "action": "rejected",
            "reason": f"teacher configured for {current_method} attendance",
            "teacher_id": teacher_code,
        }

    # 5. Teacher fingerprint enrollment check
    if not teacher.fingerprint_enrolled and not allow_unenrolled_fingerprint:
        logger.warning(f"[BIOMAX_REJECT] Teacher '{teacher_code}' is not enrolled for fingerprint on device")
        log_device_audit(
            event_type="verify",
            result="rejected",
            device_id=device_id,
            teacher_id=teacher_code,
            failure_reason="fingerprint_not_enrolled",
            school_id=teacher.school_id,
            event_time=event_time,
        )
        return {
            "action": "rejected",
            "reason": "teacher not enrolled for fingerprint",
            "teacher_id": teacher_code,
        }

    # 6. Send into the ONE Common Attendance Engine with Time Rules and IN/OUT state machine
    logger.info(f"[BIOMAX_ATTENDANCE_INVOKE] Calling record_recognition() for teacher PK={teacher.id} (school_id={teacher.school_id}, now='{event_time.isoformat()}')")
    result = record_recognition(
        person_type="teacher",
        person_id=teacher.id,
        camera_id=None,
        confidence=1.0,
        now=event_time,
        school_id=teacher.school_id,
        auth_method="FINGERPRINT",
        device_id=device_id,
        allow_unenrolled_fingerprint=allow_unenrolled_fingerprint,
    )

    # 7. Audit log the outcome
    action = result.get("action", "unknown")
    logger.info(f"[BIOMAX_ATTENDANCE_RESULT] Outcome for teacher '{teacher_code}': action='{action}', status='{result.get('status')}', reason='{result.get('reason')}'")
    log_device_audit(
        event_type="verify",
        result=action,
        device_id=device_id,
        teacher_id=teacher_code,
        failure_reason=result.get("reason"),
        school_id=teacher.school_id,
        event_time=event_time,
    )

    result["teacher_id"] = teacher_code
    result["teacher_name"] = teacher.name
    return result


def get_fingerprint_audit_logs(
    limit: int = 100,
    school_id: Optional[int | str] = None,
) -> list[dict[str, Any]]:
    """Return recent fingerprint hardware audit logs."""
    clean_sid = int(school_id) if school_id and str(school_id).isdigit() else None
    q = FingerprintDeviceAudit.query
    if clean_sid:
        q = q.filter((FingerprintDeviceAudit.school_id == clean_sid) | (FingerprintDeviceAudit.school_id.is_(None)))
    rows = q.order_by(FingerprintDeviceAudit.event_time.desc()).limit(limit).all()
    return [r.to_dict() for r in rows]
