#!/bin/bash
# Unified NVR Entrypoint Script
# Starts the Flask app via Gunicorn (production WSGI server)

set -e

echo "=========================================="
echo "  Unified NVR - Starting with Gunicorn"
echo "=========================================="
echo "  Workers: 1 (single process for shared state)"
echo "  Threads: 200 (handles concurrent streams)"
echo "  Timeout: 9900s (long-lived streaming connections)"
echo "=========================================="

# Gunicorn configuration:
#
# WORKERS: 1 (intentionally single worker)
#   - Prevents duplicate stream starts, MediaMTX publishers, ONVIF connections
#   - Multiple workers would each run module-level init code
#   - Shared state (active_streams, frame_buffers) must be in one process
#
# THREADS: 200
#   - Previous failure: 8 threads caused thread starvation
#   - 80 threads still caused health check timeouts when lock contention occurred
#   - Each MJPEG stream holds a thread for its entire duration
#   - 16 cameras × multiple clients can easily exhaust threads
#   - 200 threads provides generous headroom for API calls, HLS, WebRTC
#   - Server has 56 cores, 128GB RAM - can handle 200+ threads easily
#
# TIMEOUT: 600s (10 minutes)
#   - MJPEG streams are long-lived connections
#   - Must not timeout during normal operation
#   - Health check keeps connection alive
#
# WORKER_CLASS: gthread (default with --threads)
#   - Threading model works with FFmpeg subprocess calls
#   - gevent would NOT help - FFmpeg calls are blocking I/O

# Phone-home periodic heartbeat (non-blocking background, silent failure)
# Sends anonymous deployment fingerprint every 24h for license enforcement
if [[ -f /app/scripts/phone_home.sh ]]; then
    . /app/scripts/phone_home.sh
    nvr_phone_home_periodic &
fi

# Use gunicorn.conf.py for configuration including custom access log filtering
# This filters out high-frequency /api/snap/ requests from logs
exec gunicorn \
    --config gunicorn.conf.py \
    "app:app"
