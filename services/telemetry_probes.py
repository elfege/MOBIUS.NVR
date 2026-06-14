"""
services/telemetry_probes.py — periodic per-layer telemetry probes.

Three probe families run inside a single background loop ticking every 60s.
Each probe checks ``telemetry_settings.is_enabled()`` first and bails cheaply
when the admin has the feature off (the dominant case at fresh install).

  D. MediaMTX path diff  — every 60s.  GET /v3/paths/list, compare publisher
                           + reader-count vs last snapshot, emit transition
                           rows for any path that changed.
  D. go2rtc stream diff  — every 60s.  GET /api/streams, same shape.
  F. Resource snapshot   — every 60s.  ffmpeg subprocess count + RSS sum,
                           gunicorn worker RSS, Docker conntrack count.
  E. RTSP ffprobe        — every 5 min. In-container ``ffprobe`` against each
                           camera's hub URL (mediamtx/go2rtc — NEVER direct
                           to the camera, per RULE 11 single-consumer policy).
                           Emit probe_pass / probe_fail rows.

The probes are diagnostic-only — they read state, they never modify cameras
or streams. Failures inside a probe are logged at DEBUG and never propagate.

Concurrency model: single dedicated background thread. Probes run sequentially
inside one tick — no risk of overlapping ffprobe storms. If RTSP probes ever
take longer than the 5-minute interval, we just delay the next batch by the
overshoot; we don't pile up.
"""

import logging
import os
import subprocess
import threading
import time
from typing import Dict, Optional

try:
    import psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except ImportError:
    # psutil is a soft dep — the resource snapshot probe degrades gracefully
    # when it's missing (still records conntrack + path counts, just no
    # ffmpeg/gunicorn RSS). To enable the full set: add psutil to
    # requirements.txt and rebuild the image.
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False

import requests

from services import telemetry_settings as ts
from services import telemetry_event_log as tel

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS  = 60          # base tick — D + F
RTSP_PROBE_EVERY_N_TICKS = 5          # → 5 min for E

MEDIAMTX_API_URL  = "http://nvr-packager:9997/v3/paths/list"
MEDIAMTX_API_AUTH = ("nvr-api", "")  # matches packager/mediamtx.yml authInternalUsers
GO2RTC_API_URL   = "http://nvr-go2rtc:1984/api/streams"
GO2RTC_API_URL_FALLBACK = "http://<LAN_IP>:1984/api/streams"

CONNTRACK_PATH = "/proc/sys/net/netfilter/nf_conntrack_count"
CONNTRACK_MAX_PATH = "/proc/sys/net/netfilter/nf_conntrack_max"

FFPROBE_TIMEOUT_SECONDS = 5

# Last-seen snapshot per (api, path_name) → {'publisher': bool, 'readers': int}
_mediamtx_last: Dict[str, dict] = {}
_go2rtc_last:   Dict[str, dict] = {}

_probe_thread: Optional[threading.Thread] = None
_probe_stop_event = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
#  Probe D — MediaMTX path-state diff
# ─────────────────────────────────────────────────────────────────────────────

def _probe_mediamtx_paths() -> None:
    """Fetch /v3/paths/list, diff against last snapshot, emit transitions."""
    try:
        r = requests.get(MEDIAMTX_API_URL, auth=MEDIAMTX_API_AUTH, timeout=4)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.debug(f"[telemetry] mediamtx paths probe failed: {e}")
        return

    items = data.get('items', []) if isinstance(data, dict) else []
    seen_now = set()

    for item in items:
        name = item.get('name')
        if not name:
            continue
        seen_now.add(name)
        publisher = bool(item.get('source')) if 'source' in item else bool(item.get('sourceReady', False))
        readers   = len(item.get('readers', []) or [])

        curr = {'publisher': publisher, 'readers': readers}
        last = _mediamtx_last.get(name)

        if last is None:
            # First-seen path — record as a discovery transition.
            tel.emit_transition(
                category='mediamtx_path',
                camera_id=name,
                from_value='unknown',
                to_value='present',
                extra={'publisher': publisher, 'readers': readers},
                severity='info',
            )
        else:
            if last.get('publisher') != curr['publisher'] or last.get('readers') != curr['readers']:
                tel.emit_transition(
                    category='mediamtx_path',
                    camera_id=name,
                    from_value=last,
                    to_value=curr,
                    severity='warning' if (last.get('publisher') and not curr['publisher']) else 'info',
                )
        _mediamtx_last[name] = curr

    # Paths that disappeared since the last poll → emit "gone" transition.
    for old_name in list(_mediamtx_last.keys()):
        if old_name not in seen_now:
            tel.emit_transition(
                category='mediamtx_path',
                camera_id=old_name,
                from_value=_mediamtx_last[old_name],
                to_value='absent',
                severity='warning',
            )
            _mediamtx_last.pop(old_name, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Probe D' — go2rtc stream-state diff
# ─────────────────────────────────────────────────────────────────────────────

def _probe_go2rtc_streams() -> None:
    """Fetch /api/streams, diff against last snapshot, emit transitions."""
    data = None
    for url in (GO2RTC_API_URL, GO2RTC_API_URL_FALLBACK):
        try:
            r = requests.get(url, timeout=4)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            logger.debug(f"[telemetry] go2rtc streams probe via {url} failed: {e}")
            continue
    if data is None:
        return

    seen_now = set()
    if isinstance(data, dict):
        for name, stream in data.items():
            seen_now.add(name)
            producers = stream.get('producers') or [] if isinstance(stream, dict) else []
            consumers = stream.get('consumers') or [] if isinstance(stream, dict) else []
            curr = {'producers': len(producers), 'consumers': len(consumers)}
            last = _go2rtc_last.get(name)
            if last is None:
                tel.emit_transition(
                    category='go2rtc_path',
                    camera_id=name,
                    from_value='unknown',
                    to_value='present',
                    extra=curr,
                    severity='info',
                )
            elif last != curr:
                tel.emit_transition(
                    category='go2rtc_path',
                    camera_id=name,
                    from_value=last,
                    to_value=curr,
                    severity='warning' if (last.get('producers', 0) > 0 and curr['producers'] == 0) else 'info',
                )
            _go2rtc_last[name] = curr

    for old_name in list(_go2rtc_last.keys()):
        if old_name not in seen_now:
            tel.emit_transition(
                category='go2rtc_path',
                camera_id=old_name,
                from_value=_go2rtc_last[old_name],
                to_value='absent',
                severity='warning',
            )
            _go2rtc_last.pop(old_name, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Probe F — resource snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _read_conntrack_count() -> Optional[int]:
    try:
        with open(CONNTRACK_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _read_conntrack_max() -> Optional[int]:
    try:
        with open(CONNTRACK_MAX_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _probe_resource_snapshot() -> None:
    """Sample ffmpeg/gunicorn/conntrack counters and emit a single snapshot row."""
    ffmpeg_count = 0
    ffmpeg_rss   = 0
    gunicorn_rss = []

    if _PSUTIL_AVAILABLE:
        try:
            for proc in psutil.process_iter(['name', 'memory_info']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if 'ffmpeg' in name:
                        ffmpeg_count += 1
                        ffmpeg_rss   += proc.info['memory_info'].rss if proc.info.get('memory_info') else 0
                    elif name in ('gunicorn',) or name.startswith('gunicorn'):
                        if proc.info.get('memory_info'):
                            gunicorn_rss.append(proc.info['memory_info'].rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.debug(f"[telemetry] resource snapshot psutil iter failed: {e}")

    payload = {
        'psutil_available':       _PSUTIL_AVAILABLE,
        'ffmpeg_count':           ffmpeg_count if _PSUTIL_AVAILABLE else None,
        'ffmpeg_total_rss_bytes': ffmpeg_rss   if _PSUTIL_AVAILABLE else None,
        'gunicorn_worker_count':  len(gunicorn_rss) if _PSUTIL_AVAILABLE else None,
        'gunicorn_total_rss_bytes': sum(gunicorn_rss) if _PSUTIL_AVAILABLE else None,
        'gunicorn_worker_rss_bytes': gunicorn_rss     if _PSUTIL_AVAILABLE else None,
        'mediamtx_path_count':    len(_mediamtx_last),
        'go2rtc_stream_count':    len(_go2rtc_last),
        'conntrack_count':        _read_conntrack_count(),
        'conntrack_max':          _read_conntrack_max(),
    }

    tel.emit_snapshot(category='resource_snapshot', payload=payload, severity='info')


# ─────────────────────────────────────────────────────────────────────────────
#  Probe E — RTSP ffprobe (every 5 min)
# ─────────────────────────────────────────────────────────────────────────────

def _list_cameras_with_hub_urls() -> list:
    """
    Return [(camera_id, hub_url), ...] for cameras to probe.

    Uses the runtime camera repository so per-camera streaming_hub is
    respected (mediamtx vs go2rtc). Cameras on native_mjpeg are skipped
    because there's no RTSP URL to probe — their hub is the vendor's
    MJPEG buffer, not an RTSP endpoint.
    """
    try:
        from routes import shared
        repo = getattr(shared, 'camera_repo', None)
        if repo is None:
            return []
        cameras = repo.list_cameras() or []
    except Exception:
        return []

    out = []
    for cam in cameras:
        serial = cam.get('serial') or cam.get('camera_id')
        if not serial:
            continue
        hub = (cam.get('streaming_hub') or 'mediamtx').lower()
        if hub == 'mediamtx':
            out.append((serial, f"rtsp://nvr-packager:8554/{serial}", hub))
        elif hub == 'go2rtc':
            # go2rtc's RTSP re-export is exposed on the same broker host.
            out.append((serial, f"rtsp://nvr-go2rtc:8554/{serial}", hub))
        # native_mjpeg: skipped — no RTSP endpoint to probe.
    return out


def _ffprobe_once(url: str) -> dict:
    """
    Run a single ffprobe attempt against the URL. Returns
    {'ok': bool, 'latency_ms': int, 'error': str | None}.
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-rtsp_transport', 'tcp',
                '-timeout', str(FFPROBE_TIMEOUT_SECONDS * 1_000_000),
                '-i', url,
                '-show_entries', 'stream=codec_type',
                '-of', 'json',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFPROBE_TIMEOUT_SECONDS + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if result.returncode == 0:
            return {'ok': True, 'latency_ms': elapsed_ms, 'error': None}
        err = result.stderr.decode('utf-8', errors='replace').strip().splitlines()[-1] if result.stderr else 'nonzero'
        return {'ok': False, 'latency_ms': elapsed_ms, 'error': err[:300]}
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {'ok': False, 'latency_ms': elapsed_ms, 'error': 'ffprobe_timeout'}
    except FileNotFoundError:
        return {'ok': False, 'latency_ms': 0, 'error': 'ffprobe_not_installed'}
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {'ok': False, 'latency_ms': elapsed_ms, 'error': f'{type(e).__name__}: {e}'}


# (camera_id) -> last 'ok' bool. Transitions emit on change.
_rtsp_last_ok: Dict[str, bool] = {}


def _probe_rtsp_per_camera() -> None:
    """ffprobe every camera's hub URL. Emit pass/fail transitions."""
    cameras = _list_cameras_with_hub_urls()
    for serial, url, hub in cameras:
        result = _ffprobe_once(url)
        prev_ok = _rtsp_last_ok.get(serial)
        ok = result['ok']

        if prev_ok is None or prev_ok != ok:
            # Transition: emit a transition-style probe row with from/to.
            tel.emit(
                category='rtsp_probe',
                subcategory='probe_pass' if ok else 'probe_fail',
                camera_id=serial,
                severity='info' if ok else 'warning',
                payload={
                    'url':          url,
                    'hub':          hub,
                    'ok':           ok,
                    'latency_ms':   result['latency_ms'],
                    'error':        result['error'],
                    'from_ok':      prev_ok,
                    'to_ok':        ok,
                },
                debounce_seconds=0,
            )

        _rtsp_last_ok[serial] = ok


# ─────────────────────────────────────────────────────────────────────────────
#  Loop driver
# ─────────────────────────────────────────────────────────────────────────────

def _probe_loop() -> None:
    """Single background loop ticking all enabled probes."""
    logger.info(f"[telemetry] probe loop started (interval={PROBE_INTERVAL_SECONDS}s)")
    # Initial settling delay — gives the app a chance to fully boot before
    # we start scraping its internals.
    if _probe_stop_event.wait(45):
        return

    tick = 0
    while not _probe_stop_event.is_set():
        try:
            if ts.is_enabled():
                # D + D' — path-state diffs every tick
                _probe_mediamtx_paths()
                _probe_go2rtc_streams()
                # F — resource snapshot every tick
                _probe_resource_snapshot()
                # E — RTSP ffprobe every Nth tick
                if tick % RTSP_PROBE_EVERY_N_TICKS == 0:
                    _probe_rtsp_per_camera()
        except Exception:
            logger.exception("[telemetry] probe tick raised")

        tick += 1
        if _probe_stop_event.wait(PROBE_INTERVAL_SECONDS):
            break
    logger.info("[telemetry] probe loop stopped")


def start_probe_loop() -> None:
    """Idempotent start. Loop runs always; gates on telemetry_enabled per tick."""
    global _probe_thread
    if _probe_thread is not None and _probe_thread.is_alive():
        return
    _probe_stop_event.clear()
    _probe_thread = threading.Thread(
        target=_probe_loop,
        name='telemetry-probe-loop',
        daemon=True,
    )
    _probe_thread.start()


def stop_probe_loop() -> None:
    _probe_stop_event.set()
