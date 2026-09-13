# REST API CONTRACTS & ROUTE SPECIFICATION

## 1. Authentication & Session Endpoints (`auth.py` Blueprint: `auth`)

### `POST /signup`
* **Description**: Registers a new user account.
* **Request Body**: `{"name": "...", "email": "...", "password": "...", "department": "..."}`
* **Response**: `200 OK` (renders `verify_required.html`) or `400 Bad Request` `{"error": "..."}`
* **Security**: Password length >= 8, email uniqueness verified, CSRF token required.

### `POST /login`
* **Description**: Authenticates user credentials and issues a server-side session.
* **Request Body**: `{"email": "...", "password": "...", "remember": true}`
* **Response**: `302 Redirect` to `/dashboard` with session cookie, or `401 Unauthorized`.
* **Security**: Enforces `AUTH_MAX_LOGIN_ATTEMPTS` (5 attempts) and email verification check.

### `GET /verify-email/<token>`
* **Description**: Verifies email address via email verification token.
* **Response**: `302 Redirect` to `/login?verified=1` on success, or `400 Bad Request`.

### `POST /verify-code`
* **Description**: Verifies email address via 6-digit numeric OTP.
* **Request Body**: `{"code": "123456"}`
* **Response**: `302 Redirect` to `/login?verified=1` on success.
* **Security**: 10-minute TTL, max 5 failed attempts before code invalidation.

### `POST /resend-verification`
* **Description**: Re-sends verification code and link.
* **Security**: Rate limited by `AUTH_VERIFICATION_RESEND_COOLDOWN_SECONDS` (60s).

### `POST /logout`
* **Description**: Revokes active session in `auth_sessions` and clears session cookie.

---

## 2. Core Academic & Biometric API (`app.py`)

### `GET /api/students`
* **Query Params**: `class_name` (optional), `section` (optional), `q` (search query)
* **Response**: `200 OK` `[{"id": 1, "roll_no": "101", "name": "...", "class_name": "Class 5", ...}]`

### `POST /api/students`
* **Request Body**: `{"roll_no": "...", "name": "...", "class_name": "...", "section": "...", "mobile": "...", "email": "..."}`
* **Response**: `201 Created` `{"id": 1, ...}`

### `POST /api/students/<id>/photo`
* **Description**: Uploads 3-angle face images (Front, Left, Right) or legacy single photo for enrollment.
* **Form-Data**: `front` (file), `left` (file), `right` (file)
* **Response**: `200 OK` `{"ok": true, "encodings": ["front", "left", "right"], ...}`

### `GET /api/teachers`
* **Query Params**: `q` (search name, teacher_id, subject)
* **Response**: `200 OK` list of teacher records.

### `POST /api/teachers/<id>/photo`
* **Description**: 3-angle face enrollment for teachers, automatically refreshing live recognizers.

### `GET /api/teachers/<id>/profile`
* **Description**: Returns detailed teacher profile, registered face encodings, and monthly attendance statistics.

### `GET /api/cameras` & `POST /api/cameras`
* **Description**: List configured video feeds or add a new webcam/RTSP camera.

### `POST /api/cameras/<id>/start` & `POST /api/cameras/<id>/stop`
* **Description**: Start or stop live background face recognition on a specific camera feed.

### `GET /stream/<cid>`
* **Description**: Real-time MJPEG live stream with bounding boxes and name labels.

---

## 3. HR & Payroll API (`hr/routes.py` Prefix: `/api/hr`)

### `GET /api/hr/departments` & `POST /api/hr/departments`
* **Description**: Department management.

### `GET /api/hr/employees` & `POST /api/hr/employees`
* **Description**: Full employee CRUD.
* **Permission**: `employees.view` (GET), `employees.create` (POST).

### `POST /api/hr/sync-teachers`
* **Description**: Scans `teachers` table and provisions corresponding `employees` records idempotently.

### `POST /api/hr/employees/<id>/salary-structure`
* **Description**: Configures new salary structure revision with effective dates.

### `GET /api/hr/leaves` & `PUT /api/hr/leaves/<id>/approve`
* **Description**: Leave request review and workflow management.

### `POST /api/hr/payroll-periods` & `POST /api/hr/payroll-periods/<id>/process`
* **Description**: Create and calculate monthly payroll runs based on attendance snapshots.

### `POST /api/hr/payroll-periods/<id>/finalize`
* **Description**: Locks payroll period and generates individual payslip records.

---

## 4. Enterprise Time & Rules API (`time_management/api.py` Prefix: `/api`)

### `GET /api/time-rules/<scope>` & `PUT /api/time-rules/<scope>`
* **Scopes**: `student` | `teacher`
* **Description**: Read and update shift timings, grace minutes, half-day limits, and overtime rules.

### `GET /api/holidays` & `POST /api/holidays`
* **Description**: Manage national and institutional holidays.

### `GET /api/attendance/extended` & `GET /api/reports/<rptype>`
* **Description**: Extended attendance filtering and CSV export generators.
