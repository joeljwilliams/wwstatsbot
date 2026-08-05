# syntax=docker/dockerfile:1

# ---- builder: install dependencies into an isolated venv ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- runtime: minimal image with only the venv + app code ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HEALTH_PORT=8080

# Required at runtime (no default for the token — must be supplied):
#   BOT_TOKEN      Telegram bot token                 (required)
#   DATABASE_URL   postgresql://user:pass@host/db      (required)
#   SUPERUSER_ID   Telegram id allowed to manage admins (optional)
#   LOG_GROUP_ID   chat id for error reports           (optional)
#   HEALTH_PORT    port for /healthz & /readyz probes  (optional, default 8080)
#   LOG_LEVEL      DEBUG/INFO/WARNING/...               (optional, default INFO)
#   LOG_FORMAT     json | console                       (optional, default json off a TTY)
#   REDIS_URL      redis://host:port/db for persistence (optional; /allinfo buttons
#                                                        survive restarts when set)

# Copy the prebuilt virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY *.py ./

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# Container-level liveness check (stdlib only; slim has no curl).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTH_PORT','8080'), timeout=2).status==200 else 1)"

CMD ["python", "main.py"]
