# Eufy PTZ Local Control Research

*Last updated: January 21, 2026*

This document captures research findings on achieving local PTZ (Pan-Tilt-Zoom) control for Eufy cameras without cloud authentication.

---

## Executive Summary

**Current Status:** No fully local PTZ control solution exists for Eufy cameras. Cloud authentication is required to establish P2P sessions, even though commands can travel locally once the session is established.

**Key Insight:** The RTSP stream works locally (direct IP access with embedded credentials), but PTZ commands require Eufy's proprietary P2P protocol which needs cloud auth for session key exchange.

---

## Current NVR Implementation

Our NVR already has Eufy integration via:

- **Video Streaming:** Direct RTSP (works locally, no cloud needed)
- **PTZ Control:** `eufy-security-ws` bridge (requires cloud auth, problematic)

Relevant files:
- `services/eufy_service.py` - EufyCameraService with bridge management
- `services/eufy/eufy_bridge_client.py` - WebSocket client for bridge
- `services/eufy/eufy_bridge_login.sh` - Bridge startup script

The bridge runs on `ws://127.0.0.1:3000` and wraps `eufy-security-client`.

---

## The Core Problem

From project history (`docs/README_project_history.md`):

> "Eufy Captcha Authentication: Bridge fails due to security challenge requiring manual intervention"

The captcha/2FA requirement blocks automated PTZ control.

---

## Reverse Engineering Findings

### bropat/eufy-security-client

The main open-source library for Eufy control: https://github.com/bropat/eufy-security-client

#### PTZ Command Types (from `src/p2p/types.ts`)

```typescript
// Pan/Tilt Calibration
CMD_INDOOR_PAN_CALIBRATION = 6017
CMD_OUTDOOR_PAN_CALIBRATION = 6251

// Pan Motion Control
CMD_INDOOR_PAN_MOTION_TRACK = 6016
CMD_INDOOR_PAN_SPEED = 6015
CMD_INDOOR_ROTATE = 6030
CMD_OUTDOOR_ROTATE = 6038

// Motion Presets
CMD_FLOODLIGHT_SAVE_MOTION_PRESET_POSITION = 6032
CMD_FLOODLIGHT_DELETE_MOTION_PRESET_POSITION = 6033
CMD_FLOODLIGHT_SET_MOTION_PRESET_POSITION = 6035
CMD_FLOODLIGHT_SET_MOTION_AUTO_CRUISE = 6031

// Cross-Camera Tracking
CMD_SET_CROSS_CAMERA_TRACKING = 1065
CMD_SET_TRACKING_ASSISTANCE = 1069
CMD_SET_CONTINUOUS_TRACKING_TIME = 1070
CMD_SET_CROSS_TRACKING_CAMERA_LIST = 1072
CMD_SET_CROSS_TRACKING_GROUP_LIST = 1073
```

#### Direction Values (PanTiltDirection enum)

| Direction | Value |
|-----------|-------|
| ROTATE360 | 0 |
| LEFT | 1 |
| RIGHT | 2 |
| UP | 3 |
| DOWN | 4 |

#### panAndTilt Implementation

Located in `src/http/station.ts`:

```typescript
public panAndTilt(device: Device, direction: PanTiltDirection, command = 1): void {
    const commandData: CommandData = {
        name: CommandName.DevicePanAndTilt,
        value: direction
    }
    // ... sends via P2P session
}
```

### P2P Protocol Details

#### Network Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| UDP 32108 | Broadcast | Local device discovery |
| UDP 32100 | P2P | Device/client communication |
| TCP 443 | HTTPS | Fallback (obfuscated, not encrypted) |
| UDP 4143 | P2P | Setup/streaming |
| UDP 15000 | P2P | Setup/streaming |
| UDP 32700 | P2P | Setup/streaming |

#### Hole Punching Servers (cloud infrastructure)

- 34.235.4.153
- 18.223.127.200
- 54.153.101.7

These servers broker the initial P2P connection.

### Why Cloud Auth is Required

1. **Session Establishment:** P2P requires cloud servers for "hole punching" - NAT traversal coordination
2. **Encryption Keys:** Session encryption keys are derived from cloud authentication
3. **Device Verification:** Camera serial/ID must be verified against Eufy cloud

From eufy-security-client README:
> "A connection to the Eufy Cloud is **always a prerequisite**... You need to provide your Cloud login credentials."

---

## Academic Research

**USENIX WOOT 24 Paper:** "Reverse Engineering the Eufy Ecosystem: A Deep Dive into Security Vulnerabilities and Proprietary Protocols"

- URL: https://www.usenix.org/conference/woot24/presentation/goeman
- PDF: https://www.usenix.org/system/files/woot24-goeman.pdf

This paper documents:
- P2P protocol reverse engineering
- Encryption methods used
- Security vulnerabilities found

*Note: Full paper analysis not completed - may contain details useful for local control.*

---

## Blue Iris Compatibility

**Finding:** Eufy PTZ does NOT work in Blue Iris either.

From IPCamTalk thread (ipcamtalk.com/threads/eufy-ptz-settings.67010/):
> "Anyone been able to get PTZ working in Blue Iris for a Eufy 2K indoor PTZ camera? PTZ works fine via Eufy app."

Blue Iris would need ONVIF PTZ support, which Eufy cameras don't properly expose.

---

## Custom Firmware Option

### Thingino

Open-source IP camera firmware: https://thingino.com/

- Targets Ingenic SoC chips (common in budget cameras)
- Successfully flashed on Eufy C-120
- Blog post: https://blog.vasi.li/flashing-a-eufy-c-120-security-camera-with-custom-firmware/

**What it enables:**
- Full local RTSP/ONVIF without cloud
- No phoning home

**Limitations:**
- PTZ support not confirmed
- Model-specific firmware required
- Requires USB flashing (bricking risk)

---

## Potential Future Approaches

### 1. Session Capture & Replay (Theoretical)

If someone captured the P2P session establishment packets and reverse-engineered the encryption handshake:
1. Spoof cloud auth response locally
2. Send PTZ commands directly via UDP 32100

**Status:** No one has published this.

### 2. Firmware Modification

Custom firmware that:
- Exposes direct PTZ HTTP endpoints
- Implements proper ONVIF PTZ profile

**Status:** Thingino exists but PTZ support unclear.

### 3. Hardware Serial Interface

Some cameras have UART/debug ports that might allow:
- Direct motor control
- Firmware extraction for analysis

**Status:** Requires hardware skills, not documented.

### 4. USENIX Paper Deep Dive

The academic paper may contain enough protocol details to implement local control.

**Status:** Paper needs thorough analysis (18 pages).

---

## Home Assistant Integration

For reference, HA users can control Eufy PTZ via:

**Service:** `eufy_security.send_message`

```yaml
service: eufy_security.send_message
data:
  command: "device.pan_and_tilt"
  serialNumber: "YOUR_CAMERA_SERIAL"
  direction: 1  # 1=left, 2=right, 3=up, 4=down
```

Or using dedicated services:
- `eufy_security.ptz_up`
- `eufy_security.ptz_down`
- `eufy_security.ptz_left`
- `eufy_security.ptz_right`
- `eufy_security.ptz_360`

**Important:** These work ONLY when camera is streaming via P2P (not RTSP).

---

## Relevant Links

### Libraries & Integrations
- [bropat/eufy-security-client](https://github.com/bropat/eufy-security-client) - Main Node.js library
- [bropat/eufy-security-ws](https://github.com/bropat/eufy-security-ws) - WebSocket server wrapper
- [fuatakgun/eufy_security](https://github.com/fuatakgun/eufy_security) - Home Assistant integration

### Documentation
- [eufy-security-client types.ts](https://github.com/bropat/eufy-security-client/blob/master/src/p2p/types.ts) - Command definitions
- [eufy-security-client station.ts](https://github.com/bropat/eufy-security-client/blob/master/src/http/station.ts) - PTZ implementation

### Research
- [USENIX WOOT 24 Paper](https://www.usenix.org/conference/woot24/presentation/goeman)
- [PPPP Protocol Overview](https://palant.info/2025/11/05/an-overview-of-the-pppp-protocol-for-iot-cameras/)

### Community Discussions
- [IPCamTalk: Eufy PTZ Settings](https://ipcamtalk.com/threads/eufy-ptz-settings.67010/)
- [AlexxIT/WebRTC Issue #217](https://github.com/AlexxIT/WebRTC/issues/217)
- [Eufy Community: PTZ over RTSP](https://community.eufy.com/t/ptz-over-rtsp-config/1936591)

---

## Conclusion

Local-only Eufy PTZ control remains unsolved in the open-source community. The fundamental barrier is that P2P session establishment requires cloud authentication for:
1. NAT hole punching coordination
2. Encryption key exchange
3. Device identity verification

Until someone:
- Reverse engineers the full P2P handshake and encryption
- Creates custom firmware with local PTZ endpoints
- Finds an alternative local protocol

...we're stuck with either:
- Cloud-authenticated PTZ (captcha/2FA issues)
- No PTZ control (RTSP video only)

---

## TODO

- [ ] Deep dive into USENIX WOOT 24 paper for protocol details
- [ ] Test Thingino firmware on compatible Eufy model for PTZ support
- [ ] Monitor eufy-security-client releases for local-only improvements
- [ ] Investigate if newer Eufy cameras have different (better) local support
