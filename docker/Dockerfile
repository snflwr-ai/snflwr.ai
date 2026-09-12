# snflwr.ai API - Production Image
# Contains the 4-layer safety pipeline, freemium logic, and educator engine
# Multi-stage build: compile deps in builder, run as non-root in production

# ---------------------------------------------------------------------------
# Stage 1: Build dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
# Install from the hash-pinned lockfile. --require-hashes refuses to
# install any package whose downloaded artifact does not match a hash in
# the file, blocking PyPI typosquats and compromised mirrors. The
# lockfile is generated with:
#   pip-compile --allow-unsafe --generate-hashes \
#       --output-file=requirements.lock requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install \
        --require-hashes -r requirements.lock

# ---------------------------------------------------------------------------
# Stage 2: Production runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Copy pre-built Python packages from builder
COPY --from=builder /install /usr/local

# Apply Debian security patches and install gosu (used by the entrypoint to
# drop privileges from root → snflwr after fixing volume ownership) and rclone
# (used by scripts/backup_database.py to push backups off-host when
# OFFHOST_BACKUP_ENABLED=true).
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends gosu rclone \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true \
    && rclone version  # smoke-test gosu + rclone binaries

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash snflwr

# Create necessary directories with correct ownership
RUN mkdir -p /app/storage/databases /app/storage/logs /app/logs \
    && chown -R snflwr:snflwr /app

# Copy application code
COPY --chown=snflwr:snflwr api/ ./api/
COPY --chown=snflwr:snflwr core/ ./core/
COPY --chown=snflwr:snflwr safety/ ./safety/
COPY --chown=snflwr:snflwr models/ ./models/
COPY --chown=snflwr:snflwr storage/ ./storage/
COPY --chown=snflwr:snflwr database/ ./database/
COPY --chown=snflwr:snflwr utils/ ./utils/
COPY --chown=snflwr:snflwr tasks/ ./tasks/
COPY --chown=snflwr:snflwr config.py .
COPY --chown=snflwr:snflwr resource_detection.py .
COPY --chown=snflwr:snflwr scripts/owui_connect.py ./scripts/owui_connect.py
# Executed inside the container by the backup-cron sidecar
# (docker/compose/docker-compose.home.yml: `python /app/scripts/backup_database.py
# backup`) and imported by tasks/background_tasks.py for the Celery schedule.
# It was previously referenced only in the rclone comment above and never
# copied, so the nightly backup ran a file that did not exist — and the
# sidecar's `|| echo` swallowed the error while its heartbeat kept the
# healthcheck green. Guarded now by tests/test_image_completeness.py.
COPY --chown=snflwr:snflwr scripts/backup_database.py ./scripts/backup_database.py

# Run by deploy.sh, inside the container, on every deploy:
#   preflight_db_key.py  before `up -d`, to prove the configured DB credentials
#                        actually open the target database while the healthy
#                        container is still serving. A deploy that pointed the
#                        home stack at .env instead of .env.home took production
#                        down on 2026-09-12; the app's own checks were correct
#                        but ran only at startup, after the container was gone.
#   postdeploy_smoke.py  after health, to prove the RUNNING container carries the
#                        behaviour that was just built.
# Both are useless if they are not in the image, and their absence would look
# like a failing deploy rather than a missing file. Guarded by
# tests/test_image_completeness.py.
COPY --chown=snflwr:snflwr scripts/preflight_db_key.py ./scripts/preflight_db_key.py
COPY --chown=snflwr:snflwr scripts/postdeploy_smoke.py ./scripts/postdeploy_smoke.py

# Entrypoint (runs as root, fixes volume ownership, then drops to snflwr)
COPY docker/entrypoint.sh /usr/local/bin/snflwr-entrypoint.sh
RUN chmod +x /usr/local/bin/snflwr-entrypoint.sh

# NOTE: intentionally NOT setting `USER snflwr` here. The entrypoint runs as
# root to chown any bind-mounted / named volumes (owned by root at mount time
# before dropping privileges via gosu. Running the app itself as root would
# defeat the point — gosu hands off to the unprivileged snflwr user.
ENTRYPOINT ["/usr/local/bin/snflwr-entrypoint.sh"]

# Set Python path
ENV PYTHONPATH=/app

# Expose API port
ENV API_PORT=8000
EXPOSE ${API_PORT}

# Health check (using Python instead of curl to avoid curl CVEs)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${API_PORT}/health')" || exit 1

# Run the API server (exec form for proper PID 1 signal forwarding)
CMD ["python", "-m", "api.server"]
