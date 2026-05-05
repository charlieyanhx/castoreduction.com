# Castor research pipeline — production deploy image.
# Works for Render, Fly.io, Railway, Cloud Run, any container host.
FROM python:3.12-slim

# System deps for: selectolax (lxml/xml libs), playwright (chromium), psycopg2 (future), reportlab fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libxml2 \
    libxslt1.1 \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create the directories for sqlite + parquet caches (writable on a volume)
RUN mkdir -p /data && chmod 777 /data

# Point cache files to /data so they survive across deploys when mounted
ENV JOBS_DB_PATH=/data/jobs.sqlite \
    LLM_CACHE_PATH=/data/llm_cache.sqlite \
    HTTP_CACHE_PATH=/data/http_cache.sqlite \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8765

EXPOSE 8765

# Use shell form so $PORT expands at runtime (Render injects PORT env)
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-8765}
