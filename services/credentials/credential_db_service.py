#!/usr/bin/env python3
"""
Credential Database Service

Provides CRUD operations for camera credentials stored in PostgreSQL
via PostgREST. Credentials are encrypted at rest using AES-256-GCM
with the NVR_SECRET_KEY as the key derivation source.

Architecture:
    - Credentials are stored in the camera_credentials table
    - Encryption uses pycryptodomex (AES-GCM), already a project dependency
    - The encryption key is derived from NVR_SECRET_KEY via SHA-256
    - PostgREST is the DB access layer (same pattern as rest of the app)
    - In-memory cache avoids repeated DB lookups during normal operation
"""

import os
import json
import base64
import hashlib
import logging
import threading
from typing import Optional, Tuple, Dict

import requests
from Cryptodome.Cipher import AES

logger = logging.getLogger(__name__)

# PostgREST endpoint (same as app.py)
POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')

# Thread-safe in-memory cache: {(credential_key, credential_type): (username, password)}
_cache: Dict[Tuple[str, str], Tuple[str, str]] = {}
_cache_lock = threading.Lock()
_cache_loaded = False

# Lazy-initialized encryption key (derived from NVR_SECRET_KEY)
_enc_key: Optional[bytes] = None


def _get_encryption_key() -> bytes:
    """
    Derive a 32-byte AES key from NVR_SECRET_KEY via SHA-256.
    The key is derived once and cached for the process lifetime.
    """
    global _enc_key
    if _enc_key is not None:
        return _enc_key

    secret = os.environ.get('NVR_SECRET_KEY', '')
    if not secret:
        # Fallback: read from nvr_settings table (set by app.py on first boot)
        try:
            import requests as _req
            postgrest_url = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')
            resp = _req.get(
                f"{postgrest_url}/nvr_settings",
                params={'key': 'eq.NVR_SECRET_KEY', 'select': 'value'},
                timeout=3
            )
            if resp.status_code == 200:
                rows = resp.json()
                if rows:
                    secret = rows[0].get('value', '')
        except Exception:
            pass

    if not secret:
        raise RuntimeError(
            "NVR_SECRET_KEY is required for credential encryption. "
            "Not found in env or nvr_settings table."
        )
    _enc_key = hashlib.sha256(secret.encode('utf-8')).digest()
    return _enc_key


def _encrypt(plaintext: str) -> str:
    """
    Encrypt a string using AES-256-GCM.
    Returns base64-encoded: nonce_len (1 byte) + nonce + tag (16 bytes) + ciphertext.
    """
    key = _get_encryption_key()
    cipher = AES.new(key, AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode('utf-8'))
    nonce = cipher.nonce
    # Pack: 1-byte nonce length + nonce + tag + ciphertext, then base64 encode
    packed = bytes([len(nonce)]) + nonce + tag + ciphertext
    return base64.b64encode(packed).decode('ascii')


def _decrypt(encrypted_b64: str) -> str:
    """
    Decrypt an AES-256-GCM encrypted string.
    Input: base64-encoded nonce_len (1) + nonce (variable) + tag (16) + ciphertext.
    """
    key = _get_encryption_key()
    packed = base64.b64decode(encrypted_b64)
    nonce_len = packed[0]
    nonce = packed[1:1 + nonce_len]
    tag = packed[1 + nonce_len:1 + nonce_len + 16]
    ciphertext = packed[1 + nonce_len + 16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return plaintext.decode('utf-8')


def _postgrest_session() -> requests.Session:
    """Create a requests session with PostgREST headers."""
    session = requests.Session()
    session.headers.update({
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    })
    return session


def load_all_credentials() -> Dict[Tuple[str, str], Tuple[str, str]]:
    """
    Load all credentials from the database into memory cache.
    Called once at startup. Returns the cache dict.
    """
    global _cache, _cache_loaded
    try:
        resp = _postgrest_session().get(
            f"{POSTGREST_URL}/camera_credentials",
            params={'select': 'credential_key,credential_type,username_enc,password_enc,label'}
        )
        if resp.status_code != 200:
            logger.warning(f"Failed to load credentials from DB: HTTP {resp.status_code}")
            return _cache

        rows = resp.json()
        with _cache_lock:
            _cache.clear()
            for row in rows:
                try:
                    username = _decrypt(row['username_enc'])
                    password = _decrypt(row['password_enc'])
                    cache_key = (row['credential_key'], row['credential_type'])
                    _cache[cache_key] = (username, password)
                except Exception as e:
                    logger.error(
                        f"Failed to decrypt credentials for {row['credential_key']}: {e}. "
                        f"This may indicate NVR_SECRET_KEY has changed since credentials were stored."
                    )
            _cache_loaded = True
            logger.info(f"Loaded {len(_cache)} credentials from database")

    except requests.ConnectionError:
        logger.warning("PostgREST not available — credential DB cache not loaded")
    except Exception as e:
        logger.error(f"Error loading credentials from DB: {e}")

    return _cache


def get_credential(credential_key: str, credential_type: str = 'camera') -> Tuple[Optional[str], Optional[str]]:
    """
    Retrieve credentials from cache (or DB if cache miss).

    Args:
        credential_key: Camera serial or service identifier
        credential_type: 'camera' or 'service'

    Returns:
        (username, password) or (None, None) if not found
    """
    global _cache_loaded

    # Ensure cache is loaded on first access
    if not _cache_loaded:
        load_all_credentials()

    cache_key = (credential_key, credential_type)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    # Cache miss — try direct DB lookup (in case credential was added after startup)
    try:
        resp = _postgrest_session().get(
            f"{POSTGREST_URL}/camera_credentials",
            params={
                'credential_key': f'eq.{credential_key}',
                'credential_type': f'eq.{credential_type}',
                'select': 'username_enc,password_enc'
            }
        )
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                username = _decrypt(rows[0]['username_enc'])
                password = _decrypt(rows[0]['password_enc'])
                with _cache_lock:
                    _cache[cache_key] = (username, password)
                return (username, password)
    except Exception as e:
        logger.debug(f"DB lookup failed for {credential_key}: {e}")

    return (None, None)


def store_credential(
    credential_key: str,
    username: str,
    password: str,
    vendor: str,
    credential_type: str = 'camera',
    label: Optional[str] = None
) -> bool:
    """
    Store or update credentials in the database.

    Args:
        credential_key: Camera serial or service identifier
        username: Plaintext username
        password: Plaintext password
        vendor: Camera vendor ('eufy', 'reolink', 'unifi', 'amcrest', 'sv3c', 'system')
        credential_type: 'camera' or 'service'
        label: Human-readable label for UI

    Returns:
        True if stored successfully
    """
    try:
        encrypted_user = _encrypt(username)
        encrypted_pass = _encrypt(password)

        payload = {
            'credential_key': credential_key,
            'credential_type': credential_type,
            'vendor': vendor,
            'username_enc': encrypted_user,
            'password_enc': encrypted_pass,
            'label': label or credential_key
        }

        # Upsert: use PostgREST's on-conflict resolution
        session = _postgrest_session()
        session.headers.update({
            'Prefer': 'resolution=merge-duplicates,return=representation'
        })
        resp = session.post(
            f"{POSTGREST_URL}/camera_credentials",
            json=payload
        )

        if resp.status_code in (200, 201):
            # Update cache
            with _cache_lock:
                _cache[(credential_key, credential_type)] = (username, password)
            logger.info(f"Stored credentials for {credential_key} ({vendor}/{credential_type})")
            return True
        elif resp.status_code == 409:
            # Conflict — row already exists. Update via PATCH instead.
            patch_resp = session.patch(
                f"{POSTGREST_URL}/camera_credentials",
                params={
                    'credential_key': f'eq.{credential_key}',
                    'credential_type': f'eq.{credential_type}'
                },
                json={
                    'username_enc': encrypted_user,
                    'password_enc': encrypted_pass,
                    'vendor': vendor,
                    'label': label or credential_key
                }
            )
            if patch_resp.status_code in (200, 204):
                with _cache_lock:
                    _cache[(credential_key, credential_type)] = (username, password)
                logger.info(f"Updated credentials for {credential_key} ({vendor}/{credential_type})")
                return True
            else:
                logger.error(f"Failed to update credentials: HTTP {patch_resp.status_code} — {patch_resp.text}")
                return False
        else:
            logger.error(f"Failed to store credentials: HTTP {resp.status_code} — {resp.text}")
            return False

    except Exception as e:
        logger.error(f"Error storing credentials for {credential_key}: {e}")
        return False


def delete_credential(credential_key: str, credential_type: str = 'camera') -> bool:
    """
    Delete credentials from the database.

    Args:
        credential_key: Camera serial or service identifier
        credential_type: 'camera' or 'service'

    Returns:
        True if deleted successfully
    """
    try:
        resp = _postgrest_session().delete(
            f"{POSTGREST_URL}/camera_credentials",
            params={
                'credential_key': f'eq.{credential_key}',
                'credential_type': f'eq.{credential_type}'
            }
        )
        if resp.status_code in (200, 204):
            with _cache_lock:
                _cache.pop((credential_key, credential_type), None)
            logger.info(f"Deleted credentials for {credential_key}")
            return True
        else:
            logger.error(f"Failed to delete credentials: HTTP {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error deleting credentials for {credential_key}: {e}")
        return False


def invalidate_cache():
    """Clear the in-memory cache, forcing reload on next access."""
    global _cache_loaded
    with _cache_lock:
        _cache.clear()
        _cache_loaded = False
    logger.info("Credential cache invalidated")


def get_all_credentials_for_vendor(vendor: str) -> Dict[str, Tuple[str, str]]:
    """
    Get all credentials for a specific vendor.

    Args:
        vendor: Camera vendor name

    Returns:
        Dict of {credential_key: (username, password)}
    """
    if not _cache_loaded:
        load_all_credentials()

    result = {}
    with _cache_lock:
        for (key, ctype), (user, pwd) in _cache.items():
            # We need vendor info — check DB for vendor filter
            pass

    # Direct DB query for vendor filtering
    try:
        resp = _postgrest_session().get(
            f"{POSTGREST_URL}/camera_credentials",
            params={
                'vendor': f'eq.{vendor}',
                'select': 'credential_key,credential_type,username_enc,password_enc'
            }
        )
        if resp.status_code == 200:
            for row in resp.json():
                try:
                    result[row['credential_key']] = (
                        _decrypt(row['username_enc']),
                        _decrypt(row['password_enc'])
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Failed to query vendor credentials: {e}")

    return result
