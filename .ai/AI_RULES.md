# AI DEVELOPMENT CONSTITUTION & GOVERNANCE RULES

> **MANDATORY INSTRUCTIONS FOR ALL AI CODING AGENTS**  
> This file governs any autonomous or interactive code modification in this repository.

---

## 1. SOURCE OF TRUTH PRINCIPLES
1. **Actual Code Outranks Assumptions**: The verified Python files, templates, and static scripts present in the repository are the only authoritative source of truth.
2. **`.ai/` Project Brain is the Context Foundation**: Consult the `.ai/` directory before proposing changes, generating new features, or diagnosing bugs.
3. **No Hallucinations**: Never assume external services, tables, endpoints, or containers exist unless verified by file inspection or database schema.

---

## 2. STRICT OPERATIONAL RULES

### A. Docker & Containerization Verification
* **Fact**: Docker is **NOT PRESENT** in this repository (no `Dockerfile`, `docker-compose.yml`, or `.dockerignore`).
* **Rule**: DO NOT write docker commands, create docker-compose files, or tell users to run `docker run` unless explicitly tasked with creating container infrastructure from scratch.

### B. Database Realities
* **Fact**: The system uses **SQLite** (`instance/krishna_erp.db`) as its active database, configured via `config.py`.
* **Rule**: Schema migrations must be additive. SQLite does not support advanced DDL like `ALTER TABLE ... DROP COLUMN` or native `JSONB`. Always maintain cross-compatibility using SQLAlchemy portable constructs (`JSON().with_variant(JSONB, "postgresql")`).
* **Rule**: Never run destructive SQL commands or drop tables.

### C. Authentication & Security
* **Fact**: Auth relies on **Argon2id** password hashing, **server-side stateful sessions** (`auth_sessions`), and cryptographic **CSRF tokens**.
* **Rule**: All state-mutating requests (POST, PUT, DELETE) must pass through CSRF validation (`_require_csrf` / `@require_auth`).
* **Rule**: OAuth providers (Google, Microsoft, Apple) require external API keys. If keys are missing, endpoints return `503 Service Unavailable` with clean user messaging.

### D. Python 3.14 & Timezone Integrity
* **Rule**: `datetime.utcnow()` is strictly forbidden (deprecated in Python 3.12+). Always use `datetime.now(timezone.utc)`.
* **Rule**: Use explicit UTC handling when serializing to/from SQLite via `UTCDateTime` custom TypeDecorator.

### E. Face Recognition Pipeline
* **Fact**: Biometrics is powered by **InsightFace** (`buffalo_l` model) and **ONNX Runtime** (CPU / Apple Silicon CoreML EP).
* **Rule**: Do not add `dlib` or `face_recognition` back into dependencies; they do not provide Python 3.14 prebuilt wheels.

---

## 3. CHANGE MANAGEMENT & CONTEXT PRESERVATION
1. **Preserve Compatibility**: Keep legacy tables (`students`, `teachers`, `attendance`, `cameras`, `unknown_faces`) functioning while extending enterprise tables (`student_attendance`, `teacher_attendance`, `employees`, etc.).
2. **Synchronize Documentation**: When endpoints, database tables, or business rules are modified, immediately update the relevant `.ai/*.md` documents.
3. **Audit Before Modifying**: Run `pytest` before making changes to establish a baseline, and run `pytest` after making changes to verify zero regressions.
