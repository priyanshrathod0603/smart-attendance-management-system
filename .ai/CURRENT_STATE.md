# CURRENT SYSTEM STATE & MATURITY AUDIT

**Audit Date:** 2026-11-03  
**Active Database:** SQLite (`instance/krishna_erp.db`) / PostgreSQL 17  
**Test Suite Status:** See current pytest run; this document does not assert a global pass rate  
**Containerization:** `[IMPLEMENTED & HEALTHY]` (Dockerfile, docker-compose.yml, .dockerignore)  

---

## 1. Executive Status Matrix

| Subsystem | Maturity Status | Verified Implementation | Notes & Blockers |
| :--- | :--- | :--- | :--- |
| **Authentication & IAM** | `[VERIFIED CURRENT]` | Argon2id, Sessions, CSRF, 6-digit OTP, Token Verification, RBAC | Multi-tenant RBAC enforced across all routes. |
| **Face Biometrics** | `[VERIFIED CURRENT]` | InsightFace (`buffalo_l`), ONNX Runtime, 3-Angle Registration, Multi-camera stream | Fully operational; isolated from hardware failures. |
| **Fingerprint Integration** | `[PARTIAL / MOCK-TESTED]` | Network-only Team Office Z305 adapter stub, explicitly labeled simulator, protected normalized webhook, explicit teacher/device mapping | **Hardware Blocker:** TCP protocol, SDK, enrollment, and attendance-log/push behavior are not verified for the exact Z305 variant/firmware. TCP reachability and mock tests are not hardware verification. |
| **Academic Attendance** | `[VERIFIED CURRENT]` | Student CRUD, Class/Section filters, Daily IN/OUT, CSV Export | Legacy and Enterprise attendance paths harmonized. |
| **Teacher Attendance** | `[VERIFIED CURRENT]` | Teacher CRUD, Multi-modal (Face vs Fingerprint), Profile KPIs, Attendance History | Reuses common attendance engine and time rules. |
| **Teacher Portal & Security** | `[VERIFIED CURRENT]` | Phase 1 Responsive UI, Phase 2 RBAC, Phase 3 Portal UI/APIs, Phase 4 Self-Only Data Isolation & IDOR Defense | Server-side identity resolution from `g.user`, query param / payload spoofing rejection (403), IDOR defense on all teacher subresources. |
| **HR Subsystem** | `[VERIFIED CURRENT]` | Departments, Designations, Employees, Shifts, Employment History | Multi-tier organizational hierarchy operational. |
| **Leave Management** | `[VERIFIED CURRENT]` | Leave Types, Year-wise Balances, Approval/Rejection Workflow | Automated deduction integration with Payroll. |
| **Loans & Advances** | `[VERIFIED CURRENT]` | Loan Types, Installment Tracking, Payroll Deduction Hooks | Active/Completed lifecycle functional. |
| **Payroll Engine** | `[VERIFIED CURRENT]` | Salary Components, Formulas, Monthly Periods, Snapshotting, Payslips | Immutable snapshotting preserves historical runs. |
| **Time Rules & Policies**| `[VERIFIED CURRENT]` | Shift rules, Late thresholds, Half-day thresholds, Early-exit, Overtime | Separate rule configurations for Students & Teachers. |
| **SLM Assistant** | `[VERIFIED CURRENT]` | Rule-based regex intent parser + optional Ollama bridge | Fully functional offline; Ollama optional for chat. |
| **Containerization** | `[VERIFIED CURRENT]` | Production Dockerfile, docker-compose.yml, volume mounts, health checks | Verified build and healthy container run on port 5001. |

---

## 2. Fingerprint Device Integration Details
* **Admin Route**: `/admin/devices/fingerprint` (and `/devices/fingerprint`)
* **Adapter Abstraction**: `FingerprintDeviceAdapter` base class with:
  - `BioMaxIndigoAdapter`: legacy class name; currently a fail-closed Team Office Z305 TCP reachability stub. It does not implement a vendor protocol.
  - `MockFingerprintAdapter`: Development / automated testing simulator (`MOCK / DEVELOPMENT ONLY`).
* **Identity Bridge**:
  - Explicit string `Device User ID` -> teacher mapping; leading zeroes are preserved.
  - Device events require a unique mapping within the device's school and reject inactive or unenrolled teachers.
* **Attendance Engine Integration**:
  - Authenticated, normalized event -> registered device/school and employee mapping -> idempotency receipt -> common attendance engine (`record_recognition`).
  - No native Z305 event delivery or missed-log sync is claimed; vendor material is required.
* **Diagnostic Verification**:
  - Dedicated diagnostic test endpoint (`/api/devices/fingerprint/test-verify`) safely evaluates identity matching without writing attendance records.
* **Biometric Privacy**:
  - Raw fingerprint templates and images remain exclusively on physical device hardware storage. No templates stored in AttendanceOS.
