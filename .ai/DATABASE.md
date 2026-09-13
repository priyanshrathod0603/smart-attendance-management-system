# DATABASE SCHEMA & ORM MODELS INVENTORY

## 1. Database Configuration
* **Default Engine**: SQLite (`instance/krishna_erp.db`)
* **Config Setting**: `DATABASE_URL=sqlite:///krishna_erp.db` in `config.py`
* **Dialect Handling**: Portable JSON columns (`JSON().with_variant(JSONB, "postgresql")`)
* **UTC Timestamp Handling**: `UTCDateTime` custom SQLAlchemy TypeDecorator

---

## 2. Table Inventory (28 Tables Total)

### Group A: Core Biometrics & Legacy Tables (`database/models.py`)

#### 1. `students`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Unique student identifier |
| `roll_no` | VARCHAR(50) | UNIQUE, NOT NULL, INDEX | Roll number / Student Code |
| `name` | VARCHAR(120) | NOT NULL | Full name |
| `class_name`| VARCHAR(80) | INDEX | Enrolled class (e.g. "Class 5") |
| `section` | VARCHAR(40) | INDEX | Section (e.g. "A") |
| `mobile` | VARCHAR(20) | NULLABLE | Contact phone |
| `email` | VARCHAR(120) | NULLABLE | Contact email |
| `photo_path`| VARCHAR(255)| NULLABLE | Path to primary face image |
| `encoding` | JSON/JSONB | NULLABLE | 512-d InsightFace embedding |
| `extra_encodings` | JSON/JSONB | NULLABLE | Multi-angle encodings (left, right) |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status flag |
| `created_at`| TIMESTAMPTZ | NOT NULL, DEFAULT now() | Creation timestamp |

#### 2. `teachers`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Unique teacher identifier |
| `teacher_id`| VARCHAR(50) | UNIQUE, NOT NULL, INDEX | Teacher employee code |
| `name` | VARCHAR(120) | NOT NULL | Full name |
| `subject` | VARCHAR(120) | NULLABLE | Subject taught |
| `department`| VARCHAR(120)| INDEX | Academic department |
| `designation`| VARCHAR(120)| INDEX | Academic designation |
| `assigned_classes`| VARCHAR(255)| NULLABLE | Comma-separated classes |
| `mobile` | VARCHAR(20) | NULLABLE | Phone number |
| `email` | VARCHAR(120) | NULLABLE | Email address |
| `photo_path`| VARCHAR(255)| NULLABLE | Path to primary image |
| `encoding` | JSON/JSONB | NULLABLE | 512-d InsightFace embedding |
| `extra_encodings` | JSON/JSONB | NULLABLE | Multi-angle encodings |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |
| `created_at`| TIMESTAMPTZ | NOT NULL, DEFAULT now() | Creation timestamp |

#### 3. `cameras`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Camera ID |
| `name` | VARCHAR(120) | NOT NULL | Display name |
| `source` | VARCHAR(255) | NOT NULL | Device index (0) or RTSP/HTTP URL |
| `location` | VARCHAR(120) | NULLABLE | Physical location |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Stream status |
| `created_at`| TIMESTAMPTZ | NOT NULL, DEFAULT now() | Created timestamp |

#### 4. `attendance` (Legacy compatibility table)
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Record ID |
| `student_id`| INTEGER | FK(`students.id`), NOT NULL, INDEX | Student reference |
| `camera_id` | INTEGER | FK(`cameras.id`), NULLABLE | Camera reference |
| `timestamp` | TIMESTAMPTZ | NOT NULL, INDEX, DEFAULT now() | Timestamp of detection |
| `confidence`| FLOAT | NULLABLE | Cosine similarity score |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'present' | Status string |
| `event_type`| VARCHAR(8) | NOT NULL, DEFAULT 'in' | 'in' or 'out' |
| `session_id`| INTEGER | INDEX, NULLABLE | Linked session ID |
| `manual_edit`| BOOLEAN | NOT NULL, DEFAULT FALSE | Manual edit flag |
| `edit_reason`| VARCHAR(255)| NULLABLE | Reason for manual edit |

#### 5. `unknown_faces`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Unknown face ID |
| `camera_id` | INTEGER | FK(`cameras.id`), NULLABLE | Camera reference |
| `timestamp` | TIMESTAMPTZ | NOT NULL, INDEX, DEFAULT now() | Detected timestamp |
| `confidence`| FLOAT | NULLABLE | Detection score |
| `snapshot_path`| VARCHAR(255)| NULLABLE | Snapshot file path |
| `image_path`| VARCHAR(255)| NULLABLE | Additional image path |
| `location` | VARCHAR(120) | NULLABLE | Location string |
| `alerted` | BOOLEAN | NOT NULL, DEFAULT FALSE | Alert notification flag |

---

### Group B: Authentication & Session Models (`database/auth_models.py`)

#### 6. `auth_users`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | User ID |
| `email` | VARCHAR(254) | UNIQUE, NOT NULL, INDEX | User email |
| `password_hash`| VARCHAR(255)| NULLABLE | Argon2id hash string |
| `name` | VARCHAR(120) | NOT NULL | User full name |
| `role` | VARCHAR(32) | NOT NULL, DEFAULT 'employee' | User role |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Account active flag |
| `department`| VARCHAR(120)| NULLABLE | Department name |
| `avatar_url`| VARCHAR(500)| NULLABLE | Profile avatar image |
| `email_verified_at`| UTCDateTime | NULLABLE | Verification timestamp |
| `failed_attempts`| INTEGER | NOT NULL, DEFAULT 0 | Failed login counter |
| `locked_until`| UTCDateTime | NULLABLE | Account lockout deadline |
| `last_login_at`| UTCDateTime | NULLABLE | Last login timestamp |
| `created_at`| UTCDateTime | NOT NULL, DEFAULT utcnow | Account creation time |

#### 7. `auth_sessions`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Session record ID |
| `token_hash`| VARCHAR(64) | UNIQUE, NOT NULL, INDEX | SHA-256 of session token |
| `user_id` | INTEGER | FK(`auth_users.id`), NOT NULL, INDEX | Authenticated user |
| `device` | VARCHAR(255)| NULLABLE | User-Agent description |
| `ip_address`| VARCHAR(64) | NULLABLE | Client IP address |
| `csrf_secret`| VARCHAR(64) | NOT NULL | Per-session CSRF token |
| `expires_at`| UTCDateTime | NOT NULL | Session expiry timestamp |
| `last_seen_at`| UTCDateTime| NOT NULL, DEFAULT utcnow | Last activity timestamp |
| `revoked_at`| UTCDateTime | NULLABLE | Revocation timestamp |
| `created_at`| UTCDateTime | NOT NULL, DEFAULT utcnow | Session issue time |

#### 8. `auth_tokens`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Token ID |
| `token_hash`| VARCHAR(64) | UNIQUE, NOT NULL, INDEX | SHA-256 of raw URL token |
| `user_id` | INTEGER | FK(`auth_users.id`), NOT NULL, INDEX | User reference |
| `purpose` | VARCHAR(32) | NOT NULL, INDEX | 'verify' or 'reset' |
| `expires_at`| UTCDateTime | NOT NULL | Expiry time |
| `used_at` | UTCDateTime | NULLABLE | Timestamp when consumed |
| `verification_code_hash` | VARCHAR(64) | NULLABLE, INDEX | SHA-256 of 6-digit OTP |
| `verification_code_expires_at`| UTCDateTime| NULLABLE | OTP expiry (10 mins) |
| `verification_attempts` | INTEGER | NOT NULL, DEFAULT 0 | OTP attempt counter |
| `verification_code_used_at` | UTCDateTime| NULLABLE | OTP consumption time |

#### 9. `auth_oauth_identities`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Identity ID |
| `provider` | VARCHAR(32) | NOT NULL | 'google', 'apple', 'microsoft' |
| `subject` | VARCHAR(255)| NOT NULL | Provider subject ID |
| `user_id` | INTEGER | FK(`auth_users.id`), NOT NULL, INDEX | User reference |
| *Constraint* | Unique | `UNIQUE(provider, subject)` | Unique provider identity |

#### 10. `auth_login_events`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Event ID |
| `user_id` | INTEGER | FK(`auth_users.id`), NULLABLE, INDEX | User reference |
| `email` | VARCHAR(254)| NOT NULL | Attempted email |
| `event` | VARCHAR(32) | NOT NULL | 'login_success', 'login_failed' |
| `ip_address`| VARCHAR(64) | NULLABLE | Client IP |
| `user_agent`| VARCHAR(500)| NULLABLE | Client browser agent |
| `created_at`| UTCDateTime | NOT NULL, DEFAULT utcnow | Event time |

---

### Group C: Enterprise Attendance Models (`database/enterprise_models.py`)

#### 11. `student_attendance`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | ID |
| `student_id`| INTEGER | FK(`students.id`), NOT NULL, INDEX | Student reference |
| `attendance_date`| DATE | NOT NULL, INDEX | Attendance calendar date |
| `in_time` | TIMESTAMPTZ | NULLABLE | First IN event timestamp |
| `out_time` | TIMESTAMPTZ | NULLABLE | Last OUT event timestamp |
| `working_minutes`| INTEGER | NOT NULL, DEFAULT 0 | Total duration in minutes |
| `working_seconds`| INTEGER | NOT NULL, DEFAULT 0 | Total duration in seconds |
| `break_minutes`| INTEGER | NOT NULL, DEFAULT 0 | Break time in minutes |
| `status` | VARCHAR(24) | NOT NULL, DEFAULT 'present' | Daily status |
| `is_late` | BOOLEAN | NOT NULL, DEFAULT FALSE | Late flag |
| `is_early_exit`| BOOLEAN | NOT NULL, DEFAULT FALSE | Early departure flag |
| `overtime_minutes`| INTEGER| NOT NULL, DEFAULT 0 | Extra minutes |
| `camera_id` | INTEGER | FK(`cameras.id`), NULLABLE | Camera reference |
| `confidence`| FLOAT | NULLABLE | Matching confidence |
| `created_at`| TIMESTAMPTZ | NOT NULL, DEFAULT now() | Created timestamp |
| *Constraint*| Unique | `UNIQUE(student_id, attendance_date)`| One record per student per day |

#### 12. `teacher_attendance`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | ID |
| `teacher_id`| INTEGER | FK(`teachers.id`), NOT NULL, INDEX | Teacher reference |
| `attendance_date`| DATE | NOT NULL, INDEX | Attendance calendar date |
| `in_time` | TIMESTAMPTZ | NULLABLE | First IN event timestamp |
| `out_time` | TIMESTAMPTZ | NULLABLE | Last OUT event timestamp |
| `working_minutes`| INTEGER | NOT NULL, DEFAULT 0 | Working minutes |
| `working_seconds`| INTEGER | NOT NULL, DEFAULT 0 | Working seconds |
| `break_minutes`| INTEGER | NOT NULL, DEFAULT 0 | Break minutes |
| `overtime_minutes`| INTEGER| NOT NULL, DEFAULT 0 | Overtime minutes |
| `status` | VARCHAR(24) | NOT NULL, DEFAULT 'present' | Daily status |
| `is_late` | BOOLEAN | NOT NULL, DEFAULT FALSE | Late flag |
| `is_early_exit`| BOOLEAN | NOT NULL, DEFAULT FALSE | Early exit flag |
| `camera_id` | INTEGER | FK(`cameras.id`), NULLABLE | Camera reference |
| `confidence`| FLOAT | NULLABLE | Matching score |
| `created_at`| TIMESTAMPTZ | NOT NULL, DEFAULT now() | Created timestamp |
| *Constraint*| Unique | `UNIQUE(teacher_id, attendance_date)`| One record per teacher per day |

#### 13. `attendance_events`
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY | Event ID |
| `attendance_type`| VARCHAR(12)| NOT NULL, INDEX | 'student' or 'teacher' |
| `attendance_id`| INTEGER | NOT NULL, INDEX | ID in student/teacher attendance |
| `event_type`| VARCHAR(12)| NOT NULL | 'in', 'out', 'break_start' |
| `event_time`| TIMESTAMPTZ | NOT NULL, INDEX, DEFAULT now() | Exact event timestamp |
| `camera_id` | INTEGER | FK(`cameras.id`), NULLABLE | Camera used |
| `confidence`| FLOAT | NULLABLE | Recognition confidence |

#### 14. `teacher_time_rules` & 15. `student_time_rules`
* Contains: `office_start` (08:00), `in_end_time` (09:30), `office_end` (17:00), `late_time` (08:30), `half_day_time` (09:30), `absent_after` (11:00), `out_detection_start` (16:00), `early_exit_time` (16:30), `overtime_start` (17:00), `min_working_minutes` (480), `overtime_enabled` (True).

#### 16. `holiday_calendar`
* Columns: `id`, `holiday_date`, `name`, `applies_to` ('all', 'student', 'teacher'). Unique on `(holiday_date, applies_to)`.

#### 17. `weekly_off`
* Columns: `id`, `applies_to`, `weekday` (0=Mon..6=Sun). Unique on `(applies_to, weekday)`.

#### 18. `attendance_settings`
* Key-value toggle settings (`id`, `key`, `enabled`).

#### 19. `attendance_logs` & 20. `attendance_corrections` & 21. `camera_sessions` & 22. `notifications`
* Audit and auxiliary tables supporting manual corrections and alerts.

---

### Group D: HR & Payroll Models (`hr/models.py`)

#### 23. `departments` & 24. `designations`
* Department and designation hierarchical taxonomy with foreign keys to head employees.

#### 25. `employees`
* Central employee record. Nullable FK `teacher_id` linked to `teachers.id`.
* Stores personal info, employment status (`full_time`, `contract`, `probation`), contact info, bank details, manager hierarchy.

#### 26. `employee_employment_history`
* Tracking joining, promotions, transfers, and exits.

#### 27. `salary_components`, `employee_salary_structures`, `employee_salary_components`
* Multi-component salary calculations (fixed / percentage, taxable, PF/ESI applicability).

#### 28. `leave_types`, `employee_leave_balances`, `leave_requests`
* Comprehensive leave approval engine.

#### 29. `shifts`, `employee_shifts`, `overtime_records`, `employee_bonuses`, `employee_loans`, `loan_repayments`, `reimbursements`
* Operational HR, advance loans, and shifts.

#### 30. `payroll_periods`, `payroll_records`, `payroll_attendance_summaries`, `payroll_earnings`, `payroll_deductions`, `payslips`, `hr_audit_logs`
* Complete immutable payroll calculation engine.
