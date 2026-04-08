# Multi-stage Dockerfile for MigrateEnv (HF Spaces / SQLite mode)

# ---- Stage 1: Python dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /build

# Build deps — psycopg2 kept for optional PostgreSQL use
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- Stage 2: Runtime image ----
FROM python:3.11-slim

WORKDIR /app

# Runtime libs — libpq5 kept for optional PostgreSQL; no postgresql-client needed for SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/
COPY db/ ./db/
COPY baseline.py .
COPY openenv.yaml .
COPY inference.py .

# Environment defaults
# Override DATABASE_URL to use PostgreSQL in self-hosted deployments
ENV DATABASE_URL=sqlite:///./northwind.db
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# HF Spaces requires port 7860
EXPOSE 7860

# Health check on port 7860
HEALTHCHECK --interval=15s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:7860/health').raise_for_status()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
