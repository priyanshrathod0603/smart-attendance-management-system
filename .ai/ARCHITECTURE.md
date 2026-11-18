# SYSTEM ARCHITECTURE & DATA FLOW

## 1. Architectural Overview
AttendanceOS is structured as a modular monolithic web application in Python, combining high-speed asynchronous video processing threads, hardware biometric device adapters, a RESTful Flask application, and relational database persistence.

```
+-----------------------------------------------------------------------------------+
|                                 CLIENT LAYER                                      |
|  - HTML5 / Jinja2 Templates (Apple Liquid Glass Design System)                    |
|  - Fingerprint Device Management Page (/admin/devices/fingerprint)                |
|  - Modular Vanilla JS (app.js, common.js, auth.js, enterprise_dashboard.js)       |
|  - Live MJPEG Stream Player (/stream/<cid>)                                       |
+------------------------------------------+----------------------------------------+
                                           |
                                 HTTP / REST / WebSocket
                                           |
+------------------------------------------v----------------------------------------+
|                                APPLICATION CORE (Flask)                           |
|                                                                                   |
|  +-------------------+  +-------------------+  +-------------------------------+  |
|  |   Auth Blueprint  |  |   Core App Routes |  |     HR & Payroll Blueprint    |  |
|  |   (/auth/*)       |  |   (/api/students) |  |     (/api/hr/*)               |  |
|  |   - Argon2id      |  |   (/api/teachers) |  |     - Employees / Salary      |  |
|  |   - Sessions/CSRF |  |   (/api/cameras)  |  |     - Leaves / Loans / Shifts |  |
|  |   - Multi-tenant  |  |   (/api/devices/*)|  |     - Payroll Processing      |  |
|  +---------+---------+  +---------+---------+  +---------------+---------------+  |
|            |                      |                            |                  |
|  +---------v----------------------v----------------------------v---------------+  |
|  |                       Enterprise Service & Query Layer                      |  |
|  |  - attendance_service.py (Single write path for biometrics)                 |  |
|  |  - biometrics/fingerprint_service.py (Device lifecycle & identity bridge)   |  |
|  |  - enterprise_query.py   (Unified KPI calculator & aggregations)            |  |
|  |  - hr/employee_service.py & hr/payroll_service.py                            |  |
|  |  - slm/nlp_engine.py     (Rule & Ollama NLP engine)                         |  |
|  +--------------------------------+--------------------------------------------+  |
+-----------------------------------|-----------------------------------------------+
                                    |
+-----------------------------------v-----------------------------------------------+
|                       BIOMETRICS & HARDWARE ADAPTER LAYER                         |
|  - camera/stream.py: RecognitionManager managing VideoCapture threads             |
|  - recognition/recognizer.py: FaceRecognizer background thread loop (InsightFace) |
|  - biometrics/fingerprint_adapter.py:                                             |
|      * FingerprintDeviceAdapter (Abstract Base)                                   |
|      * BioMaxIndigoAdapter (Production TCP/IP Socket & HTTP Push Receiver)        |
|      * MockFingerprintAdapter (Isolated In-Memory Test Simulator)                 |
+-----------------------------------+-----------------------------------------------+
                                    |
+-----------------------------------v-----------------------------------------------+
|                            PERSISTENCE & DATA LAYER                               |
|  - SQLAlchemy 2.0 ORM                                                             |
|  - SQLite (instance/krishna_erp.db) / PostgreSQL 17                                |
|  - Models: Student, Teacher, Camera, Attendance, FingerprintDevice,               |
|            FingerprintDeviceAudit, StudentAttendance, TeacherAttendance, etc.     |
+-----------------------------------------------------------------------------------+
```

---

## 2. Core Subsystems & Component Boundaries

### A. Team Office Z305 Fingerprint Integration & Identity Bridge
```
Administrator / Web UI
      ↓
/admin/devices/fingerprint (Protected by RBAC: Admin, Principal)
      ↓
AttendanceOS Backend REST API (/api/devices/fingerprint/*)
      ↓
biometrics/fingerprint_service.py
      ↓
FingerprintDeviceAdapter (Abstraction Layer)
      ↓
[ Team Office Z305 network-only adapter ]  <-- TCP reachability only; port is vendor-configured
      |
      | (Authenticated AttendanceOS-normalized event; native Z305 push is unverified)
      v
Teacher ID Identity Bridge (Unique 1-to-1 Mapping)
      ↓
Common Attendance Engine (attendance_service.record_recognition)
      ↓
Teacher Time Rules (Office Start, Late, Half-Day, Out Detection, Overtime)
      ↓
TeacherAttendance Record + AttendanceEvent (auth_method = "FINGERPRINT")
```

1. **Protocol Boundary**: No Z305-specific protocol, port, SDK method, native push, or ZKTeco compatibility is assumed. The real adapter fails closed for enrollment and log polling until vendor documentation is supplied.
2. **Event Security**: Device webhook requests require per-device bearer credentials and a registered active device. AttendanceOS-normalized events are validated, tenant-scoped, timezone-normalized, and deduplicated.
3. **Simulation Boundary**: Mock adapter operations are explicitly marked `simulated` and are not physical hardware evidence.
4. **Failure Isolation**: An offline or unconfigured fingerprint device never impacts Face Attendance or system stability.
5. **Diagnostic Verification Mode**: Allows administrators to test identity resolution safely without marking attendance.

### B. Live Face Recognition Pipeline
1. **Ingestion**: `RecognitionManager` starts a dedicated `FaceRecognizer` thread per camera.
2. **Frame Capture**: OpenCV reads raw frames from device `/dev/video*` or RTSP network streams.
3. **Face Detection & Alignment**: InsightFace detects bounding boxes and aligns landmarks.
4. **Embedding Extraction**: Generates 512-d normalized floating-point vectors.
5. **Matching**: Vector dot product with cached in-memory embeddings.
6. **Persistence**: Invokes `attendance_service.record_recognition()`, writing to `teacher_attendance` / `student_attendance` and `attendance_events`.

### C. Enterprise Attendance Engine
* **Single Write Path**: `attendance_service.py` is the exclusive writer for normalized live recognition events (both Face and Fingerprint).
* **IN/OUT State Machine**:
  - First detection of the day -> Creates IN record, calculates `is_late` / `is_half_day`.
  - Detections after `out_detection_start` -> Updates OUT record, calculates `working_minutes`, `is_early_exit`, and `overtime_minutes`.
* **Dual Time Rules**:
  - `student_time_rules` governs student hours and gate closing times.
  - `teacher_time_rules` governs staff shifts and overtime thresholds.
