# PROJECT CONTEXT: AttendanceOS / Krishna Payroll

## 1. Project Overview & Identity
**AttendanceOS** (also referred to internally as **Krishna Payroll / Krishna ERP**) is an enterprise-grade School & Institutional ERP, Biometric Attendance, and HR/Payroll Management System. 

It unifies:
1. **Real-Time Facial Recognition Attendance**: Real-time CCTV/webcam stream ingestion, face detection, 512-dimensional feature embedding via InsightFace (`buffalo_l`), and cosine-similarity matching for Students and Teachers/Staff.
2. **Enterprise Time & Attendance Management**: Flexible policy engine supporting shift timings, late grace thresholds, half-day triggers, early-exit detection, overtime computation, holiday calendars, and weekly-off management.
3. **Comprehensive HR & Payroll Subsystem**: Complete employee lifecycle management (joining, designation, department, reporting manager), salary structures (earnings & deductions with configurable formulas), leave management with approval workflows, loans/advances with salary deductions, expense reimbursements, immutable payroll generation, and payslip creation.
4. **Hardened Identity & Access Management (Auth & RBAC)**: Argon2id password hashing, server-side stateful session tokens (`auth_sessions`), per-session CSRF protection, two-factor / email verification (both 6-digit OTP code and secure token links), password reset workflows, and role-based permissions across 7 discrete user roles.
5. **Natural Language Attendance Assistant (SLM)**: Offline regex-based deterministic intent engine paired with an optional local Small Language Model (SLM) fallback powered by Ollama (`phi3:mini` / `llama3.2:1b`).

---

## 2. Business & Operational Domains

### Domain 1: Face Recognition & Live Monitoring
* Operates on live video streams (webcam index or RTSP/HTTP network video feeds).
* Features multi-angle enrollment (Front, Left, Right) to minimize false negatives from angular variations.
* Employs configurable cooldown intervals (default 300s) to prevent duplicate attendance triggers on the same subject.
* Captures and stores unrecognized face snapshots in `unknown_faces` for administrative assignment or security alert review.

### Domain 2: Academic & Institutional Attendance
* Manages student rosters categorized by `class_name` (Nursery through Class 8) and `section` (A, B, C, D).
* Tracks student IN/OUT times, daily attendance status (`present`, `late`, `half_day`, `absent`, `early_exit`, `overtime`), and working duration.
* Generates exportable attendance summaries in CSV format.

### Domain 3: Human Resources (HR) & Staff Management
* Links teaching staff (`teachers` table) seamlessly to the HR employee layer (`employees` table) via foreign key `teacher_id` without breaking legacy modules.
* Models hierarchical organizational structures: Departments, Designations, and Reporting Managers.
* Manages full employment lifecycle and history (`employee_employment_history`).

### Domain 4: Time Tracking, Leaves & Shifts
* Shift management with customizable grace periods, break allowances, and overtime triggers.
* Leave tracking system (`leave_types`, `employee_leave_balances`, `leave_requests`) with approval workflows.
* Holiday calendar supporting national, institutional, and emergency holidays across student/teacher scopes.

### Domain 5: Payroll & Compensation Engine
* Modular salary structures (`employee_salary_structures`) with dynamic earnings (Basic, DA, HRA, Allowances) and deductions (PF, ESI, TDS, Professional Tax).
* Automatic reconciliation of attendance data into immutable monthly payroll runs (`payroll_periods`, `payroll_records`, `payroll_attendance_summaries`).
* One-to-one payslip generation (`payslips`) and audit logging (`hr_audit_logs`).

### Domain 6: Identity, Session Governance & RBAC
* Secure user management (`auth_users`) with verified email gates.
* Server-side session tracking (`auth_sessions`) enabling active session audits and remote revocations.
* 7 Role levels: `super_admin`, `hr_admin`, `payroll_manager`, `finance_manager`, `manager`, `employee`, `viewer`.

---

## 3. High-Level System Architecture

```
                      +-----------------------------------+
                      |   Client Browser (Web UI / SPA)   |
                      |   Jinja2 + Vanilla JS + CSS Grid  |
                      +-----------------+-----------------+
                                        |
                            HTTP / JSON / MJPEG
                                        |
                      +-----------------v-----------------+
                      |     Flask Web Server (App Core)   |
                      +---+-------------+-------------+---+
                          |             |             |
        +-----------------+             |             +-----------------+
        |                               |                               |
+-------v-------+               +-------v-------+               +-------v-------+
|  Auth & RBAC  |               |  HR & Payroll |               |  Live Camera  |
|  (auth.py)    |               |  (hr/routes)  |               |  & Recognition|
|  Argon2 / CSRF|               |  Services     |               |  (InsightFace)|
+-------+-------+               +-------+-------+               +-------+-------+
        |                               |                               |
        +-----------------+             |             +-----------------+
                          |             |             |
                      +---v-------------v-------------v---+
                      |      SQLAlchemy 2.0 ORM Layer     |
                      +-----------------+-----------------+
                                        |
                      +-----------------v-----------------+
                      |     SQLite Primary Database       |
                      |   (instance/krishna_erp.db)       |
                      |   [PostgreSQL 17 Compatible]      |
                      +-----------------------------------+
```

---

## 4. Key Terminology & Domain Glossary

| Term | Definition in AttendanceOS |
| :--- | :--- |
| **Student** | Academic enrollee associated with a class and section; subject to student attendance rules. |
| **Teacher** | Academic instructor record; acts as the primary biometric identity for teaching staff. |
| **Employee** | Comprehensive HR entity representing teaching and non-teaching staff. |
| **InsightFace** | ONNX-based deep learning library utilized for face detection and 512-d feature extraction. |
| **Session ID** | Server-side cryptographic token representing an authenticated user session. |
| **Payroll Snapshot** | Immutable capture of attendance metrics at the exact moment payroll is calculated. |
| **SLM** | Small Language Model / rule engine used to answer queries regarding live attendance. |
| **Weekly Off** | Institutional non-working weekday (default: Sunday = 6). |
