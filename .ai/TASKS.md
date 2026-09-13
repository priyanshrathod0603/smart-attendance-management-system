# ACTIONABLE ENGINEERING TASKS & ROADMAP

## 1. Backlog Categorization

### `COMPLETED`
- [x] Integrate InsightFace ONNX biometric pipeline with Python 3.14 support.
- [x] Implement 3-angle face enrollment (Front, Left, Right) for Students and Teachers.
- [x] Build comprehensive HR subsystem (Departments, Designations, Employees, Shifts).
- [x] Build multi-component payroll calculation engine with payslip generation.
- [x] Implement hardened authentication with Argon2id, server sessions, CSRF, and 6-digit OTP.
- [x] Establish unified query calculation engine in `enterprise_query.py`.
- [x] Pass all 76 unit and integration test suites.

### `TODO` (High Priority)
- [ ] Create official `Dockerfile` and `docker-compose.yml` for containerized production deployments.
- [ ] Initialize Alembic migration environment to replace procedural `ensure_schema_compat()`.
- [ ] Implement field-level encryption for employee banking data (`bank_account_number`, `pan_number`).
- [ ] Add global IP-based rate limiting on public endpoints (`/login`, `/signup`, `/api/ask`).

### `PLANNED` (Medium / Low Priority)
- [ ] Configure live OAuth credentials for Google, Microsoft, and Apple integrations.
- [ ] Add PDF export generator for generated payslips.
- [ ] Implement WebSocket live notification channel for real-time attendance alerts.
- [ ] Set up GitHub Actions CI/CD test automation pipeline.
