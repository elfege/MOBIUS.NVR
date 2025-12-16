# UniFi Protect ONVIF Protocol Support: Technical Analysis

UniFi's ONVIF implementation has a critical directional distinction: **UniFi Protect accepts incoming ONVIF cameras** (added September 2024), but **UniFi cameras themselves cannot act as ONVIF servers** for third-party systems. This fundamental asymmetry defines the entire integration landscape.

## Does UniFi Protect support ONVIF protocol for camera control?

**Yes, with significant limitations.** UniFi Protect version 5.0.20+ (released September 2024) introduced ONVIF client support, allowing third-party ONVIF-compliant cameras to be adopted into the Protect ecosystem. This marked Ubiquiti's first departure from their traditionally closed architecture.

The implementation supports basic video streaming and continuous recording with no per-camera licensing fees—a major differentiator from competitors like Synology and QNAP. However, Protect only implements **ONVIF Profile S** (streaming), providing minimal functionality compared to native UniFi cameras. Critical features like motion detection, PTZ control, audio streaming, and AI analytics are explicitly unsupported for third-party ONVIF cameras.

Adoption requires enabling "Discover Third-Party Cameras" in Settings > System. Auto-discovery works only on the same subnet; cross-VLAN deployments require manual "Advanced Adoption" by IP address. The feature remains marked as "Beta Labs" with active development addressing stability issues through frequent updates (13 micro-releases in the first week).

## Do UniFi cameras support ONVIF protocol?

**No. UniFi cameras categorically do not support ONVIF.** All UniFi camera models—including G3, G4, G5, AI series, and both PTZ variants (G4-PTZ and G5-PTZ)—are designed exclusively for the UniFi Protect ecosystem using proprietary protocols. This is not an oversight but an architectural decision maintained since UniFi Video 3.0 removed direct RTSP access in 2013-2014.

Official Ubiquiti datasheets for all camera models explicitly omit ONVIF from specifications. Third-party verification from certified trainers and community documentation confirms these cameras cannot function as ONVIF servers. UniFi cameras cannot be added to Blue Iris, Frigate, Synology Surveillance Station, or any third-party NVR via ONVIF discovery.

The only integration path for UniFi cameras with external systems is through RTSP re-streaming from the Protect controller (not directly from cameras), which requires the entire Protect ecosystem and provides only video feeds without camera control capabilities. For G3-series cameras, a legacy "Standalone" mode enables direct RTSP access, but this feature was removed in G4/G5 generations.

## Which ONVIF services are supported by UniFi?

UniFi Protect's ONVIF implementation is **severely feature-restricted**, supporting only core streaming functions:

**Supported services:**

- Live video streaming (H.264 and H.265 codecs)
- Continuous recording to Protect storage
- Basic playback and timeline review
- Dual-stream detection (high and low quality)
- Snapshot retrieval (if camera supports)

**Explicitly unsupported services:**

- **PTZ control** – No pan, tilt, zoom commands
- **PTZ presets** – Cannot save or recall positions
- **Continuous move** – No ONVIF PTZ operations
- **Absolute positioning** – No coordinate-based control
- **Motion detection events** – ONVIF motion triggers ignored
- **Audio streaming** – Completely disabled
- **Two-way audio** – Non-functional
- **Smart detections** – No AI analytics from third-party cameras

The sole exception is the **AI Port** accessory ($199), which adds PTZ support for ONVIF cameras when used as an intermediary device. However, each AI Port currently supports only one ONVIF camera, making this an expensive workaround at $199 per camera.

Ubiquiti's official documentation explicitly states: *"Motion detections, PTZ control, Audio, and other advanced features are not supported on third party cameras."* This positions Protect's ONVIF support as a "basic video recorder" suitable for compliance recording or gradual migration scenarios, not full-featured security operations.

## ONVIF port configuration for UniFi cameras

**For third-party ONVIF cameras adopted into Protect:**

- Standard ONVIF ports are **80** (HTTP) or **8000** (common alternative)
- RTSP streams typically use port **554** (standard)
- Port specification required for manual adoption across VLANs: `192.168.1.100:8000`
- ONVIF service endpoint format: `http://<camera_ip>/onvif/device_service`

**For UniFi cameras (not ONVIF):**

- **Port 7447** – Non-secure RTSP streams from Protect controller
- **Port 7441** – Secure RTSPS streams with TLS encryption
- **Port 80** – Camera web interface (limited functionality)
- RTSP URL format: `rtsp://<protect_ip>:7447/<camera_id>_<quality>`
  - Quality suffixes: `_0` (highest), `_1` (medium), `_2` (lowest)
  - Example: `rtsp://192.168.1.1:7447/5adede05e4b096b258e7ba98_0`

**Critical integration note:** Third-party NVR systems like Blue Iris require using `rtsp://` protocol on port 7447 rather than `rtsps://` on port 7441. The `?enableSrtp` parameter must be removed from URLs for compatibility. UniFi cameras use non-standard ports specifically to prevent direct third-party access, forcing integration through the Protect controller.

## Known limitations and quirks with UniFi ONVIF implementation

**RTSP stream monopolization** is the most significant issue. When Protect adopts an ONVIF camera, it consumes both main and substreams (2 streams total), often exhausting the camera's stream capacity. Users report that concurrent access by other applications (VLC, Blue Iris, Frigate) becomes impossible after adoption. This isn't Protect explicitly blocking streams but resource exhaustion on budget cameras. The only workaround is using higher-end cameras with 3+ simultaneous stream support or running parallel NVR systems before adoption completes.

**Cross-VLAN discovery failure** represents a fundamental architectural limitation. ONVIF auto-discovery uses multicast WS-Discovery protocol, which doesn't traverse Layer 3 boundaries. While manual "Advanced Adoption" by IP address works, community reports describe ONVIF implementation as "not doing well across VLANs" with authentication failures and connection instability. Best practice requires placing cameras and the Protect console on the same subnet, limiting network segmentation options for security-conscious deployments.

**Authentication complexity** manifests in multiple ways. Third-party cameras must use **Digest authentication with WS-UsernameToken** mode—a specific requirement that causes failures if misconfigured. Hikvision cameras require explicitly setting authentication to "Digest & ws-username token" rather than defaults. Blank passwords are rejected by Protect. Date/time synchronization is critical; authentication fails silently if camera clocks are incorrect. Many cameras require creating dedicated ONVIF credentials separate from admin accounts.

**Vendor-specific incompatibilities** are widespread:

- **Reolink**: ONVIF disabled by default, must enable via Reolink app first; stream stability issues reported as "buggy at best"
- **Hikvision**: Requires separate ONVIF user creation with Operator/Media rights
- **Dahua**: Good compatibility but SMD (Smart Motion Detection) and IVS events don't trigger recordings
- **Amcrest**: Mixed results with frequent disconnections

**Software stability concerns** persist despite rapid updates. Early adopters (September-October 2024) reported system crashes when enabling ONVIF, required cold reboots after adding cameras, black video after adoption, broken thumbnails, and timeline scrubbing issues. While Ubiquiti released 13 micro-updates in the first week addressing many bugs, the feature remains marked "Beta Labs" with production deployment risks.

**Stream selection limitations** prevent optimization. Protect automatically detects streams but users cannot manually specify which to use. This causes high-resolution cameras to record low-quality substreams instead of main streams. Multi-lens cameras (like Reolink Duo) show only a single stitched view without individual lens configuration. Stream settings changes require removing and re-adding the entire camera.

**Hardware performance degradation** affects resource-constrained consoles. UDM SE devices struggle with 11+ third-party 4K cameras, displaying "approaching limit" warnings. Third-party cameras consume 2-3x more CPU resources than native UniFi cameras due to transcoding overhead. User experience shows lower refresh rates in dashboard views and occasional feed freezing.

## Can ONVIF control UniFi cameras through Protect or must cameras be accessed directly?

**UniFi cameras cannot be controlled via ONVIF under any circumstances** because they fundamentally do not implement ONVIF server functionality. The question's premise doesn't apply to UniFi cameras—they exist solely within the proprietary Protect ecosystem.

For **third-party ONVIF cameras adopted into Protect**, cameras must be **accessed directly at the device level** during initial configuration. The workflow requires:

1. Accessing camera's native web interface via IP address
2. Enabling ONVIF in camera settings (location varies by manufacturer)
3. Creating ONVIF username and password credentials
4. Configuring authentication method (Digest + WS-UsernameToken)
5. Setting accurate date/time/timezone
6. Then adopting the camera into Protect

After adoption, Protect provides only streaming and recording capabilities—no camera management functions. All configuration changes (exposure, white balance, overlays, motion zones, privacy masks) must still be performed through the camera's native interface. Protect cannot adjust camera settings via ONVIF. This creates a fragmented management experience requiring switching between Protect and individual camera web UIs for administration.

**Alternative integration paths** for those requiring ONVIF control:

- **Home Assistant**: Use direct ONVIF integration to cameras (bypassing Protect) for full PTZ and motion detection support
- **Blue Iris/Frigate**: Connect cameras directly via RTSP/ONVIF rather than routing through Protect
- **AI Port**: Adds PTZ control for one ONVIF camera at $199 per unit

The community consensus strongly recommends managing ONVIF cameras directly through superior NVR platforms (Blue Iris, Frigate) rather than routing through Protect's limited implementation.

## What authentication method does UniFi use for ONVIF?

UniFi Protect requires **Digest authentication combined with WS-UsernameToken** (also called "Digest & ws-username token" or "WS-Security UsernameToken with Digest"). This dual-method approach follows ONVIF Profile S specifications but requires explicit configuration on the camera side—many cameras default to digest-only or basic authentication, causing adoption failures.

**Configuration requirements:**

- ONVIF username and password must be explicitly created (admin credentials may not work)
- Authentication mode must be set to "Digest & ws-username token" in camera settings
- Blank passwords are rejected—minimum password strength varies by camera
- Date/time/timezone must be accurately synchronized (authentication includes timestamps)
- Some cameras require assigning specific ONVIF user roles (Operator or Media User)

**Vendor-specific authentication notes:**

- **Hikvision**: Official documentation specifies changing from default to "Digest & ws-username token"; separate ONVIF user recommended rather than using admin account
- **Dahua/Amcrest**: Generally compatible with standard digest authentication
- **Reolink**: Requires enabling ONVIF first via mobile app before web interface exposes settings
- **Sony**: Requires enabling video encoders for each ONVIF profile

**Authentication failure troubleshooting:**

- Verify ONVIF enabled on camera
- Confirm authentication method matches Digest + WS-UsernameToken
- Check date/time synchronization (especially cameras blocked from NTP servers)
- Test credentials with third-party ONVIF tool (ONVIF Device Manager) before Protect adoption
- Review camera logs for specific authentication errors

**Security considerations:** The authentication mechanism is sufficiently secure for internal networks but cameras should be VLAN-isolated and blocked from WAN access. Protect stores ONVIF credentials encrypted in its configuration database. Multi-factor authentication is not supported in ONVIF protocol specifications.

## Technical integration guidance for developers

**For developers integrating with UniFi Protect via ONVIF**, recognize that Protect acts as an **ONVIF client only**—it cannot export UniFi cameras as ONVIF sources. Integration paths include:

**Path 1: Adding third-party cameras to Protect (ONVIF → Protect)**

- Target Protect 5.0.20+ API endpoints
- Enable "Discover Third-Party Cameras" via Settings API
- Use ONVIF WS-Discovery for same-subnet detection
- Manual adoption via IP requires POST to adoption endpoint with credentials
- Expect only streaming/recording functionality; no control plane access

**Path 2: Accessing UniFi cameras from external systems (Protect → RTSP)**

- Use Protect's RTSP re-streaming on port 7447
- Authenticate via Protect API to obtain camera IDs and stream URLs
- Format: `rtsp://<protect_ip>:7447/<camera_id>_<quality>`
- Cannot use ONVIF—protocol unsupported by UniFi cameras
- PTZ control requires Protect's proprietary WebSocket API

**Path 3: Home automation integration**

- Home Assistant: Use official UniFi Protect integration (not ONVIF)
- Node-RED: Access via Protect's REST API
- MQTT: Use third-party bridges (unifi-cam-proxy, scrypted)

**Network topology recommendations:**

- **Isolated camera VLAN** (e.g., 192.168.50.0/24)
- **Separate Protect VLAN** (e.g., 192.168.40.0/24)
- **Layer 3 routing** with firewall rules:
  - Allow camera VLAN → Protect console IP (specific ports: 80, 443, 554, 7447)
  - Block camera VLAN → WAN (prevent Chinese cameras phoning home)
  - Block camera VLAN → gateway/other networks
- **mDNS reflection** not required (ONVIF uses unicast for adoption)

**Testing methodology:**

1. Verify ONVIF functionality with ONVIF Device Manager before Protect adoption
2. Test RTSP stream access with VLC using `rtsp://<camera_ip>:554/<path>`
3. Confirm date/time synchronization via NTP or manual setting
4. Validate authentication with digest credentials
5. Monitor Protect logs during adoption for specific error messages
6. Use Wireshark to capture ONVIF SOAP messages if troubleshooting

**Current API limitations (Protect 5.0.x):**

- No API endpoints for PTZ control of third-party cameras
- Cannot query ONVIF motion events programmatically
- Third-party camera settings not exposed via Protect API
- Must use direct camera management for configuration changes

**Future development considerations:** Ubiquiti's roadmap suggests motion detection and PTZ support are planned but unscheduled. Build integrations with fallback paths to direct camera access for features Protect doesn't support. Monitor release notes for each Protect update as ONVIF implementation is under active development.

## Conclusion

UniFi's ONVIF implementation represents a **strategic compromise** between ecosystem openness and feature differentiation. Protect's ability to accept third-party ONVIF cameras (no licensing fees, Profile S support, automated discovery) provides migration flexibility for existing deployments, but severe limitations—no motion detection, no PTZ, no audio—restrict it to basic recording scenarios.

The critical asymmetry—**Protect accepts ONVIF but UniFi cameras don't provide ONVIF**—reveals Ubiquiti's competitive strategy: enable foot-in-the-door installations with legacy cameras while incentivizing gradual replacement with native UniFi cameras for full functionality. This approach works for SMB migrations and compliance recording but fails for serious security operations requiring motion-triggered recording, PTZ patrol patterns, or AI analytics from third-party cameras.

**For developers and integrators**, the path forward depends on requirements: use native UniFi cameras for full Protect integration, or choose ONVIF-compliant brands (Dahua, Hikvision, Axis) with superior NVR platforms (Blue Iris, Frigate) if advanced features, PTZ control, or multi-system flexibility are required. Attempting to bridge these worlds through Protect's limited ONVIF implementation or RTSP re-streaming introduces complexity without meaningful benefit.
