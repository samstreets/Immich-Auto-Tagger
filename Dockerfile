# ---------------------------------------------------------------------------
# Immich Auto-Tagger — Dockerfile
# ---------------------------------------------------------------------------
FROM python:3.14.4-slim

LABEL maintainer="you@example.com"
LABEL description="Automatically tags Immich assets with people, location and date hierarchies."

# ---------- OS dependencies -------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python setup ----------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Application files -----------------------------------------------
COPY main.py .
COPY immich_api.py .
COPY tagger.py .
COPY scheduler.py .

# ---------- State directory --------------------------------------------------
# Persists last_run.json across container restarts when a volume is mounted here
RUN mkdir -p /app/state
VOLUME ["/app/state"]

# ---------- Runtime ---------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Required — must be set via environment / docker-compose
# ENV IMMICH_URL=http://immich-server:2283
# ENV IMMICH_API_KEY=your_api_key_here

# Optional tunables (shown with defaults)
# ENV SCAN_INTERVAL_MINUTES=15
# ENV SCAN_PAGE_SIZE=100
# ENV INITIAL_SCAN_DAYS=0
# ENV STATE_FILE=/app/state/last_run.json
# ENV IMMICH_TIMEOUT=30

CMD ["python", "main.py"]
