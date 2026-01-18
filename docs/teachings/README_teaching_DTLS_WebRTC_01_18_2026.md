# Teaching Document: DTLS and WebRTC for iOS Safari

*Created: January 18, 2026*
*Branch: `dtls_webrtc_ios_JAN_18_2026_a`*

---

## Overview

This document explains why iOS Safari requires DTLS encryption for WebRTC, and how enabling it in MediaMTX will reduce iOS streaming latency from ~2-4 seconds (HLS) to ~200ms (WebRTC).

---

## The Problem

**Current behavior:**

- Desktop browsers: WebRTC works (low latency ~200ms)
- iOS Safari: Falls back to HLS (high latency 2-4 seconds)

**Why?** iOS Safari requires DTLS-SRTP encryption for WebRTC connections.

---

## Understanding the Protocols

### WebRTC (Web Real-Time Communication)

WebRTC is a peer-to-peer protocol for real-time audio/video streaming.

```text
┌──────────────┐                      ┌──────────────┐
│   Browser    │◄────── WebRTC ──────►│  MediaMTX    │
│  (Frontend)  │    (UDP, ~200ms)     │   Server     │
└──────────────┘                      └──────────────┘
```

**Key characteristics:**

- Uses UDP for low latency (no TCP handshake delays)
- Peer-to-peer architecture (even server acts as a "peer")
- Built for real-time - no buffering needed
- ~100-300ms typical latency

### HLS (HTTP Live Streaming)

HLS is Apple's adaptive streaming protocol using HTTP.

```text
┌──────────────┐                      ┌──────────────┐
│   Browser    │◄─────── HLS ────────►│  MediaMTX    │
│  (Frontend)  │   (TCP, 2-4s)        │   Server     │
└──────────────┘                      └──────────────┘
```

**Key characteristics:**

- Uses TCP (reliable but slower)
- Segments video into chunks (typically 2-6 seconds each)
- Requires buffering multiple segments before playback
- 2-10 seconds typical latency (LL-HLS improves to 2-4s)

### DTLS (Datagram Transport Layer Security)

DTLS is TLS for UDP. Since WebRTC uses UDP, regular TLS (designed for TCP) won't work.

```text
Without DTLS:
┌──────────┐         UDP Packets         ┌──────────┐
│  Browser │ ◄─────── PLAIN ────────────►│  Server  │
└──────────┘   (Anyone can intercept)    └──────────┘

With DTLS:
┌──────────┐         UDP Packets         ┌──────────┐
│  Browser │ ◄─────── ENCRYPTED ────────►│  Server  │
└──────────┘   (Protected by DTLS)       └──────────┘
```

### SRTP (Secure Real-time Transport Protocol)

SRTP encrypts the actual media (audio/video) data within WebRTC.

```text
WebRTC Security Stack:
┌─────────────────────────────────────────┐
│            Application Layer            │
├─────────────────────────────────────────┤
│  SRTP (Encrypts audio/video data)       │
├─────────────────────────────────────────┤
│  DTLS (Secures key exchange)            │
├─────────────────────────────────────────┤
│  UDP (Fast, unreliable transport)       │
└─────────────────────────────────────────┘
```

**How they work together:**

1. DTLS handshake establishes a secure channel
2. DTLS negotiates SRTP encryption keys
3. SRTP encrypts the actual media streams
4. Result: End-to-end encrypted real-time media

---

## Why iOS Safari Requires DTLS

### Browser Security Policies

Different browsers have different WebRTC security requirements:

| Browser | DTLS Required? | Notes |
|---------|----------------|-------|
| iOS Safari | **Yes** | Apple enforces strict security |
| Chrome | Optional* | Works without on localhost/LAN |
| Firefox | Optional* | Works without on localhost/LAN |
| Edge | Optional* | Works without on localhost/LAN |

*Chrome/Firefox allow unencrypted WebRTC only in "secure contexts" (localhost, HTTPS)

### Apple's Position

Apple requires DTLS for WebRTC because:

1. **No "LAN-only" exception**: Safari doesn't distinguish between LAN and internet
2. **User privacy**: All WebRTC must be encrypted to prevent eavesdropping
3. **Corporate environments**: Many "LANs" are actually monitored networks

**Result:** When MediaMTX has `webrtcEncryption: no`, iOS Safari refuses to connect and falls back to HLS.

---

## MediaMTX Configuration

### Current Configuration (No DTLS)

```yaml
# packager/mediamtx.yml
webrtcEncryption: no    # Disabled for LAN-only deployment
```

**Pros:**

- Lower CPU usage (no encryption overhead)
- Simpler setup (no certificates needed)
- Works fine for Chrome/Firefox on LAN

**Cons:**

- iOS Safari cannot use WebRTC
- iOS users get 2-4s latency instead of 200ms

### Updated Configuration (With DTLS)

```yaml
# packager/mediamtx.yml
webrtcEncryption: yes   # Enable DTLS for iOS Safari support
```

**Pros:**

- iOS Safari can use WebRTC
- All devices get ~200ms latency
- Industry-standard security

**Cons:**

- CPU overhead (~10-20ms per frame for encryption)
- DTLS handshake adds ~100-200ms on connection start
- Self-signed certificate warnings (can be ignored on LAN)

---

## Latency Breakdown

### Without DTLS (Current - iOS uses HLS)

```text
iOS Safari Connection:
1. Request index.m3u8         →  50ms
2. Parse playlist             →  10ms
3. Request first segment      →  50ms
4. Buffer 3 segments          →  2000ms (LL-HLS)
5. Start playback             →  Total: ~2-4 seconds
```

### With DTLS (iOS can use WebRTC)

```text
iOS Safari Connection:
1. SDP exchange               →  50ms
2. ICE candidate gathering    →  50ms
3. DTLS handshake             →  100-200ms
4. SRTP key exchange          →  10ms
5. First frame arrives        →  Total: ~200-400ms
```

**Net improvement: 1.5-3.5 seconds faster**

---

## Implementation Plan

### 1. Add Setting to cameras.json

```json
{
  "global_settings": {
    "webrtc_encryption": true  // New setting
  }
}
```

**Why cameras.json?**

- Single source of truth for all NVR settings
- Already used for streaming configuration
- Can be toggled without code changes

### 2. Update MediaMTX Configuration Generator

The Python code that generates `mediamtx.yml` needs to read this setting:

```python
# In the mediamtx config generator
webrtc_encryption = cameras_config.get('global_settings', {}).get('webrtc_encryption', False)

mediamtx_config['webrtcEncryption'] = 'yes' if webrtc_encryption else 'no'
```

### 3. Update Frontend (Optional)

If DTLS is enabled, iOS can attempt WebRTC before falling back:

```javascript
// In stream.js
async function getStreamTypeForDevice() {
    if (isIOSDevice()) {
        // Check if server has DTLS enabled
        const serverConfig = await fetch('/api/config/streaming').then(r => r.json());
        if (serverConfig.webrtc_encryption) {
            return 'WEBRTC';  // Try WebRTC first
        }
        return isGridView() ? 'SNAPSHOT' : 'HLS';
    }
    // ... existing logic
}
```

---

## Testing Plan

### Manual Testing

1. **Desktop Chrome/Firefox** - Verify WebRTC still works
2. **iOS Safari Grid View** - Should now use WebRTC (not snapshots)
3. **iOS Safari Fullscreen** - Should use WebRTC (not HLS)
4. **Latency measurement** - Confirm ~200ms vs previous 2-4s

### Automated Testing

```bash
# Test DTLS handshake from command line
openssl s_client -dtls -connect localhost:8889 -cert /dev/null

# Test WebRTC connectivity (requires webrtc-cli tool)
webrtc-cli connect --url ws://localhost:8889/camera_id/whip
```

---

## Rollback Plan

If DTLS causes issues:

1. Set `webrtc_encryption: false` in cameras.json
2. Restart NVR container
3. iOS will fall back to HLS (higher latency but stable)

---

## References

- [WebRTC Security Architecture (W3C)](https://www.w3.org/TR/webrtc/#security-considerations)
- [DTLS RFC 6347](https://tools.ietf.org/html/rfc6347)
- [SRTP RFC 3711](https://tools.ietf.org/html/rfc3711)
- [MediaMTX Documentation](https://github.com/bluenviron/mediamtx)
- [Apple WebRTC Requirements](https://developer.apple.com/documentation/webrtc)

---

## Summary

| Aspect | Without DTLS | With DTLS |
|--------|--------------|-----------|
| iOS Safari | HLS (2-4s latency) | WebRTC (200ms latency) |
| Chrome/Firefox | WebRTC (200ms) | WebRTC (200ms) |
| CPU overhead | None | ~10-20ms/frame |
| Setup complexity | Simple | Moderate |
| Security | Unencrypted | Encrypted |

**Recommendation:** Enable DTLS for production use. The latency improvement for iOS users far outweighs the minimal CPU overhead.
