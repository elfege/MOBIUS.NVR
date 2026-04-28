# MJPEG Settings Flow Diagram

*Left-to-right algorithmic decision graph*

## Grid View Stream Selection

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│  Device     │───>│  Is Portable?    │───>│  Has Native MJPEG?  │───>│  Stream Type     │
│  Request    │    │  (iOS/Android)   │    │  (reolink/unifi/    │    │  Decision        │
└─────────────┘    └──────────────────┘    │   amcrest)          │    └──────────────────┘
                          │                └─────────────────────┘              │
                          │                          │                          │
                          ▼                          ▼                          ▼
                   ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
                   │     NO       │          │     YES      │          │ Use MJPEG    │
                   │  (Desktop)   │          │              │          │ (<img> tag)  │
                   └──────────────┘          └──────────────┘          └──────────────┘
                          │                          │
                          │                          │
                          ▼                          ▼
                   ┌──────────────┐          ┌──────────────┐
                   │ Use stream's │          │     NO       │
                   │ configured   │          │ (eufy/sv3c/  │
                   │ stream_type  │          │  neolink)    │
                   │ (HLS/WebRTC) │          └──────────────┘
                   └──────────────┘                  │
                                                     │
                                                     ▼
                                             ┌──────────────┐
                                             │ Use HLS      │
                                             │ (fallback)   │
                                             └──────────────┘
```

## MJPEG Source Selection (Backend)

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│  Camera     │───>│  mjpeg_source    │───>│  max_connections    │───>│  Final Source    │
│  Config     │    │  field value     │    │  validation         │    │  Decision        │
└─────────────┘    └──────────────────┘    └─────────────────────┘    └──────────────────┘
                          │                          │                          │
        ┌─────────────────┼─────────────────┐        │                          │
        ▼                 ▼                 ▼        ▼                          ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   ┌──────────────┐
│  multipart   │  │ mediaserver  │  │  snapshots   │                   │  Valid       │
│  (native     │  │ (FFmpeg tap) │  │  (Snap API   │                   │  Config      │
│  endpoint)   │  │ NOT IMPL YET │  │   polling)   │                   └──────────────┘
└──────────────┘  └──────────────┘  └──────────────┘
        │                 │                 │
        │                 │                 │
        ▼                 ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                           Validation Rules (camera_repository.py)                     │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  IF max_connections == 1 AND mjpeg_source IN ('snapshots', 'multipart'):             │
│     FORCE mjpeg_source = 'mediaserver'                                                │
│     REASON: Can't open second connection for snapshots/multipart                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## MJPEG Source by Camera Type (Default Assignments)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              Camera Type Defaults                                    │
├─────────────────┬───────────────────┬────────────────────┬──────────────────────────┤
│  Camera Type    │  mjpeg_source     │  max_connections   │  Reason                  │
├─────────────────┼───────────────────┼────────────────────┼──────────────────────────┤
│  Eufy           │  mediaserver      │  1                 │  Single RTSP connection  │
│  SV3C           │  mediaserver      │  1                 │  Budget cam, 1 stream    │
│  E1 (Neolink)   │  mediaserver      │  1                 │  Via Neolink bridge      │
├─────────────────┼───────────────────┼────────────────────┼──────────────────────────┤
│  Amcrest        │  multipart        │  2                 │  Native /mjpg/video.cgi  │
├─────────────────┼───────────────────┼────────────────────┼──────────────────────────┤
│  RLC Reolink    │  snapshots        │  2-4               │  Can handle extra conn   │
├─────────────────┼───────────────────┼────────────────────┼──────────────────────────┤
│  UniFi          │  snapshots        │  8                 │  Via Protect API         │
└─────────────────┴───────────────────┴────────────────────┴──────────────────────────┘
```

## Fullscreen Stream Switching (Portable Devices)

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│  User Taps  │───>│  Current View    │───>│  Target View        │───>│  Stream Switch   │
│  Camera     │    │  (Grid/Full)     │    │  (Full/Grid)        │    │  Action          │
└─────────────┘    └──────────────────┘    └─────────────────────┘    └──────────────────┘
                          │                          │
        ┌─────────────────┴─────────────────┐        │
        ▼                                   ▼        │
┌──────────────┐                    ┌──────────────┐ │
│  GRID VIEW   │                    │  FULLSCREEN  │ │
│  (MJPEG)     │                    │  VIEW        │ │
└──────────────┘                    └──────────────┘ │
        │                                   │        │
        ▼                                   ▼        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  openFullscreen():                                                                 │
│    1. Stop MJPEG stream                                                            │
│    2. Restore original stream_type (HLS/WebRTC)                                    │
│    3. Start HLS with 'main' quality (for audio support)                            │
├───────────────────────────────────────────────────────────────────────────────────┤
│  closeFullscreen():                                                                │
│    1. Stop HLS stream                                                              │
│    2. Check if portable device                                                     │
│    3. If portable + native MJPEG camera → restart as MJPEG                         │
│    4. Else → restart with original stream_type                                     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| `mjpeg_source` field | ✅ Done | Added to cameras.json |
| `max_connections` field | ✅ Done | Added to cameras.json |
| Backend validation | ✅ Done | camera_repository.py |
| Portable device detection | ✅ Done | stream.js `isPortableDevice()` |
| Native MJPEG override | ✅ Done | reolink/unifi/amcrest only |
| Fullscreen MJPEG→HLS switch | ✅ Done | stream.js |
| Sequential stream loading | ✅ Done | 300ms delays, all UIs |
| `mediaserver` MJPEG backend | ❌ Pending | FFmpeg tap needed |
| Advanced Settings UI | ❌ Future | WTForms, Phase 3 |

---

*Created: January 6, 2026 00:40 EST*
