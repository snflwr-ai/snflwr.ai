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

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Production runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Copy pre-built Python packages from builder
COPY --from=builder /install /usr/local

# Apply Debian security patches (no extra runtime deps needed)
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

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
COPY --chown=snflwr:snflwr utils/ ./utils/
COPY --chown=snflwr:snflwr tasks/ ./tasks/
COPY --chown=snflwr:snflwr config.py .
COPY --chown=snflwr:snflwr resource_detection.py .

# Run as non-root
USER snflwr

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
