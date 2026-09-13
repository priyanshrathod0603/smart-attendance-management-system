# TECHNOLOGY STACK & RUNTIME ARCHITECTURE

## 1. Core Runtime & Language
* **Language**: Python 3.14.0+ (Tested on macOS Apple Silicon `arm64` and Linux `x86_64`)
* **Framework**: Flask 3.0+ (WSGI Application)
* **Application Factory**: `create_app()` pattern in `app.py`
* **Concurrency**: Multithreaded request handling (`threaded=True`) + Dedicated OpenCV capture threads per camera source

---

## 2. Database & Data Access Layer
* **Primary Database (Default)**: SQLite (`instance/krishna_erp.db`)
* **Production Database Compatibility**: PostgreSQL 17 (via `psycopg` v3 driver)
* **ORM**: SQLAlchemy 2.0.36+ / Flask-SQLAlchemy 3.1+
* **Dialect Handling**: Portable JSON columns (`JSON().with_variant(JSONB, "postgresql")`) supporting SQLite JSON functions and PostgreSQL JSONB.
* **Connection Pooling**: `pool_pre_ping=True`, `pool_recycle=1800`

---

## 3. Computer Vision & Machine Learning
* **Face Recognition Toolkit**: `insightface>=0.7.3`
* **Model Pack**: `buffalo_l` (512-dimensional normalized embeddings)
* **Inference Engine**: `onnxruntime>=1.19.0`
  - Execution Providers: `CPUExecutionProvider` / `CoreMLExecutionProvider` (Apple Silicon)
* **Video Ingestion & Image Processing**: `opencv-python>=4.10.0.84`, `Pillow>=11.0.0`, `numpy>=2.1.0`
* **Matching Algorithm**: Cosine similarity (Dot product of L2-normalized vectors; threshold = 0.35)

---

## 4. Authentication, Cryptography & Security
* **Password Hashing**: `argon2-cffi>=23.1.0` (Argon2id algorithm)
* **Session Management**: Server-side stateful tokens hashed with SHA-256 (`auth_sessions` table)
* **CSRF Protection**: Cryptographic per-session CSRF secrets (`csrf_secret`) verified on state-changing requests
* **OAuth 2.0 / OpenID Connect**: `Authlib>=1.3.1` (Google, Microsoft, Apple integration scaffolds)
* **Email Delivery**: Python standard library `smtplib` + `email.mime` (STARTTLS on port 587)

---

## 5. Small Language Model (SLM) & NLP
* **Deterministic Rule Engine**: Custom regex intent classification (`slm/nlp_engine.py`)
* **Optional LLM/SLM Bridge**: Ollama REST API integration (`requests>=2.32.3`)
* **Supported Local Models**: `phi3:mini`, `llama3.2:1b` (via `http://localhost:11434`)

---

## 6. Frontend Architecture & Design System
* **Templating Engine**: Jinja2 (Server-side rendering)
* **Client-Side Scripting**: Modular Vanilla JavaScript (ES6+), Fetch API
* **Styling**: Custom CSS (`static/styles.css`, ~90KB) utilizing CSS Variables, Modern Flexbox & CSS Grid
* **Assets & Icons**: Custom inline and SVG assets (Google, Microsoft, Apple icons)

---

## 7. Testing & Quality Assurance
* **Test Runner**: `pytest>=9.1.1`
* **Test Suites**:
  - `tests/test_authentication.py`
  - `tests/test_camera_source.py`
  - `tests/test_hr_database.py`
  - `tests/test_hr_enhancements.py`
  - `tests/test_hr_sync.py`
  - `tests/test_teacher_module.py`
* **Test Execution Speed**: ~76 passed tests in ~130 seconds

---

## 8. Dependencies Breakdown (`requirements.txt`)

```text
Flask>=3.0,<4.0
Flask-SQLAlchemy>=3.1,<4.0
SQLAlchemy>=2.0.36,<3.0
psycopg[binary]>=3.2.3
opencv-python>=4.10.0.84
numpy>=2.1.0
Pillow>=11.0.0
python-dotenv>=1.0.1
requests>=2.32.3
argon2-cffi>=23.1.0
Authlib>=1.3.1
insightface>=0.7.3
onnxruntime>=1.19.0
```
