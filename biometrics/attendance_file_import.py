"""Import Z305 attendance exports without depending on the vendor SDK.

This accepts *attendance punches only*. It cannot create device users or enroll
fingerprints. Re-scanning files is safe because each normalized punch is stored
in the same persistent receipt table used by the authenticated device receiver.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database.db import db
from database.enterprise_models import FingerprintDevice, FingerprintEventReceipt
from database.models import Teacher

logger = logging.getLogger(__name__)

_USER_HEADERS = {"userid", "userno", "usernumber", "pin", "employeeid", "deviceuserid", "uid", "staffid"}
_STAMP_HEADERS = {"datetime", "punchtime", "checktime", "eventtime", "verifytime", "timestamp"}
_DATE_HEADERS = {"date", "punchdate", "checkdate", "eventdate"}
_TIME_HEADERS = {"time", "punchtimeofday", "checktimeofday", "eventtimeofday"}
_EVENT_HEADERS = {"eventid", "logid", "recordid", "serialno"}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _parse_datetime(value: object, date_value: object, time_value: object, tz: ZoneInfo) -> datetime:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        text = str(value).strip()
        iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(iso_text)
        except ValueError:
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
    if parsed is None and date_value not in (None, "") and time_value not in (None, ""):
        if isinstance(date_value, datetime):
            parsed_date = date_value.date()
        elif isinstance(date_value, date):
            parsed_date = date_value
        else:
            parsed_date = None
            text = str(date_value).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    parsed_date = datetime.strptime(text, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed_date is None:
                raise ValueError(f"invalid attendance date: {text!r}")
        if isinstance(time_value, time):
            parsed_time = time_value
        elif isinstance(time_value, datetime):
            parsed_time = time_value.time()
        else:
            parsed_time = None
            text = str(time_value).strip()
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    parsed_time = datetime.strptime(text, fmt).time()
                    break
                except ValueError:
                    continue
            if parsed_time is None:
                raise ValueError(f"invalid attendance time: {text!r}")
        parsed = datetime.combine(parsed_date, parsed_time)
    if parsed is None:
        raise ValueError("attendance row requires a recognized timestamp or date and time columns")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _dict_rows(headers: list[object], values: Iterable[object], timezone_name: str) -> dict | None:
    normalized = [_key(item) for item in headers]
    row = {key: value for key, value in zip(normalized, values)}
    user_key = next((key for key in normalized if key in _USER_HEADERS), None)
    stamp_key = next((key for key in normalized if key in _STAMP_HEADERS), None)
    date_key = next((key for key in normalized if key in _DATE_HEADERS), None)
    time_key = next((key for key in normalized if key in _TIME_HEADERS), None)
    event_key = next((key for key in normalized if key in _EVENT_HEADERS), None)
    if not user_key:
        return None
    user_id = str(row.get(user_key, "")).strip()
    if not user_id or user_id.lower() in {"none", "nan"}:
        raise ValueError("attendance row has an empty device employee ID")
    tz = ZoneInfo(timezone_name)
    event_time = _parse_datetime(row.get(stamp_key) if stamp_key else None,
                                 row.get(date_key) if date_key else None,
                                 row.get(time_key) if time_key else None, tz)
    return {"user_id": user_id, "timestamp": event_time,
            "event_id": str(row.get(event_key)).strip() if event_key and row.get(event_key) not in (None, "") else None}


def read_attendance_export(path: str | Path, timezone_name: str = "Asia/Kolkata") -> list[dict]:
    """Read CSV/TSV/TXT or XLSX with header rows; reject ambiguous layouts."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("XLSX import requires openpyxl; install it with `pip install openpyxl`.") from exc
        workbook = load_workbook(source, read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = list(next(rows, ()))
        result = []
        for line_number, values in enumerate(rows, start=2):
            if not any(value not in (None, "") for value in values):
                continue
            parsed = _dict_rows(headers, values, timezone_name)
            if parsed is None:
                raise ValueError("missing recognized employee ID column in spreadsheet header")
            parsed["line"] = line_number
            result.append(parsed)
        workbook.close()
        return result
    if suffix not in {".csv", ".tsv", ".txt"}:
        raise ValueError("supported export formats are .csv, .tsv, .txt, and .xlsx (not legacy .xls)")
    with source.open("r", encoding="utf-8-sig", newline="", errors="replace") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        rows = csv.reader(handle, dialect)
        headers = next(rows, [])
        if not headers:
            return []
        result = []
        for line_number, values in enumerate(rows, start=2):
            if not any(value.strip() for value in values):
                continue
            parsed = _dict_rows(headers, values, timezone_name)
            if parsed is None:
                raise ValueError("missing recognized employee ID column in export header")
            parsed["line"] = line_number
            result.append(parsed)
        return result


def import_attendance_export(path: str | Path, *, device_id: str, school_id: int,
                             timezone_name: str = "Asia/Kolkata") -> dict:
    """Persist parsed device punches through AttendanceOS's common engine."""
    from biometrics.fingerprint_service import verify_and_record_attendance

    device_id = str(device_id).strip()
    if not device_id or len(device_id) > 60:
        raise ValueError("device_id must be 1 to 60 characters")
    school_id = int(school_id)
    source = Path(path)
    rows = read_attendance_export(source, timezone_name)
    device = FingerprintDevice.query.filter_by(device_id=device_id, school_id=school_id).first()
    if device is None:
        device = FingerprintDevice(school_id=school_id, device_id=device_id,
                                   name="Team Office Z305 USB export", model="Team Office Z305",
                                   connection_type="USB_IMPORT", status="file_import", is_active=True)
        db.session.add(device)
        db.session.flush()

    result = {"file": str(source), "device_id": device_id, "rows": len(rows),
              "imported": 0, "duplicates": 0, "rejected": 0, "errors": []}
    for item in rows:
        user_id = item["user_id"]
        event_time = item["timestamp"]
        mapped = Teacher.query.filter_by(fingerprint_user_id=user_id, school_id=school_id).all()
        if len(mapped) != 1 or not mapped[0].is_active or (mapped[0].attendance_method or "").upper() != "FINGERPRINT":
            result["rejected"] += 1
            result["errors"].append({"line": item["line"], "user_id": user_id,
                                     "reason": "device user ID must map uniquely to an active teacher configured for fingerprint attendance"})
            continue
        canonical = {"device_id": device_id, "user_id": user_id,
                     "timestamp": event_time.astimezone(timezone.utc).isoformat(),
                     "verify_type": "FINGERPRINT"}
        if item.get("event_id"):
            canonical["event_id"] = item["event_id"]
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        identity = f"{device_id}:{item['event_id']}" if item.get("event_id") else serialized
        event_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if FingerprintEventReceipt.query.filter_by(event_key=event_key).first():
            result["duplicates"] += 1
            continue
        receipt = FingerprintEventReceipt(device_id=device_id, event_key=event_key,
                                          payload_hash=payload_hash, device_user_id=user_id,
                                          event_time=event_time, result="processing")
        db.session.add(receipt)
        try:
            db.session.flush()
            attendance_result = verify_and_record_attendance(
                device_user_id=user_id, device_id=device_id, event_time=event_time,
                school_id=school_id, allow_unenrolled_fingerprint=True,
            )
            receipt.result = str(attendance_result.get("action", "unknown"))[:30]
            receipt.response_json = attendance_result
            device.last_activity_at = datetime.now(timezone.utc)
            db.session.commit()
            if attendance_result.get("action") in {"inserted", "updated"}:
                result["imported"] += 1
            elif attendance_result.get("action") == "ignored":
                result["duplicates"] += 1
            else:
                result["rejected"] += 1
                result["errors"].append({"line": item["line"], "user_id": user_id,
                                         "reason": attendance_result.get("reason", "event rejected")})
        except IntegrityError:
            db.session.rollback()
            result["duplicates"] += 1
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.exception("Z305 export import failed for %s line %s", source, item["line"])
            result["errors"].append({"line": item["line"], "user_id": user_id, "reason": str(exc)})
            result["rejected"] += 1
    return result
