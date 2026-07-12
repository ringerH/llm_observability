# --- Stage 1: Build Stage ---
FROM python:3.10-slim AS builder

WORKDIR /build

# Install build dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Stage 2: Final Runtime Stage ---
FROM python:3.10-slim AS runner

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root group and user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /bin/bash appuser

# Copy application files and set ownership
COPY eval_harness/ eval_harness/
COPY tests/ tests/
COPY pyproject.toml .

RUN chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

# Default entrypoint runs the CLI runner module
ENTRYPOINT ["python", "-m", "eval_harness.runner"]
CMD ["--help"]
