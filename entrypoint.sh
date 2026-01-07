#!/bin/bash
# Unified NVR Entrypoint Script
# Starts the Flask app via Gunicorn (production WSGI server)

set -e

echo "=========================================="
echo "  Unified NVR - Starting..."
echo "=========================================="

# Optional: Run any pre-startup tasks here
# e.g., database migrations, config validation, etc.

# Start Gunicorn with:
# - 1 worker (sufficient for I/O-bound camera streaming)
# - 8 threads per worker (handles concurrent requests)
# - Bind to all interfaces on port 5000
# - 120s timeout (some camera operations are slow)
# - Access log to stdout for Docker logging
#
# NOTE: Using 1 worker is intentional - prevents duplicate:
#   - Stream starts
#   - MediaMTX publishers
#   - ONVIF connections
# Multiple workers would each run module-level init code.

exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --threads 8 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --enable-stdio-inheritance \
    "app:app"
