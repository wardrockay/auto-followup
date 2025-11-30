# Use Python 3.11 slim image
FROM python:3.11-slim as builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements/base.txt requirements/
RUN pip install --no-cache-dir -r requirements/base.txt

# Production image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080 \
    ENVIRONMENT=production

# Create non-root user
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# Set work directory
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ src/
COPY pyproject.toml .

# Install the application
RUN pip install --no-cache-dir -e . --no-deps

# Change ownership to non-root user
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run the application with gunicorn
# - workers=1: Cloud Run scales via container instances, not workers
# - threads=8: Handle concurrent requests within a single worker
# - timeout=0: Disable timeout, Cloud Run handles request timeouts
# - graceful-timeout=30: Allow 30s for graceful shutdown on SIGTERM
# - keep-alive=60: Match Cloud Run's idle connection timeout
CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "--graceful-timeout", "30", "--keep-alive", "60", "--access-logfile", "-", "--error-logfile", "-", "auto_followup.app:app"]
