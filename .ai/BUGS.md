# KNOWN ISSUES, RISKS & DEPRECATION AUDIT

## 1. Discovered Deprecations & Warnings

### A. SQLAlchemy 2.0 `Query.get()` Legacy Warnings
* **Location**: `hr/employee_service.py` and `hr/payroll_service.py`
* **Warning**: `LegacyAPIWarning: The Query.get() method is considered legacy as of the 1.x series of SQLAlchemy... Use Session.get() instead.`
* **Severity**: `LOW` (Does not impact functionality in SQLAlchemy 2.0.x, but recommended for clean 2.1+ migration).
* **Remediation**: Refactor `Model.query.get(id)` to `db.session.get(Model, id)`.

### B. Authlib Jose Module Deprecation
* **Location**: `auth.py:17`
* **Warning**: `AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.`
* **Severity**: `LOW` (Compatible before Authlib 2.0.0).
* **Remediation**: Migrate to `joserfc` or standard Authlib OAuth client assertions.

---

## 2. Architectural Risks & Edge Cases
1. **Camera Process Concurrency**: Starting multiple camera streams on low-end hardware can cause CPU bottlenecking during InsightFace ONNX inference.
2. **SQLite Write Concurrency**: SQLite locks the database file on write transactions. High-concurrency environments should switch to PostgreSQL 17.
