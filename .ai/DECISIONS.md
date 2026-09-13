# ARCHITECTURE DECISION RECORDS (ADRs)

## ADR-001: SQLite as Default Primary Database
* **Status**: `ACCEPTED`
* **Context**: The application required a lightweight, zero-configuration local database for local development and edge CCTV attendance terminals.
* **Decision**: Adopt SQLite (`instance/krishna_erp.db`) via SQLAlchemy as the primary development database while preserving PostgreSQL 17 compatibility.
* **Impact**: Eliminates mandatory local PostgreSQL daemon setup; enables instant out-of-the-box execution.

---

## ADR-002: InsightFace + ONNX Runtime Over Dlib / face_recognition
* **Status**: `ACCEPTED`
* **Context**: `dlib` and `face_recognition` required complex C++ build chains (CMake) and lacked prebuilt wheels for Python 3.14 on macOS Apple Silicon (`arm64`).
* **Decision**: Replace `dlib` with `insightface` (`buffalo_l` model) utilizing `onnxruntime` (`CPUExecutionProvider` / `CoreMLExecutionProvider`).
* **Impact**: Zero CMake dependency; 512-d embeddings; superior accuracy and speed on modern hardware.

---

## ADR-003: Server-Side Stateful Sessions with SHA-256 Hashes
* **Status**: `ACCEPTED`
* **Context**: Stateless JWTs cannot be instantly revoked without distributed blacklists and expose session metadata to clients.
* **Decision**: Implement server-side stateful sessions (`auth_sessions` table) storing SHA-256 hashes of 32-byte cryptographic tokens.
* **Impact**: Instant server-side revocation, active device visibility, and per-session CSRF protection.

---

## ADR-004: Additive HR & Enterprise Schema Layering
* **Status**: `ACCEPTED`
* **Context**: Adding comprehensive HR and Payroll features risked breaking existing Student and Teacher attendance tables.
* **Decision**: Implement HR models as additive layers (`employees.teacher_id` as nullable FK) and enterprise attendance as parallel normalized tables (`student_attendance`, `teacher_attendance`).
* **Impact**: 100% backward compatibility with legacy endpoints while enabling enterprise ERP features.

---

## ADR-005: Immutable Payroll Attendance Snapshotting
* **Status**: `ACCEPTED`
* **Context**: Retroactive attendance edits or leave approvals could alter previously finalized historical payroll runs.
* **Decision**: Create `payroll_attendance_summaries` as an immutable snapshot table written only at payroll processing time.
* **Impact**: Guarantees financial and audit integrity for finalized pay periods.
