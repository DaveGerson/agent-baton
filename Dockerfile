# Agent Baton — multi-stage Docker image
#
# Usage:
#
#   # API server mode (default)
#   docker run -e ANTHROPIC_API_KEY=sk-... agent-baton
#
#   # Daemon mode (headless worker)
#   docker run -e BATON_MODE=daemon -e ANTHROPIC_API_KEY=sk-... agent-baton
#
# Important notes:
#   - WORKDIR must point to your project root (volume-mount it at /workspace)
#   - Volume-mount .claude/team-context/ to preserve execution state across restarts
#   - --foreground is REQUIRED in containers (double-fork daemonisation causes
#     PID-1 to exit, killing the container)
#   - Set BATON_LOG_LEVEL=DEBUG for verbose output; default is INFO

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools
RUN pip install --upgrade pip setuptools wheel

# Copy only the dependency manifests first so the layer is cached.
COPY pyproject.toml README.md ./
COPY agent_baton/__init__.py agent_baton/__init__.py

# Install with api + classify extras so the image is feature-complete.
# python-json-logger is included for structured JSON logging (B4).
RUN pip install --no-cache-dir ".[api,classify]" python-json-logger


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN groupadd --gid 1001 baton && \
    useradd --uid 1001 --gid baton --shell /bin/bash --create-home baton

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/baton /usr/local/bin/baton

# Project workspace (mounted at runtime)
WORKDIR /workspace
RUN chown baton:baton /workspace

# Default env vars — override at runtime
ENV BATON_LOG_LEVEL=INFO \
    BATON_MODE=api \
    BATON_HOST=0.0.0.0 \
    BATON_PORT=8741

USER baton

# Health check against the readiness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${BATON_PORT}/api/v1/health')" || exit 1

EXPOSE 8741

# Entrypoint selects mode via BATON_MODE env var:
#   api    → baton daemon start --foreground --serve (default)
#   daemon → baton daemon start --foreground (worker only, no API)
ENTRYPOINT ["/bin/sh", "-c", \
    "if [ \"$BATON_MODE\" = \"daemon\" ]; then \
        exec baton daemon start --foreground; \
     else \
        exec baton daemon start --foreground --serve --host $BATON_HOST --port $BATON_PORT; \
     fi"]
