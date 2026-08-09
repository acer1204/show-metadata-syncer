# syntax=docker/dockerfile:1.6
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=7711 \
    TZ=Asia/Taipei

WORKDIR /app

# 系統相依（lxml 需要 libxml2 / libxslt，html5lib 不需要編譯器）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# 先裝 Python 相依，提高 layer cache 命中率
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案檔（output 由 volume 掛載）
COPY server.py tvdb_crawler.py index.html ./

# 預先建立輸出目錄
RUN mkdir -p /app/output

EXPOSE 7711

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/ >/dev/null || exit 1

# 用 uvicorn 跑 FastAPI app；單一 worker 因為 TASKS 是 in-memory dict
CMD ["sh", "-c", "exec uvicorn server:app --host ${HOST} --port ${PORT} --workers 1 --log-level info"]
