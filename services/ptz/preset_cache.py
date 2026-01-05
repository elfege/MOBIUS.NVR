#!/usr/bin/env python3
"""
PTZ Preset Cache Service - Database-backed caching for ONVIF PTZ presets

Reduces ONVIF queries by caching presets in PostgreSQL via PostgREST.
Presets are cached with configurable TTL (default 6 days) and automatically
invalidated when presets are created/deleted.

Usage:
    from services.ptz.preset_cache import PresetCache

    # Get cached presets (or None if cache miss/expired)
    presets = PresetCache.get_cached_presets(camera_serial)

    # Store presets in cache
    PresetCache.cache_presets(camera_serial, presets)

    # Invalidate cache for a camera
    PresetCache.invalidate_cache(camera_serial)
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import requests

logger = logging.getLogger(__name__)

# Cache TTL in days (6 days as per user specification)
PRESET_CACHE_TTL_DAYS = 6

# PostgREST URL (internal docker network)
# Default matches the container alias used in docker-compose
POSTGREST_URL = os.getenv('POSTGREST_URL', 'http://postgrest:3001')


class PresetCache:
    """
    Database-backed cache for PTZ presets using PostgREST.

    Provides methods to:
    - Get cached presets for a camera
    - Store presets in cache
    - Invalidate cache when presets change
    - Check cache validity based on TTL
    """

    @classmethod
    def get_cached_presets(cls, camera_serial: str) -> Optional[List[Dict]]:
        """
        Get cached presets for a camera if cache is valid.

        Args:
            camera_serial: Camera serial number

        Returns:
            List of preset dicts [{'token': str, 'name': str}] or None if cache miss/expired
        """
        try:
            # Query PostgREST for this camera's presets
            response = requests.get(
                f"{POSTGREST_URL}/ptz_presets",
                params={
                    'camera_serial': f'eq.{camera_serial}',
                    'order': 'preset_token.asc'
                },
                timeout=5
            )

            if response.status_code != 200:
                logger.warning(f"PostgREST query failed for {camera_serial}: {response.status_code}")
                return None

            records = response.json()

            if not records or len(records) == 0:
                logger.debug(f"No cached presets found for {camera_serial}")
                return None

            # Check if cache is still valid (within TTL)
            # Use the oldest cached_at timestamp to determine validity
            oldest_cached = min(
                datetime.fromisoformat(r['cached_at'].replace('Z', '+00:00'))
                for r in records
            )
            expiry_time = oldest_cached + timedelta(days=PRESET_CACHE_TTL_DAYS)

            if datetime.now(oldest_cached.tzinfo) > expiry_time:
                logger.info(f"Preset cache expired for {camera_serial}, cached at {oldest_cached}")
                return None

            # Convert to preset format
            presets = [
                {'token': r['preset_token'], 'name': r['preset_name']}
                for r in records
            ]

            logger.debug(f"Cache hit: {len(presets)} presets for {camera_serial}")
            return presets

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get cached presets for {camera_serial}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting cached presets: {e}")
            return None

    @classmethod
    def cache_presets(cls, camera_serial: str, presets: List[Dict]) -> bool:
        """
        Store presets in cache for a camera.

        Replaces existing cache entries for the camera with new presets.
        Skips caching if presets list is empty (per user specification).

        Args:
            camera_serial: Camera serial number
            presets: List of preset dicts [{'token': str, 'name': str}]

        Returns:
            True if caching succeeded, False otherwise
        """
        # Skip caching empty presets
        if not presets or len(presets) == 0:
            logger.debug(f"Skipping cache for {camera_serial}: no presets to cache")
            return True

        try:
            # First, delete existing cache entries for this camera
            cls.invalidate_cache(camera_serial)

            # Insert new preset records
            current_time = datetime.now().isoformat()
            records = [
                {
                    'camera_serial': camera_serial,
                    'preset_token': p['token'],
                    'preset_name': p.get('name', p['token']),
                    'cached_at': current_time
                }
                for p in presets
            ]

            response = requests.post(
                f"{POSTGREST_URL}/ptz_presets",
                json=records,
                headers={'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                timeout=5
            )

            if response.status_code in (200, 201):
                logger.info(f"Cached {len(presets)} presets for {camera_serial}")
                return True
            else:
                logger.error(f"Failed to cache presets for {camera_serial}: {response.status_code} {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to cache presets for {camera_serial}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error caching presets: {e}")
            return False

    @classmethod
    def invalidate_cache(cls, camera_serial: str) -> bool:
        """
        Delete all cached presets for a camera.

        Should be called when presets are created, modified, or deleted.

        Args:
            camera_serial: Camera serial number

        Returns:
            True if invalidation succeeded, False otherwise
        """
        try:
            response = requests.delete(
                f"{POSTGREST_URL}/ptz_presets",
                params={'camera_serial': f'eq.{camera_serial}'},
                timeout=5
            )

            if response.status_code in (200, 204):
                logger.debug(f"Invalidated preset cache for {camera_serial}")
                return True
            else:
                logger.warning(f"Failed to invalidate cache for {camera_serial}: {response.status_code}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to invalidate cache for {camera_serial}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error invalidating cache: {e}")
            return False

    @classmethod
    def is_cache_valid(cls, camera_serial: str) -> bool:
        """
        Check if cache exists and is within TTL for a camera.

        Args:
            camera_serial: Camera serial number

        Returns:
            True if valid cache exists, False otherwise
        """
        # get_cached_presets already handles TTL check
        return cls.get_cached_presets(camera_serial) is not None
