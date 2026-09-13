# INFRASTRUCTURE & DEPLOYMENT ARCHITECTURE

## 1. Verified Containerization (Docker) Status

### Docker Implementation
* **Status**: `[IMPLEMENTED & VERIFIED]`
* **Dockerfile**: `PRESENT` (Production-ready Python 3.12 slim with OpenCV & InsightFace runtime)
* **Docker Compose**: `PRESENT` (`docker-compose.yml` with persistent volume mappings)
* **.dockerignore**: `PRESENT` (Excludes caches, secrets, test artifacts, and local databases)
* **Containerization**: `IMPLEMENTED`
* **Docker-based Development**: `YES` (`docker compose up -d`)
* **Docker-based Production**: `YES`
* **Health Check Status**: `PASS` (`healthy` status verified via `/login` endpoint)

---

## 2. Docker Architecture & Files

### Files Created
1. `Dockerfile`:
   - Base Image: `python:3.12-slim`
   - System Libraries: `libgl1`, `libglib2.0-0`, `libgomp1` (for OpenCV & ONNX Runtime)
   - Dependency Strategy: Prebuilt manylinux wheels via `pip install --no-cache-dir -r requirements.txt`
   - Data Directories: `/app/instance`, `/app/static/known_faces`, `/root/.insightface/models`
   - Exposed Port: `5000`
   - Healthcheck: `HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; resp = urllib.request.urlopen('http://localhost:5000/login'); exit(0 if resp.getcode() == 200 else 1)" || exit 1`
2. `docker-compose.yml`:
   - Service: `attendance-os`
   - Host Port Mapping: `${PORT:-5001}:5000` (Avoids collision with macOS AirPlay receiver on port 5000)
   - Volumes:
     - `attendance_instance:/app/instance` (Persistent SQLite DB storage)
     - `attendance_faces:/app/static/known_faces` (Persistent registered face images)
     - `insightface_models:/root/.insightface/models` (Persistent ONNX model weight cache)
   - Environment: Dynamic passthrough with safe development defaults.
3. `.dockerignore`:
   - Excludes Python `__pycache__`, `.git`, `.env`, `instance/*.db`, `.ai/`, and local caches.

---

## 3. Build, Run & Health Check Commands

### Build Image
```bash
docker build -t attendance-os:latest .
```

### Run with Docker Compose
```bash
docker compose up -d
```

### Check Logs & Status
```bash
docker logs -f attendance-os
docker ps --filter name=attendance-os
```

### Stop Service
```bash
docker compose down
```

---

## 4. Environment Variables Specification

| Variable | Default / Example | Purpose |
| :--- | :--- | :--- |
| `PORT` | `5001` (Host) -> `5000` (Container) | Web service listening port |
| `FLASK_SECRET` | `attendance-os-production-secret-change-me` | Session encryption secret |
| `FLASK_DEBUG` | `false` | Production debug toggle |
| `DATABASE_URL` | `sqlite:///krishna_erp.db` | Active database connection URI |
| `CAMERA_SOURCE`| `0` or `rtsp://...` | Default camera device or RTSP URL |
| `ATTENDANCE_COOLDOWN_SECONDS` | `300` | Cooldown between face marks |
| `FACE_MATCH_THRESHOLD` | `0.35` | InsightFace cosine threshold |
| `USE_OLLAMA` | `false` | Local SLM toggle |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama daemon endpoint |
| `SMTP_HOST` | `smtp.gmail.com` | Outgoing email server |
| `SMTP_PORT` | `587` | Outgoing email port |

---

## 5. Storage & Persistence
* **Database Volume (`attendance_instance`)**: Retains `krishna_erp.db` across container lifecycle.
* **Biometrics Volume (`attendance_faces`)**: Retains multi-angle face photos.
* **Model Cache Volume (`insightface_models`)**: Prevents re-downloading the ~300MB `buffalo_l` model on restart.
