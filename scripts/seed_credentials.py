#!/usr/bin/env python3
"""
seed_credentials.py

Seeds service-level credentials from environment variables into the
camera_credentials table (AES-256-GCM encrypted).

Run at startup (after nvr-postgres is ready, before docker compose up).
Idempotent — uses INSERT ... ON CONFLICT DO UPDATE (upsert).

This bridges the gap between AWS Secrets Manager (host env vars loaded by
get_cameras_credentials) and the DB-centric credential architecture.
Credentials added here become available to generate_go2rtc_config.py and
all credential providers in services/credentials/.

Credential map (env_var_prefix → credential_key, credential_type):
    NVR_REOLINK_USERNAME / NVR_REOLINK_PASSWORD  → reolink_admin / service
    (extend the CREDENTIAL_MAP below to add more)
"""

import os
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


# ── Credential map: (username_env, password_env, credential_key, credential_type, vendor)
CREDENTIAL_MAP = [
    ('NVR_REOLINK_USERNAME', 'NVR_REOLINK_PASSWORD', 'reolink_admin', 'service', 'reolink'),
]


# ── ANSI colours ─────────────────────────────────────────────────────────────

RED    = '\033[0;31m'
GREEN  = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN   = '\033[0;36m'
NC     = '\033[0m'


# ── DB access ─────────────────────────────────────────────────────────────────

def _psql(query: str) -> list:
    result = subprocess.run(
        ['docker', 'exec', 'nvr-postgres', 'psql',
         '-U', 'nvr_api', '-d', 'nvr', '-A', '-t', '-c', query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"{RED}ERROR: psql failed: {result.stderr.strip()}{NC}", file=sys.stderr)
        sys.exit(1)
    return [line for line in result.stdout.splitlines() if line.strip()]


# ── Encryption ────────────────────────────────────────────────────────────────

def _get_encryption_key() -> bytes:
    secret = os.environ.get('NVR_SECRET_KEY', '')
    if not secret:
        rows = _psql("SELECT value FROM nvr_settings WHERE key='NVR_SECRET_KEY';")
        if rows:
            secret = rows[0].strip()
    if not secret:
        print(f"{RED}ERROR: NVR_SECRET_KEY not found in env or nvr_settings.{NC}", file=sys.stderr)
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

def upsert_credential(credential_key: str, credential_type: str,
                      username: str, password: str, vendor: str) -> None:
    user_enc = _encrypt(username)
    pass_enc = _encrypt(password)
    query = (
        "INSERT INTO camera_credentials "
        "  (credential_key, credential_type, vendor, username_enc, password_enc) "
        f" VALUES ('{credential_key}', '{credential_type}', '{vendor}', '{user_enc}', '{pass_enc}') "
        "ON CONFLICT (credential_key, credential_type) DO UPDATE "
        "  SET username_enc = EXCLUDED.username_enc, "
        "      password_enc = EXCLUDED.password_enc, "
        "      vendor = EXCLUDED.vendor;"
    )
    _psql(query)
    print(f"  {GREEN}✓{NC} {credential_key}/{credential_type}/{vendor} → seeded")


# ── Main ──────────────────────────────────────────────────────────────────────

def seed_per_camera_go2rtc_credentials() -> int:
    """
    Seed per-camera go2rtc credentials for cameras that have go2rtc_source set
    but no (serial, 'go2rtc') entry in camera_credentials yet.

    Uses the camera's vendor type to look up the appropriate global service credential
    and copies it as the per-camera go2rtc credential. Idempotent — NOT EXISTS guard
    prevents overwriting credentials the user has set explicitly via the UI.

    Camera type → global service credential key mapping:
        reolink → reolink_api / service   (API user for rtsp://)
        amcrest → amcrest / service
        sv3c    → sv3c / service

    Returns the number of per-camera entries seeded.
    """
    # Query cameras with go2rtc_source that lack per-camera go2rtc credentials
    rows = _psql(
        "SELECT c.serial, c.type FROM cameras c "
        "WHERE c.go2rtc_source IS NOT NULL AND c.go2rtc_source <> '' "
        "  AND c.streaming_hub = 'go2rtc' "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM camera_credentials cc "
        "    WHERE cc.credential_key = c.serial AND cc.credential_type = 'go2rtc'"
        "  );"
    )

    if not rows:
        return 0

    # Resolve global service credentials once
    # Reolink: use admin credentials (reolink_admin), not API user (reolink_api).
    # baichuan:// and reolink:// native protocols require the camera admin account.
    # The admin creds are seeded first by the CREDENTIAL_MAP block above.
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
            f"WHERE credential_key = '{svc_key}' AND credential_type = '{svc_type}';"
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


def main():
    print(f"{CYAN}=== Seed service credentials from env → camera_credentials ==={NC}")
    print()

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

        upsert_credential(cred_key, cred_type, username, password, vendor)
        seeded += 1

    print()
    print(f"{GREEN}✓{NC} Seeded {seeded} service credential(s), skipped {skipped}")

    # ── Per-camera go2rtc credential seeding ──────────────────────────────────
    print()
    print(f"{CYAN}Seeding per-camera go2rtc credentials from global service creds...{NC}")
    go2rtc_seeded = seed_per_camera_go2rtc_credentials()
    print(f"{GREEN}✓{NC} {go2rtc_seeded} per-camera go2rtc credential(s) seeded")


if __name__ == '__main__':
    main()
