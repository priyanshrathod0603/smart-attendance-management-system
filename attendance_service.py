"""Single write path for live enterprise attendance recognition.

The recognizer calls :func:`record_recognition` once per matched face. This
service is the only writer to the normalised ``student_attendance`` /
``teacher_attendance`` tables and the ``attendance_events`` log; the legacy
``attendance`` table is left untouched.

Every derived field is recomputed from the configured time rules so the
dashboard, IN/OUT page, and reports can rely on the row's stored values
without re-deriving them.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
import logging
from typing import Optional

logger = logging.getLogger("attendance_service")

from database.db import db
from database.enterprise_models import (
    AttendanceEvent,
    AttendanceLog,
    StudentAttendance,
    StudentTimeRule,
    TeacherAttendance,
    TeacherTimeRule,
)


# --------------------------------------------------------------------- helpers
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes even for timezone-aware columns."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _clock(value: Optional[str], default: str) -> time:
    try:
        h, m = (value or default).split(":", 1)
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return _clock(default, default)


def _to_local_minutes(dt: datetime) -> int:
    """Return local HHMM as total minutes since 00:00, robust to naive datetimes."""
    if dt is None:
        return 0
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.hour * 60 + dt.minute


def _diff_minutes(later: datetime, earlier: datetime) -> int:
    if not later or not earlier:
        return 0
    delta = _aware(later) - _aware(earlier)
    return max(int(delta.total_seconds() // 60), 0)


# --------------------------------------------------------------------- rules
def _rules_for(kind: str, school_id: Optional[int | str] = None):
    """Resolve applicable time rules following the multi-tenant hierarchy:
    1. Prefer an applicable rule belonging to the given school_id.
    2. If no school-specific rule exists, fall back to a global rule where school_id IS NULL.
    3. If neither exists, instantiate and persist a default rule instance.
    """
    model = StudentTimeRule if kind == "student" else TeacherTimeRule

    clean_sid = None
    if school_id is not None and str(school_id).strip() not in ("", "None", "null"):
        try:
            clean_sid = int(school_id)
        except (ValueError, TypeError):
            clean_sid = str(school_id).strip()

    rules = None

    # 1. Prefer an applicable rule belonging to the person's school_id
    if clean_sid is not None:
        rules = model.query.filter_by(school_id=clean_sid).first()

    # 2. If no school-specific rule exists, fall back to a global rule where school_id IS NULL
    if rules is None:
        rules = model.query.filter(model.school_id.is_(None)).first()

    # 3. If no applicable rule exists at all, preserve documented default behavior
    if rules is None:
        rules = model(school_id=clean_sid)
        if rules.id is None:
            db.session.add(rules)
            db.session.flush()

    logger.info(
        f"[TIME_RULES_RESOLVE] Resolved {kind} time rule: id={rules.id}, "
        f"school_id={rules.school_id} (requested={clean_sid}), "
        f"office_start='{rules.office_start}', late_time='{rules.late_time}'"
    )
    return rules



def _initial_status(now_clock: time, rules) -> tuple[str, bool, bool]:
    """Return ``(status, is_late, is_half_day)`` for a fresh IN event."""
    half_day_at = _clock(rules.half_day_time, "09:30")
    late_at = _clock(rules.late_time, "08:30")
    if now_clock >= half_day_at:
        return "half_day", False, True
    if now_clock >= late_at:
        return "late", True, False
    return "present", False, False


def _recompute_out_fields(row, rules, now: datetime) -> None:
    """Recompute every derived column on a row after IN or OUT changes."""
    # Working minutes
    if row.in_time and row.out_time:
        total = _diff_minutes(row.out_time, row.in_time)
        row.working_minutes = total
        row.working_seconds = total * 60
    else:
        row.working_minutes = 0
        row.working_seconds = 0

    # Break time is implicit; no separate break events are recorded yet.
    row.break_minutes = 0

    # A half-day is a completed session shorter than the configured minimum.
    # Keep this independent from the late flag: a person can be both late and
    # half-day, and the dashboard/report layer exposes both facts.
    if row.out_time:
        try:
            min_working = int(getattr(rules, "min_working_minutes", 480))
        except (TypeError, ValueError):
            min_working = 480
        is_half_day = row.working_minutes < min_working
    else:
        is_half_day = row.status == "half_day"

    # Early-exit: out before early_exit_time
    if row.out_time:
        early_at = _clock(rules.early_exit_time, "16:30")
        out_min = _to_local_minutes(row.out_time)
        early_min = early_at.hour * 60 + early_at.minute
        row.is_early_exit = out_min < early_min
    else:
        row.is_early_exit = False

    # Overtime: out after overtime_start (only when overtime enabled)
    overtime_enabled = bool(getattr(rules, "overtime_enabled", True))
    if row.out_time and overtime_enabled:
        ot_at = _clock(rules.overtime_start, "17:00")
        out_min = _to_local_minutes(row.out_time)
        ot_min = ot_at.hour * 60 + ot_at.minute
        diff = out_min - ot_min
        if diff > 0:
            row.overtime_minutes = diff
        else:
            row.overtime_minutes = 0
    else:
        row.overtime_minutes = 0

    # Final status: re-evaluate in priority order
    if row.out_time is None:
        # Still inside the school — keep IN-time derived status.
        if row.status in ("absent",):
            return
    if is_half_day:
        row.status = "half_day"
    elif row.is_early_exit:
        row.status = "early_exit"
    elif (row.overtime_minutes or 0) > 0:
        row.status = "overtime"
    elif row.is_late:
        row.status = "late"


# --------------------------------------------------------------------- public API
def record_recognition(
    person_type: str,
    person_id: int,
    camera_id: Optional[int] = None,
    confidence: float = 1.0,
    now: Optional[datetime] = None,
    school_id: Optional[str] = None,
    auth_method: str = "FACE",
    device_id: Optional[str] = None,
    allow_unenrolled_fingerprint: bool = False,
) -> dict:
    """Persist exactly one enterprise IN/OUT event.

    Single common attendance engine for all identification methods (FACE, FINGERPRINT).

    Parameters
    ----------
    person_type : str
        ``"student"`` or ``"teacher"``.
    person_id : int
        Database primary key of the matched person.
    camera_id : int | None
        Camera row that produced the detection (for face events).
    confidence : float
        Cosine similarity or verification confidence (0..1).
    now : datetime | None
        Override the current time. Used in tests; defaults to ``datetime.now(UTC)``.
    school_id : str | None
        Tenant identifier.
    auth_method : str
        Identification method used (``"FACE"`` or ``"FINGERPRINT"``).
    device_id : str | None
        Hardware device identifier (e.g. BioMax terminal ID).
    """
    if person_type not in {"student", "teacher"}:
        raise ValueError(f"unsupported person type: {person_type}")

    auth_method_norm = (auth_method or "FACE").upper().strip()

    from database.models import Student, Teacher
    p_model = Student if person_type == "student" else Teacher
    person_obj = db.session.get(p_model, person_id)

    if person_obj is None:
        print(f"[ATTENDANCE] rejected {person_type}={person_id}: person not found in database")
        return {"action": "rejected", "reason": f"{person_type} not found"}

    if not person_obj.is_active:
        print(f"[ATTENDANCE] rejected {person_type}={person_id}: person is inactive")
        return {"action": "rejected", "reason": f"{person_type} inactive"}

    # Validate configured attendance method for teachers
    if person_type == "teacher":
        expected_method = (getattr(person_obj, "attendance_method", None) or "FACE").upper().strip()
        if auth_method_norm != expected_method:
            print(
                f"[ATTENDANCE] rejected teacher={person_id}: "
                f"configured for {expected_method} attendance, received {auth_method_norm}"
            )
            return {
                "action": "rejected",
                "reason": f"invalid attendance method: teacher configured for {expected_method}, received {auth_method_norm}",
            }
        if (auth_method_norm == "FINGERPRINT" and not allow_unenrolled_fingerprint
                and not getattr(person_obj, "fingerprint_enrolled", False)):
            print(f"[ATTENDANCE] rejected teacher={person_id}: fingerprint not enrolled on device")
            return {
                "action": "rejected",
                "reason": "teacher not enrolled for fingerprint",
            }
    elif person_type == "student":
        if auth_method_norm != "FACE":
            print(f"[ATTENDANCE] rejected student={person_id}: students only support FACE attendance")
            return {
                "action": "rejected",
                "reason": f"students only support FACE attendance, received {auth_method_norm}",
            }

    model, fk = (
        (StudentAttendance, "student_id")
        if person_type == "student"
        else (TeacherAttendance, "teacher_id")
    )
    if not school_id and person_obj and person_obj.school_id:
        school_id = person_obj.school_id

    now = _aware(now) or _now()
    rules = _rules_for(person_type, school_id=school_id)
    now_clock = now.astimezone().time().replace(tzinfo=None)

    method_label = "Fingerprint verification" if auth_method_norm == "FINGERPRINT" else "Face recognition match"

    row = model.query.filter_by(**{fk: person_id, "attendance_date": now.date()}).first()
    if row is None:
        # --- new day, no record yet -----------------------------------
        if now_clock < _clock(rules.office_start, "08:00"):
            logger.warning(f"[ATTENDANCE_REJECT] Rejected {person_type}={person_id}: before IN start ({now_clock} < {rules.office_start})")
            print(f"[ATTENDANCE] rejected {person_type}={person_id}: before IN start")
            return {"action": "rejected", "reason": "before in start"}
        if now_clock >= _clock(rules.absent_after, "11:00"):
            row = model(
                **{
                    fk: person_id,
                    "attendance_date": now.date(),
                    "status": "absent",
                    "camera_id": camera_id,
                    "confidence": confidence,
                    "school_id": school_id,
                }
            )
            db.session.add(row)
            try:
                db.session.commit()
                logger.info(f"[ATTENDANCE_COMMIT] Marked absent {person_type}={person_id} attendance_id={row.id}: absent-after threshold ({now_clock} >= {rules.absent_after})")
            except Exception as exc:
                db.session.rollback()
                logger.error(f"[ATTENDANCE_ROLLBACK] Failed to record absent row for {person_type}={person_id}: {exc}")
                raise
            print(f"[ATTENDANCE] rejected {person_type}={person_id}: absent-after threshold")
            return {"action": "rejected", "reason": "absent after", "attendance_id": row.id}

        status, is_late, is_half_day = _initial_status(now_clock, rules)
        row = model(
            **{
                fk: person_id,
                "attendance_date": now.date(),
                "in_time": now,
                "status": status,
                "is_late": is_late,
                "is_early_exit": False,
                "camera_id": camera_id,
                "confidence": confidence,
                "school_id": school_id,
            }
        )
        db.session.add(row)
        db.session.flush()
        db.session.add(
            AttendanceEvent(
                attendance_type=person_type,
                attendance_id=row.id,
                event_type="in",
                auth_method=auth_method_norm,
                device_id=device_id,
                event_time=now,
                camera_id=camera_id,
                confidence=confidence,
                school_id=school_id,
            )
        )
        _recompute_out_fields(row, rules, now)
        db.session.add(
            AttendanceLog(
                entity_type=f"{person_type}_attendance",
                entity_id=row.id,
                field="in_time",
                old_value=None,
                new_value=now.isoformat(),
                edited_by="system",
                reason=f"{method_label} (confidence: {confidence:.3f}, method: {auth_method_norm})",
                school_id=school_id,
            )
        )
        try:
            db.session.commit()
            logger.info(
                f"[ATTENDANCE_COMMIT] Inserted {person_type}={person_id} "
                f"attendance_id={row.id} event=in status={row.status} method={auth_method_norm} confidence={confidence:.3f}"
            )
        except Exception as exc:
            db.session.rollback()
            logger.error(f"[ATTENDANCE_ROLLBACK] Failed to commit IN attendance for {person_type}={person_id}: {exc}")
            raise
        print(
            f"[ATTENDANCE] inserted {person_type}={person_id} "
            f"attendance_id={row.id} event=in status={row.status} method={auth_method_norm} confidence={confidence:.3f}"
        )
        return {
            "action": "inserted",
            "event": "in",
            "attendance_id": row.id,
            "status": row.status,
            "method": auth_method_norm,
        }

    if row.out_time is None:
        if row.in_time is None:
            logger.warning(
                f"[ATTENDANCE_REJECT] Rejected {person_type}={person_id}: "
                f"daily status is {row.status}; no IN exists"
            )
            print(
                f"[ATTENDANCE] rejected {person_type}={person_id}: "
                f"daily status is {row.status}; no IN exists"
            )
            return {"action": "rejected", "reason": "no in time", "attendance_id": row.id}
        if now_clock < _clock(rules.out_detection_start, "16:00"):
            logger.info(
                f"[ATTENDANCE_DUPLICATE_IGNORED] Ignored punch {person_type}={person_id}: "
                f"OUT window opens at {rules.out_detection_start} (current={now_clock})"
            )
            print(
                f"[ATTENDANCE] ignored {person_type}={person_id}: "
                f"OUT window opens at {rules.out_detection_start}"
            )
            return {
                "action": "ignored",
                "reason": "before out detection start",
                "attendance_id": row.id,
            }
        row.out_time = now
        row.camera_id = camera_id
        row.confidence = confidence
        db.session.add(
            AttendanceEvent(
                attendance_type=person_type,
                attendance_id=row.id,
                event_type="out",
                auth_method=auth_method_norm,
                device_id=device_id,
                event_time=now,
                camera_id=camera_id,
                confidence=confidence,
                school_id=school_id or row.school_id,
            )
        )
        _recompute_out_fields(row, rules, now)
        db.session.add(
            AttendanceLog(
                entity_type=f"{person_type}_attendance",
                entity_id=row.id,
                field="out_time",
                old_value=None,
                new_value=now.isoformat(),
                edited_by="system",
                reason=f"{method_label} (confidence: {confidence:.3f}, method: {auth_method_norm})",
                school_id=school_id or row.school_id,
            )
        )
        try:
            db.session.commit()
            logger.info(
                f"[ATTENDANCE_COMMIT] Updated {person_type}={person_id} "
                f"attendance_id={row.id} event=out status={row.status} method={auth_method_norm}"
            )
        except Exception as exc:
            db.session.rollback()
            logger.error(f"[ATTENDANCE_ROLLBACK] Failed to commit OUT attendance for {person_type}={person_id}: {exc}")
            raise
        print(
            f"[ATTENDANCE] updated {person_type}={person_id} "
            f"attendance_id={row.id} event=out status={row.status} method={auth_method_norm}"
        )
        return {
            "action": "updated",
            "event": "out",
            "attendance_id": row.id,
            "status": row.status,
            "method": auth_method_norm,
        }

    logger.warning(f"[ATTENDANCE_DUPLICATE_REJECT] Rejected duplicate punch {person_type}={person_id}: daily IN and OUT already exist")
    print(f"[ATTENDANCE] rejected {person_type}={person_id}: daily IN and OUT already exist")
    return {"action": "rejected", "reason": "daily attendance complete", "attendance_id": row.id}


# Unified entry point alias for multi-modal identity processing
process_attendance_identity = record_recognition

