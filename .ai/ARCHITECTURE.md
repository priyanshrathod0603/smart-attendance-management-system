# SYSTEM ARCHITECTURE & DATA FLOW

## 1. Architectural Overview
AttendanceOS is structured as a modular monolithic web application in Python, combining high-speed asynchronous video processing threads with a RESTful Flask application and relational database persistence.

```
+-----------------------------------------------------------------------------------+
|                                 CLIENT LAYER                                      |
|  - HTML5 / Jinja2 Templates                                                       |
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
|  |   - OTP / Links   |  |   (/api/stats)    |  |     - Payroll Processing      |  |
|  +---------+---------+  +---------+---------+  +---------------+---------------+  |
|            |                      |                            |                  |
|  +---------v----------------------v----------------------------v---------------+  |
|  |                       Enterprise Service & Query Layer                      |  |
|  |  - attendance_service.py (Single write path for biometrics)                 |  |
|  |  - enterprise_query.py   (Unified KPI calculator & aggregations)            |  |
|  |  - hr/employee_service.py & hr/payroll_service.py                            |  |
|  |  - slm/nlp_engine.py     (Rule & Ollama NLP engine)                         |  |
|  +--------------------------------+--------------------------------------------+  |
+-----------------------------------|-----------------------------------------------+
                                    |
+-----------------------------------v-----------------------------------------------+
|                       COMPUTER VISION & BACKGROUND ENGINE                         |
|  - camera/stream.py: RecognitionManager managing VideoCapture threads             |
|  - recognition/recognizer.py: FaceRecognizer background thread loop               |
|  - recognition/encoder.py: InsightFace buffalo_l ONNX feature extraction          |
|  - recognition/face_registration.py: Multi-angle (Front/Left/Right) enrollment    |
+-----------------------------------+-----------------------------------------------+
                                    |
+-----------------------------------v-----------------------------------------------+
|                            PERSISTENCE & DATA LAYER                               |
|  - SQLAlchemy 2.0 ORM                                                             |
|  - SQLite (instance/krishna_erp.db) / PostgreSQL 17                                |
+-----------------------------------------------------------------------------------+
```

---

## 2. Core Subsystems & Component Boundaries

### A. Live Face Recognition Pipeline
1. **Ingestion**: `RecognitionManager` starts a dedicated `FaceRecognizer` thread per camera.
2. **Frame Capture**: OpenCV reads raw frames from device `/dev/video*` or RTSP network streams.
3. **Face Detection & Alignment**: InsightFace detects bounding boxes and aligns landmarks.
4. **Embedding Extraction**: Generates 512-d normalized floating-point vectors.
5. **Matching**: Vector dot product with cached in-memory embeddings from active Students and Teachers. If score >= `FACE_MATCH_THRESHOLD` (0.35), identity is resolved.
6. **Cooldown Check**: Verifies if subject was marked within `ATTENDANCE_COOLDOWN_SECONDS` (300s).
7. **Persistence**: Invokes `attendance_service.record_recognition()`, writing to `student_attendance` / `teacher_attendance` and `attendance_events`.
8. **Unrecognized Faces**: If no match found and confidence exceeds minimum detector threshold, logs to `unknown_faces` with snapshot image for admin review.

### B. Enterprise Attendance Engine
* **Single Write Path**: `attendance_service.py` is the exclusive writer for normalized live recognition events.
* **IN/OUT State Machine**:
  - First detection of the day -> Creates IN record, calculates `is_late` / `is_half_day`.
  - Detections after `out_detection_start` -> Updates OUT record, calculates `working_minutes`, `is_early_exit`, and `overtime_minutes`.
* **Dual Time Rules**:
  - `student_time_rules` governs student hours and gate closing times.
  - `teacher_time_rules` governs staff shifts and overtime thresholds.

### C. HR & Payroll Lifecycle
1. **Teacher Sync**: Integrates academic teachers into HR without biometric data duplication.
2. **Salary Structure**: Defines base salary, earnings (HRA, DA, Special Allowance), and deductions (PF, ESI, Tax).
3. **Monthly Payroll Run**:
   - `draft` -> Period created.
   - `processing` -> Calculates payable days, leave deductions, overtime pay, loan installments.
   - `finalized` -> Generates immutable `payroll_attendance_summaries` snapshot and creates `payslips`.

### D. Identity, Security & Session Governance
* **Stateful Sessions**: `auth_sessions` table stores `token_hash` (SHA-256 of 32-byte secret), IP address, device user-agent, `csrf_secret`, and expiry (12 hours).
* **Double Verification Flow**:
  - Account created in unverified state (`email_verified_at = NULL`).
  - Verification code (6-digit OTP, 10-min TTL) and verification token (24-hour TTL) generated.
  - Verification succeeds via link or 6-digit code entry -> `email_verified_at` stamped.
* **Role-Based Access Control**: Decorators `@require_auth` and permission mappings validate incoming requests against `ROLE_PERMISSIONS`.
