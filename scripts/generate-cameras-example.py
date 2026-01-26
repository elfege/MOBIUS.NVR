#!/usr/bin/env python3
"""
Generate cameras.json.example from cameras.json

Sanitizes sensitive data by replacing:
- Camera names with generic "Camera_1", "Camera_2", etc.
- Serial numbers with UUIDs
- IP addresses with 192.168.1.x pattern
- MAC addresses with example MACs
- Hostnames with generic names

Called by pre-commit hook to keep example file in sync with schema.
"""

import json
import uuid
import re
import sys
from pathlib import Path

# Path configuration
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CAMERAS_JSON = CONFIG_DIR / "cameras.json"
CAMERAS_EXAMPLE = CONFIG_DIR / "cameras.json.example"
RECORDING_SETTINGS_JSON = CONFIG_DIR / "recording_settings.json"
RECORDING_SETTINGS_EXAMPLE = CONFIG_DIR / "recording_settings.json.example"
GO2RTC_YAML = CONFIG_DIR / "go2rtc.yaml"
GO2RTC_EXAMPLE = CONFIG_DIR / "go2rtc.yaml.example"


def generate_uuid():
    """Generate a random UUID string for serial numbers."""
    return str(uuid.uuid4()).upper().replace("-", "")[:16]


def sanitize_ip(index):
    """Generate example IP address."""
    return f"192.168.1.{100 + index}"


def sanitize_mac(index):
    """Generate example MAC address."""
    return f"AA:BB:CC:DD:EE:{index:02X}"


def sanitize_camera(camera_data, index, camera_type):
    """
    Sanitize a single camera entry.

    Preserves structure and _note fields, replaces identifying information.
    """
    sanitized = {}

    # Generate consistent fake identifiers
    fake_serial = generate_uuid()
    fake_name = f"{camera_type.upper()}_Camera_{index + 1}"
    fake_ip = sanitize_ip(index)
    fake_mac = sanitize_mac(index)

    for key, value in camera_data.items():
        # Keep documentation/note fields as-is
        if key.startswith("_"):
            sanitized[key] = value
            continue

        # Sanitize specific fields
        if key == "name":
            sanitized[key] = fake_name
        elif key == "serial":
            sanitized[key] = fake_serial
        elif key == "host":
            sanitized[key] = fake_ip
        elif key == "mac":
            sanitized[key] = fake_mac
        elif key == "station":
            # Eufy station ID
            sanitized[key] = f"T8010P{generate_uuid()[:10]}"
        elif key == "camera_id":
            # Eufy camera ID
            model = camera_data.get('model') or '400'
            sanitized[key] = f"T8{model[:3]}P{generate_uuid()[:10]}"
        elif key == "id":
            # UniFi/other internal ID
            sanitized[key] = generate_uuid()[:24].lower()
        elif key == "power_supply_device_id":
            if value:
                sanitized[key] = f"device_{index + 1}"
            else:
                sanitized[key] = value
        elif key == "ll_hls" and isinstance(value, dict):
            # Sanitize nested ll_hls config
            sanitized[key] = sanitize_ll_hls(value, fake_serial, fake_ip)
        elif key == "rtsp" and isinstance(value, dict):
            sanitized[key] = sanitize_rtsp(value, fake_ip)
        elif key == "neolink" and isinstance(value, dict):
            sanitized[key] = sanitize_neolink(value, fake_serial)
        elif key == "two_way_audio" and isinstance(value, dict):
            # Keep two_way_audio structure, just sanitize any stream names
            sanitized[key] = sanitize_two_way_audio(value, fake_serial)
        else:
            # Keep other fields as-is (capabilities, settings, etc.)
            sanitized[key] = value

    return sanitized


def sanitize_ll_hls(ll_hls_data, fake_serial, fake_ip):
    """Sanitize ll_hls configuration."""
    sanitized = {}

    for key, value in ll_hls_data.items():
        if key.startswith("_"):
            sanitized[key] = value
        elif key == "publisher" and isinstance(value, dict):
            pub = value.copy()
            if "path" in pub:
                pub["path"] = fake_serial
            if "host" in pub and pub["host"] not in ["nvr-packager", "localhost"]:
                pub["host"] = fake_ip
            sanitized[key] = pub
        else:
            sanitized[key] = value

    return sanitized


def sanitize_rtsp(rtsp_data, fake_ip):
    """Sanitize RTSP configuration."""
    sanitized = {}

    for key, value in rtsp_data.items():
        if key.startswith("_"):
            sanitized[key] = value
        elif key in ["main", "sub"] and isinstance(value, str):
            # Replace IP in RTSP URL
            sanitized[key] = re.sub(
                r"rtsp://([^:]+):([^@]+)@[\d.]+",
                f"rtsp://user:password@{fake_ip}",
                value
            )
        else:
            sanitized[key] = value

    return sanitized


def sanitize_neolink(neolink_data, fake_serial):
    """Sanitize neolink configuration."""
    sanitized = neolink_data.copy()
    # Neolink config usually just has port/buffer settings, keep as-is
    return sanitized


def sanitize_two_way_audio(twa_data, fake_serial):
    """Sanitize two_way_audio configuration."""
    sanitized = {}

    for key, value in twa_data.items():
        if key.startswith("_"):
            sanitized[key] = value
        elif key == "onvif" and isinstance(value, dict):
            onvif = value.copy()
            if "go2rtc_stream" in onvif and onvif["go2rtc_stream"]:
                onvif["go2rtc_stream"] = f"camera_{fake_serial[:8].lower()}"
            sanitized[key] = onvif
        else:
            sanitized[key] = value

    return sanitized


def sanitize_go2rtc():
    """
    Sanitize go2rtc.yaml by replacing IPs and camera-specific names with examples.
    """
    if not GO2RTC_YAML.exists():
        print(f"Warning: {GO2RTC_YAML} not found, skipping")
        return

    # Create a sanitized example go2rtc.yaml
    example_content = '''# go2rtc Configuration for NVR System
# Purpose: ONVIF AudioBackChannel (two-way audio) for cameras that support it
#
# Architecture (Flow 3):
#   Browser -> Flask WebSocket -> go2rtc API -> ONVIF Backchannel -> Camera
#   Camera -> RTSP -> MediaMTX (unchanged for video)
#
# go2rtc handles ONLY the audio backchannel, MediaMTX continues serving video.
#
# Documentation: https://github.com/AlexxIT/go2rtc

api:
  listen: ":1984"           # Web UI and API
  origin: "*"               # Allow CORS for NVR integration

rtsp:
  listen: ":8555"           # RTSP server (internal use)

webrtc:
  listen: ":8556"           # WebRTC signaling
  ice_servers:
    - urls: ["stun:stun.l.google.com:19302"]

# Stream definitions for cameras with ONVIF AudioBackChannel
# Note: Credentials are injected via environment variables in docker-compose
#
# IMPORTANT: We only configure ONVIF here for backchannel (two-way audio).
# Video/RTSP is handled by MediaMTX - do NOT add RTSP sources here as it would
# create a second connection to cameras that only support one RTSP session.
streams:
  # Example Camera 1 (has ONVIF two-way audio)
  # ONVIF-only: backchannel for two-way audio, no RTSP (MediaMTX handles video)
  camera_1:
    - "onvif://admin:${CAMERA1_PASSWORD}@192.168.1.101:8080"

  # Example Camera 2 (has ONVIF two-way audio)
  # ONVIF-only: backchannel for two-way audio, no RTSP (MediaMTX handles video)
  camera_2:
    - "onvif://${CAMERA2_USERNAME}:${CAMERA2_PASSWORD}@192.168.1.102:80"

  # Example Camera 3 (indoor PTZ with speaker)
  # ONVIF-only: backchannel for two-way audio, no RTSP (MediaMTX handles video)
  camera_3:
    - "onvif://${CAMERA3_USERNAME}:${CAMERA3_PASSWORD}@192.168.1.103:8000"

# Note: UniFi cameras are NOT included here because they're accessed via
# UniFi Protect controller, not direct RTSP/ONVIF. UniFi Protect has its own
# two-way audio mechanism via the Protect API.
'''

    with open(GO2RTC_EXAMPLE, "w") as f:
        f.write(example_content)

    print(f"Generated {GO2RTC_EXAMPLE}")
    print(f"  - Sanitized go2rtc configuration")


def sanitize_recording_settings(cameras_serial_map):
    """
    Sanitize recording_settings.json using the same serial mapping.

    Args:
        cameras_serial_map: Dict mapping original serial -> sanitized serial
    """
    if not RECORDING_SETTINGS_JSON.exists():
        print(f"Warning: {RECORDING_SETTINGS_JSON} not found, skipping")
        return

    with open(RECORDING_SETTINGS_JSON, "r") as f:
        data = json.load(f)

    sanitized = {}
    count = 0

    for original_serial, settings in data.items():
        # Use the same mapping from cameras.json if available
        if original_serial in cameras_serial_map:
            new_serial = cameras_serial_map[original_serial]
        else:
            # Generate new UUID for serials not in cameras.json
            new_serial = generate_uuid()

        sanitized[new_serial] = settings
        count += 1

    with open(RECORDING_SETTINGS_EXAMPLE, "w") as f:
        json.dump(sanitized, f, indent=2)
        f.write("\n")

    print(f"Generated {RECORDING_SETTINGS_EXAMPLE}")
    print(f"  - Sanitized {count} recording settings")


def main():
    """Main entry point."""
    if not CAMERAS_JSON.exists():
        print(f"Error: {CAMERAS_JSON} not found", file=sys.stderr)
        sys.exit(1)

    # Load real cameras.json
    with open(CAMERAS_JSON, "r") as f:
        data = json.load(f)

    # Sanitize each camera (key is "devices" in cameras.json)
    # Also build mapping of original serial -> sanitized serial for recording_settings
    sanitized_data = {"devices": {}}
    serial_mapping = {}  # original_serial -> new_serial

    for index, (original_serial, camera_data) in enumerate(data.get("devices", {}).items()):
        camera_type = camera_data.get("type", "unknown")
        sanitized_camera = sanitize_camera(camera_data, index, camera_type)

        # Use the new fake serial as the key
        new_serial = sanitized_camera.get("serial", generate_uuid())
        sanitized_data["devices"][new_serial] = sanitized_camera

        # Track mapping for recording_settings.json
        serial_mapping[original_serial] = new_serial

    # Copy any top-level non-devices keys (like settings, metadata)
    for key, value in data.items():
        if key != "devices":
            if key == "last_updated":
                sanitized_data[key] = "EXAMPLE_FILE"
            elif key == "total_devices":
                sanitized_data[key] = len(sanitized_data["devices"])
            else:
                sanitized_data[key] = value

    # Write cameras.json.example
    with open(CAMERAS_EXAMPLE, "w") as f:
        json.dump(sanitized_data, f, indent=2)
        f.write("\n")  # Trailing newline

    print(f"Generated {CAMERAS_EXAMPLE}")
    print(f"  - Sanitized {len(sanitized_data['devices'])} cameras")

    # Also sanitize recording_settings.json using the same serial mapping
    sanitize_recording_settings(serial_mapping)

    # Also sanitize go2rtc.yaml
    sanitize_go2rtc()

    return 0


if __name__ == "__main__":
    sys.exit(main())
