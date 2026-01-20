"""
Gunicorn Configuration File
===========================
This file configures gunicorn for the NVR application.
Key feature: Custom access log filter to suppress high-frequency /api/snap/ requests.
"""

import re

# Server socket
bind = "0.0.0.0:5000"

# Worker configuration
# WORKERS: 1 (intentionally single worker)
#   - Prevents duplicate stream starts, MediaMTX publishers, ONVIF connections
#   - Multiple workers would each run module-level init code
#   - Shared state (active_streams, frame_buffers) must be in one process
workers = 1

# THREADS: 300
#   - Previous failure: 8 threads caused thread starvation
#   - 80 threads still caused health check timeouts when lock contention occurred
#   - Each MJPEG stream holds a thread for its entire duration
#   - 16 cameras x multiple clients can easily exhaust threads
#   - 300 threads provides generous headroom for API calls, HLS, WebRTC
#   - Server has 56 cores, 128GB RAM - can handle 300+ threads easily
threads = 300

# Timeout configuration
# TIMEOUT: 600s (10 minutes)
#   - MJPEG streams are long-lived connections
#   - Must not timeout during normal operation
#   - Health check keeps connection alive
timeout = 600
graceful_timeout = 30
keepalive = 5

# Logging
errorlog = "-"
accesslog = "-"
loglevel = "info"
capture_output = True
enable_stdio_inheritance = True

# Custom access log format (optional - use default for now)
# access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Paths to filter from access logs (high-frequency polling endpoints)
FILTERED_PATHS = [
    '/api/snap/',           # iOS polls every 1s per camera
    '/api/health',          # Health checks
    '/api/status',          # Status polling
]

# Compiled regex for efficient filtering
FILTERED_PATTERN = re.compile('|'.join(re.escape(p) for p in FILTERED_PATHS))


def pre_request(worker, req):
    """
    Called just before a worker processes the request.
    We use this to mark requests that should be filtered from access logs.
    """
    # Mark request for filtering if path matches
    req.filtered_from_access_log = bool(FILTERED_PATTERN.search(req.path))


def post_request(worker, req, environ, resp):
    """
    Called after a worker processes the request.
    We suppress the access log for filtered requests by not calling the default logger.
    """
    # Note: This hook doesn't directly control access logging.
    # We'll use a custom logger class instead.
    pass


class FilteredGunicornLogger:
    """
    Custom logger that filters out high-frequency requests from access logs.
    This prevents /api/snap/ and similar endpoints from flooding the logs.
    """

    def __init__(self, cfg):
        from gunicorn.glogging import Logger
        self._logger = Logger(cfg)
        # Copy all attributes from parent
        for attr in dir(self._logger):
            if not attr.startswith('_') and attr != 'access':
                setattr(self, attr, getattr(self._logger, attr))

    def access(self, resp, req, environ, request_time):
        """
        Override access log method to filter out noisy endpoints.
        """
        # Check if path matches any filtered patterns
        path = environ.get('PATH_INFO', '')
        if FILTERED_PATTERN.search(path):
            # Skip logging for filtered paths
            return

        # Log normally for all other requests
        self._logger.access(resp, req, environ, request_time)

    def __getattr__(self, name):
        """Delegate unknown attributes to the parent logger."""
        return getattr(self._logger, name)


# Use custom logger class
logger_class = FilteredGunicornLogger
