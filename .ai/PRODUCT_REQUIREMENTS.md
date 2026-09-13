# PRODUCT REQUIREMENTS DOCUMENT (PRD)

## 1. Product Summary
AttendanceOS / Krishna Payroll is an integrated institutional management platform designed for educational institutions, academies, and enterprise organizations. It automates biometric attendance capture and connects daily presence data directly into HR records and payroll calculation.

---

## 2. Verified Functional Requirements

### Module 1: Biometric Attendance & Multi-Camera Monitoring
* **REQ-BIO-01**: System shall detect human faces from webcam or RTSP network streams using InsightFace ONNX models.
* **REQ-BIO-02**: System shall support 3-angle biometric enrollment (Front, Left, Right) for both Students and Teachers.
* **REQ-BIO-03**: System shall match face vectors against active registered embeddings using cosine similarity with a configurable threshold (default 0.35).
* **REQ-BIO-04**: System shall enforce a cooldown period (default 300 seconds) between successive attendance triggers for the same individual.
* **REQ-BIO-05**: System shall capture snapshots of unrecognized faces and log them in `unknown_faces` for review.

### Module 2: Academic Roster & Attendance
* **REQ-ACAD-01**: System shall maintain student rosters with unique roll numbers, full name, class, section, contact info, and face encodings.
* **REQ-ACAD-02**: System shall record daily IN and OUT timestamps per student.
* **REQ-ACAD-03**: System shall generate downloadable CSV attendance reports filterable by date range, class, and section.

### Module 3: Human Resources & Employee Lifecycle
* **REQ-HR-01**: System shall manage organizational hierarchy (Departments, Designations, Reporting Managers).
* **REQ-HR-02**: System shall synchronize academic teachers into the HR Employee directory without duplicating biometric embeddings.
* **REQ-HR-03**: System shall track employee employment history including join date, confirmation date, promotions, and status changes.
* **REQ-HR-04**: System shall manage leave types, yearly balances, and leave approval/rejection workflows.
* **REQ-HR-05**: System shall record employee salary advance loans and track monthly installment repayments.

### Module 4: Compensation & Payroll Calculation
* **REQ-PAY-01**: System shall support configurable salary components (earnings and deductions, fixed or percentage-based).
* **REQ-PAY-02**: System shall calculate monthly payroll runs based on working days, present days, unpaid leaves, late deductions, and overtime bonuses.
* **REQ-PAY-03**: System shall take an immutable attendance snapshot at the time of payroll calculation to preserve financial integrity.
* **REQ-PAY-04**: System shall generate individual payslip records linked to payroll line items.

### Module 5: Identity, Security & RBAC
* **REQ-SEC-01**: System shall secure user passwords using Argon2id hashing.
* **REQ-SEC-02**: System shall enforce email verification via 6-digit OTP code (10-min TTL) or secure email link (24-hr TTL).
* **REQ-SEC-03**: System shall manage server-side stateful sessions with 12-hour expiration and remote revocation capabilities.
* **REQ-SEC-04**: System shall enforce per-session CSRF validation on all state-mutating requests.
* **REQ-SEC-05**: System shall restrict administrative and operational features according to 7 discrete RBAC roles and 38 permissions.

### Module 6: Natural Language Attendance Query (SLM)
* **REQ-SLM-01**: System shall answer common attendance queries ("Who is present today?", "Find student John") offline using regex intent matching.
* **REQ-SLM-02**: System shall optionally delegate natural language chat queries to a local Ollama instance when configured.
