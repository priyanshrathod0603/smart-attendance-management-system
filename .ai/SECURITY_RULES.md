# SECURITY AUDIT & CODING SECURITY RULES

## 1. Security Architecture & Audit

### A. Implemented Security Controls `[VERIFIED CURRENT]`
1. **Argon2id Password Hashing**: State-of-the-art password hashing resisting GPU/ASIC attacks.
2. **Server-Side Stateful Sessions**: Session tokens are cryptographically generated (32 bytes) and hashed via SHA-256 in the database.
3. **CSRF Protection**: Every active session has a unique `csrf_secret`. Mutating endpoints require `X-CSRF-Token` header or form payload.
4. **SQL Injection Prevention**: SQLAlchemy parameterized queries and ORM abstractions used exclusively.
5. **Account Lockout Protection**: Automatic lockout after 5 consecutive failed login attempts.
6. **Rate-Limited Verification**: 6-digit OTP codes have a 10-minute expiry and invalidate after 5 failed attempts.
7. **Granular RBAC**: 38 discrete permissions strictly enforced across 7 system roles.

### B. Partial Security Controls `[PARTIAL]`
1. **OAuth Error Handling**: Gracefully returns 503 when credentials are unconfigured.
2. **Session Security Headers**: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`. `SESSION_COOKIE_SECURE` enabled when `DEBUG=False`.

### C. Missing Controls & Recommended Enhancements `[RECOMMENDED]`
1. **Field-Level Encryption**: Sensitive financial fields (`bank_account_number`, `pan_number`) in `employees` should be encrypted with AES-256 before database insertion.
2. **Global Rate Limiting**: Implement IP-based rate limiting (Flask-Limiter) across all public endpoints to mitigate DoS risks.
3. **HTTP Security Headers**: Add headers such as `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.

### D. Biometric & Hardware Device Security Controls `[VERIFIED CURRENT]`
1. **Zero Raw Biometric Storage**: AttendanceOS does not store raw fingerprint images or templates.
2. **Device Credential Masking**: Communication keys (`comm_key`) and webhook tokens are not returned in plaintext; webhook tokens are stored as SHA-256 hashes and never logged.
3. **Admin-Only Device Governance**: Strictly enforced RBAC (`devices.view`, `devices.manage`, `devices.enroll`, `devices.test`). Teachers, employees, and general staff are strictly forbidden from accessing device settings, credentials, or enrollment endpoints.
4. **Diagnostic Verification Isolation**: Diagnostic verification tests (`/api/devices/fingerprint/test-verify`) NEVER create or alter attendance records.
5. **Webhook Authentication and Idempotency**: Anonymous device events require a per-device bearer token; authenticated administrative requests retain session/RBAC/CSRF checks. Active device, school, explicit user mapping, payload, timestamp, and duplicate receipt are validated before attendance is written.
6. **Hardware Verification Boundary**: Team Office Z305 native protocol and credential/header capabilities are not verified. If native bearer headers are unsupported, require TLS reverse proxy/firewall source-IP allowlisting, header injection, direct-backend blocking, and proxy-level rate limiting.
7. **No Simulated Hardware Claims**: Mock enrollment and log results are marked `simulated`; TCP reachability does not imply protocol authentication or operational status.

### E. Teacher Self-Only Authorization & Data Scoping `[VERIFIED CURRENT]`
1. **Server-Side Identity Resolution**: Authenticated `g.user` is resolved to `Teacher` and `Employee` records exclusively on the server. Client parameters never dictate identity.
2. **Identity Spoofing Interception**: Any client request containing `teacher_id`, `employee_id`, `user_id`, or `school_id` differing from the authenticated user identity is rejected with `403 Forbidden`.
3. **IDOR Defense**: Resource detail endpoints (`/api/teacher/attendance/<id>`, `/api/teacher/leaves/<id>`, `/api/teacher/corrections/<id>`) verify record ownership and tenant permissions prior to returning data. Non-owned or cross-tenant records return `403 Forbidden` / `404 Not Found`.
4. **Administrative & Mutation Endpoint Lockdown**: Teachers are strictly denied access to mass data listings (`/api/users`, `/api/cameras`, `/api/students`, `/api/teachers`, `/api/hr/*`) and direct attendance mutations (`PUT /api/attendance/<id>`, `POST /api/enterprise/attendance`).

---

## 2. Security Rules for Future AI Coding Agents
* **NEVER trust client-supplied identity**: Never use query params or JSON body `teacher_id`/`employee_id` to filter SQL queries for teachers without strict authorization matching against `g.user`.
* **NEVER store raw biometric data**: Never extract or store fingerprint images or templates outside the physical hardware terminal.
* **NEVER log secrets or sensitive data**: Do not log full passwords, OTP codes (in production), bank account numbers, PANs, or raw biometric signatures.
* **ALWAYS validate CSRF**: Never disable CSRF checks on mutating endpoints.
* **Enforce Permission Checks**: When adding new routes, declare the corresponding permission in `ROLE_PERMISSIONS` and decorate with `@require_auth`.
* **Sanitize File Uploads**: Always use `secure_filename()` when storing student/teacher face photos in `static/known_faces/`.
* **Unique Identity Mapping**: Prevent duplicate device user ID assignments across teachers.
