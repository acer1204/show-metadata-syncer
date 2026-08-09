# ─── Stage 1: build the React frontend ────────────────────────────────
FROM node:24-alpine AS frontend-build
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: Python runtime + static files ───────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# lxml 需要 libxml2 / libxslt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /build/dist ./static

# Persisted runtime settings live in /app/data; NFO output in /app/output
RUN mkdir -p /app/data /app/output
VOLUME ["/app/data", "/app/output"]

ENV CORS_ORIGINS=* \
    OUTPUT_DIR=/app/output \
    DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Taipei

EXPOSE 7711

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7711/api/health || exit 1

# 單一 worker：TASKS 與快取都是 in-memory
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7711", "--workers", "1"]
