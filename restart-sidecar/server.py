#!/usr/bin/env python3
"""
NVR Admin Console — lightweight host service that runs start.sh.

Installed as a systemd service by start.sh. Listens on localhost:$NVR_ADMIN_PORT.
The NVR container calls POST /restart to trigger a full start.sh cycle.

Endpoints:
    POST /restart          — trigger start.sh
    GET  /restart/status   — current restart status
    GET  /health           — service health check
"""

import os
import subprocess
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s [nvr-admin] %(message)s')
logger = logging.getLogger(__name__)

PROJECT_DIR = os.environ.get('NVR_PROJECT_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
START_SH = os.path.join(PROJECT_DIR, 'start.sh')
LOG_FILE = os.path.join(PROJECT_DIR, 'restart_from_app.log')
STATUS_FILE = '/tmp/nvr_start_status.json'

restart_in_progress = False
restart_lock = threading.Lock()
last_result = None


def run_start_sh():
    """Run start.sh. Streams output to log file and stdout."""
    global restart_in_progress, last_result

    try:
        logger.info(f"Running {START_SH} ...")
        with open(LOG_FILE, 'w') as log_f:
            proc = subprocess.Popen(
                ['bash', START_SH],
                cwd=PROJECT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            for line in proc.stdout:
                line_stripped = line.rstrip()
                log_f.write(line)
                log_f.flush()
                if line_stripped:
                    logger.info(line_stripped)
            proc.wait(timeout=600)

        if proc.returncode == 0:
            logger.info("start.sh completed successfully")
            last_result = {'status': 'success', 'error': None}
        else:
            status = _read_status_file()
            error_msg = status.get('error', f'exit code {proc.returncode}')
            step = status.get('step', 'unknown')
            logger.error(f"start.sh FAILED at step '{step}': {error_msg}")
            last_result = {'status': 'failed', 'step': step, 'error': error_msg}

    except subprocess.TimeoutExpired:
        logger.error("start.sh timed out after 600s")
        proc.kill()
        last_result = {'status': 'failed', 'step': 'timeout', 'error': 'start.sh hung for 600s'}
    except Exception as e:
        logger.error(f"start.sh failed: {e}")
        last_result = {'status': 'failed', 'step': 'exception', 'error': str(e)}
    finally:
        with restart_lock:
            restart_in_progress = False


def _read_status_file():
    """Read the status JSON that start.sh writes during execution."""
    try:
        with open(STATUS_FILE, 'r') as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError):
        return {'status': 'unknown', 'step': 'unknown', 'error': None}


class AdminHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        global restart_in_progress

        if self.path != '/restart':
            self._respond(404, {'error': 'not found'})
            return

        with restart_lock:
            if restart_in_progress:
                self._respond(409, {'error': 'restart already in progress'})
                return
            restart_in_progress = True

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        reason = data.get('reason', 'admin console restart')
        logger.info(f"Restart requested: {reason}")

        threading.Thread(target=run_start_sh, daemon=False, name="start-sh").start()
        self._respond(200, {'success': True, 'message': 'start.sh triggered'})

    def do_GET(self):
        if self.path == '/restart/status':
            if restart_in_progress:
                status = _read_status_file()
            elif last_result:
                status = last_result
            else:
                status = {'status': 'idle', 'step': None, 'error': None}
            self._respond(200, status)
            return

        if self.path == '/health':
            self._respond(200, {'status': 'ok', 'restart_in_progress': restart_in_progress})
            return

        self._respond(404, {'error': 'not found'})

    def _respond(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    port = int(os.environ.get('NVR_ADMIN_PORT', '9100'))
    server = HTTPServer(('0.0.0.0', port), AdminHandler)
    logger.info(f"NVR Admin Console listening on port {port}")
    server.serve_forever()
