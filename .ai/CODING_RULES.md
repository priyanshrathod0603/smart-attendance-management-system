# CODEBASE CONVENTIONS & CODING RULES

## 1. Python 3.14 Standards
* **Type Annotations**: Use modern Python type hints (`str | None`, `list[dict]`, `tuple[int, ...]`).
* **Timezone Handling**: NEVER use `datetime.utcnow()`. ALWAYS use `datetime.now(timezone.utc)`.
* **String Formatting**: Prefer f-strings over `%` or `.format()`.

---

## 2. SQLAlchemy & Database Practices
* **Dialect Compatibility**: Ensure all column definitions function seamlessly in both SQLite and PostgreSQL.
* **Additive Schema Design**: When adding fields, make them nullable or provide explicit defaults.
* **Idempotent Migration Scripts**: Schema updates in `scripts/` must check for column existence before running `ALTER TABLE`.
* **Relationship Cascades**: Define explicit `cascade="all, delete-orphan"` where appropriate to maintain relational integrity.

---

## 3. Error Handling & API Conventions
* **JSON Responses**: Standard format `jsonify({"error": "message"}), status_code` for failures and `jsonify(result), 200/201` for success.
* **Clean HTTP Status Codes**:
  - `200 OK`: Successful retrieval or update.
  - `201 Created`: Successful resource creation.
  - `400 Bad Request`: Validation failure.
  - `401 Unauthorized`: Missing or invalid session.
  - `403 Forbidden`: Insufficient RBAC permission.
  - `404 Not Found`: Resource does not exist.
  - `503 Service Unavailable`: Unconfigured third-party service (e.g. OAuth).

---

## 4. Biometric & Threading Conventions
* **Locking**: Always acquire `self.lock` or `threading.Lock()` when accessing shared camera buffers or recognizer state.
* **Model Singletons**: Never re-instantiate InsightFace `FaceAnalysis` per request; use the process-level singleton in `recognition/encoder.py`.
* **Normalized Encodings**: Encodings must remain L2-normalized 512-d float vectors.
