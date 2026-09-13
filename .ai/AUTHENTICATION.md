# AUTHENTICATION & ACCESS CONTROL (IAM / RBAC)

## 1. Authentication Architecture
AttendanceOS implements a hardened, production-grade authentication subsystem located in `auth.py`.

```
                  +-----------------------------------+
                  |          User Request             |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |   Session Cookie Validation       |
                  |   (krishna_payroll_session)       |
                  +-----------------+-----------------+
                                    |
                       Valid Token Hash in DB?
                                   /                             No   /     \  Yes
                               /                          +------------v----+  +---v---------------+
                 |  Redirect Login |  | Load User to g.user|
                 +-----------------+  +---+---------------+
                                          |
                               +----------v----------+
                               |  Permission Guard   |
                               |  _require_permission|
                               +----------+----------+
                                          |
                               Has Required Permission?
                                         /                                   No   /     \  Yes
                                     /                                +------------v----+  +---v---------------+
                       |  403 Forbidden  |  | Execute Controller|
                       +-----------------+  +-------------------+
```

---

## 2. Password Security & Argon2id
* Passwords are never stored in plaintext.
* Hashed using `argon2-cffi` (Argon2id configuration).
* Password policy requires a minimum length of 8 characters.
* Lockout policy triggers after 5 failed login attempts (`failed_attempts >= 5`).

---

## 3. Server-Side Stateful Sessions
* Session identifiers are 32-byte cryptographically secure tokens.
* Stored in the database as SHA-256 hashes (`token_hash`) inside `auth_sessions`.
* Session lifetime defaults to 12 hours (`AUTH_SESSION_HOURS=12`).
* Each session maintains a dedicated `csrf_secret` for CSRF validation.

---

## 4. Two-Factor / Email Verification Flows
1. **Link Verification**:
   - URL token generated with 24-hour expiration (`auth_tokens.purpose = 'verify'`).
   - Clicking `/verify-email/<token>` verifies the account and marks token as used.
2. **6-Digit OTP Code**:
   - 6-digit numeric code hashed and stored with 10-minute expiration.
   - Entering code at `/verify-code` verifies the account immediately.
   - 5 failed attempts permanently invalidates the code.
3. **Debug Mode Fallback**:
   - If SMTP is not configured in local development (`DEBUG=True`), verification links and 6-digit codes are logged directly to the server terminal.

---

## 5. Role-Based Access Control (RBAC)

### 7 Roles:
1. `super_admin`: Full system access across all modules.
2. `hr_admin`: Academic, attendance, camera, HR, payroll, and user management.
3. `payroll_manager`: Payroll runs, salary structures, leave approvals, reports.
4. `finance_manager`: Payroll viewing and finalization, financial reporting.
5. `manager`: Departmental attendance, camera monitoring, employee leave approvals.
6. `employee`: Personal dashboard, student/attendance viewing, chat assistant.
7. `viewer`: Read-only viewing permissions across dashboards and reports.
