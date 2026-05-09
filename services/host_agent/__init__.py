"""
Host agent — companion daemon installed on every machine that runs the
NVR's browser kiosk.

What it does:

  - Polls X11 DPMS state (display on / off / standby / suspend)
  - Polls /proc/loadavg + (when present) `nvidia-smi` for CPU + GPU
  - POSTs the snapshot to /api/host/state on the NVR every few seconds

Why it exists:

  Chrome on Linux X11 in kiosk mode does NOT reliably fire the Page
  Visibility API's `visibilitychange` when the monitor goes DPMS-off.
  The frontend's existing visibility-manager (services/streaming/
  visibility-manager.js) therefore can't tear down streams when nobody
  is looking, and the GPU keeps decoding 16+ tiles full-tilt — fans
  scream, hardware burns out (the user has lost two PCs to this).

  This agent provides the missing signal: instead of relying on the
  browser to detect display state, the host tells the server, and the
  server broadcasts to the page over the existing /stream_events
  SocketIO namespace. The visibility-manager subscribes and treats
  display=off the same as document.hidden=true.

  Same path is used to broadcast load metrics, so the page can
  throttle (drop to snapshot mode) when CPU is sustained-high.

Layout:

  agent.py                    — the daemon
  host-agent.service.tmpl     — systemd user unit template
  install_host_agent.sh       — one-shot installer (copies + enables)
  README.md                   — per-host install steps + auth setup
"""

__all__ = []
