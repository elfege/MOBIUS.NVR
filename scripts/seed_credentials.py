#!/usr/bin/env python3
"""
seed_credentials.py

Seeds ALL camera credentials from environment variables into the
camera_credentials table (AES-256-GCM encrypted).

Run at startup (after nvr-postgres is ready, before docker compose up).
Idempotent — uses INSERT ... ON CONFLICT DO UPDATE (upsert).

This bridges the gap between AWS Secrets Manager (host env vars loaded by
get_cameras_credentials) and the DB-centric credential architecture.
Credentials seeded here become available to all credential providers in
services/credentials/ and to generate_streaming_configs.py.

Credential categories:
    1. Service-level (shared across cameras of the same vendor):
       - reolink_admin / service — Reolink admin (Baichuan, RTSP admin)
       - reolink_api / service   — Reolink API user (RTSP api-user)
       - amcrest / service       — Amcrest cameras
       - sv3c / service          — SV3C cameras
       - unifi_protect / service — UniFi Protect NVR
       - eufy_bridge / service   — Eufy Security bridge

    2. Per-camera (Eufy cameras have individual credentials):
       - {serial} / camera — auto-detected from NVR_EUFY_CAMERA_{serial}_*

    3. Per-camera go2rtc (copied from service creds for go2rtc hub cameras):
       - {serial} / go2rtc — seeded from matching service credential
"""

import os
import re
import sys
import base64
import hashlib
import subprocess

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Random import get_random_bytes
except ImportError:
    print("ERROR: pycryptodomex not installed. Run: pip install pycryptodomex", file=sys.stderr)
    sys.exit(1)


# ── Service-level credential map ─────────────────────────────────────────────
# (username_env, password_env, credential_key, credential_type, vendor)
CREDENTIAL_MAP = [
    # Reolink admin (Baichuan protocol, RTSP admin access)
    ('NVR_REOLINK_USERNAME',        'NVR_REOLINK_PASSWORD',         'reolink_admin', 'service', 'reolink'),
    # Reolink API user (RTSP api-user access, MJPEG snap polling)
    ('NVR_REOLINK_API_USER',        'NVR_REOLINK_API_PASSWORD',     'reolink_api',   'service', 'reolink'),
    # Amcrest
    ('NVR_AMCREST_LOBBY_USERNAME',  'NVR_AMCREST_LOBBY_PASSWORD',   'amcrest',       'service', 'amcrest'),
    # SV3C
    ('NVR_SV3C_USERNAME',           'NVR_SV3C_PASSWORD',            'sv3c',          'service', 'sv3c'),
    # UniFi Protect
    ('NVR_PROTECT_USERNAME',        'NVR_PROTECT_SERVER_PASSWORD',  'unifi_protect', 'service', 'unifi'),
    # Eufy Security bridge
    ('NVR_EUFY_BRIDGE_USERNAME',    'NVR_EUFY_BRIDGE_PASSWORD',     'eufy_bridge',   'service', 'eufy'),
]

# Pattern for per-camera Eufy credentials: NVR_EUFY_CAMERA_{SERIAL}_USERNAME
EUFY_CAMERA_PATTERN = re.compile(r'^NVR_EUFY_CAMERA_([A-Z0-9]+)_USERNAME$')

# Pattern for UniFi per-camera RTSP token alias: NVR_CAMERA_{SERIAL}_TOKEN_ALIAS
UNIFI_TOKEN_PATTERN = re.compile(r'^NVR_CAMERA_([a-f0-9]+)_TOKEN_ALIAS$')


# ── ANSI colours ─────────────────────────────────────────────────────────────

RED    = '\033[0;31m'
GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN   = '\033[0;36m'
NC     = '\033[0m'


# ── DB access ─────────────────────────────────────────────────────────────────

def _psql(query: str, exit_on_error: bool = True) -> list:
    result = subprocess.run(
        ['docker', 'exec', 'nvr-postgres', 'psql',
         '-U', 'nvr_api', '-d', 'nvr', '-A', '-t', '-c', query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        if exit_on_error:
            print(f"{RED}ERROR: psql failed: {result.stderr.strip()}{NC}", file=sys.stderr)
            sys.exit(1)
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the database before trying to query it."""
    rows = _psql(
        f"SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        f"WHERE table_name = '{table_name}');",
        exit_on_error=False
    )
    return rows and rows[0].strip() == 't'


# ── Encryption ────────────────────────────────────────────────────────────────

def _get_encryption_key() -> bytes:
    # ALWAYS read from DB first — env may have a DIFFERENT NVR_SECRET_KEY
    # from AWS Secrets Manager that doesn't match the one the app uses.
    # The DB value is the canonical encryption key set by app.py on first run.
    secret = ''
    rows = _psql("SELECT value FROM nvr_settings WHERE key='NVR_SECRET_KEY';",
                  exit_on_error=False)
    if rows:
        secret = rows[0].strip()
    if not secret:
        secret = os.environ.get('NVR_SECRET_KEY', '')
    if not secret:
        print(f"{RED}ERROR: NVR_SECRET_KEY not found in DB or env.{NC}", file=sys.stderr)
        sys.exit(1)
    return hashlib.sha256(secret.encode('utf-8')).digest()


def _encrypt(plaintext: str) -> str:
    """
    Encrypt with AES-256-GCM.
    Format: base64(nonce_len[1] + nonce[12] + tag[16] + ciphertext)
    Matches credential_db_service._encrypt().
    """
    key = _get_encryption_key()
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode('utf-8'))
    packed = bytes([len(nonce)]) + nonce + tag + ciphertext
    return base64.b64encode(packed).decode('utf-8')


# ── Upsert ────────────────────────────────────────────────────────────────────

def seed_credential(credential_key: str, credential_type: str,
                    username: str, password: str, vendor: str,
                    label: str = '') -> bool:
    """
    Insert credential ONLY if it doesn't already exist.
    Never overwrites existing credentials — UI-entered values take priority.
    Returns True if inserted, False if already existed.
    """
    user_enc = _encrypt(username)
    pass_enc = _encrypt(password)
    label_escaped = label.replace("'", "''")
    query = (
        "INSERT INTO camera_credentials "
        "  (credential_key, credential_type, vendor, username_enc, password_enc, label) "
        f" VALUES ('{credential_key}', '{credential_type}', '{vendor}', "
        f"         '{user_enc}', '{pass_enc}', '{label_escaped}') "
        "ON CONFLICT (credential_key, credential_type) DO NOTHING;"
    )
    # DO NOTHING returns 0 rows affected when credential already exists
    rows = _psql(query)
    # Check if it was actually inserted by querying
    check = _psql(
        f"SELECT 1 FROM camera_credentials "
        f"WHERE credential_key = '{credential_key}' "
        f"  AND credential_type = '{credential_type}' "
        f"  AND label LIKE '%seeded from env%';",
        exit_on_error=False
    )
    if check:
        print(f"  {GREEN}✓{NC} {credential_key}/{credential_type}/{vendor} → seeded (new)")
        return True
    else:
        print(f"  {CYAN}·{NC} {credential_key}/{credential_type}/{vendor} → already exists, skipped")
        return False


# ── Per-camera Eufy credentials ──────────────────────────────────────────────

def seed_eufy_per_camera_credentials() -> int:
    """
    Scan env vars for NVR_EUFY_CAMERA_{SERIAL}_USERNAME / _PASSWORD patterns
    and seed them as (serial, 'camera', 'eufy') credentials.

    These are per-camera Eufy RTSP credentials used by the Eufy credential
    provider when connecting to individual cameras.

    Returns the number of per-camera entries seeded.
    """
    seeded = 0
    seen_serials = set()

    for env_key in sorted(os.environ.keys()):
        match = EUFY_CAMERA_PATTERN.match(env_key)
        if not match:
            continue

        serial = match.group(1)
        if serial in seen_serials:
            continue
        seen_serials.add(serial)

        username = os.environ.get(f'NVR_EUFY_CAMERA_{serial}_USERNAME', '').strip()
        password = os.environ.get(f'NVR_EUFY_CAMERA_{serial}_PASSWORD', '').strip()

        if not username or not password:
            print(f"  {YELLOW}SKIP{NC} {serial}/camera/eufy "
                  f"(NVR_EUFY_CAMERA_{serial}_USERNAME or _PASSWORD not set)")
            continue

        seed_credential(serial, 'camera', username, password, 'eufy',
                          label=f'Eufy camera {serial} (seeded from env)')
        seeded += 1

    return seeded


# ── Per-camera UniFi RTSP token aliases ───────────────────────────────────────

def seed_unifi_token_aliases() -> int:
    """
    Scan env vars for NVR_CAMERA_{SERIAL}_TOKEN_ALIAS patterns and seed them
    as (serial, 'camera', 'unifi') credentials.

    UniFi Protect uses RTSP token aliases (not username/password) for stream
    authentication. The token alias is stored in the username_enc field;
    password_enc is set to a placeholder since it's not used.

    Returns the number of token aliases seeded.
    """
    seeded = 0

    for env_key in sorted(os.environ.keys()):
        match = UNIFI_TOKEN_PATTERN.match(env_key)
        if not match:
            continue

        serial = match.group(1)
        token_alias = os.environ.get(env_key, '').strip()

        if not token_alias:
            print(f"  {YELLOW}SKIP{NC} {serial}/camera/unifi "
                  f"(NVR_CAMERA_{serial}_TOKEN_ALIAS is empty)")
            continue

        # Token alias stored as username, password is unused placeholder
        seed_credential(serial, 'camera', token_alias, 'unused', 'unifi',
                        label=f'UniFi RTSP token alias for {serial} (seeded from env)')
        seeded += 1

    return seeded


# ── Per-camera go2rtc credentials ────────────────────────────────────────────

def seed_per_camera_go2rtc_credentials() -> int:
    """
    Seed per-camera go2rtc credentials for cameras that have go2rtc_source set
    but no (serial, 'go2rtc') entry in camera_credentials yet.

    Uses the camera's vendor type to look up the appropriate global service credential
    and copies it as the per-camera go2rtc credential. Idempotent — NOT EXISTS guard
    prevents overwriting credentials the user has set explicitly via the UI.

    Camera type → global service credential key mapping:
        reolink → reolink_admin / service   (Baichuan protocol needs admin)
        amcrest → amcrest / service
        sv3c    → sv3c / service

    Returns the number of per-camera entries seeded.
    """
    # Check if cameras table has go2rtc_source column (may not exist yet before migrations)
    rows = _psql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'cameras' AND column_name = 'go2rtc_source';",
        exit_on_error=False
    )
    if not rows:
        print(f"  {YELLOW}SKIP{NC} go2rtc seeding — cameras.go2rtc_source column not yet created")
        return 0

    rows = _psql(
        "SELECT c.serial, c.type FROM cameras c "
        "WHERE c.go2rtc_source IS NOT NULL AND c.go2rtc_source <> '' "
        "  AND c.streaming_hub = 'go2rtc' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM camera_credentials cc "
        "    WHERE cc.credential_key = c.serial AND cc.credential_type = 'go2rtc'"
        "  );",
        exit_on_error=False
    )

    if not rows:
        return 0

    # Resolve global service credentials once
    # Reolink: use admin credentials (reolink_admin), not API user (reolink_api).
    # baichuan:// and reolink:// native protocols require the camera admin account.
    service_key_map = {
        'reolink': ('reolink_admin', 'service', 'reolink'),
        'amcrest': ('amcrest',       'service', 'amcrest'),
        'sv3c':    ('sv3c',          'service', 'sv3c'),
    }

    seeded = 0
    for row in rows:
        parts = row.split('|')
        if len(parts) != 2:
            continue
        serial, cam_type = parts
        cam_type = cam_type.lower()

        mapping = service_key_map.get(cam_type)
        if not mapping:
            print(f"  {YELLOW}SKIP{NC} {serial} — no global service credential mapping for type '{cam_type}'")
            continue

        svc_key, svc_type, vendor = mapping
        svc_rows = _psql(
            f"SELECT username_enc, password_enc FROM camera_credentials "
            f"WHERE credential_key = '{svc_key}' AND credential_type = '{svc_type}';",
            exit_on_error=False
        )
        if not svc_rows:
            print(f"  {YELLOW}SKIP{NC} {serial} — global service credential '{svc_key}/{svc_type}' not found in DB")
            continue

        svc_parts = svc_rows[0].split('|')
        if len(svc_parts) != 2:
            continue
        user_enc, pass_enc = svc_parts

        # Insert directly (already encrypted) — same ciphertext is fine since key is the same
        query = (
            "INSERT INTO camera_credentials "
            "  (credential_key, credential_type, vendor, username_enc, password_enc, label) "
            f" VALUES ('{serial}', 'go2rtc', '{vendor}', '{user_enc}', '{pass_enc}', "
            f"         'go2rtc credentials for {serial} (seeded from {svc_key})') "
            "ON CONFLICT (credential_key, credential_type) DO NOTHING;"
        )
        _psql(query)
        print(f"  {GREEN}✓{NC} {serial} → seeded go2rtc credentials from {svc_key}/{svc_type}")
        seeded += 1

    return seeded


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"{CYAN}=== Seed service credentials from env → camera_credentials ==={NC}")
    print()

    # Guard: check if camera_credentials table exists (migrations may not have run yet)
    if not _table_exists('camera_credentials'):
        print(f"  {YELLOW}SKIP{NC} camera_credentials table does not exist yet (run migrations first)")
        print(f"  Credentials will be seeded on next startup after migrations.")
        return

    # ── 1. Service-level credentials ──────────────────────────────────────────
    seeded = 0
    skipped = 0

    for user_env, pass_env, cred_key, cred_type, vendor in CREDENTIAL_MAP:
        username = os.environ.get(user_env, '').strip()
        password = os.environ.get(pass_env, '').strip()

        if not username or not password:
            print(f"  {YELLOW}SKIP{NC} {cred_key}/{cred_type} "
                  f"({user_env} or {pass_env} not set in env)")
            skipped += 1
            continue

        seed_credential(cred_key, cred_type, username, password, vendor,
                          label=f'{vendor} service credential (seeded from env)')
        seeded += 1

    print()
    print(f"{GREEN}✓{NC} Seeded {seeded} service credential(s), skipped {skipped}")

    # ── 2. Per-camera Eufy credentials ────────────────────────────────────────
    print()
    print(f"{CYAN}Seeding per-camera Eufy credentials from env...{NC}")
    eufy_seeded = seed_eufy_per_camera_credentials()
    print(f"{GREEN}✓{NC} {eufy_seeded} per-camera Eufy credential(s) seeded")

    # ── 3. Per-camera UniFi RTSP token aliases ──────────────────────────────────
    print()
    print(f"{CYAN}Seeding per-camera UniFi RTSP token aliases from env...{NC}")
    unifi_seeded = seed_unifi_token_aliases()
    print(f"{GREEN}✓{NC} {unifi_seeded} per-camera UniFi token alias(es) seeded")

    # ── 4. Per-camera go2rtc credentials ──────────────────────────────────────
    print()
    print(f"{CYAN}Seeding per-camera go2rtc credentials from global service creds...{NC}")
    go2rtc_seeded = seed_per_camera_go2rtc_credentials()
    print(f"{GREEN}✓{NC} {go2rtc_seeded} per-camera go2rtc credential(s) seeded")

    # ── Summary ───────────────────────────────────────────────────────────────
    total = seeded + eufy_seeded + unifi_seeded + go2rtc_seeded
    print()
    print(f"{GREEN}✓{NC} Total: {total} credential(s) seeded into camera_credentials")


if __name__ == '__main__':
    main()
