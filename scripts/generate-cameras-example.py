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
CAMERAS_EXAMPLE = PROJECT_ROOT / "cameras.json.example"


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


def main():
    """Main entry point."""
    if not CAMERAS_JSON.exists():
        print(f"Error: {CAMERAS_JSON} not found", file=sys.stderr)
        sys.exit(1)

    # Load real cameras.json
    with open(CAMERAS_JSON, "r") as f:
        data = json.load(f)

    # Sanitize each camera (key is "devices" in cameras.json)
    sanitized_data = {"devices": {}}

    for index, (serial, camera_data) in enumerate(data.get("devices", {}).items()):
        camera_type = camera_data.get("type", "unknown")
        sanitized_camera = sanitize_camera(camera_data, index, camera_type)

        # Use the new fake serial as the key
        new_serial = sanitized_camera.get("serial", generate_uuid())
        sanitized_data["devices"][new_serial] = sanitized_camera

    # Copy any top-level non-devices keys (like settings, metadata)
    for key, value in data.items():
        if key != "devices":
            if key == "last_updated":
                sanitized_data[key] = "EXAMPLE_FILE"
            elif key == "total_devices":
                sanitized_data[key] = len(sanitized_data["devices"])
            else:
                sanitized_data[key] = value

    # Write example file
    with open(CAMERAS_EXAMPLE, "w") as f:
        json.dump(sanitized_data, f, indent=2)
        f.write("\n")  # Trailing newline

    print(f"Generated {CAMERAS_EXAMPLE}")
    print(f"  - Sanitized {len(sanitized_data['devices'])} cameras")

    return 0


if __name__ == "__main__":
    sys.exit(main())
