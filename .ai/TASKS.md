# ACTIONABLE ENGINEERING TASKS & ROADMAP

## 1. Backlog Categorization

### `COMPLETED`
- [x] Integrate InsightFace ONNX biometric pipeline with Python 3.14 support.
- [x] Implement 3-angle face enrollment (Front, Left, Right) for Students and Teachers.
- [x] Build comprehensive HR subsystem (Departments, Designations, Employees, Shifts).
- [x] Build multi-component payroll calculation engine with payslip generation.
- [x] Implement hardened authentication with Argon2id, server sessions, CSRF, and 6-digit OTP.
- [x] Establish unified query calculation engine in `enterprise_query.py`.
- [x] Build fingerprint device management page and mock adapter for software integration tests.
- [x] Add an adapter boundary and fail-closed Team Office Z305 TCP reachability stub (native protocol is not implemented).
- [x] Implement explicit Teacher <-> Device User ID mapping and normalized event processing.
- [x] Route validated fingerprint events through the common Attendance Engine and existing Time Rules.
- [x] Build safe Diagnostic Verification Test (without altering attendance records).
- [x] Pass automated fingerprint integration and regression test suites.
- [x] **Teacher Portal Phase 1**: Mobile-first responsive UI across Desktop, Tablet, and Mobile viewports.
- [x] **Teacher Portal Phase 2**: Teacher Role-Based Access Control (RBAC), authentication integration, endpoint authorization guards, and admin route isolation.
- [x] **Teacher Portal Phase 3**: Teacher Portal UI & APIs (Dashboard, Attendance History, Monthly Summary, Leaves, Correction Requests, Profile).
- [x] **Teacher Portal Phase 4**: Self-Only Backend Authorization, Query Parameter & Body Spoofing Rejection, IDOR-Protected Single Resource Queries, and Data Isolation Hardening.

### `TODO` (High Priority)
- [ ] Obtain exact Team Office Z305 variant/firmware, official SDK package, model support matrix, protocol docs, enrollment sample, and attendance-log API before implementing hardware operations.
- [ ] Verify device webhook/header or polling support and deploy HTTPS, source-IP allowlisting, and proxy-level rate limiting as required.
- [ ] Initialize Alembic migration environment to replace procedural `ensure_schema_compat()`.
- [ ] Implement field-level encryption for employee banking data (`bank_account_number`, `pan_number`).
- [ ] Add global IP-based rate limiting on public endpoints (`/login`, `/signup`, `/api/ask`).

### `PLANNED` (Teacher Portal Subsequent Phases)
- [ ] Teacher Portal Phase 5: Attendance Correction Workflow & Administrative Approval Engine.
- [ ] Teacher Portal Phase 6: Complete Audit Trail & Attendance Mutation Logging.
