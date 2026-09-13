# COMPLETE REPOSITORY FILE MAP

```text
AttendanceOS/
├── .ai/                             # AI Project Brain & Long-Term Source of Truth
│   ├── AI_RULES.md                  # Constitution for future AI agents
│   ├── PROJECT_CONTEXT.md           # Executive project overview & domain dictionary
│   ├── CURRENT_STATE.md             # Verified maturity & implementation state
│   ├── ARCHITECTURE.md              # Architectural blueprint & data flows
│   ├── TECH_STACK.md                # Runtime, libraries, and frameworks
│   ├── PRODUCT_REQUIREMENTS.md      # Domain requirements & business logic
│   ├── DATABASE.md                  # Exhaustive 28-table database schema
│   ├── API_CONTRACTS.md             # REST API specifications across all routes
│   ├── AUTHENTICATION.md            # Auth, Sessions, CSRF, OTP & RBAC specification
│   ├── INFRASTRUCTURE.md            # Infrastructure, hosting, and Docker audit
│   ├── SECURITY_RULES.md            # Security audit & guidelines
│   ├── UI_RULES.md                  # UI design system & template conventions
│   ├── CODING_RULES.md              # Python 3.14 & SQLAlchemy coding rules
│   ├── FILE_MAP.md                  # This file map
│   ├── DECISIONS.md                 # Architecture Decision Records (ADRs)
│   ├── TASKS.md                     # Actionable engineering tasks & backlog
│   ├── BUGS.md                      # Discovered risks & deprecation logs
│   ├── CHANGELOG.md                 # Historical project evolution
│   ├── TESTING.md                   # Test suite documentation & coverage
│   └── SESSION_STATE.md             # Agent memory handoff & immediate priorities
├── camera/                          # Camera & Video Ingestion Subsystem
│   ├── __init__.py
│   └── stream.py                    # RecognitionManager multi-camera thread manager (108 lines)
├── database/                        # Database ORM Models & Singletons
│   ├── __init__.py                  # Central ORM registry & exports (52 lines)
│   ├── db.py                        # SQLAlchemy singleton instance (5 lines)
│   ├── models.py                    # Student, Teacher, Camera, Attendance, UnknownFace (246 lines)
│   ├── auth_models.py               # User, AuthSession, AuthToken, OAuthIdentity, LoginEvent (86 lines)
│   └── enterprise_models.py         # StudentAttendance, TeacherAttendance, TimeRules, Holidays (163 lines)
├── hr/                              # Human Resources & Payroll Subsystem
│   ├── __init__.py                  # Blueprint registration (12 lines)
│   ├── models.py                    # 23 HR models: Employees, Salary, Leaves, Loans, Payroll (1491 lines)
│   ├── routes.py                    # HR REST API endpoints under /api/hr (827 lines)
│   ├── employee_service.py          # Employee lifecycle & leave business logic (928 lines)
│   └── payroll_service.py           # Salary calculations & immutable payroll runs (849 lines)
├── recognition/                     # InsightFace Biometric Recognition Pipeline
│   ├── __init__.py
│   ├── encoder.py                   # InsightFace buffalo_l ONNX feature extraction (112 lines)
│   ├── face_registration.py         # Multi-angle (Front/Left/Right) enrollment helper (191 lines)
│   └── recognizer.py                # FaceRecognizer OpenCV background matching thread (386 lines)
├── slm/                             # Small Language Model / Natural Language Query
│   ├── __init__.py
│   └── nlp_engine.py                # Regex intent parser + optional Ollama bridge (244 lines)
├── time_management/                 # Enterprise Time Rules & Session Management
│   ├── __init__.py                  # Time management exports (20 lines)
│   ├── models.py                    # AttendanceTimeRule, AttendanceSession, AttendanceHoliday (245 lines)
│   ├── service.py                   # Time calculations, rules, holidays (402 lines)
│   └── api.py                       # Time management REST API endpoints under /api (723 lines)
├── templates/                       # Server-Side Jinja2 HTML Templates
│   ├── base.html                    # Master layout with sidebar & modals (506 lines)
│   ├── dashboard.html               # Academic dashboard (118 lines)
│   ├── dashboard_enterprise.html    # Enterprise time dashboard (244 lines)
│   ├── students.html                # Student directory & 3-angle enrollment (114 lines)
│   ├── teachers.html                # Teacher directory & profile modal (98 lines)
│   ├── cameras.html                 # Multi-camera live stream monitoring (24 lines)
│   ├── attendance.html              # Attendance logs & filtering (68 lines)
│   ├── attendance_extended.html     # Extended attendance grid (105 lines)
│   ├── reports.html                 # Academic reports generator (62 lines)
│   ├── reports_enterprise.html      # Enterprise time reports (110 lines)
│   ├── time_rules.html              # Time rules configuration (96 lines)
│   ├── settings.html                # General system settings (104 lines)
│   ├── chat.html                    # SLM chat interface (64 lines)
│   ├── 404.html                     # Error page (12 lines)
│   ├── auth/                        # Authentication UI templates
│   │   ├── login.html               # Sign in page (102 lines)
│   │   ├── signup.html              # Sign up page (78 lines)
│   │   ├── verify_code.html         # 6-digit OTP verification page (182 lines)
│   │   ├── verify_email.html        # Email link verification handler (64 lines)
│   │   ├── verify_required.html     # Post-signup verification reminder (48 lines)
│   │   ├── forgot_password.html     # Password reset request (54 lines)
│   │   ├── reset_password.html      # Password reset submission (62 lines)
│   │   └── account.html             # User profile & security settings (290 lines)
│   ├── hr/                          # HR UI templates
│   │   ├── dashboard.html           # HR executive overview (248 lines)
│   │   ├── employees.html           # Employee directory (720 lines)
│   │   ├── employee_profile.html    # Comprehensive employee profile (890 lines)
│   │   ├── payroll.html             # Payroll run & payslip processing (412 lines)
│   │   ├── reports.html             # HR & Payroll reports (310 lines)
│   │   └── settings.html            # HR policy & salary component settings (680 lines)
│   └── landing/                     # Public landing page
│       └── index.html               # Marketing & product landing page (540 lines)
├── static/                          # Static Frontend Assets (JavaScript, CSS, Images)
│   ├── styles.css                   # Master CSS stylesheet (~90KB)
│   ├── app.js                       # Core dashboard logic (1080 lines)
│   ├── common.js                    # Shared utilities, modals, toasts, CSRF helpers (610 lines)
│   ├── auth.js                      # Authentication client helpers (115 lines)
│   ├── enterprise_dashboard.js      # Enterprise analytics widgets (270 lines)
│   ├── reports.js                   # Report table rendering (145 lines)
│   ├── reports_enterprise.js        # Enterprise report filters (75 lines)
│   ├── time_rules.js                # Time rule form bindings (160 lines)
│   ├── attendance_v2.js             # Attendance table enhancements (85 lines)
│   ├── attendance_extended.js       # Extended attendance view logic (95 lines)
│   ├── auth/                        # OAuth & visual assets (SVGs & login background)
│   └── known_faces/                 # Storage directory for registered face photos
├── scripts/                         # Operational & Migration Scripts
│   ├── init_db.py                   # Idempotent database table initializer (29 lines)
│   ├── migrate_auth.py              # Auth column migration & backfill (52 lines)
│   ├── migrate_hr.py                # HR schema migration script (46 lines)
│   ├── migrate.py                   # Legacy schema migration script (18 lines)
│   ├── seed_students.py             # Mock student generator for testing (58 lines)
│   └── smoke_test.py                # Quick verification smoke test (36 lines)
├── tests/                           # Pytest Test Suites (76 passing tests)
│   ├── test_authentication.py       # Auth, Session, CSRF, OTP, RBAC tests (1180 lines)
│   ├── test_camera_source.py        # Camera URL normalization tests (42 lines)
│   ├── test_hr_database.py          # HR models, constraints, cascades (215 lines)
│   ├── test_hr_enhancements.py      # HR revisions, shifts, salary tests (140 lines)
│   ├── test_hr_sync.py              # Teacher-to-employee sync tests (125 lines)
│   └── test_teacher_module.py       # Teacher CRUD, biometrics, attendance (160 lines)
├── instance/                        # Runtime Database Instance
│   └── krishna_erp.db               # Active SQLite database file
├── app.py                           # Main application factory & core routes (1794 lines)
├── auth.py                          # Auth blueprint, Argon2id, CSRF, RBAC (981 lines)
├── attendance_service.py            # Single write path for live face attendance (309 lines)
├── config.py                        # Centralized configuration & environment loader (109 lines)
├── enterprise_query.py              # Unified KPI calculator & query engine (922 lines)
├── requirements.txt                 # Exact pinned dependencies (22 lines)
└── .env.example                     # Template environment variables (41 lines)
```
