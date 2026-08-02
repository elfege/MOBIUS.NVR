# MOBIUS.NVR

**A vendor-agnostic Network Video Recorder — one interface for every camera on the property.**

MOBIUS.NVR unifies mixed-brand IP camera fleets behind a single web application:
live low-latency video, PTZ control, motion detection, continuous and
motion-triggered recording, and per-user preferences — regardless of which
manufacturer sits behind each tile.

[**elfegesystems.com**](https://elfegesystems.com) · [**Contact**](https://elfegesystems.com/contact) · [elfege@elfege.com](mailto:elfege@elfege.com)

---

## The problem

Camera hardware is a patchwork. A single site accumulates cameras from different
manufacturers over years, and each speaks its own dialect — different streaming
protocols, different PTZ command sets, different quirks in how (and whether) it
exposes a second stream. The usual result is one vendor app per brand, no shared
recording policy, and a live view that only works well in the vendor's own
walled garden.

MOBIUS.NVR removes the vendor from the equation. You stop caring which brand is
behind each tile.

## What it does

**One interface, many vendors.** Cameras from several major and budget brands —
plus any standards-compliant ONVIF device — present in a single grid. The RTSP
path for a generic ONVIF camera is discovered automatically rather than
hardcoded, and streams are made browser-safe (transcoded only when the codec
requires it).

**Latency you can choose.** Per-camera streaming from sub-second WebRTC through
low-latency HLS to lightweight snapshot polling for constrained mobile grids,
so a control-room wall and a phone on cellular each get an appropriate mode.

**Recording that fits the site.** Continuous, motion-triggered, and manual
recording with timeline playback, tiered storage, and retention policy.

**PTZ, audio, and motion across brands.** Pan/tilt/zoom, two-way audio, and
motion detection are normalized across vendor command sets behind one set of
controls.

**Runtime configuration as data.** The system's tunable values — thresholds,
labels, display tokens, per-user preferences — live in the database and can be
changed live, without redeploying, and are audited when they change.

**Health that self-heals.** A backend watchdog and a frontend freeze detector
based on decoded-frame progress catch the "connected but frozen" failure that a
naive uptime check misses, and recover the stream automatically.

**Private by design.** Credentials are encrypted at rest, access is per-user, and
the whole system runs on infrastructure the operator controls.

## Who it is for

**Engineering teams evaluating a build.** MOBIUS.NVR is a working example of a
hard systems problem solved end to end: real-time multi-protocol media, a
database-as-source-of-truth architecture, self-healing health monitoring, and a
data-driven configuration model. If your team is weighing a custom video or
real-time-media capability, it is a concrete reference for what that looks like
in production.

**Recruiters and hiring teams.** This is a production system maintained by a
single engineer — spanning media streaming, backend services, a browser
front-end, database design, and deployment. It runs continuously against real
hardware, not as a demo.

## Status

MOBIUS.NVR is in continuous production use, running a mixed-vendor camera fleet
across multiple streaming protocols. It is developed by Elfege Systems LLC and
offered commercially; the source and its history are maintained privately.

## By the numbers

<!-- STATS:START -->
| | |
|---|---|
| **Current release** | `v6.20.0` — actively developed (1323 commits) |
| **Cameras unified** | 6 vendor families — Eufy · Reolink · UniFi · Amcrest · SV3C · generic ONVIF |
| **Codebase** | ~118k lines (Python backend + React-Native app) |
| **Quality gate** | 293 automated tests across 45 suites, run on every release |

<sub>Figures regenerated from the private repository on each release.</sub>
<!-- STATS:END -->

## Commercial and licensing enquiries

Licensing, deployment, integration, and consulting:

**[elfegesystems.com](https://elfegesystems.com)** · [**Get in touch**](https://elfegesystems.com/contact) · [elfege@elfege.com](mailto:elfege@elfege.com)

Use of this software is governed by the terms in [LICENSE](LICENSE) — commercial
use requires a paid license.

---

*MOBIUS.NVR is developed by Elfege Systems LLC as part of the MOBIUS ecosystem.*
