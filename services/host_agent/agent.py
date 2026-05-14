#!/usr/bin/env python3
"""
mobius-nvr-host-agent

Per-host daemon that reports display state + CPU/GPU load to the NVR
server, so the browser kiosk can sleep when displays are off and
throttle when load is sustained-high.

Configuration is read from ``$XDG_CONFIG_HOME/mobius-nvr-host-agent/config``
(default: ``~/.config/mobius-nvr-host-agent/config``). It's a tiny
INI-style ``KEY=VALUE`` file. Required keys:

    SERVER_URL=https://mobius.nvr:8444
    HOST_LABEL=rog
    API_TOKEN=<value of NVR_API_TOKEN on server>

Optional:

    POLL_INTERVAL=5        # seconds between snapshots; default 5
    INSECURE_TLS=1         # skip TLS verification (self-signed certs)

The daemon is intentionally dependency-light: only ``requests``. No
psutil, no pynvml, no D-Bus — just shell-out to xset / loadavg /
nvidia-smi. Keeps it portable across distros and avoids a heavyweight
install on the kiosk machine.

Exit codes: only on fatal misconfiguration (missing config, missing
required keys). Network errors are logged and retried indefinitely
because the kiosk machine should keep trying to report after the
server reboots.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: 'requests' is required. Install with: pip install --user requests\n"
    )
    sys.exit(2)

# --------------------------------------------------------------------------
# Logging — to stderr so journalctl captures it cleanly under systemd.
# --------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("MOBIUS_HOST_AGENT_LOG", "INFO"),
    format="%(asctime)s %(levelname)-7s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mobius-nvr-host-agent")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def _config_path() -> Path:
    """Per-XDG path. Falls back to ~/.config if XDG_CONFIG_HOME unset."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "mobius-nvr-host-agent" / "config"


def _load_config() -> Dict[str, str]:
    """Load KEY=VALUE config file. Lines starting with '#' are comments."""
    p = _config_path()
    if not p.exists():
        log.error("config file missing: %s", p)
        log.error("create it with at minimum:")
        log.error("    SERVER_URL=https://mobius.nvr:8444")
        log.error("    HOST_LABEL=%s", socket.gethostname())
        log.error("    API_TOKEN=<value of NVR_API_TOKEN on server>")
        sys.exit(2)

    cfg: Dict[str, str] = {}
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()

    for required in ("SERVER_URL", "HOST_LABEL", "API_TOKEN"):
        if not cfg.get(required):
            log.error("config %s missing required key: %s", p, required)
            sys.exit(2)

    return cfg


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------
_DPMS_RE = re.compile(r"Monitor is (\w+)")


def probe_display_state() -> Optional[str]:
    """
    Return the X DPMS state as a normalized string.

    Possible return values:
      * ``"on"``       — display is awake
      * ``"standby"``  — DPMS standby
      * ``"suspend"``  — DPMS suspend
      * ``"off"``      — DPMS off
      * ``None``       — couldn't determine (xset missing, no DISPLAY,
                         non-X session). Caller should treat as
                         "unknown" and not change state.

    xset is universally available on X11 sessions (part of the X tools
    package). For Wayland, this returns None — the agent simply omits
    display state on Wayland hosts.
    """
    if not shutil.which("xset"):
        return None
    if not os.environ.get("DISPLAY"):
        # systemd user services typically inherit DISPLAY via
        # systemctl --user import-environment, but if it's missing we
        # can't probe. Caller treats None as "unknown".
        return None
    try:
        out = subprocess.run(
            ["xset", "-display", os.environ["DISPLAY"], "q"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    m = _DPMS_RE.search(out.stdout)
    if not m:
        return None
    return m.group(1).lower()


def probe_cpu_load() -> Dict[str, float]:
    """Return loadavg + cpu_count from /proc/loadavg + /proc/cpuinfo."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            l1 = float(parts[0])
            l5 = float(parts[1])
            l15 = float(parts[2])
    except Exception as e:
        log.debug("loadavg read failed: %s", e)
        return {}

    cpu_count = os.cpu_count() or 1
    return {
        "load_1m": l1,
        "load_5m": l5,
        "load_15m": l15,
        "cpu_count": cpu_count,
        # Convenient pre-normalized "fraction of total CPU" — page can
        # decide its own threshold from this without re-deriving.
        "cpu_load_norm": l1 / cpu_count if cpu_count else 0.0,
    }


def probe_memory() -> Dict[str, float]:
    """
    Return system memory usage from /proc/meminfo. Reports `mem_used_pct`
    (used / total) and the raw MB values for the UI to display.

    Uses (MemTotal - MemAvailable) / MemTotal as "used" — MemAvailable
    is the kernel's own estimate of what's actually reclaimable for new
    allocations, which is the meaningful "used" number for capacity
    planning (vs. raw MemFree which excludes reclaimable cache).
    """
    try:
        with open("/proc/meminfo") as f:
            info: Dict[str, int] = {}
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val = parts[1].strip().split()[0]  # value is in kB
                try:
                    info[key] = int(val)
                except ValueError:
                    continue
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", 0)
        if total_kb <= 0:
            return {}
        used_kb = max(0, total_kb - avail_kb)
        return {
            "mem_total_mb": round(total_kb / 1024, 1),
            "mem_used_mb": round(used_kb / 1024, 1),
            "mem_used_pct": round(used_kb / total_kb * 100, 1),
        }
    except Exception as e:
        log.debug("meminfo read failed: %s", e)
        return {}


def probe_gpu_load() -> Optional[Dict[str, float]]:
    """
    Return GPU utilization from `nvidia-smi --query-gpu=...`.

    Returns None for non-NVIDIA hosts (most laptops; AMD/Intel are
    intentionally skipped because the relevant tooling is heterogeneous
    and not worth the per-host complexity tonight).
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.memory,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    # Multiple GPUs: aggregate by max utilization (worst card drives the heat).
    rows = [r.strip() for r in out.stdout.strip().splitlines() if r.strip()]
    if not rows:
        return None
    best: Dict[str, float] = {"gpu_util": 0.0, "gpu_mem_util": 0.0, "gpu_temp_c": 0.0}
    for r in rows:
        try:
            util, mem, temp = (float(x.strip()) for x in r.split(","))
        except ValueError:
            continue
        best["gpu_util"] = max(best["gpu_util"], util)
        best["gpu_mem_util"] = max(best["gpu_mem_util"], mem)
        best["gpu_temp_c"] = max(best["gpu_temp_c"], temp)
    return best


# --------------------------------------------------------------------------
# Reporter
# --------------------------------------------------------------------------
def build_payload(host_label: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "host": host_label,
        "ts": time.time(),
    }
    ds = probe_display_state()
    if ds is not None:
        payload["display_state"] = ds
    payload.update(probe_cpu_load())
    payload.update(probe_memory())
    gpu = probe_gpu_load()
    if gpu is not None:
        payload.update(gpu)
    return payload


def report(session: requests.Session, server_url: str, payload: Dict[str, Any], verify: bool) -> None:
    url = server_url.rstrip("/") + "/api/host/state"
    try:
        resp = session.post(url, json=payload, timeout=10, verify=verify)
        if resp.status_code >= 400:
            log.warning("server %s -> %s: %s", url, resp.status_code, resp.text[:200])
    except requests.RequestException as e:
        log.warning("post failed to %s: %s", url, e)


def main() -> int:
    cfg = _load_config()
    server_url = cfg["SERVER_URL"]
    host_label = cfg["HOST_LABEL"]
    api_token = cfg["API_TOKEN"]
    poll_interval = float(cfg.get("POLL_INTERVAL", "5"))
    verify_tls = cfg.get("INSECURE_TLS", "0").strip() not in ("1", "true", "yes")
    if not verify_tls:
        # Suppress urllib3 InsecureRequestWarning that floods the log
        try:
            import urllib3  # type: ignore
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            pass

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "User-Agent": f"mobius-nvr-host-agent/0.1 ({host_label})",
    })

    log.info(
        "started: server=%s host=%s poll=%ss verify_tls=%s",
        server_url, host_label, poll_interval, verify_tls,
    )

    while True:
        payload = build_payload(host_label)
        log.debug("payload: %s", json.dumps(payload))
        report(session, server_url, payload, verify_tls)
        time.sleep(poll_interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("interrupted")
        sys.exit(0)
