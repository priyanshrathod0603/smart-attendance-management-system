# TESTING INFRASTRUCTURE & VERIFICATION SUITE

## 1. Test Overview & Execution
* **Framework**: `pytest 9.1.1`
* **Execution Command**: `pytest -v`
* **Current Test Count**: 76 passing tests (100% pass rate)

---

## 2. Test Suites Breakdown

### 1. `tests/test_authentication.py` (37 Tests)
* Verifies Argon2id password hashing and minimum length policies.
* Tests 12-hour session issuance, database persistence, and revocation.
* Validates CSRF token protection on mutating endpoints.
* Tests 6-digit numeric OTP code expiration, attempt limiting (5 failed attempts), and link verification.
* Tests RBAC permission boundaries across roles.

### 2. `tests/test_camera_source.py` (6 Tests)
* Tests camera source coercion and normalization (webcam indices, RTSP URLs, IP webcam HTTP URLs).

### 3. `tests/test_hr_database.py` (6 Tests)
* Tests Department and Designation CRUD.
* Tests Employee-to-Teacher foreign key mapping and integrity.
* Tests Salary Structure date-bound revisions.
* Tests Leave Requests and balance reconciliation.
* Tests Loan creation and repayment constraints.
* Tests Payroll run generation and attendance snapshot immutability.

### 4. `tests/test_hr_enhancements.py` (3 Tests)
* Tests Employee creation with initial salary structure.
* Tests salary history tracking across promotions.
* Tests Shift management CRUD.

### 5. `tests/test_hr_sync.py` (3 Tests)
* Tests automatic synchronization of Teachers into Employee records.
* Verifies idempotency of sync operations.
* Verifies preservation of manually updated HR fields.

### 6. `tests/test_teacher_module.py` (3 Tests)
* Tests Teacher CRUD and profile metadata endpoints.
* Tests Teacher biometric enrollment and live attendance recording.
* Tests Teacher dashboard summary and report generators.
