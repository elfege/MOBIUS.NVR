#!/usr/bin/env python3
"""
generate_go2rtc_config.py

Generates the auto-generated section of config/go2rtc.yaml from the DB.

Replaces scripts/update_go2rtc_config.sh for credential-bearing streams.

Why Python instead of bash:
    Credentials are stored AES-256-GCM encrypted in the camera_credentials table.
    Bash cannot decrypt them. This script queries psql directly (PostgREST is not
    yet up at startup time), decrypts credentials using the same logic as
    credential_db_service.py, substitutes ${ENV_VAR} placeholders in go2rtc_source
    values, and writes the resolved YAML. No env vars needed in the go2rtc container.

Execution context:
    Called by start.sh before `docker compose up`. nvr-postgres must be running.
    Requires NVR_SECRET_KEY in the shell environment for AES key derivation.

go2rtc.yaml structure:
    Static section (hand-maintained): API, RTSP/WebRTC ports, ONVIF backchannel,
    doorbell. Not touched by this script.

    Auto-generated section (written by this script): video relay streams for all
    cameras with a non-null go2rtc_source in the DB. ${ENV_VAR} placeholders are
    resolved to actual credentials from the camera_credentials table.

Boundary marker: '# VIDEO RELAY STREAMS'
    Everything above the === separator before this marker is preserved.
    Everything at or below it is regenerated.

Credential mapping (go2rtc_source placeholder -> camera_credentials lookup):

    Per-camera (resolved first, set via UI Credentials tab):
    ${go2rtc_username} / ${go2rtc_password} -> (camera_serial, 'go2rtc')

    Global service-level fallbacks (legacy ${NVR_*} style):
    ${NVR_EUFY_BRIDGE_USERNAME/PASSWORD}    -> ('eufy_bridge',   'service')
    ${NVR_REOLINK_API_USER/PASSWORD}        -> ('reolink_api',   'service')
    ${NVR_REOLINK_USERNAME/PASSWORD}        -> ('reolink_admin', 'service')  # admin, for reolink:// protocol
    ${NVR_SV3C_USERNAME/PASSWORD}           -> ('sv3c',          'service')
    ${NVR_AMCREST_LOBBY_USERNAME/PASSWORD}  -> ('amcrest',       'service')
"""

import os
import sys
import re
import subprocess
import hashlib
import base64

try:
    from Cryptodome.Cipher import AES
except ImportError:
    print("ERROR: pycryptodomex not installed. Run: pip install pycryptodomex", file=sys.stderr)
    sys.exit(1)


# ── ANSI colours (matches update_go2rtc_config.sh output style) ─────────────

RED    = '\033[0;31m'
GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN   = '\033[0;36m'
NC     = '\033[0m'


# ── Encryption (mirrors credential_db_service._decrypt) ─────────────────────

def _get_encryption_key() -> bytes:
    """
    Derive 32-byte AES key from NVR_SECRET_KEY via SHA-256.

    Resolution order (mirrors app.py _get_or_create_secret_key):
        1. NVR_SECRET_KEY environment variable
        2. nvr_settings table in DB (key='NVR_SECRET_KEY')
    """
    secret = os.environ.get('NVR_SECRET_KEY', '')
    if not secret:
        # Query DB — NVR_SECRET_KEY is stored in nvr_settings (set by app.py on first boot)
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
    Identical to credential_db_service._decrypt().
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
    """
    Run a psql query via docker exec and return non-empty output lines.
    Exits with error if psql fails.
    """
    result = subprocess.run(
        ['docker', 'exec', 'nvr-postgres', 'psql',
         '-U', 'nvr_api', '-d', 'nvr', '-A', '-t', '-c', query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"{RED}ERROR: psql failed: {result.stderr.strip()}{NC}", file=sys.stderr)
        sys.exit(1)
    return [line for line in result.stdout.splitlines() if line.strip()]


def load_credentials() -> dict:
    """
    Load and decrypt all rows from camera_credentials table.

    Returns:
        dict mapping (credential_key, credential_type) -> (username, password)
    """
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


def load_go2rtc_credentials(creds: dict) -> dict:
    """
    Extract per-camera go2rtc credentials from the already-loaded credentials dict.

    These are stored as (camera_serial, 'go2rtc') in camera_credentials — set via
    the UI Credentials tab → go2rtc Credentials section.

    Args:
        creds: Full credentials dict from load_credentials()

    Returns:
        dict mapping camera_serial -> (username, password)
    """
    go2rtc_creds = {}
    for (key, ctype), pair in creds.items():
        if ctype == 'go2rtc':
            go2rtc_creds[key] = pair
    return go2rtc_creds


def build_substitution_map(creds: dict) -> dict:
    """
    Map ${ENV_VAR} placeholder strings to actual credential values.

    The go2rtc_source column stores source URLs with ${ENV_VAR} placeholders
    for credentials. This maps each placeholder to the corresponding value
    from camera_credentials, using the same lookup keys as the Python
    credential providers in services/credentials/.

    Placeholders not found in creds are left unresolved (warning emitted later).
    """
    subs = {}

    def _add(env_var: str, cred_key: str, cred_type: str, index: int):
        """Add a substitution if the credential exists."""
        pair = creds.get((cred_key, cred_type))
        if pair:
            subs[f'${{{env_var}}}'] = pair[index]
        else:
            print(f"{YELLOW}WARNING: No credential found for {cred_key}/{cred_type} "
                  f"(needed for ${{{env_var}}}){NC}", file=sys.stderr)

    # Eufy bridge (account credentials for eufy:// P2P protocol)
    _add('NVR_EUFY_BRIDGE_USERNAME', 'eufy_bridge', 'service', 0)
    _add('NVR_EUFY_BRIDGE_PASSWORD', 'eufy_bridge', 'service', 1)

    # Reolink API credentials (shared across all Reolink cameras)
    _add('NVR_REOLINK_API_USER',     'reolink_api', 'service', 0)
    _add('NVR_REOLINK_API_PASSWORD', 'reolink_api', 'service', 1)

    # SV3C (brand-level credentials)
    _add('NVR_SV3C_USERNAME', 'sv3c', 'service', 0)
    _add('NVR_SV3C_PASSWORD', 'sv3c', 'service', 1)

    # Amcrest (service-level credentials)
    _add('NVR_AMCREST_LOBBY_USERNAME', 'amcrest', 'service', 0)
    _add('NVR_AMCREST_LOBBY_PASSWORD', 'amcrest', 'service', 1)

    # Reolink admin (for reolink:// native protocol — seeded from AWS by seed_credentials.py)
    _add('NVR_REOLINK_USERNAME', 'reolink_admin', 'service', 0)
    _add('NVR_REOLINK_PASSWORD', 'reolink_admin', 'service', 1)

    return subs


def resolve_source(source: str, subs: dict) -> str:
    """
    Substitute all ${ENV_VAR} placeholders in a go2rtc_source string.

    Args:
        source: Raw go2rtc_source value from DB (may contain ${ENV_VAR})
        subs:   Mapping of placeholder -> resolved value

    Returns:
        Source URL with credentials substituted in place.
    """
    for placeholder, value in subs.items():
        source = source.replace(placeholder, value)

    # Warn about any remaining unresolved placeholders
    unresolved = re.findall(r'\$\{[^}]+\}', source)
    if unresolved:
        print(f"{YELLOW}WARNING: Unresolved placeholders remain: {unresolved}{NC}", file=sys.stderr)

    return source


# ── go2rtc.yaml static section preservation ─────────────────────────────────

MARKER = '# VIDEO RELAY STREAMS'


def load_static_section(yaml_path: str) -> str:
    """
    Return the static (hand-maintained) portion of go2rtc.yaml.

    Preserves everything from the top of the file up to (not including) the
    '# ===' separator line that precedes the VIDEO RELAY STREAMS marker block.
    If the marker is absent, returns the entire file (append mode).
    """
    with open(yaml_path) as f:
        lines = f.readlines()

    # Find the marker line
    marker_idx = None
    for i, line in enumerate(lines):
        if MARKER in line:
            marker_idx = i
            break

    if marker_idx is None:
        print(f"{YELLOW}WARNING: Marker '{MARKER}' not found — appending to end of file{NC}")
        return ''.join(lines)

    # Walk backward from the marker to find the === separator block start
    sep_idx = marker_idx
    while sep_idx > 0:
        candidate = lines[sep_idx - 1].strip()
        if candidate.startswith('# ==='):
            sep_idx -= 1
            break
        sep_idx -= 1

    return ''.join(lines[:sep_idx])


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    yaml_path   = os.path.join(project_dir, 'config', 'go2rtc.yaml')

    print(f"{CYAN}=== Generate go2rtc.yaml video relay streams from DB ==={NC}")
    print()

    if not os.path.exists(yaml_path):
        print(f"{RED}ERROR: go2rtc.yaml not found at {yaml_path}{NC}", file=sys.stderr)
        print(f"{YELLOW}Hint: Copy config/go2rtc.yaml.example to config/go2rtc.yaml{NC}", file=sys.stderr)
        sys.exit(1)

    # ── Load + decrypt credentials ───────────────────────────────────────────
    print(f"{CYAN}Loading credentials from camera_credentials table...{NC}")
    creds      = load_credentials()
    subs       = build_substitution_map(creds)      # global service-level placeholders
    go2rtc_creds = load_go2rtc_credentials(creds)   # per-camera go2rtc credentials
    print(f"{GREEN}✓{NC} {len(creds)} credential entries loaded, {len(subs)} global substitutions, "
          f"{len(go2rtc_creds)} per-camera go2rtc credential(s)")
    print()

    # ── Query cameras with go2rtc_source ─────────────────────────────────────
    print(f"{CYAN}Querying cameras with go2rtc_source from DB...{NC}")
    rows = _psql(
        "SELECT serial, COALESCE(name, serial), go2rtc_source "
        "FROM cameras "
        "WHERE go2rtc_source IS NOT NULL AND go2rtc_source <> '' "
        "  AND streaming_hub = 'go2rtc' "
        "ORDER BY name;"
    )

    if not rows:
        print(f"{YELLOW}No cameras with go2rtc_source found in DB — nothing to generate.{NC}")
        return

    camera_count = len(rows)
    print(f"{CYAN}Found {camera_count} camera(s) with go2rtc_source{NC}")
    print()

    # ── Preserve static section ───────────────────────────────────────────────
    static_part = load_static_section(yaml_path)

    # ── Build auto-generated section ─────────────────────────────────────────
    auto_lines = [
        '',
        '  # =========================================================================',
        f'  {MARKER} (auto-generated by scripts/generate_go2rtc_config.py)',
        '  # =========================================================================',
        '  # DO NOT EDIT BELOW THIS LINE — changes will be overwritten on restart.',
        '  #',
        '  # Source of truth: DB cameras.go2rtc_source column.',
        '  # Credentials resolved from camera_credentials table (AES-256-GCM).',
        '  # go2rtc connects to each camera (single consumer per the single-consumer',
        '  # policy) and re-exports:',
        '  #   - WebRTC  → browser (primary viewing)',
        '  #   - RTSP :8555/{serial} → FFmpeg recording + motion detection',
        '  #',
        '  # ${ENV_VAR} placeholders from go2rtc_source are resolved by this script;',
        '  # no env vars are needed in the nvr-go2rtc container environment.',
    ]

    for row in rows:
        parts = row.split('|')
        if len(parts) != 3:
            print(f"{YELLOW}WARNING: Unexpected row format: {row}{NC}", file=sys.stderr)
            continue
        serial, name, source = parts

        # Build per-camera substitution map: global subs + per-camera go2rtc credentials.
        # Per-camera ${go2rtc_username} / ${go2rtc_password} take precedence — they are set
        # via the UI Credentials tab and allow different credentials per camera.
        # Falls back to legacy ${NVR_*} global placeholders if no per-camera creds are set.
        per_cam_subs = dict(subs)
        if serial in go2rtc_creds:
            user, pw = go2rtc_creds[serial]
            per_cam_subs['${go2rtc_username}'] = user
            per_cam_subs['${go2rtc_password}'] = pw
        else:
            # No per-camera go2rtc creds — warn only if the source URL uses the placeholders
            if '${go2rtc_username}' in source or '${go2rtc_password}' in source:
                print(f"{YELLOW}WARNING: {name} ({serial}) uses ${{go2rtc_username}}/${{go2rtc_password}} "
                      f"but no per-camera go2rtc credentials are set in the DB{NC}", file=sys.stderr)

        resolved = resolve_source(source, per_cam_subs)
        auto_lines.extend([
            '',
            f'  # {name} ({serial})',
            f'  {serial}:',
            f'    - "{resolved}"',
        ])
        print(f"  {GREEN}+{NC} {name} ({serial})")

    # ── Write updated go2rtc.yaml ─────────────────────────────────────────────
    content = static_part + '\n'.join(auto_lines) + '\n'
    with open(yaml_path, 'w') as f:
        f.write(content)

    print()
    print(f"{GREEN}✓{NC} go2rtc.yaml updated with {camera_count} video relay stream(s)")
    print(f"{GREEN}✓{NC} Credentials resolved from DB — no env vars needed in go2rtc container")


if __name__ == '__main__':
    main()
