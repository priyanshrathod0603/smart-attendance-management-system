# AttendanceOS

<p align="center">
  <strong>Real-Time Face Recognition Attendance, Enterprise HR Management & Payroll Engine for Schools & Enterprises.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Status-Active%20Production-007AFF?style=for-the-badge" alt="Active Production">
  <img src="https://img.shields.io/badge/Python-3.14%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.14+">
  <img src="https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask 3.x">
  <img src="https://img.shields.io/badge/OpenCV-4.10%2B-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" alt="OpenCV 4.10+">
  <img src="https://img.shields.io/badge/AI-InsightFace-6F42C1?style=for-the-badge" alt="InsightFace">
  <img src="https://img.shields.io/badge/Database-SQLite%20%7C%20PostgreSQL-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="SQLite / PostgreSQL">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=for-the-badge" alt="Platform">
  <img src="https://img.shields.io/badge/Tests-23%2F23%20Passing-green?style=for-the-badge" alt="Tests Passing">
</p>

<p align="center">
  <a href="#-feature-status-matrix">Feature Matrix</a> ·
  <a href="#-system-architecture">Architecture</a> ·
  <a href="#-hr-management-system">HR & Payroll</a> ·
  <a href="#-installation--quickstart">Installation</a> ·
  <a href="#-api-reference">API Reference</a> ·
  <a href="#-testing--verification">Testing</a>
</p>

---

## 📌 Feature Status Matrix

This authoritative matrix reflects the exact state of AttendanceOS based on direct source code verification:

| Feature | Status | Implementation Details / Module Reference |
|---|---|---|
| **Face Recognition Engine** | ✅ IMPLEMENTED | InsightFace `buffalo_l` (512-d embeddings, cosine similarity) in `recognition/` |
| **Multi-Camera Management** | ✅ IMPLEMENTED | OpenCV streams (Webcams, RTSP, HTTP IP cameras) & MJPEG live stream in `camera/` |
| **Student Attendance** | ✅ IMPLEMENTED | Daily IN/OUT logs, time rules, late/half-day/early-exit tracking in `attendance_service.py` |
| **Teacher Attendance** | ✅ IMPLEMENTED | Dedicated daily teacher attendance logging and time-rule calculations in `database/enterprise_models.py` |
| **Teacher ↔ Employee Sync** | ✅ IMPLEMENTED | Idempotent sync service linking Teachers to HR Employees via `employee.teacher_id` in `hr/employee_service.py` |
| **HR Directory & Profiles** | ✅ IMPLEMENTED | Directory, multi-column forms, tabbed employee profiles in `hr/` & `templates/hr/` |
| **Leave Management** | ✅ IMPLEMENTED | Leave types, yearly leave balances, leave requests & approval workflow in `hr/employee_service.py` |
| **Loans & Repayments** | ✅ IMPLEMENTED | Principal tracking, monthly installments & payroll deduction safeguards in `hr/employee_service.py` |
| **Overtime Management** | ✅ IMPLEMENTED | Overtime hours logging, rate multipliers (1.0x, 1.5x, 2.0x) & approval workflow in `hr/` |
| **Reimbursements System** | ✅ IMPLEMENTED | Employee expense claim submissions, approvals, and payroll additions in `hr/employee_service.py` |
| **Payroll Processing Engine**| ✅ IMPLEMENTED | Pro-rated daily salary, late deduction policies, atomic DB transactions & immutability snapshots in `hr/payroll_service.py` |
| **Payslip Generation** | ✅ IMPLEMENTED | Printable HTML/PDF payslip layout with detailed earnings/deductions breakdown in `hr/payroll_service.py` |
| **HR & Time Audit Logs** | ✅ IMPLEMENTED | JSON-serialized audit trails for HR actions, time rules, and attendance edits in `hr/models.py` |
| **Authentication System** | ✅ IMPLEMENTED | User registration, Argon2 password hashing, session tokens, CSRF protection, and login events in `auth.py` |
| **OAuth Integration** | 🟡 PARTIAL | OAuth identity models (`auth_oauth_identities`) exist in `auth_models.py`; provider flows handle unconfigured states gracefully |
| **Enterprise RBAC** | 🟡 PARTIAL | Role headers/session roles checked; basic role checks implemented in `auth.py` |
| **Unknown Face Handling** | ✅ IMPLEMENTED | 30s embedding-based deduplication per camera & review API in `app.py` |
| **Offline AI Assistant** | ✅ IMPLEMENTED | Rule-based local intent parser with optional Ollama fallback in `slm/nlp_engine.py` |
| **Reports & Export Engine** | ✅ IMPLEMENTED | Exports to CSV, printable HTML, JSON, native Excel (`openpyxl`) & PDF (`reportlab`) in `app.py` |
| **Liveness / Anti-Spoofing** | ❌ NOT IMPLEMENTED | Toggles exist in settings schema, but no active computer vision liveness model exists |
| **WhatsApp / SMS Gateway** | ❌ NOT IMPLEMENTED | `NotificationRecord` table exists, but no outbound gateway integration exists |
| **Mobile Applications** | ❌ NOT IMPLEMENTED | Web frontend only; native Android/iOS mobile apps are not implemented |

---

## ✨ System Overview

**AttendanceOS** is an enterprise workforce & school management platform built with Python and Flask. It combines real-time camera face recognition with a full **Human Resource Management System (HRMS)** and **Payroll Engine**.

### Core Capabilities
1. **Camera-Based Face Recognition**: Processes live feeds from webcams, RTSP streams, or IP cameras to record IN/OUT attendance automatically.
2. **School & Workforce Management**: Manages Students, Teachers, and Non-Teaching Staff (Accountants, IT Administrators, Clerks, Security).
3. **Teacher ↔ Employee Linkage**: Automatically syncs existing Teachers into HR Employees while preserving teacher face encodings and attendance records.
4. **Time Rules Engine**: Computes late entry, half-day status, early exit, overtime, and working durations.
5. **Comprehensive HRMS**: Manages employee directory, organization hierarchy (Departments & Designations), Leave requests/balances, Employee Loans, Overtime claims, and Expense Reimbursements.
6. **Enterprise Payroll Engine**: Computes pro-rated daily salaries, applies late deduction policies (`none`, `fixed`, `per_minute`, `per_occurrence`), calculates overtime pay, deducts loan installments, freezes attendance snapshots, and generates printable payslips.

---

## 🤖 Face Recognition Engine

The recognition pipeline is implemented in `recognition/encoder.py` and `recognition/recognizer.py`:

- **Model Pack**: InsightFace `buffalo_l` running on ONNX Runtime.
- **Embedding Vectors**: 512-dimensional, L2-normalized floating-point vectors.
- **Matching Algorithm**: Dot product cosine similarity using NumPy.
- **Configurable Match Threshold**: `FACE_MATCH_THRESHOLD` in `config.py` (Default: `0.35`).
- **Cooldown Guard**: `ATTENDANCE_COOLDOWN_SECONDS` (Default: `300` seconds / 5 minutes) prevents duplicate logs for continuously visible individuals.
- **Multi-Camera Workers**: `stream.py` spawns an isolated background thread (`FaceRecognizer`) per active camera stream.
- **Unknown Face Deduplication**: Detects unrecognized faces, caches their 512-d embedding per camera with a 30-second cooldown, preventing duplicate database rows or image writes for the same visitor.

---

## 📹 Camera System Architecture

The camera subsystem in `camera/stream.py` handles multi-source video capture:

- **Supported Stream Sources**:
  - Webcams & USB cameras (numeric device indexes like `0`, `1`).
  - IP Cameras & RTSP streams (`rtsp://user:pass@ip:port/h264`).
  - HTTP Video Streams (`http://ip:port/video`).
  - Video files (`file:///path/to/video.mp4`).
- **Camera Operations**: Add, edit, delete, start, stop, test, and monitor status.
- **Auto-Reconnect**: Re-initializes OpenCV `VideoCapture` streams automatically upon frame drop or network disconnect.
- **MJPEG Live Streaming**: Serves annotated video feeds at `/stream/<camera_id>`.

---

## 🎓 Student Management

- **Directory & Enrolment**: Create, search, filter, update, deactivate, and delete student records.
- **Data Attributes**: Roll number, full name, class, section, contact details, profile photo path, activity status, and 512-d face embedding.
- **Validation**: Enforces unique roll numbers and normalizes class/section formatting.
- **Photo Enrolment**: Uploads student photo and extracts embedding only when a face is detected.

---

## 👩‍🏫 Teacher Management

- **Directory & Enrolment**: Create, search, update, deactivate, and delete teacher records.
- **Data Attributes**: Teacher ID, full name, subject, department, designation, assigned classes, contact details, profile photo path, and 512-d face embedding.
- **Attendance**: Integrated with live camera face recognition and daily attendance tracking.

---

## ⏱️ Attendance System & Time Rules

```mermaid
flowchart TD
    Frame[Camera Frame] --> Detect[InsightFace Face Detection]
    Detect --> Embed[Generate 512-d Embedding]
    Embed --> Match{Cosine Similarity >= 0.35?}
    Match -- No --> Unknown[Deduplicate & Log Unknown Face]
    Match -- Yes --> Identify[Identify Active Student or Teacher]
    Identify --> Cooldown{Cooldown Elapsed?}
    Cooldown -- No --> Ignore[Ignore Repeated Frames]
    Cooldown -- Yes --> Rules[Evaluate Time Rules]
    Rules --> Decision{IN, OUT, or Invalid?}
    Decision --> InRecord[Record Daily IN & Attendance Event]
    Decision --> OutRecord[Update Daily OUT & Working Minutes]
    InRecord --> DB[(Database)]
    OutRecord --> DB
    DB --> Dashboard[Live Dashboard & Reports]
```

### Time Rule Parameters:
- **Office Start / End**: Shift timing boundaries.
- **Late Grace Period**: First IN after `late_time` flags the record as **Late**.
- **Half-Day Threshold**: First IN after `half_day_time` flags the record as **Half-Day**.
- **OUT Window**: Prevents accidental OUT marks until `out_detection_start` opens.
- **Early Exit**: OUT before `early_exit_time` flags an **Early Exit**.
- **Overtime**: OUT after `overtime_start` accumulates approved overtime minutes.

---

## 💼 HR Management System

The HR system in `hr/` provides a comprehensive HRMS experience:

### 1. Employee Directory & Profiles (`/hr/employees`)
- Search, filter by department/designation/status, and view directory.
- Multi-column glassmorphic modal for adding and editing employees.
- **Tabbed Profile View (`/hr/employees/<id>`)**:
  - *Personal*: Full Name, DOB, Gender, Email, Phone, Address, Emergency Contact.
  - *Employment*: Code, Department, Designation, Type, Status, Joining Date, Linked Teacher badge.
  - *Attendance*: Historical IN/OUT events and daily session status.
  - *Leave*: Yearly leave balances & leave request history.
  - *Salary*: Basic/gross/CTC breakdown, salary components, and salary revision history timeline.
  - *Overtime*: Logged overtime hours, rates, multipliers & approval statuses.
  - *Loans*: Active loans, principal, remaining balance, installment amounts & repayment history.
  - *Payslips*: Issued payslips with instant view/print options.

### 2. Leave Management
- Configurable leave types (Paid, Casual, Sick).
- Yearly quota balance allocation per employee.
- Leave application, manager approval/rejection workflows, and leave balance updating.

### 3. Employee Loans & Repayments
- Loan issuance with principal amount, interest rate (if applicable), and monthly installment calculations.
- Repayment installment tracking and automatic payroll deduction guards to prevent over-deduction.

### 4. Overtime & Reimbursements
- Logged overtime hours with rate multipliers (1.0x, 1.5x, 2.0x).
- Expense claim submission, receipt linking, approval flows, and payroll addition.

---

## 🔄 Teacher → Employee Synchronization

AttendanceOS links existing school Teachers with the enterprise HR system:

```mermaid
flowchart LR
    Teacher["Teacher (T001 - Rahul Sharma)"] --> Sync["sync_teachers_to_employees()"]
    Sync --> Employee["HR Employee (EMP-T001, teacher_id=1)"]
    TeacherAtt["Teacher Attendance (Camera Pipeline)"] --> Employee
    Employee --> Salary["Employee Salary Structure (e.g. ₹45,000/mo)"]
    Salary --> Payroll["Payroll Engine"]
    Payroll --> Payslip["Printable Payslip"]
```

### Integration Principles:
1. **Idempotent Sync**: `sync_teachers_to_employees()` in `hr/employee_service.py` connects existing Teachers to HR Employees via `employee.teacher_id`. Running sync multiple times produces zero duplicates.
2. **Preserves HR Edits**: Re-running sync will **never overwrite** manually edited HR fields (such as updated phone numbers, residential addresses, or custom salary components).
3. **Direct Attendance Consumption**: HR and Payroll consume existing `TeacherAttendance` records directly without duplicating attendance rows.
4. **Independent Salaries**: Linked Teacher T1 (₹45,000/mo), Teacher T2 (₹32,000/mo), and Non-Teaching Staff (₹25,000/mo) maintain independent salary structures.
5. **Non-Teaching Staff Support**: Non-teacher staff (accountants, clerks, HR, security) are created with `teacher_id = NULL`.

---

## 💰 Enterprise Payroll Processing Engine

The payroll engine in `hr/payroll_service.py` executes salary processing:

```mermaid
flowchart TD
    Period[Select Payroll Period] --> Fetch[Load Active Employees]
    Fetch --> Base[Calculate Pro-Rated Daily Basic & Gross Salary]
    Base --> Att[Fetch Attendance Summary & Freeze Snapshot]
    Att --> Late[Apply Late Deduction Policy]
    Late --> OT[Add Approved Overtime Earnings]
    OT --> Bonus[Add Approved Bonuses & Reimbursements]
    Bonus --> Loan[Deduct Active Loan Installment]
    Loan --> Net[Compute Gross Earnings, Total Deductions & Net Pay]
    Net --> Lock[Save PayrollRecord & Create Immutability Snapshot]
    Lock --> Payslip[Generate Printable Payslip]
```

### Payroll Features & Technical Safeguards:
- **Atomic Transactions**: Payroll runs execute within a single database transaction. Any error triggers a full rollback (`db.session.rollback()`).
- **Attendance Snapshot Immutability**: `PayrollAttendanceSummary` captures a frozen snapshot of present, absent, late, half-day, and leave counters. Once a `PayrollPeriod` is marked `FINALIZED`, payroll figures cannot be altered by subsequent attendance edits.
- **Configurable Deduction Policies**:
  - `none`: No salary penalty for late arrival.
  - `fixed`: Fixed deduction amount per late occurrence.
  - `per_minute`: Deducts exact calculated per-minute rate for late minutes.
  - `per_occurrence`: Deducts fractional daily salary per late occurrence.
- **Overtime Multipliers**: Supports 1.0x (standard), 1.5x (time-and-a-half), and 2.0x (double time) overtime rates.

---

## 📄 Payslip Generation & Printing

- Generates structured payslip records (`Payslip` model).
- Includes company header, employee details, working days, attendance breakdown, itemized earnings (Basic, HRA, DA, OT, Bonuses), itemized deductions (Late penalties, Loans, Tax), and net pay.
- Displays responsive, print-ready HTML/PDF viewable directly in browser at `/api/hr/payslips/<id>`.

---

## 📊 Reports & Export Engine

- **Export Formats**: CSV, printable HTML, JSON, native Excel (`openpyxl`), and PDF (`reportlab`).
- **Report Types**: Daily attendance, weekly/monthly summary, department reports, teacher reports, late reports, half-day reports, overtime reports, working-hours distribution, and audit logs.

---

## 💬 Offline AI Assistant

- Implemented in `slm/nlp_engine.py`.
- **Primary Engine**: Rule-based intent parser for attendance queries, time rules, and system statistics.
- **Fallback Engine**: Optional local Ollama HTTP integration (`OLLAMA_MODEL`, default `phi3:mini`) for unhandled questions.

---

## 🔐 Authentication & Security

Implemented in `auth.py` and `database/auth_models.py`:

- **User Registration & Login**: Form authentication with email/password.
- **Password Hashing**: Secure Argon2 hashing via `argon2-cffi`.
- **Session Security**: Session tokens, expiration timestamps (`auth_sessions`), and CSRF protection.
- **Login Audit**: Records login events (`auth_login_events`).
- **OAuth Table Models**: OAuth identity models (`auth_oauth_identities`) exist in `auth_models.py`.

---

## 🧰 Tech Stack

| Layer | Technology | Specifier in `requirements.txt` |
|---|---|---|
| **Backend Framework** | Python 3.14+, Flask 3.x, Flask-SQLAlchemy | `Flask>=3.0,<4.0`, `Flask-SQLAlchemy>=3.1,<4.0` |
| **ORM / Database** | SQLAlchemy 2.x, SQLite / PostgreSQL | `SQLAlchemy>=2.0.36,<3.0`, `psycopg[binary]>=3.2.3` |
| **Computer Vision** | OpenCV 4.10+, NumPy, Pillow | `opencv-python>=4.10.0.84`, `numpy>=2.1.0`, `Pillow>=11.0.0` |
| **AI / Machine Learning** | InsightFace `buffalo_l`, ONNX Runtime | `insightface>=0.7.3`, `onnxruntime>=1.19.0` |
| **Authentication** | Argon2, Authlib | `argon2-cffi>=23.1.0`, `Authlib>=1.3.1` |
| **Frontend UI** | HTML5, ES6 JavaScript, CSS (Apple Liquid Glass) | Lucide Icons, Google Inter font |
| **Reporting / Testing** | pytest, requests, python-dotenv | `pytest`, `requests>=2.32.3`, `python-dotenv>=1.0.1` |

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    UI["Apple Liquid Glass Frontend\n(HTML5 + JS + CSS)"] --> Flask["Flask Application\nPage Routes & REST APIs"]
    Flask --> Auth["Authentication Module\n(auth.py - Argon2 & Sessions)"]
    Flask --> HR["HRMS & Payroll Engine\n(hr/employee_service.py & hr/payroll_service.py)"]
    Flask --> CameraManager["RecognitionManager\nOne worker per camera stream"]
    CameraManager --> OpenCV["OpenCV VideoCapture"]
    OpenCV --> InsightFace["InsightFace buffalo_l\n512-d Face Embeddings"]
    InsightFace --> Matcher["Cosine Similarity Matcher"]
    Matcher --> AttendanceService["attendance_service.py\nIN/OUT & Time Rules"]
    HR --> DB[("SQLAlchemy Models (50 Tables)\nSQLite / PostgreSQL")]
    AttendanceService --> DB
    Auth --> DB
    DB --> Flask
```

---

## 🗃️ Database Architecture & Schema Summary

AttendanceOS contains **50 database tables** managed via SQLAlchemy:

| Category | Table Count | Table Names |
|---|---|---|
| **Core Models** | 5 | `students`, `teachers`, `cameras`, `attendance`, `unknown_faces` |
| **Enterprise Attendance** | 16 | `student_attendance`, `teacher_attendance`, `attendance_events`, `attendance_session`, `attendance_time_rules`, `student_time_rules`, `teacher_time_rules`, `attendance_holiday`, `holiday_calendar`, `weekly_off`, `attendance_audit_log`, `attendance_logs`, `attendance_corrections`, `camera_sessions`, `notification_records`, `attendance_settings` |
| **HRMS & Payroll** | 24 | `departments`, `designations`, `employees`, `employee_employment_history`, `salary_components`, `employee_salary_structures`, `employee_salary_components`, `leave_types`, `employee_leave_balances`, `leave_requests`, `shifts`, `employee_shifts`, `overtime_records`, `employee_bonuses`, `employee_loans`, `loan_repayments`, `reimbursements`, `payroll_periods`, `payroll_records`, `payroll_attendance_summaries`, `payroll_earnings`, `payroll_deductions`, `payslips`, `hr_audit_logs` |
| **Authentication** | 5 | `auth_users`, `auth_sessions`, `auth_tokens`, `auth_oauth_identities`, `auth_login_events` |

---

## 🔌 API Reference

AttendanceOS serves **122 Flask routes** (83 REST API endpoints):

### HR & Payroll APIs (`/api/hr/*`)
| Method | Endpoint | Description |
|---|---|---|
| `GET, POST` | `/api/hr/employees` | List/search employees or create new HR employee |
| `GET, PUT, DELETE` | `/api/hr/employees/<id>` | Fetch details, update HR fields, or deactivate employee |
| `POST` | `/api/hr/sync-teachers` | Trigger idempotent Teacher → Employee synchronization |
| `GET, POST` | `/api/hr/departments` | List active departments or create department |
| `PUT` | `/api/hr/departments/<id>` | Update department details |
| `GET, POST` | `/api/hr/designations` | List designations or create designation |
| `GET, POST` | `/api/hr/salary-components` | Manage global earning/deduction component templates |
| `GET, POST` | `/api/hr/employees/<id>/salary-structure` | Fetch active salary structure or create new revision |
| `GET` | `/api/hr/employees/<id>/salary-history` | View historical salary revisions timeline |
| `GET, POST` | `/api/hr/employees/<id>/leaves` | Query leave requests or apply for leave |
| `POST` | `/api/hr/leave-requests/<id>/approve` | Approve leave request and update balances |
| `POST` | `/api/hr/leave-requests/<id>/reject` | Reject leave request |
| `GET, POST` | `/api/hr/employees/<id>/loans` | Query active loans or issue loan |
| `POST` | `/api/hr/loans/<id>/repay` | Record loan repayment installment |
| `GET, POST` | `/api/hr/payroll-periods` | List payroll periods or create period |
| `POST` | `/api/hr/payroll-periods/<id>/process` | Run atomic payroll calculation engine |
| `POST` | `/api/hr/payroll-periods/<id>/finalize` | Finalize & lock payroll period |
| `GET` | `/api/hr/payroll-periods/<id>/records` | Fetch calculated employee payroll line items |
| `GET` | `/api/hr/payslips/<id>` | Retrieve full payslip data for printing/downloading |
| `GET` | `/api/hr/dashboard-summary` | Real-time HR KPI metrics |
| `GET` | `/api/hr/audit-log` | HR audit trail records |

### Core & Enterprise Attendance APIs
| Method | Endpoint | Description |
|---|---|---|
| `GET, POST` | `/api/students` | List/filter or enrol student records |
| `POST` | `/api/students/<id>/photo` | Upload and encode student face photo |
| `GET, POST` | `/api/teachers` | List/filter or enrol teacher records |
| `POST` | `/api/teachers/<id>/photo` | Upload and encode teacher face photo |
| `GET, POST` | `/api/cameras` | List configured cameras or add camera stream |
| `POST` | `/api/cameras/<id>/start` | Start live recognition worker for camera |
| `POST` | `/api/cameras/<id>/stop` | Stop recognition worker for camera |
| `GET` | `/api/cameras/status` | Live recognition status & face counts per camera |
| `GET` | `/stream/<camera_id>` | Live annotated MJPEG video stream |
| `GET, POST` | `/api/enterprise/attendance` | Query daily attendance or manual mark |
| `GET` | `/api/enterprise/dashboard` | Enterprise dashboard analytics & trends |
| `GET, PUT` | `/api/time-rules/<scope>` | Read or update student/teacher time rules |
| `GET` | `/api/unknown-faces` | Query latest unknown face detections |
| `POST` | `/api/unknown-faces/<id>/action` | Review or assign unknown face detection |
| `POST` | `/api/ask` | Query offline AI attendance assistant |

---

## 🎨 Frontend Architecture

The frontend consists of Jinja2 templates, ES6 JavaScript, and Vanilla CSS implementing the **Apple Liquid Glass Design Language**:

- **38 Web Page Routes**: Dashboard (`/`), Enterprise Dashboard (`/dashboard-enterprise`), Students (`/students`), Teachers (`/teachers`), Cameras (`/cameras`), Attendance (`/attendance`), Reports (`/reports`), HR Dashboard (`/hr/dashboard`), Employees (`/hr/employees`), Profile (`/hr/employees/<id>`), Payroll (`/hr/payroll`), HR Reports (`/hr/reports`), HR Settings (`/hr/settings`), Time Rules (`/time-rules/student`), Settings (`/settings`), Assistant (`/chat`).
- **Sidebar Navigation**: Accordion navigation with compact scrollable class selectors (`max-height: 200px`) and active route highlighting.

---

## 🗂️ Folder Structure

```text
AttendanceOS/
├── app.py                         # Flask application factory, web routes, APIs & stream
├── config.py                      # Environment configuration & settings
├── attendance_service.py          # Real-time attendance pipeline & IN/OUT rules
├── auth.py                        # Authentication routes & session handling
├── hr/                            # Complete HR & Payroll Module
│   ├── models.py                  # 25 Normalized SQLAlchemy HR models
│   ├── employee_service.py        # Employee CRUD, leave, loans & teacher sync
│   ├── payroll_service.py         # Centralized payroll engine & snapshots
│   └── routes.py                  # 37 REST API endpoints (/api/hr/*)
├── database/
│   ├── db.py                      # SQLAlchemy instance
│   ├── models.py                  # Core Student, Teacher, Camera & Attendance models
│   ├── enterprise_models.py       # Enterprise session & time models
│   └── auth_models.py             # User, session, token & OAuth models
├── camera/
│   └── stream.py                  # Multi-camera recognizer manager
├── recognition/
│   ├── encoder.py                 # InsightFace frame encoder
│   └── recognizer.py              # Background OpenCV worker thread
├── scripts/
│   ├── migrate_hr.py              # Idempotent HR database migration script
│   └── init_db.py                 # Database initialization script
├── static/
│   ├── styles.css                 # Apple Liquid Glass CSS design system
│   └── common.js                  # API helpers & modal utilities
├── templates/
│   ├── base.html                  # Main layout with accordion navigation
│   └── hr/                        # HR & Payroll UI templates
└── tests/                         # Complete Pytest test suite
    ├── test_authentication.py
    ├── test_camera_source.py
    ├── test_hr_database.py
    ├── test_hr_sync.py
    └── test_teacher_module.py
```

---

## 📦 Installation & Quickstart

### 1. Prerequisites
- **Python**: 3.14+ (or Python 3.10+)
- **Git**: Installed
- **Camera**: Webcam, USB Camera, or RTSP/HTTP IP Camera

### 2. Clone Repository & Setup Virtual Environment

#### macOS / Linux:
```bash
git clone https://github.com/priyanshrathod0603/AttendanceOS.git
cd AttendanceOS
python3 -m venv .venv
source .venv/bin/activate
```

#### Windows (PowerShell):
```powershell
git clone https://github.com/priyanshrathod0603/AttendanceOS.git
cd AttendanceOS
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Database Setup

#### Local SQLite (Default):
No extra setup required! Database file will be auto-created at `database/attendanceos.db`.

#### PostgreSQL Setup:
Create PostgreSQL database, then set environment variable:
```bash
export DATABASE_URL="postgresql+psycopg://postgres:password@localhost:5432/attendanceos"
```

### 5. Run Database Migration
```bash
python scripts/migrate_hr.py
```

### 6. Start Server
```bash
python app.py
```
Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## 🪟 Windows Setup & Troubleshooting Guide

1. **PowerShell Execution Policy**: If `.venv\Scripts\Activate.ps1` gives a script execution error, run PowerShell as Administrator once:
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```
2. **Correct Installation Command**: Always run `pip install -r requirements.txt` (do NOT run `pip install requirements.txt`).
3. **Camera Index on Windows**: Built-in webcams use index `0`. External USB webcams typically use index `1` or `2`.
4. **Port Conflicts**: If port 5000 is occupied by IIS or another service, launch with a custom port:
   ```cmd
   set PORT=5001
   python app.py
   ```

---

## 🍎 macOS / Apple Silicon Setup Guide

1. **Camera Permissions**: Ensure Terminal / VS Code / Python has Camera access granted under `System Settings -> Privacy & Security -> Camera`.
2. **ONNX Runtime Acceleration**: InsightFace automatically utilizes CPU execution provider on Apple Silicon (M1/M2/M3/M4) chips.

---

## 🛠️ Troubleshooting Guide

| Issue | Cause | Solution |
|---|---|---|
| `pip install -r requirements.txt` fails | Outdated pip wheel builders | Run `python -m pip install --upgrade pip setuptools wheel` first. |
| `InsightFace model download error` | Internet disconnect on first launch | InsightFace automatically downloads `buffalo_l` (~280MB) on first run to `~/.insightface/models/`. Ensure internet is connected during initial face enrolment. |
| Camera feed is black / empty | Incorrect camera index or missing permission | Test source via `POST /api/cameras/<id>/test` or check system camera permissions. |
| `Address already in use` | Port 5000 occupied | Run `export PORT=5001` (Linux/macOS) or `set PORT=5001` (Windows). |
| PostgreSQL connection error | Invalid DB URL format | Ensure DB URL uses `postgresql+psycopg://user:pass@localhost:5432/dbname`. |

---

## 🧪 Testing & Verification

AttendanceOS features an automated pytest test suite covering authentication, camera sources, teacher management, HR database operations, payroll snapshot immutability, and teacher synchronization:

```bash
python -m pytest tests/ -v
```

### Verified Test Suite Output:
```text
tests/test_authentication.py::test_unauthenticated_requests_are_contained PASSED
tests/test_authentication.py::test_signup_login_logout_and_csrf PASSED
tests/test_authentication.py::test_expired_or_revoked_server_session_cannot_be_reused PASSED
tests/test_authentication.py::test_employee_cannot_write_privileged_api_resources PASSED
tests/test_authentication.py::test_unconfigured_oauth_providers_fail_gracefully PASSED
tests/test_camera_source.py::test_blank_source_falls_back_to_default_camera_index PASSED
tests/test_camera_source.py::test_invalid_text_source_falls_back_to_default_camera_index PASSED
tests/test_camera_source.py::test_rtsp_source_is_preserved PASSED
tests/test_camera_source.py::test_http_ip_webcam_source_appends_video PASSED
tests/test_camera_source.py::test_http_ip_webcam_source_with_slash_appends_video PASSED
tests/test_camera_source.py::test_http_ip_webcam_source_already_has_video PASSED
tests/test_hr_database.py::test_department_and_designation_crud PASSED
tests/test_hr_database.py::test_employee_teacher_mapping PASSED
tests/test_hr_database.py::test_salary_structures_and_history PASSED
tests/test_hr_database.py::test_leave_request_and_balance PASSED
tests/test_hr_database.py::test_loan_and_repayment_guard PASSED
tests/test_hr_database.py::test_payroll_run_and_snapshot_immutability PASSED
tests/test_hr_sync.py::test_teacher_sync_creates_employees_and_is_idempotent PASSED
tests/test_hr_sync.py::test_sync_preserves_manually_edited_hr_fields PASSED
tests/test_hr_sync.py::test_independent_salaries_per_teacher_and_non_teacher PASSED
tests/test_teacher_module.py::test_teacher_crud_and_profile PASSED
tests/test_teacher_module.py::test_teacher_recognition_and_attendance PASSED
tests/test_teacher_module.py::test_dashboard_summary_and_reports PASSED

======================= 23 passed in 1.35s =======================
```

---

## 🔐 Security Audit & Biometric Privacy

### Implemented Security Controls:
- **Authentication & Sessions**: Password hashing using Argon2 (`argon2-cffi`), session token tracking, expiration handling, and CSRF protection.
- **Input Validation**: Sanitizes camera sources, student roll numbers, teacher IDs, and monetary inputs.
- **File Upload Protection**: Uses `secure_filename()` for uploaded face images.
- **Coercion Rejection**: Images without detectable faces are rejected during enrolment.
- **Audit Logging**: JSON-serialized audit trails recorded in `hr_audit_logs` and `attendance_audit_log`.

### Current Security Limitations:
- **Enterprise RBAC**: Fine-grained role-based permission enforcement is basic/stubbed.
- **Liveness Detection**: Computer-vision liveness detection is not active. Use hardware liveness cameras in high-security environments.
- **Biometric Privacy**: Face embeddings (512-d vectors) are stored in the database. Ensure user consent and compliance with local biometric privacy regulations (e.g. GDPR, CCPA, IT Act).

---

## ⚙️ Configuration Reference

| Variable | Default | Required? | Purpose |
|---|---|---|---|
| `FLASK_SECRET` | `dev-secret-change-me` | Recommended | Flask session secret key |
| `DATABASE_URL` | SQLite path | No | Database connection URI (SQLite or PostgreSQL) |
| `CAMERA_SOURCE` | `0` | No | Initial camera stream source |
| `FACE_MATCH_THRESHOLD` | `0.35` | No | Cosine similarity threshold for face recognition |
| `ATTENDANCE_COOLDOWN_SECONDS` | `300` | No | Recognition cooldown per person (seconds) |
| `PORT` | `5000` | No | HTTP server listening port |
| `USE_OLLAMA` | `false` | No | Enables local Ollama fallback for AI chat assistant |
| `OLLAMA_MODEL` | `phi3:mini` | No | Ollama model name |

---

## 🚀 Product Roadmap

### ✅ Implemented Capabilities
- Real-time face detection, 512-d embedding extraction & cosine matching
- Multi-camera manager & MJPEG streaming
- Student and Teacher attendance tracking
- Teacher ↔ Employee synchronization
- Full HRMS (Employees, Profiles, Depts, Designations, Leaves, Loans, Overtime, Reimbursements)
- Enterprise Payroll engine with pro-ration, late rules, overtime multipliers & snapshot immutability
- Payslip view & print generation
- Authentication system with Argon2 password hashing & CSRF protection
- Automated test suite (23/23 passing)

### 🟡 Partial / In Progress
- OAuth Identity provider integrations (`auth_oauth_identities`)
- Enterprise RBAC middleware refinement

### 🔮 Future Roadmap (Not Implemented)
- Native Mobile Applications (iOS / Android)
- Computer-vision Face Liveness Detection / Anti-Spoofing
- WhatsApp / SMS / Email Outbound Gateway Integrations
- QR Code & RFID Biometric Hardware Integrations

---

## 📄 License & Author

**AttendanceOS** — Real-Time Face Recognition Attendance, HRMS & Payroll System.

Created and maintained by [Priyansh Rathod](https://github.com/priyanshrathod0603).

> *Note on Licensing*: This repository is intended for open-source distribution under the **MIT License**. A standalone `LICENSE` file can be added to the root directory.
