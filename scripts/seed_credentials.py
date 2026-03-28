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


# ── Credential map: (username_env, password_env) → (credential_key, credential_type)
CREDENTIAL_MAP = [
    ('NVR_REOLINK_USERNAME', 'NVR_REOLINK_PASSWORD', 'reolink_admin', 'service'),
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
                      username: str, password: str) -> None:
    user_enc = _encrypt(username)
    pass_enc = _encrypt(password)
    query = (
        "INSERT INTO camera_credentials "
        "  (credential_key, credential_type, username_enc, password_enc) "
        f" VALUES ('{credential_key}', '{credential_type}', '{user_enc}', '{pass_enc}') "
        "ON CONFLICT (credential_key, credential_type) DO UPDATE "
        "  SET username_enc = EXCLUDED.username_enc, "
        "      password_enc = EXCLUDED.password_enc;"
    )
    _psql(query)
    print(f"  {GREEN}✓{NC} {credential_key}/{credential_type} → seeded")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"{CYAN}=== Seed service credentials from env → camera_credentials ==={NC}")
    print()

    seeded = 0
    skipped = 0

    for user_env, pass_env, cred_key, cred_type in CREDENTIAL_MAP:
        username = os.environ.get(user_env, '').strip()
        password = os.environ.get(pass_env, '').strip()

        if not username or not password:
            print(f"  {YELLOW}SKIP{NC} {cred_key}/{cred_type} "
                  f"({user_env} or {pass_env} not set in env)")
            skipped += 1
            continue

        upsert_credential(cred_key, cred_type, username, password)
        seeded += 1

    print()
    print(f"{GREEN}✓{NC} Seeded {seeded} credential(s), skipped {skipped}")


if __name__ == '__main__':
    main()
