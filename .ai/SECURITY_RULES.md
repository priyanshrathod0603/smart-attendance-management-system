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

---

## 2. Security Rules for Future AI Coding Agents
* **NEVER log secrets or sensitive data**: Do not log full passwords, OTP codes (in production), bank account numbers, or PANs.
* **ALWAYS validate CSRF**: Never disable CSRF checks on mutating endpoints.
* **Enforce Permission Checks**: When adding new routes, declare the corresponding permission in `ROLE_PERMISSIONS` and decorate with `@require_auth`.
* **Sanitize File Uploads**: Always use `secure_filename()` when storing student/teacher face photos in `static/known_faces/`.
