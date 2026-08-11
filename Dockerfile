# syntax=docker/dockerfile:1

# ---- builder: install dependencies into an isolated venv ----
FROM python:3.12-slim AS builder

# uv is pinned by digest-free tag; --frozen below makes the *dependency* set exact
# regardless, since it refuses to build if uv.lock disagrees with pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv

# UV_PROJECT_ENVIRONMENT is what redirects `uv sync` to an absolute path; VIRTUAL_ENV
# is not (uv sync manages the *project* env, which otherwise defaults to ./.venv and
# would leave the copied /opt/venv empty).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
# Only the lockfile and manifest, so this layer caches until dependencies change
# (app code lands in the runtime stage below and doesn't bust it).
COPY pyproject.toml uv.lock ./
# --no-dev: no pytest/ruff in the image. --no-install-project: the bot is a flat set
# of modules run as `python main.py`, not an installable package.
RUN uv sync --frozen --no-dev --no-install-project

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
# The glob covers only top-level modules — it does NOT recurse, so every package needs
# its own COPY. Missing one still builds a valid image; the bot then dies at startup with
# ModuleNotFoundError. The docker CI job runs `python main.py` for exactly this reason.
COPY *.py ./
COPY handlers/ ./handlers/

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# Container-level liveness check (stdlib only; slim has no curl).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTH_PORT','8080'), timeout=2).status==200 else 1)"

CMD ["python", "main.py"]
