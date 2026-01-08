#!/bin/bash
# Unified NVR Entrypoint Script
# Starts the Flask app via Gunicorn (production WSGI server)

set -e

echo "=========================================="
echo "  Unified NVR - Starting with Gunicorn"
echo "=========================================="
echo "  Workers: 1 (single process for shared state)"
echo "  Threads: 80 (handles concurrent MJPEG streams)"
echo "  Timeout: 600s (long-lived streaming connections)"
echo "=========================================="

# Gunicorn configuration:
#
# WORKERS: 1 (intentionally single worker)
#   - Prevents duplicate stream starts, MediaMTX publishers, ONVIF connections
#   - Multiple workers would each run module-level init code
#   - Shared state (active_streams, frame_buffers) must be in one process
#
# THREADS: 80
#   - Previous failure: 8 threads caused thread starvation
#   - Each MJPEG stream holds a thread for its entire duration
#   - 16 cameras × 2 clients = 32 threads minimum
#   - 80 threads allows headroom for API calls, HLS, WebRTC
#   - Server has 56 cores, 128GB RAM - can easily handle 80 threads
#
# TIMEOUT: 600s (10 minutes)
#   - MJPEG streams are long-lived connections
#   - Must not timeout during normal operation
#   - Health check keeps connection alive
#
# WORKER_CLASS: gthread (default with --threads)
#   - Threading model works with FFmpeg subprocess calls
#   - gevent would NOT help - FFmpeg calls are blocking I/O

exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 1 \
    --threads 80 \
    --timeout 600 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --enable-stdio-inheritance \
    --log-level info \
    "app:app"
