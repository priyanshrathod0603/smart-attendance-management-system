r"""Watch a folder for Z305 USB attendance exports and sync them into AttendanceOS.

Example (PowerShell):
  .\venv\Scripts\python.exe scripts\watch_z305_exports.py --folder D:\\Z305\\exports --device-id Z305_MAIN --school-id 1

The device must first export attendance data to USB and the files must be copied
to this folder. This does not enroll fingerprints or communicate with the device.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from biometrics.attendance_file_import import import_attendance_export

SUPPORTED = {".csv", ".tsv", ".txt", ".xlsx"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True, help="Folder where exported attendance files are copied")
    parser.add_argument("--device-id", required=True, help="Stable AttendanceOS device ID for this terminal")
    parser.add_argument("--school-id", required=True, type=int, help="AttendanceOS school database ID")
    parser.add_argument("--timezone", default="Asia/Kolkata", help="Device clock timezone (default: Asia/Kolkata)")
    parser.add_argument("--interval", type=float, default=10, help="Folder rescan interval in seconds")
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    args = parser.parse_args()
    folder = Path(args.folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    last_seen: dict[Path, tuple[int, int]] = {}
    completed: dict[Path, tuple[int, int]] = {}
    logging.info("Watching %s for Z305 attendance exports; device=%s school=%s", folder, args.device_id, args.school_id)
    with app.app_context():
        while True:
            for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED):
                try:
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    if completed.get(path) == signature:
                        continue
                    if not args.once and last_seen.get(path) != signature:
                        last_seen[path] = signature
                        continue  # wait until a write has stopped before reading
                    outcome = import_attendance_export(path, device_id=args.device_id,
                                                       school_id=args.school_id,
                                                       timezone_name=args.timezone)
                    logging.info("Import result %s", outcome)
                    if outcome["rejected"] == 0 and not outcome["errors"]:
                        completed[path] = signature
                except Exception:
                    logging.exception("Could not import %s; it will be retried", path)
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
