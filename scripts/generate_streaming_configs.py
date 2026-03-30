#!/usr/bin/env python3
"""
generate_streaming_configs.py

Unified config generator for all three streaming hubs:
    - go2rtc    → /dev/shm/nvr-go2rtc/go2rtc.yaml
    - MediaMTX  → packager/mediamtx.yml
    - neolink   → config/neolink.toml

EXCLUSIVE ASSIGNMENT: Each camera appears in exactly ONE hub's config based on
its `streaming_hub` DB field. No camera can be in two hubs simultaneously.

Hub assignment rules:
    streaming_hub = 'go2rtc'   → go2rtc.yaml (requires go2rtc_source)
    streaming_hub = 'neolink'  → neolink.toml (requires type = 'reolink')
    streaming_hub = 'mediamtx' (or NULL/default) → mediamtx.yml paths

Fallback: Invalid assignments fall back to mediamtx with a warning.

Replaces:
    - scripts/generate_go2rtc_config.py
    - scripts/update_mediamtx_paths.sh
    - update_neolink_configuration.sh

Execution context:
    Called by start.sh before `docker compose up`. nvr-postgres must be running.
    Requires NVR_SECRET_KEY in the shell environment for AES key derivation.
"""

import os
import sys
import re
import subprocess
import hashlib
import base64
from datetime import datetime

try:
    from Cryptodome.Cipher import AES
except ImportError:
    print("ERROR: pycryptodomex not installed. Run: pip install pycryptodomex", file=sys.stderr)
    sys.exit(1)


# ── ANSI colours ─────────────────────────────────────────────────────────────

RED    = '\033[0;31m'
GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN   = '\033[0;36m'
NC     = '\033[0m'


# ── Encryption (mirrors credential_db_service._decrypt) ─────────────────────

def _get_encryption_key() -> bytes:
    """
    Derive 32-byte AES key from NVR_SECRET_KEY via SHA-256.

    Resolution order:
        1. NVR_SECRET_KEY environment variable
        2. nvr_settings table in DB (key='NVR_SECRET_KEY')
    """
    secret = os.environ.get('NVR_SECRET_KEY', '')
    if not secret:
        rows = _psql("SELECT value FROM nvr_settings WHERE key='NVR_SECRET_KEY';")
        if rows:
            secret = rows[0].strip()

    if not secret:
        print(f"{RED}ERROR: NVR_SECRET_KEY not found in env or nvr_settings table.{NC}", file=sys.stderr)
        sys.exit(1)

    return hashlib.sha256(secret.encode('utf-8')).digest()


def _decrypt(encrypted_b64: str) -> str:
    """
    Decrypt an AES-256-GCM encrypted string.
    Format: base64(nonce_len[1] + nonce[variable] + tag[16] + ciphertext).
    """
    key = _get_encryption_key()
    packed = base64.b64decode(encrypted_b64)
    nonce_len = packed[0]
    nonce      = packed[1 : 1 + nonce_len]
    tag        = packed[1 + nonce_len : 1 + nonce_len + 16]
    ciphertext = packed[1 + nonce_len + 16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode('utf-8')


# ── DB access (direct psql — PostgREST not yet up at startup) ────────────────

def _psql(query: str) -> list:
    """Run a psql query via docker exec and return non-empty output lines."""
    result = subprocess.run(
        ['docker', 'exec', 'nvr-postgres', 'psql',
         '-U', 'nvr_api', '-d', 'nvr', '-A', '-t', '-c', query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"{RED}ERROR: psql failed: {result.stderr.strip()}{NC}", file=sys.stderr)
        sys.exit(1)
    return [line for line in result.stdout.splitlines() if line.strip()]


# ── Credential loading ───────────────────────────────────────────────────────

def load_credentials() -> dict:
    """Load and decrypt all rows from camera_credentials table.
    Returns dict mapping (credential_key, credential_type) -> (username, password)"""
    rows = _psql(
        "SELECT credential_key, credential_type, username_enc, password_enc "
        "FROM camera_credentials;"
    )
    creds = {}
    for row in rows:
        parts = row.split('|')
        if len(parts) != 4:
            continue
        key, ctype, user_enc, pass_enc = parts
        try:
            creds[(key, ctype)] = (_decrypt(user_enc), _decrypt(pass_enc))
        except Exception as e:
            print(f"{YELLOW}WARNING: Could not decrypt credentials for '{key}': {e}{NC}", file=sys.stderr)
    return creds


def build_substitution_map(creds: dict) -> dict:
    """Map ${ENV_VAR} placeholder strings to actual credential values."""
    subs = {}

    def _add(env_var, cred_key, cred_type, index):
        pair = creds.get((cred_key, cred_type))
        if pair:
            subs[f'${{{env_var}}}'] = pair[index]

    # Service-level credential mappings
    _add('NVR_EUFY_BRIDGE_USERNAME', 'eufy_bridge', 'service', 0)
    _add('NVR_EUFY_BRIDGE_PASSWORD', 'eufy_bridge', 'service', 1)
    _add('NVR_REOLINK_API_USER',     'reolink_api', 'service', 0)
    _add('NVR_REOLINK_API_PASSWORD', 'reolink_api', 'service', 1)
    _add('NVR_SV3C_USERNAME',        'sv3c',        'service', 0)
    _add('NVR_SV3C_PASSWORD',        'sv3c',        'service', 1)
    _add('NVR_AMCREST_LOBBY_USERNAME', 'amcrest',   'service', 0)
    _add('NVR_AMCREST_LOBBY_PASSWORD', 'amcrest',   'service', 1)
    _add('NVR_REOLINK_USERNAME',     'reolink_admin', 'service', 0)
    _add('NVR_REOLINK_PASSWORD',     'reolink_admin', 'service', 1)

    return subs


def resolve_source(source: str, subs: dict) -> str:
    """Substitute all ${ENV_VAR} placeholders in a go2rtc_source string."""
    for placeholder, value in subs.items():
        source = source.replace(placeholder, value)
    unresolved = re.findall(r'\$\{[^}]+\}', source)
    if unresolved:
        print(f"{YELLOW}WARNING: Unresolved placeholders remain: {unresolved}{NC}", file=sys.stderr)
    return source


# ── go2rtc.yaml generation ───────────────────────────────────────────────────

GO2RTC_MARKER = '# VIDEO RELAY STREAMS'


def load_go2rtc_static_section(yaml_path: str) -> str:
    """Return the static (hand-maintained) portion of go2rtc.yaml,
    preserving everything before the VIDEO RELAY STREAMS marker."""
    with open(yaml_path) as f:
        lines = f.readlines()

    marker_idx = None
    for i, line in enumerate(lines):
        if GO2RTC_MARKER in line:
            marker_idx = i
            break

    if marker_idx is None:
        return ''.join(lines)

    # Walk backward to find === separator
    sep_idx = marker_idx
    while sep_idx > 0:
        if lines[sep_idx - 1].strip().startswith('# ==='):
            sep_idx -= 1
            break
        sep_idx -= 1

    return ''.join(lines[:sep_idx]).rstrip('\n') + '\n'


def _resolve_host_ip() -> str:
    """Get the host IP for WebRTC ICE candidates.
    Resolution order: NVR_HOST_IP env var → hostname -I first IP → fallback."""
    host_ip = os.environ.get('NVR_HOST_IP', '')
    if not host_ip:
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
            ips = result.stdout.strip().split()
            if ips:
                host_ip = ips[0]
        except Exception:
            pass
    if not host_ip:
        host_ip = '0.0.0.0'
        print(f"{YELLOW}WARNING: Could not determine host IP for WebRTC candidates — using 0.0.0.0{NC}")
    return host_ip


def generate_go2rtc_config(cameras, creds, subs, project_dir):
    """Generate go2rtc.yaml with only go2rtc-hub cameras."""
    shm_dir = '/dev/shm/nvr-go2rtc'
    os.makedirs(shm_dir, exist_ok=True)
    yaml_path = os.path.join(shm_dir, 'go2rtc.yaml')

    # Always read static section from template (not from previous output)
    # so ${NVR_HOST_IP} and other placeholders are resolved fresh each time.
    template_path = os.path.join(project_dir, 'config', 'go2rtc.yaml.template')
    if not os.path.exists(template_path):
        print(f"{RED}ERROR: go2rtc.yaml template not found{NC}", file=sys.stderr)
        return 0

    # Per-camera go2rtc credentials
    go2rtc_creds = {k: v for (k, t), v in creds.items() if t == 'go2rtc'}

    # Brand-level go2rtc credential keys
    go2rtc_brand_key_map = {
        'reolink': 'reolink_go2rtc',
        'amcrest': 'amcrest_go2rtc',
        'sv3c':    'sv3c_go2rtc',
        'eufy':    'eufy_go2rtc',
    }

    # Read static section from template — ${NVR_HOST_IP} is resolved by
    # go2rtc itself from its container environment (set via docker-compose).
    static_part = load_go2rtc_static_section(template_path)

    auto_lines = [
        '',
        '  # =========================================================================',
        f'  {GO2RTC_MARKER} (auto-generated by scripts/generate_streaming_configs.py)',
        '  # =========================================================================',
        '  # DO NOT EDIT BELOW THIS LINE — changes will be overwritten on restart.',
        '  # Only cameras with streaming_hub = "go2rtc" in the DB appear here.',
    ]

    count = 0
    for cam in cameras:
        serial, name, cam_type = cam['serial'], cam['name'], cam['type']
        source = cam.get('go2rtc_source', '')

        if not source:
            continue

        # Build per-camera substitution map
        per_cam_subs = dict(subs)
        if serial in go2rtc_creds:
            user, pw = go2rtc_creds[serial]
            per_cam_subs['${go2rtc_username}'] = user
            per_cam_subs['${go2rtc_password}'] = pw
        else:
            brand_key = go2rtc_brand_key_map.get(cam_type.lower(), '')
            brand_cred = creds.get((brand_key, 'service'))
            if brand_cred:
                per_cam_subs['${go2rtc_username}'] = brand_cred[0]
                per_cam_subs['${go2rtc_password}'] = brand_cred[1]

        resolved = resolve_source(source, per_cam_subs)
        auto_lines.extend([
            '',
            f'  # {name} ({serial})',
            f'  {serial}:',
            f'    - "{resolved}"',
        ])
        count += 1
        print(f"  {GREEN}+{NC} [go2rtc] {name} ({serial})")

    content = static_part + '\n'.join(auto_lines) + '\n'
    with open(yaml_path, 'w') as f:
        f.write(content)

    return count


# ── mediamtx.yml generation ──────────────────────────────────────────────────

def generate_mediamtx_config(cameras, project_dir):
    """Generate mediamtx.yml paths section with only mediamtx-hub cameras."""
    mediamtx_path = os.path.join(project_dir, 'packager', 'mediamtx.yml')

    if not os.path.exists(mediamtx_path):
        print(f"{YELLOW}WARNING: mediamtx.yml not found at {mediamtx_path}{NC}")
        return 0

    # Backup
    backup_dir = os.path.join(project_dir, 'packager', 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    import shutil
    shutil.copy2(mediamtx_path, os.path.join(backup_dir, f'mediamtx.yml.{timestamp}'))

    # Read file, preserve everything before 'paths:'
    with open(mediamtx_path) as f:
        lines = f.readlines()

    # DTLS: always webrtcEncryption: yes
    new_lines = []
    for line in lines:
        if line.startswith('webrtcEncryption:'):
            new_lines.append('webrtcEncryption: yes # DTLS is unconditional — required for WebRTC\n')
        else:
            new_lines.append(line)
    lines = new_lines

    # Find 'paths:' line
    paths_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('paths:'):
            paths_idx = i
            break

    if paths_idx is None:
        print(f"{RED}ERROR: 'paths:' section not found in mediamtx.yml{NC}", file=sys.stderr)
        return 0

    # Keep everything before paths:
    header = ''.join(lines[:paths_idx])

    # Build paths section
    paths_section = 'paths:\n'
    paths_section += '  # Auto-generated by scripts/generate_streaming_configs.py\n'
    paths_section += '  # Only cameras with streaming_hub = "mediamtx" (or default) appear here.\n'

    count = 0
    for cam in cameras:
        serial = cam['serial']
        name = cam['name']

        # Sub stream (grid view — transcoded low-res)
        paths_section += f'  {serial}:\n'
        paths_section += f'    source: publisher\n'
        # Main stream (fullscreen — native resolution passthrough)
        paths_section += f'  {serial}_main:\n'
        paths_section += f'    source: publisher\n'

        count += 1
        print(f"  {GREEN}+{NC} [mediamtx] {name} ({serial})")

    with open(mediamtx_path, 'w') as f:
        f.write(header + paths_section)

    return count


# ── neolink.toml generation ──────────────────────────────────────────────────

def generate_neolink_config(cameras, creds, project_dir):
    """Generate neolink.toml with only neolink-hub Reolink cameras.
    Credentials come from DB camera_credentials table, not env vars."""
    neolink_path = os.path.join(project_dir, 'config', 'neolink.toml')

    # Get Reolink admin credentials from DB
    reolink_admin = creds.get(('reolink_admin', 'service'))
    if not reolink_admin and cameras:
        print(f"{YELLOW}WARNING: No reolink_admin service credentials found for neolink cameras{NC}")

    header = f"""\
################################################################################
# NEOLINK CONFIGURATION - AUTO-GENERATED
#
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Source: DB cameras table (streaming_hub = 'neolink', type = 'reolink')
# Script: scripts/generate_streaming_configs.py
#
# DO NOT EDIT MANUALLY — regenerated on every start.sh
################################################################################

bind = "0.0.0.0"
bind_port = 8554
log_level = "info"

"""

    count = 0
    camera_sections = []

    for cam in cameras:
        serial = cam['serial']
        name = cam['name']
        host = cam.get('host', '')
        neolink_cfg = cam.get('neolink', {}) or {}

        # Per-camera credentials first, then reolink_admin fallback
        per_cam_cred = creds.get((serial, 'camera'))
        if per_cam_cred:
            username, password = per_cam_cred
        elif reolink_admin:
            username, password = reolink_admin
        else:
            print(f"{YELLOW}WARNING: No credentials for neolink camera {name} ({serial}){NC}")
            continue

        port = neolink_cfg.get('port', 9000)
        stream = neolink_cfg.get('stream', 'mainStream')
        buffer_size = neolink_cfg.get('buffer_size', 20)

        section = f"""\
################################################################################
# Camera: {name}
# Serial: {serial}
# Host: {host}
################################################################################

[[cameras]]
name = "{serial}"
username = "{username}"
password = "{password}"
uid = ""
address = "{host}:{port}"
stream = "{stream}"
buffer_size = {buffer_size}
buffer_duration = 1000
use_splash = true
idle_disconnect = true
push_notifications = false

  [cameras.pause]
  on_client = true
  timeout = 2.0

"""
        camera_sections.append(section)
        count += 1
        print(f"  {GREEN}+{NC} [neolink] {name} ({serial})")

    with open(neolink_path, 'w') as f:
        f.write(header)
        for section in camera_sections:
            f.write(section)
        if not camera_sections:
            f.write('# No cameras assigned to neolink hub.\n')

    return count


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    print(f"{CYAN}=== Generate exclusive streaming configs from DB ==={NC}")
    print()

    # ── Load credentials ─────────────────────────────────────────────────────
    print(f"{CYAN}Loading credentials from camera_credentials table...{NC}")
    creds = load_credentials()
    subs  = build_substitution_map(creds)
    print(f"{GREEN}✓{NC} {len(creds)} credential entries loaded")
    print()

    # ── Query all cameras ────────────────────────────────────────────────────
    print(f"{CYAN}Querying cameras from DB...{NC}")
    rows = _psql(
        "SELECT serial, COALESCE(name, serial), COALESCE(type, ''), "
        "       COALESCE(streaming_hub, 'mediamtx'), "
        "       COALESCE(go2rtc_source, ''), "
        "       COALESCE(host, ''), "
        "       COALESCE(neolink::text, '{}') "
        "FROM cameras "
        "ORDER BY name;"
    )

    if not rows:
        print(f"{YELLOW}No cameras found in DB{NC}")
        return

    # ── Split into exclusive lists ───────────────────────────────────────────
    go2rtc_cameras = []
    mediamtx_cameras = []
    neolink_cameras = []

    for row in rows:
        parts = row.split('|')
        if len(parts) != 7:
            print(f"{YELLOW}WARNING: Unexpected row format: {row}{NC}", file=sys.stderr)
            continue

        serial, name, cam_type, hub, go2rtc_source, host, neolink_json = parts
        hub = (hub or 'mediamtx').lower()

        cam = {
            'serial': serial,
            'name': name,
            'type': cam_type,
            'hub': hub,
            'go2rtc_source': go2rtc_source,
            'host': host,
        }

        # Parse neolink JSONB
        try:
            import json
            cam['neolink'] = json.loads(neolink_json) if neolink_json else {}
        except Exception:
            cam['neolink'] = {}

        # Exclusive assignment with validation
        if hub == 'go2rtc':
            if not go2rtc_source:
                print(f"{YELLOW}WARNING: {name} ({serial}) assigned to go2rtc but has no go2rtc_source "
                      f"— falling back to mediamtx{NC}")
                mediamtx_cameras.append(cam)
            else:
                go2rtc_cameras.append(cam)
        elif hub == 'neolink':
            if cam_type.lower() != 'reolink':
                print(f"{YELLOW}WARNING: {name} ({serial}) assigned to neolink but type is '{cam_type}' "
                      f"(not reolink) — falling back to mediamtx{NC}")
                mediamtx_cameras.append(cam)
            else:
                neolink_cameras.append(cam)
        else:
            # mediamtx (default)
            mediamtx_cameras.append(cam)

    total = len(go2rtc_cameras) + len(mediamtx_cameras) + len(neolink_cameras)
    print(f"{CYAN}Found {total} camera(s){NC}")
    print()

    # ── Generate configs ─────────────────────────────────────────────────────

    # go2rtc
    print(f"{CYAN}── go2rtc ({len(go2rtc_cameras)} cameras) ──{NC}")
    if go2rtc_cameras:
        go2rtc_count = generate_go2rtc_config(go2rtc_cameras, creds, subs, project_dir)
    else:
        # Still need to write an empty streams section to clear old entries
        go2rtc_count = generate_go2rtc_config([], creds, subs, project_dir)
        print(f"  (none)")
    print()

    # MediaMTX
    print(f"{CYAN}── MediaMTX ({len(mediamtx_cameras)} cameras) ──{NC}")
    if mediamtx_cameras:
        mediamtx_count = generate_mediamtx_config(mediamtx_cameras, project_dir)
    else:
        mediamtx_count = generate_mediamtx_config([], project_dir)
        print(f"  (none)")
    print()

    # Neolink
    print(f"{CYAN}── Neolink ({len(neolink_cameras)} cameras) ──{NC}")
    if neolink_cameras:
        neolink_count = generate_neolink_config(neolink_cameras, creds, project_dir)
    else:
        neolink_count = generate_neolink_config([], creds, project_dir)
        print(f"  (none)")
    print()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"{GREEN}{'═' * 50}{NC}")
    print(f"{GREEN}  MediaMTX: {mediamtx_count:>2} cameras{NC}")
    print(f"{GREEN}  go2rtc:   {go2rtc_count:>2} cameras{NC}")
    print(f"{GREEN}  neolink:  {neolink_count:>2} cameras{NC}")
    print(f"{GREEN}  Total:    {mediamtx_count + go2rtc_count + neolink_count:>2} cameras (exclusive, 0 conflicts){NC}")
    print(f"{GREEN}{'═' * 50}{NC}")


if __name__ == '__main__':
    main()
