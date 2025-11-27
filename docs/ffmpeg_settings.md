---
title: "ffmpeg settings guide"
layout: default
---

<!-- markdownlint-disable MD025 MD033 -->

# 📘 NVR Encoding & Resolution Reference Guide

## 📑 Table of Contents

1. [🖥️ 16:9 Aspect Ratio (Widescreen Standard)](#-169-aspect-ratio-widescreen-standard)
2. [🧾 4:3 Aspect Ratio (Classic / Security / Legacy)](#-43-aspect-ratio-classic--security--legacy)
3. [⚙️ FFmpeg Transcode Presets](#️-ffmpeg-transcode-presets)

   * [Overview — System Impact by Layer](#overview--system-impact-by-layer)
   * [Quick Links](#quick-links)
   * [Preset Details](#preset-details)
   * [Quick Comparison Summary](#quick-comparison-summary)
   * [Pipeline Load Comparison](#pipeline-load-comparison)
4. [📄 FFmpeg Stream Configuration Parameters](#-ffmpeg-stream-configuration-parameters)

   * [Overview](#overview)
   * [Parameter Reference Table](#parameter-reference-table)
   * [Performance & Interdependencies](#performance--interdependencies)
   * [Recommended Profiles](#recommended-profiles)

---

## 🖥️ 16:9 Aspect Ratio (Widescreen Standard)

| Label     | Resolution            | Notes                  |
| :-------- | :-------------------- | :--------------------- |
| 128×72    | Extremely low         | Thumbnail-level        |
| 160×90    | Minimal viable stream |                        |
| 213×120   | Ultra-low bandwidth   |                        |
| 256×144   | 144p                  | YouTube’s lowest tier  |
| 320×180   | Tiny preview          | Typical “tiny preview” |
| 426×240   | 240p                  | Mobile baseline        |
| 640×360   | 360p                  | Low SD                 |
| 854×480   | 480p                  | DVD-quality SD         |
| 960×540   | qHD                   | Quarter of Full HD     |
| 1280×720  | 720p                  | HD                     |
| 1366×768  | HD variant            | Common on laptops      |
| 1600×900  | HD+                   | High-quality preview   |
| 1920×1080 | 1080p                 | Full HD                |
| 2560×1440 | 1440p                 | QHD / 2K               |
| 3200×1800 | QHD+                  | High-res laptop        |
| 3840×2160 | 2160p                 | 4K UHD                 |
| 5120×2880 | 5K                    | High-end display       |
| 7680×4320 | 4320p                 | 8K UHD                 |

---

## 🧾 4:3 Aspect Ratio (Classic / Security / Legacy)

| Label     | Resolution                  | Notes              |
| :-------- | :-------------------------- | :----------------- |
| 128×96    | Icon-size                   | Ultra-small legacy |
| 160×120   | QQVGA                       | Very low           |
| 192×144   | Classic low-res             | Legacy webcams     |
| 240×180   | Slightly wider than 320×180 |                    |
| 320×240   | QVGA                        | Webcam baseline    |
| 400×300   | Surveillance SD             |                    |
| 640×480   | VGA                         | Analog capture     |
| 800×600   | SVGA                        | Early digital SD   |
| 1024×768  | XGA                         | 1990s LCD standard |
| 1280×960  | SXGA-lite                   | 1.2 MP             |
| 1600×1200 | UXGA                        | 2 MP 4:3           |
| 2048×1536 | QXGA                        | 3 MP               |
| 2560×1920 | QSXGA                       | 5 MP               |
| 3264×2448 | 8 MP                        | Modern 4:3 sensors |

---

## ⚙️ FFmpeg Transcode Presets

### Overview — System Impact by Layer

| Layer                    | Affected by `-preset`? | How / Why                                                                 |
| :----------------------- | :--------------------- | :------------------------------------------------------------------------ |
| **Camera**               | ❌ No                   | Cameras output RTSP/H.264; unaffected by presets.                         |
| **Server / NVR Backend** | ✅ Yes (Primary)        | Determines CPU usage and compression efficiency during HLS transcoding.   |
| **Browser / Client**     | ⚠️ Indirectly          | Affects bitrate and decoding complexity; mobile devices feel impact most. |

---

### Quick Links

[ultrafast](#ultrafast) · [superfast](#superfast) · [veryfast](#veryfast) · [faster](#faster) · [fast](#fast) · [medium](#medium) · [slow](#slow) · [slower](#slower) · [veryslow](#veryslow) · [placebo](#placebo)

---

### Preset Details

#### ultrafast

* **Purpose:** Minimum CPU, maximum speed.
* **Use Case:** Debug, testing, low-end hardware, high camera counts.
* **Trade-Off:** Massive file size, weak compression, highest bitrate.
* **Performance:**

  * CPU: 🔹 Very low
  * Latency: ⚡ Extremely low (~1s)
  * Bandwidth: 🔺 High (6–12 Mbps per 720p stream)

#### superfast

* **Purpose:** Slightly better compression, still lightweight.
* **Use Case:** Real-time streaming on low-power servers.
* **Performance:**

  * CPU: 🔹 Low
  * Latency: ⚡ Very low (~1.5s)
  * Bandwidth: 🔺 High (4–8 Mbps @ 720p)

#### veryfast

* **Purpose:** Practical default for live HLS / NVR pipelines.
* **Use Case:** Real-time multi-camera encoding.
* **Performance:**

  * CPU: ⚖️ Moderate
  * Latency: ⚡ 2–3s
  * Bandwidth: ⚖️ 3–6 Mbps
  * **Special Behavior:** Enforces `-g 30 -bf 0` (low-latency GOP).

#### faster

* **Purpose:** Higher quality at modest CPU increase.
* **Use Case:** Midrange hardware, smaller camera sets.
* **Performance:**

  * CPU: 🟠 Moderate-high
  * Latency: ⏱ Slightly higher (~3–4s)
  * Bandwidth: 🟢 Lower (2–4 Mbps)

#### fast

* **Purpose:** Quality-focused streaming; fewer cameras.
* **Use Case:** Archival-quality real-time or small setups.
* **Performance:**

  * CPU: 🔴 High
  * Latency: ⏱ 3–5s
  * Bandwidth: 🟢 Low (2–3 Mbps)

#### medium

* **Purpose:** Default FFmpeg preset; best quality per bitrate.
* **Use Case:** Archival or near-live workloads.
* **Performance:**

  * CPU: 🔴 Very High
  * Latency: 🕐 5–7s
  * Bandwidth: 🟢 Low (~2 Mbps @ 720p)

#### slow / slower / veryslow

* **Purpose:** Archival / offline encoding only.
* **Performance:**

  * CPU: 🟥 Extreme
  * Latency: 🕐 8–20s+
  * Bandwidth: 🟢 Minimal (~1.5 Mbps)
  * **Note:** Not viable for real-time NVR systems.

#### placebo

* **Purpose:** Testing only; maximum compression depth.
* **Performance:**

  * CPU: 🟥🟥 Absurdly high
  * Latency: 🕐 Minutes per minute
  * **Never use live.**

---

### Quick Comparison Summary

| Preset          | Relative Speed    | Compression Efficiency | Latency  | Typical Use                 |
| :-------------- | :---------------- | :--------------------- | :------- | :-------------------------- |
| ultrafast       | 🟢 Fastest        | 🔴 Poor                | ⚡ Lowest | Debug / multi-cam           |
| superfast       | 🟢 Very Fast      | 🟠 Low                 | ⚡ Low    | Low-power servers           |
| veryfast        | ⚖️ Balanced       | 🟡 Medium              | ⚡ 2–3s   | Default live preset         |
| faster          | 🟡 Medium         | 🟢 Good                | ⏱ 3–4s   | Quality bias                |
| fast            | 🟠 Medium-Slow    | 🟢 Good+               | ⏱ 4–5s   | Quality + bandwidth balance |
| medium          | 🟠 Slow           | 🟢 High                | 🕐 5–7s  | Archival                    |
| slow → veryslow | 🔴 Slower–Extreme | 🟢🔵 Excellent         | 🕐 8–20s | Offline only                |
| placebo         | 🐢 Absurdly Slow  | 🟢 Max                 | 🕐 >20s  | Benchmark only              |

---

### Pipeline Load Comparison

| Preset               | Camera Load | NVR CPU Load | Bandwidth / Disk | Client Load | Typical Impact                   |
| :------------------- | :---------- | :----------- | :--------------- | :---------- | :------------------------------- |
| ultrafast            | None        | 🟢 Very Low  | 🔴 Very High     | 🟠 High     | Real-time multi-cam; inefficient |
| superfast            | None        | 🟢 Low       | 🔴 High          | 🟠 Medium   | Stable low-latency               |
| veryfast             | None        | ⚖️ Medium    | ⚖️ Moderate      | ⚖️ Moderate | Balanced; LL-HLS default         |
| faster               | None        | 🟠 High      | 🟢 Lower         | 🟡 Medium   | Better quality                   |
| fast                 | None        | 🔴 High      | 🟢 Low           | 🟢 Low      | Excellent quality, fewer streams |
| medium               | None        | 🔴 Very High | 🟢 Low           | 🟢 Low      | Archival                         |
| slow/slower/veryslow | None        | 🟥 Extreme   | 🟢 Minimal       | 🟢 Low      | Offline transcoding only         |

---

## 📄 FFmpeg Stream Configuration Parameters

### Overview

These parameters define how  NVR’s FFmpeg transcode layer behaves.
They control latency, buffering, CPU utilization, and compatibility with browsers (especially HLS.js clients).

---

### Parameter Reference Table

| **Parameter**                 | **Purpose / Behavior**                      | **Common Values / Examples**                | **Performance Impact**                                        |
| :---------------------------- | :------------------------------------------ | :------------------------------------------ | :------------------------------------------------------------ |
| `stream_type`                 | Defines output stream format.               | `"HLS"` or `"MJPEG"`                        | HLS = segmented (2–5s latency), MJPEG = instant but heavy.    |
| `rtsp_transport`              | Selects RTSP transport protocol.            | `"tcp"` (reliable), `"udp"` (lower latency) | TCP safer for LANs with drops; UDP faster but fragile.        |
| `timeout`                     | Socket timeout (µs).                        | `5000000` (5s typical)                      | Higher = better stability; slight stall delay on disconnects. |
| `analyzeduration`             | Input probing duration.                     | `1000000`–`2000000`                         | Lower = faster startup; risk of misdetection.                 |
| `probesize`                   | Data size (bytes) used for probing streams. | `500000`–`2000000`                          | Larger improves stream accuracy, slower start.                |
| `use_wallclock_as_timestamps` | Syncs output timestamps to real time.       | `"1"`                                       | Required for LL-HLS to prevent drift; negligible cost.        |
| `fflags`                      | Global FFmpeg flags.                        | `"nobuffer"`                                | Reduces buffering latency; may increase CPU slightly.         |
| `flags`                       | Encoder-level options.                      | `"low_delay"`                               | Minimizes frame buffering; essential for real-time.           |
| `frame_rate_full_screen`      | Target FPS for full-screen mode.            | `18`, `24`, or `30`                         | Higher = smoother, more CPU.                                  |
| `frame_rate_grid_mode`        | FPS for grid/preview mode.                  | `5`–`8` typical                             | Reduces CPU by dropping frames in preview view.               |
| `resolution_main`             | Main encoding resolution.                   | `"1920x1080"`, `"1280x720"`, `"640x360"`    | Defines quality and bandwidth; scales with preset.            |
| `resolution_sub`              | Low-bitrate fallback resolution.            | `"320x180"`, `"256x144"`                    | Used for mobile or thumbnails.                                |
| `hls_mode`                    | Transcoding or direct copy.                 | `"transcode"` or `"copy"`                   | Transcode = CPU-heavy, reliable; Copy = fast, error-prone.    |
| `preset`             | FFmpeg preset selection.                    | `"ultrafast"` → `"veryslow"`                | Directly affects CPU load and bitrate.                        |
| `hls_time`          | Duration of each `.ts` segment.             | `1`–`4` seconds typical                     | Shorter = lower latency, more filesystem I/O.                 |
| `hls_list_size`               | Playlist size (segments retained).          | `1`–`10` typical                            | Larger = longer DVR buffer, higher memory use.                |
| `hsl_delete_threshold`        | Max segment backlog before cleanup.         | `1`–`3` typical                             | Prevents disk bloating; too low risks abrupt playback drops.  |

---

### Performance & Interdependencies

| Parameter Pair                               | Interaction                                                 | Tuning Notes                                                      |
| :------------------------------------------- | :---------------------------------------------------------- | :---------------------------------------------------------------- |
| `hls_time` + `hls_list_size`       | Controls total stream buffer length (`length × list_size`). | For LL-HLS: aim for ≤6s total buffer.                             |
| `fflags=nobuffer` + `flags=low_delay`        | Together minimize end-to-end latency.                       | Use both for <3s latency; disables internal frame queues.         |
| `frame_rate_full_screen` + `preset` | Determines total CPU load per stream.                       | Example: 1080p @ 30fps + `veryfast` ≈ 60–80% of one core.         |
| `rtsp_transport` + network stability         | Impacts reconnection frequency.                             | TCP avoids desyncs; UDP reduces delay but drops under congestion. |

---

### Recommended Profiles

| Profile                         | Goal                   | Example Key Parameters                                                                                            | Expected Outcome                              |
| :------------------------------ | :--------------------- | :---------------------------------------------------------------------------------------------------------------- | :-------------------------------------------- |
| **LL-HLS Real-Time (Balanced)** | Multi-cam, low latency | `hls_time=2`, `hls_list_size=3`, `preset="veryfast"`, `flags="low_delay"`, `fflags="nobuffer"` | 2–3s latency, medium CPU (~1 core per stream) |
| **High Efficiency Archival**    | Storage optimization   | `hls_time=4`, `preset="fast"`, `rtsp_transport="tcp"`                                          | 4–5s latency, smaller files, higher CPU       |
| **Lightweight (Edge Device)**   | Low-power NVR          | `hls_time=2`, `preset="ultrafast"`, `frame_rate_grid_mode=5`                                   | <2s latency, larger files, minimal CPU        |
| **High Quality Export**         | Forensic / review      | `preset="medium"`, `frame_rate_full_screen=24`, `hls_time=4`                                   | Near-lossless quality; offline only           |

---

✅ **Final Summary**

* **Preset controls CPU usage** — slower = higher compression, higher CPU.
* **Camera hardware unaffected** — encoding occurs in FFmpeg, not the device.
* **Most NVRs perform best with `veryfast` preset**, 720p @ 18–24 fps, 2 s segments, and 3-segment playlists.
* **Always balance CPU, latency, and bandwidth** — no single setting fits all cameras or purposes.
