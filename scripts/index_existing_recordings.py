#!/usr/bin/env python3
"""
Index existing recording files into the PostgreSQL database.
Scans the recording directories and inserts metadata for each mp4 file.

Usage:
    python3 scripts/index_existing_recordings.py [--dry-run]
"""

import os
import re
import sys
import json
import requests
import argparse
from pathlib import Path
from datetime import datetime, timezone
import subprocess

# Recording directories to scan
RECORDING_DIRS = {
    'recent': '/recordings',
    'archive': '/recordings/STORAGE'
}

# Recording types
RECORDING_TYPES = ['motion', 'continuous', 'manual']

# PostgREST URL
POSTGREST_URL = os.environ.get('POSTGREST_URL', 'http://postgrest:3001')


def parse_filename(filename: str) -> dict:
    """
    Parse recording filename to extract metadata.
    Format: SERIAL_YYYYMMDD_HHMMSS.mp4

    Returns:
        dict with camera_id, timestamp, or None if unparseable
    """
    # Pattern: SERIAL_YYYYMMDD_HHMMSS.mp4
    pattern = r'^([A-Za-z0-9]+)_(\d{8})_(\d{6})\.mp4$'
    match = re.match(pattern, filename)

    if not match:
        return None

    serial = match.group(1)
    date_str = match.group(2)
    time_str = match.group(3)

    try:
        timestamp = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
        timestamp = timestamp.replace(tzinfo=timezone.utc)
        return {
            'camera_id': serial,
            'timestamp': timestamp.isoformat()
        }
    except ValueError:
        return None


def get_video_duration(file_path: str) -> int:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return 0


def scan_recordings(base_path: str, storage_tier: str, recording_type: str) -> list:
    """
    Scan a recording directory and return metadata for each file.

    Args:
        base_path: Base recording path
        storage_tier: 'recent' or 'archive'
        recording_type: 'motion', 'continuous', or 'manual'

    Returns:
        List of recording metadata dicts
    """
    recordings = []

    # Build path: /recordings/{type}/ or /recordings/STORAGE/{type}/
    if storage_tier == 'archive':
        scan_path = Path(base_path) / 'STORAGE' / recording_type
    else:
        scan_path = Path(base_path) / recording_type

    if not scan_path.exists():
        print(f"  Path does not exist: {scan_path}")
        return recordings

    print(f"  Scanning: {scan_path}")

    # Walk directory tree
    for root, dirs, files in os.walk(scan_path):
        for filename in files:
            if not filename.endswith('.mp4'):
                continue

            file_path = Path(root) / filename
            metadata = parse_filename(filename)

            if not metadata:
                print(f"    Skipping (unparseable): {filename}")
                continue

            # Get file info
            try:
                stat = file_path.stat()
                file_size = stat.st_size
            except Exception as e:
                print(f"    Error getting stat for {filename}: {e}")
                continue

            # Build recording entry
            recording = {
                'camera_id': metadata['camera_id'],
                'timestamp': metadata['timestamp'],
                'file_path': str(file_path),
                'file_name': filename,
                'storage_tier': storage_tier,
                'file_size_bytes': file_size,
                'motion_triggered': recording_type == 'motion',
                'status': 'completed'
            }

            recordings.append(recording)

    return recordings


def insert_recordings(recordings: list, dry_run: bool = False) -> tuple:
    """
    Insert recordings into the database via PostgREST.

    Args:
        recordings: List of recording metadata dicts
        dry_run: If True, don't actually insert

    Returns:
        (success_count, error_count)
    """
    success = 0
    errors = 0

    for rec in recordings:
        if dry_run:
            print(f"    [DRY RUN] Would insert: {rec['file_name']}")
            success += 1
            continue

        try:
            response = requests.post(
                f"{POSTGREST_URL}/recordings",
                json=rec,
                headers={'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                timeout=10
            )

            if response.status_code in [200, 201, 204]:
                success += 1
            elif response.status_code == 409:
                # Duplicate, skip
                pass
            else:
                print(f"    Error inserting {rec['file_name']}: {response.status_code} {response.text}")
                errors += 1

        except Exception as e:
            print(f"    Exception inserting {rec['file_name']}: {e}")
            errors += 1

    return success, errors


def main():
    parser = argparse.ArgumentParser(description='Index existing recordings into database')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without inserting')
    parser.add_argument('--type', choices=RECORDING_TYPES, help='Only index specific type')
    parser.add_argument('--tier', choices=['recent', 'archive'], help='Only index specific tier')
    args = parser.parse_args()

    print("=" * 60)
    print("Recording Indexer - Scanning filesystem and populating database")
    print("=" * 60)
    print(f"PostgREST URL: {POSTGREST_URL}")
    print(f"Dry run: {args.dry_run}")
    print()

    total_success = 0
    total_errors = 0
    total_files = 0

    # Determine which tiers and types to scan
    tiers_to_scan = [args.tier] if args.tier else ['recent', 'archive']
    types_to_scan = [args.type] if args.type else RECORDING_TYPES

    for tier in tiers_to_scan:
        print(f"\n{'='*40}")
        print(f"Tier: {tier.upper()}")
        print(f"{'='*40}")

        base_path = '/recordings'

        for rec_type in types_to_scan:
            print(f"\nType: {rec_type}")

            recordings = scan_recordings(base_path, tier, rec_type)
            total_files += len(recordings)

            if recordings:
                print(f"  Found {len(recordings)} files")
                success, errors = insert_recordings(recordings, args.dry_run)
                total_success += success
                total_errors += errors
                print(f"  Inserted: {success}, Errors: {errors}")
            else:
                print(f"  No files found")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total files scanned: {total_files}")
    print(f"Successfully indexed: {total_success}")
    print(f"Errors: {total_errors}")

    if args.dry_run:
        print("\n[DRY RUN] No changes made to database")


if __name__ == '__main__':
    main()
