# syntax=docker/dockerfile:1
# AttendanceOS / Krishna Payroll Production Dockerfile

FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=5000

# Install runtime system libraries required for OpenCV and InsightFace
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies from prebuilt wheels
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure instance, known_faces, and model directories exist
RUN mkdir -p /app/instance /app/static/known_faces /root/.insightface/models

# Expose web service port
EXPOSE 5000

# Health check using public unauthenticated login endpoint
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; resp = urllib.request.urlopen('http://localhost:5000/login'); exit(0 if resp.getcode() == 200 else 1)" || exit 1

# Start the application
CMD ["python", "app.py"]
