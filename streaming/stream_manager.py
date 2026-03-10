#!/usr/bin/env python3
"""
Refactored Stream Manager - Orchestrator using Strategy Pattern
Delegates vendor-specific logic to stream handlers
"""

from .handlers.reolink_stream_handler import ReolinkStreamHandler
from .handlers.unifi_stream_handler import UniFiStreamHandler
from .handlers.eufy_stream_handler import EufyStreamHandler
from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
from services.credentials.unifi_credential_provider import UniFiCredentialProvider
from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
from .handlers.amcrest_stream_handler import AmcrestStreamHandler
from .handlers.sv3c_stream_handler import SV3CStreamHandler
from services.credentials.sv3c_credential_provider import SV3CCredentialProvider

from services.credentials.eufy_credential_provider import EufyCredentialProvider
from services.camera_repository import CameraRepository
from low_level_handlers.cleanup_handler import kill_ffmpeg
from low_level_handlers.process_reaper import (
    terminate_process_gracefully,
    kill_processes_by_pattern,
    reap_child_processes
)
import tempfile
import os
import signal
import time
import subprocess
import threading
import traceback
import logging
from pathlib import Path
from typing import Dict, Optional
import stat
import shutil
import errno


logger = logging.getLogger(__name__)


class StreamManager:
    """
    Orchestrates video streaming for all camera types
    Uses Strategy Pattern - delegates to vendor-specific handlers
    """

    def __init__(self, camera_repo: CameraRepository):
        """
        Initialize stream manager

        Args:
            camera_repo: Camera repository for data access
        """
        self.camera_repo = camera_repo

        # Initialize vendor-specific credential providers internally
        eufy_cred_provider = EufyCredentialProvider()
        unifi_cred_provider = UniFiCredentialProvider()
        reolink_cred_provider = ReolinkCredentialProvider()
        amcrest_cred_provider = AmcrestCredentialProvider()
        sv3c_cred_provider = SV3CCredentialProvider()



        # Initialize stream handlers for each vendor
        self.handlers = {
            'eufy': EufyStreamHandler(
                eufy_cred_provider,
                camera_repo.get_eufy_bridge_config()
            ),
            'unifi': UniFiStreamHandler(
                unifi_cred_provider,
                camera_repo.get_unifi_protect_config()
            ),
            'reolink': ReolinkStreamHandler(
                reolink_cred_provider,
                camera_repo.get_reolink_config()
            ),
            'amcrest': AmcrestStreamHandler(
                amcrest_cred_provider,
                camera_repo.get_amcrest_config()
            ),
            'sv3c': SV3CStreamHandler(
                sv3c_cred_provider,
                # camera_repo.get_sv3c_config()  # Or {} if no config needed
                {}
            )
        }

        # Stream tracking
        # One FFmpeg per camera
        self.active_streams: dict[str, dict] = {}
        # Watchdog/restart bookkeeping
        self._restart_locks: dict[str, threading.Lock] = {}   # camera_serial -> Lock
        # camera_serial -> {"in_progress": bool, "failures": int, "last_ok": float}
        self._restart_state: dict[str, dict] = {}

        # CRITICAL: Master lock for thread-safe access to shared state
        self._streams_lock = threading.RLock()  # RLock allows re-entrance from same thread

        # NOTE: self.watchdogs and self.stop_flags removed - old per-stream watchdog
        # Now using services/stream_watchdog.py with CameraStateTracker

        # HLS output directory
        self.hls_dir = Path('./streams')
        self.hls_dir.mkdir(exist_ok=True)

        # Ensure correct ownership
        self._ensure_streams_directory_ownership()

        self.last_log_active_streams = time.time()
        self._log_lock = threading.Lock()


        logger.info("Stream Manager initialized with handlers: " +
                    ", ".join(self.handlers.keys()))

    def _ensure_streams_directory_ownership(self):
        """
        Ensure streams directory and all subdirectories are owned by current user
        Prevents permission errors from previous Docker/sudo runs
        """
        try:
            current_uid = os.getuid()
            current_gid = os.getgid()

            # Check main streams directory
            if self.hls_dir.exists():
                dir_stat = self.hls_dir.stat()

                # If owned by different user (e.g., root from Docker)
                if dir_stat.st_uid != current_uid or dir_stat.st_gid != current_gid:
                    logger.warning(
                        f"Streams directory owned by uid={dir_stat.st_uid}, "
                        f"current user is uid={current_uid}. Attempting to fix..."
                    )

                    # Try to change ownership (will fail if not root, but that's ok)
                    try:
                        os.chown(self.hls_dir, current_uid, current_gid)
                        logger.info(f"✅ Fixed ownership of {self.hls_dir}")
                    except PermissionError:
                        logger.error(
                            f"❌ Cannot fix ownership of {self.hls_dir}. "
                            f"Run: sudo chown -R $USER:$USER {self.hls_dir}"
                        )
                        raise PermissionError(
                            f"Streams directory owned by different user. "
                            f"Fix with: sudo chown -R $USER:$USER {self.hls_dir}"
                        )

                # Check all subdirectories (camera directories)
                for camera_dir in self.hls_dir.iterdir():
                    if camera_dir.is_dir():
                        dir_stat = camera_dir.stat()

                        if dir_stat.st_uid != current_uid or dir_stat.st_gid != current_gid:
                            logger.warning(
                                f"Camera directory {camera_dir.name} owned by "
                                f"uid={dir_stat.st_uid}, fixing..."
                            )

                            try:
                                # Change ownership of directory and all contents
                                os.chown(camera_dir, current_uid, current_gid)

                                # Also fix all files inside
                                for item in camera_dir.rglob('*'):
                                    os.chown(item, current_uid, current_gid)

                                logger.info(
                                    f"✅ Fixed ownership of {camera_dir.name}")

                            except PermissionError as e:
                                logger.error(
                                    f"❌ Cannot fix ownership of {camera_dir.name}. "
                                    f"Run: sudo chown -R $USER:$USER {camera_dir}"
                                )
                                raise PermissionError(
                                    f"Camera directory {camera_dir.name} owned by different user. "
                                    f"Fix with: sudo chown -R $USER:$USER {self.hls_dir}"
                                )

            # Ensure directory exists with correct permissions
            self.hls_dir.mkdir(mode=0o755, exist_ok=True)

            logger.info(
                f"✅ Streams directory ownership verified: {self.hls_dir}")

        except Exception as e:
            logger.error(f"Error checking streams directory ownership: {e}")
            raise

    def _clear_camera_segments(self, camera_serial: str) -> bool:
        """
        Clear all segments and playlists for a camera.
        Use sparingly - normally let FFmpeg handle cleanup via -hls_flags delete_segments.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if cleared successfully or nothing to clear
        """
        try:
            stream_dir = self.hls_dir / camera_serial
            if not stream_dir.exists():
                return True

            # Remove segments and playlists
            cleared = 0
            for pattern in ['*.ts', '*.m3u8']:
                for f in stream_dir.glob(pattern):
                    try:
                        f.unlink()
                        cleared += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete {f.name}: {e}")

            if cleared > 0:
                logger.info(f"Cleared {cleared} files for {camera_serial}")
            return True

        except Exception as e:
            print(traceback.print_exc())
            logger.error(f"Error clearing segments for {camera_serial}: {e}")
            return False

    def _kill_all_ffmpeg_for_camera(self, camera_serial: str) -> bool:
        """
        Kill all FFmpeg processes for a camera using pkill.
        This is a FALLBACK method - normal stop_stream() uses process handles.
        
        Args:
            camera_serial: Camera serial number (string)
        
        Returns:
            bool: True if all processes killed or none existed
        """
        logger.info(f"Nuclear cleanup for {camera_serial} - killing all FFmpeg processes")
        
        # Use reaper utility instead of manual pkill
        result = kill_processes_by_pattern(
            pattern=f'streams/{camera_serial}',
            signal_type=signal.SIGKILL,
            verify=True
        )
        
        if result:
            logger.info(f"✅ Killed all FFmpeg processes for {camera_serial}")
        else:
            logger.error(f"❌ Failed to kill all FFmpeg processes for {camera_serial}")
        
        return result

    # Public API
    def start_stream(self, camera_serial: str, resolution: str = 'sub', protocol_override: str = None) -> Optional[str]:
        """Start stream asynchronously and return immediately

        Args:
            camera_serial: Camera identifier
            resolution: 'sub' for grid view (low-res), 'main' for fullscreen (high-res)
                        Note: This is different from cameras.json 'stream_type' which is protocol (HLS, LL_HLS, etc.)
            protocol_override: If set, use this protocol instead of the camera's stored stream_type.
                              Used when switching from MJPEG to a MediaMTX-based type — the camera
                              config still says MJPEG but we need FFmpeg to start for the new type.

        For LL_HLS/NEOLINK cameras with dual-output FFmpeg:
            - A single FFmpeg process publishes BOTH sub and main streams
            - When requesting 'main', if sub stream is running, just return main URL
            - No need to start a second FFmpeg process
        """
        # Check camera protocol type first
        camera = self.camera_repo.get_camera(camera_serial)
        # Use protocol_override when switching from MJPEG to a MediaMTX-based type
        protocol = protocol_override.upper() if protocol_override else (camera or {}).get('stream_type', 'HLS').upper()

        # For LL_HLS/NEOLINK/WEBRTC with dual-output: main stream is always available if sub is running
        # The single FFmpeg process publishes to both /camera and /camera_main
        # WEBRTC uses same FFmpeg→MediaMTX pipeline, just different delivery to browser
        if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC') and resolution == 'main':
            # Check if sub stream (the actual FFmpeg process) is ACTUALLY running
            # NOTE: We must verify the process is alive, not just that a slot exists.
            # A zombie slot with 'starting' status but dead/no process should not return main URL.
            sub_entry = self.active_streams.get(camera_serial)
            if sub_entry and sub_entry.get('status') == 'active':
                # Verify FFmpeg process is actually running
                process = sub_entry.get('process')
                if process and process.poll() is None:
                    path = camera.get('packager_path') or camera_serial
                    main_url = f"/hls/{path}_main/index.m3u8"
                    print(f"[DUAL-OUTPUT] Main stream already available via dual-output FFmpeg: {main_url}")
                    return main_url
                else:
                    # Process died but slot wasn't cleaned up - log and continue to restart
                    print(f"[DUAL-OUTPUT] Zombie slot detected for {camera_serial} - process not running")

        # Derive stream_key from camera_serial + resolution
        # For non-LL_HLS cameras, this allows separate sub and main streams
        stream_key = f"{camera_serial}_main" if resolution == 'main' else camera_serial

        with self._streams_lock:
            # Check if already running
            entry = self.active_streams.get(stream_key)
            if entry and entry.get('status') == 'starting':
                # Check if this is a stale 'starting' slot (older than 30 seconds)
                # This can happen if FFmpeg failed to start but exception wasn't caught
                start_time = entry.get('start_time') or 0
                slot_age = time.time() - start_time if start_time else float('inf')

                if slot_age > 30:
                    # Stale slot - remove it and allow fresh start
                    print(f"[ZOMBIE] Removing stale 'starting' slot for {stream_key} (age: {slot_age:.1f}s)")
                    self.active_streams.pop(stream_key, None)
                    # Continue to create new slot below
                else:
                    print(f"... Stream already starting for {stream_key} (age: {slot_age:.1f}s) ...")

                    # LL_HLS/NEOLINK/WEBRTC use MediaMTX path
                    if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC'):
                        path = camera.get('packager_path') or camera_serial
                        # Main resolution uses different MediaMTX path
                        if resolution == 'main':
                            return f"/hls/{path}_main/index.m3u8"
                        return f"/hls/{path}/index.m3u8"

                    if resolution == 'main':
                        return f"/api/streams/{camera_serial}_main/playlist.m3u8"
                    return f"/api/streams/{camera_serial}/playlist.m3u8"

            if entry and self.is_stream_alive(stream_key):
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                print(f"Stream already active for {stream_key}")
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                return self.get_stream_url(stream_key)

            # Reserve the slot IMMEDIATELY to prevent duplicate starts
            # Mark as "starting" before spawning thread
            if stream_key not in self.active_streams:
                camera_name = camera.get('name', camera_serial) if camera else camera_serial

                self.active_streams[stream_key] = {
                    'process': None,  # Will be set by thread
                    'playlist_path': None,  # Will be set by thread
                    'stream_dir': None,  # Will be set by thread
                    'status': 'starting',  # Mark as starting
                    'camera_name': camera_name,
                    'camera_serial': camera_serial,  # Store original serial for config lookup
                    'resolution': resolution,  # Store sub/main for reference
                    'start_time': time.time()  # Set immediately to prevent zombie false-positives
                }
                print(f"[RESERVED] Slot for {stream_key} - preventing duplicate starts")
            # ===== END NEW BLOCK =====

        # Start in background thread - pass stream_key for storage, camera_serial for config
        threading.Thread(
            target=self._start_stream,
            args=(camera_serial, resolution, stream_key, protocol_override),
            daemon=True
        ).start()

        # Return placeholder URL immediately
        # WEBRTC cameras also use FFmpeg→MediaMTX dual-output pipeline (same as LL_HLS/NEOLINK)
        # so they return /hls/ URLs too - WebRTC is just the delivery method to browser
        if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC'):
            path = camera.get('packager_path') or camera_serial
            if resolution == 'main':
                return f"/hls/{path}_main/index.m3u8"
            return f"/hls/{path}/index.m3u8"

        if resolution == 'main':
            return f"/api/streams/{camera_serial}_main/playlist.m3u8"
        return f"/api/streams/{camera_serial}/playlist.m3u8"

    # Private implementation
    def _start_stream(self, camera_serial: str, resolution: str = 'sub', stream_key: str = None, protocol_override: str = None) -> Optional[str]:
        """Internal stream start implementation

        Args:
            camera_serial: Camera identifier (used for config lookup)
            resolution: 'sub' for grid view, 'main' for fullscreen
            stream_key: Key for active_streams dict (camera_serial or camera_serial_main)
            protocol_override: If set, use this instead of camera's stored stream_type
        """
        # Default stream_key if not provided (backwards compatibility)
        if stream_key is None:
            stream_key = f"{camera_serial}_main" if resolution == 'main' else camera_serial

        try:
            # Step 1: Quick checks WITH lock
            with self._streams_lock:
                if stream_key in self.active_streams:
                    entry = self.active_streams[stream_key]
                    if entry.get('status') == 'starting':
                        print(f"[THREAD] Found reserved slot for {stream_key}, proceeding...")
                    else:
                        print(f"[THREAD] Stream already active for {stream_key}, aborting thread")
                        return None

                # Nuclear cleanup ONLY for truly orphaned processes (no tracked entry)
                # Skip if we just did a graceful stop_stream() - that already terminated the process
                # This prevents killing freshly-started FFmpeg during watchdog restart cycles
                #
                # NOTE: Removed unconditional nuclear cleanup (was causing "torn down" messages
                # in MediaMTX during watchdog restarts). Now MediaMTX handles stream lifecycle.
                # Keep this as emergency fallback for crashed/orphaned processes only.
                #
                # self._kill_all_ffmpeg_for_camera(stream_key)  # DISABLED - too aggressive
            
            # Step 2: Get camera config WITHOUT lock (no shared state modified)
            camera = self.camera_repo.get_camera(camera_serial)
            if not camera:
                logger.error(f"Camera {camera_serial} not found")
                raise Exception(f"Camera {camera_serial} not found")
            
            camera_name = camera.get('name', camera_serial)
            camera_type = camera.get('type', '').lower()
            
            # Validation checks
            if camera.get('hidden', False):
                print(f"{camera_name} is hidden. Skipping.")
                raise Exception(f"{camera_name} is hidden")
            
            if 'streaming' not in camera.get('capabilities', []):
                logger.warning(f"{camera_name} doesn't have streaming capability")
                raise Exception(f"{camera_name} doesn't have streaming capability")
            
            # Get handler
            handler = self.handlers.get(camera_type)
            if not handler:
                logger.error(f"No handler for camera type: {camera_type}")
                raise Exception(f"No handler for camera type: {camera_type}")
            
            # Step 3: Build URL (resolution determines main/sub stream path)
            # IMPORTANT: For dual-output LL_HLS/NEOLINK/WEBRTC with passthrough mode,
            # we MUST use the camera's MAIN stream as input, not sub stream.
            # This allows:
            #   - Sub output: Scale down from main → 320x240 (transcoded)
            #   - Main output: Passthrough main → native resolution (copy)
            # If we used sub stream as input, passthrough would just copy the sub stream!
            protocol = protocol_override.upper() if protocol_override else camera.get('stream_type', 'HLS').upper()
            ll_cfg = camera.get('ll_hls', {})
            main_cv = (ll_cfg.get('video_main') or {}).get('c:v', '')
            main_is_passthrough = str(main_cv).lower() == 'copy'

            # Determine which camera stream to use as input
            if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC') and main_is_passthrough:
                # Dual-output with passthrough: ALWAYS use camera's main stream
                url_stream_type = 'main'
                print(f"════════ Building URL for {camera_name} (MAIN for passthrough dual-output) ════════")
            else:
                # Non-passthrough or single-output: use requested resolution
                url_stream_type = resolution
                print(f"════════ Building URL for {camera_name} ({resolution}) ════════")

            source_url = handler.build_rtsp_url(camera, stream_type=url_stream_type)

            if not source_url:
                logger.error(f"Failed to build URL for {camera_name}")
                raise Exception(f"Failed to build URL for {camera_name}")

            print(f"✅ URL built: {source_url}")
            
            # Protocol branching (RTMP vs HLS)
            # Use protocol_override when switching from MJPEG to a MediaMTX-based type
            protocol = protocol_override.upper() if protocol_override else camera.get('stream_type', 'HLS').upper()

            if protocol == 'MJPEG':
                logger.info(f"Camera {camera_name} uses MJPEG proxy - no FFmpeg needed")
                return None  # Don't start FFmpeg
            
            if protocol == 'RTMP':
                # Spawn FFmpeg for RTMP
                cmd = ['ffmpeg', '-i', source_url, '-c', 'copy', '-f', 'flv', '-']
                process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL, 
                    bufsize=10**8,
                    # start_new_session=True                    
                )
                
                print(f"════════ FFmpeg RTMP command ════════")
                print(' '.join(cmd))
                print(f"════════════════════════════════")
                              
                time.sleep(2)
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    print(f"════════ FFmpeg STDERR {camera_name}════════")
                    print(f"FFmpeg exit code: {process.returncode}")
                    print("Command was:")
                    print(' '.join(cmd))
                    print("═════════════════════════════════════════════")
                    raise Exception(f"FFmpeg died with code {process.returncode}")
                
                # Step 4: Register WITH lock (quick)
                with self._streams_lock:
                    self.active_streams[stream_key] = {
                        'process': process,
                        'protocol': 'rtmp',
                        'rtsp_url': source_url,
                        'stream_dir': None,
                        'camera_name': camera_name,
                        'camera_serial': camera_serial,
                        'camera_type': camera_type,
                        'resolution': resolution,
                        'start_time': time.time(),
                        'playlist_path': None,
                        'status': 'active'
                    }

                logger.info(f"Started RTMP stream for {camera_name} ({resolution})")
                return f"/api/camera/{stream_key}/flv"
            
            # ===== LL_HLS publisher path =====
            # NEOLINK uses LL_HLS through MediaMTX for lower latency and
            # motion detection support (MediaMTX provides RTSP output for tapping)
            # WEBRTC also uses this path - same FFmpeg→MediaMTX pipeline, WebRTC delivery to browser
            if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC'):
                # Ask the vendor handler to build the publish argv and the play URL
                argv, play_url = handler._build_ll_hls_publish(camera_config=camera, rtsp_url=source_url)

                print("════════ FFmpeg LL-HLS publish command ════════")
                print(' '.join(argv))
                print("═══════════════════════════════════════════════")
                
                # Temporary file for debugging startup issues
                stderr_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, 
                                                        prefix=f'ffmpeg_llhls_{camera_serial}_',
                                                        suffix='.log')

                process = subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,  # ← Write to temp file instead of PIPE
                )

                time.sleep(3) # Give FFmpeg time to fail early (bad args, connection refused)
                if process.poll() is not None:
                    # Process died - read the log file
                    stderr_file.seek(0)
                    error_output = stderr_file.read()
                    stderr_file.close()

                    print(f"════════ FFmpeg DIED for {camera_name} ════════")
                    print(f"Exit code: {process.returncode}")
                    print("Command was:")
                    print(' '.join(argv))
                    print("STDERR output:")
                    print(error_output)
                    print("═══════════════════════════════════════════════")

                    os.unlink(stderr_file.name)  # Clean up temp file

                    raise Exception(f"LL-HLS publisher died (code {process.returncode}): {error_output[:3000]}")

                # Close stderr file handle (temp file stays for post-mortem debugging)
                stderr_file.close()

                # FFmpeg process is alive - wait for MediaMTX to confirm publisher ready
                # This closes the race condition where we marked 'active' before MediaMTX
                # had accepted the publisher (5-15s depending on camera connection speed)
                from services.camera_state_tracker import camera_state_tracker
                publisher_ready = camera_state_tracker.wait_for_publisher_ready(
                    camera_serial, timeout=15
                )

                if not publisher_ready:
                    # FFmpeg running but MediaMTX path not ready after 15s
                    # Still register as active (FFmpeg may still be connecting to camera)
                    # but log a warning - watchdog will handle it if it never comes up
                    logger.warning(
                        f"Camera {camera_serial} FFmpeg alive but MediaMTX publisher "
                        f"not ready after 15s - registering anyway, watchdog will monitor"
                    )

                # Register as active (with or without confirmed publisher)
                with self._streams_lock:
                    self.active_streams[stream_key] = {
                        'process': process,
                        'protocol': 'll_hls',
                        'rtsp_url': source_url,
                        'stream_dir': None,
                        'camera_name': camera_name,
                        'camera_serial': camera_serial,
                        'camera_type': camera_type,
                        'resolution': resolution,
                        'start_time': time.time(),
                        'playlist_path': None,
                        'status': 'active',
                        'stream_url': play_url,
                    }

                # NOTE: Old per-stream watchdog removed. StreamWatchdog service now monitors
                # all streams via CameraStateTracker centrally.

                logger.info(f"Started LL-HLS publisher for {camera_name} ({resolution})")
                return play_url
            # ===== end LL_HLS branch =====

            else:
                # Legacy HLS path (FFmpeg writes segments directly to disk)
                # Used by cameras without MediaMTX integration
                stream_dir = self.hls_dir / stream_key
                stream_dir.mkdir(exist_ok=True)

                playlist_path = stream_dir / "playlist.m3u8"

                # pick extension based on camera config
                hls_cfg  = (camera.get('rtsp_output') or {})
                seg_ext  = "m4s" if str(hls_cfg.get('hls_segment_type', '')).lower() == "fmp4" else "ts"
                segment_pattern = stream_dir / f"segment_%03d.{seg_ext}"

                process = self._start_ffmpeg(
                    rtsp_url=source_url,
                    playlist_path=playlist_path,
                    segment_pattern=segment_pattern,
                    handler=handler,
                    stream_type=resolution,  # Pass resolution for output params
                    camera_config=camera
                )

                time.sleep(2)
                if process.poll() is not None:
                    raise Exception(f"FFmpeg died immediately")

                # Register WITH lock
                with self._streams_lock:
                    self.active_streams[stream_key] = {
                        'process': process,
                        'protocol': 'hls',
                        'rtsp_url': source_url,
                        'stream_dir': stream_dir,
                        'camera_name': camera_name,
                        'camera_serial': camera_serial,
                        'camera_type': camera_type,
                        'resolution': resolution,
                        'start_time': time.time(),
                        'playlist_path': playlist_path,
                        'status': 'active'

                    }

                # Wait for playlist (outside lock)
                self._wait_for_playlist(stream_key)

                # NOTE: Old per-stream watchdog removed. StreamWatchdog service now monitors
                # all streams via CameraStateTracker centrally.

                logger.info(f"Started stream for {camera_name} ({resolution})")
                return self.get_stream_url(stream_key)

        except Exception as e:
            # CRITICAL: Clean up the stream entry on failure (regardless of status)
            logger.error(f"❌ Failed to start stream for {camera_name}: {e}")
            print(traceback.print_exc())

            # Remove the stream entry - could be 'starting' or 'active' depending on
            # when the failure occurred. Either way, the stream is not working.
            with self._streams_lock:
                if stream_key in self.active_streams:
                    entry = self.active_streams.get(stream_key, {})
                    status = entry.get('status', 'unknown')
                    logger.warning(f"Removing failed stream slot for {stream_key} (status was: {status})")
                    self.active_streams.pop(stream_key, None)

            return None
        
    def _start_ffmpeg(self, rtsp_url: str, playlist_path: Path,
                    segment_pattern: Path, handler, stream_type: str = 'sub', camera_config: Optional[Dict] = None) -> subprocess.Popen:
        """
        Start FFmpeg process with vendor-specific parameters
        """
        # Build FFmpeg command
        cmd = ['ffmpeg']
        cmd.extend(handler.get_ffmpeg_input_params(camera_config=camera_config))
        cmd.extend(['-i', rtsp_url])
        cmd.extend(handler.get_ffmpeg_output_params(stream_type=stream_type, camera_config=camera_config))
        cmd.extend([
            '-hls_segment_filename', str(segment_pattern),
            '-y',
            str(playlist_path)
        ])

        print(f"════════ FFmpeg command ════════")
        print(' '.join(cmd))
        print(f"════════════════════════════════")

        # Start process WITH stderr capture for debugging
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,      # ← Changed: capture stdout
                stderr=subprocess.DEVNULL,      # ← Changed: capture stderr separately
                # start_new_session=True,
            )

            # Wait a moment and check if it died
            time.sleep(1)
            if process.poll() is not None:
                # Process died - get the error output
                stdout, stderr = process.communicate()
                if isinstance(stderr, (bytes, bytearray)):
                    stderr_text = stderr.decode('utf-8', errors='replace')
                elif isinstance(stderr, str):
                    stderr_text = stderr
                else:
                    stderr_text = '[no stderr captured]'
                print(stderr_text)
                raise Exception(f"Failed to start FFmpeg (exit code {process.returncode}): {stderr_text}")

            return process
        except FileNotFoundError:
            raise Exception("FFmpeg not found. Please install FFmpeg.")
        except Exception as e:
            raise Exception(f"Failed to start FFmpeg: {e}")

    def printout_active_streams(self, caller="Unknown"):
        # Check if at least 10 seconds have passed since last log
        with self._log_lock:
            elapsed = int(time.time() - self.last_log_active_streams)
            if time.time() - self.last_log_active_streams >= 10:
                self.last_log_active_streams = time.time()
                print(f"############### ACTIVE STREAMS (called by: {caller} ############### ")
                print(f"elapsed time since last call: {elapsed} seconds")
                # Create a snapshot of keys to avoid iteration during modification

                active_keys = list(self.active_streams.keys())
                for stream in active_keys:
                    print(stream)
                print("####################################################################")

    def stop_stream(self, camera_serial: str, stop_watchdog: bool = True) -> bool:
        """
        Stop streaming for a camera with proper process termination and zombie reaping.

        Args:
            camera_serial: Camera serial number
            stop_watchdog: Deprecated parameter, kept for backward compatibility.
                          Old per-stream watchdog removed - now using StreamWatchdog service.

        Returns:
            bool: True if stopped successfully
        """
        # NOTE: Old watchdog stop logic removed. Now handled by StreamWatchdog service.

        # Log throttled output
        self.printout_active_streams(caller="stop_stream")

        with self._streams_lock:
            if camera_serial not in self.active_streams:
                logger.warning(f"Cannot stop {camera_serial} - not in active_streams")
                return False
            try:
                stream_info = self.active_streams[camera_serial]
                camera_name = stream_info.get('camera_name', camera_serial)
                process = stream_info.get('process')
                
                if not process:
                    logger.error(f"No process handler for {camera_name}")
                    # Still remove from dict and clean up
                    self.active_streams.pop(camera_serial, None)
                    self._clear_camera_segments(camera_serial)
                    return False

                # NEW: LL-HLS — just kill the publisher and clear state
                if stream_info.get('protocol') == 'll_hls':
                    proc = stream_info.get('process')
                    stderr_log = stream_info.get('stderr_log') 
                    try:
                        if proc and proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except Exception:
                                proc.kill()
                        stream_info['status'] = 'stopped'
                    finally:
                        # Clean up stderr log file if it exists
                        if stderr_log:
                            try:
                                if os.path.exists(stderr_log):
                                    os.unlink(stderr_log)
                                    logger.info(f"Cleaned up stderr log: {stderr_log}")
                            except Exception as e:
                                logger.warning(f"Failed to clean up stderr log {stderr_log}: {e}")                        
                        self.active_streams.pop(camera_serial, None)
                        
                    logger.info(f"Stopped LL-HLS publisher for {camera_serial}")
                    return True
                # ════════ END OF LL-HLS TERMINATION ════════
                
                # Use the reaper utility to terminate properly
                if terminate_process_gracefully(process, timeout=5, process_name=f"{camera_name} FFmpeg"):
                    # Process is now dead AND reaped (no zombie)
                    self.active_streams.pop(camera_serial, None)
                    logger.info(f"✅ Stopped stream for {camera_name}")
                    
                    # Clear segments after process is dead
                    self._clear_camera_segments(camera_serial)
                    
                    return True
                else:
                    # Graceful termination failed - use nuclear option
                    logger.warning(f"Falling back to pkill for {camera_name}")
                    if self._kill_all_ffmpeg_for_camera(camera_serial):  # ← Pass camera_serial, not stream_info
                        self.active_streams.pop(camera_serial, None)
                        self._clear_camera_segments(camera_serial)
                        # Reap any zombies created by pkill
                        reap_child_processes()
                        return True
                    return False
                    
            except Exception as e:
                print(traceback.print_exc())
                logger.error(f"Error in stop_stream for {camera_serial}: {e}")
                return False

        # NOTE: Old per-stream watchdog cleanup removed.
        # StreamWatchdog service handles all stream monitoring now.

        return True

    def _classify_ffmpeg_exit(self, stderr_log_path: Optional[str], exit_code: int) -> str:
        """
        Classify FFmpeg exit reason by analyzing stderr log.

        Args:
            stderr_log_path: Path to FFmpeg stderr log file
            exit_code: FFmpeg process exit code

        Returns:
            str: Exit reason classification:
                - "buffer_management": Broken pipe due to unconsumed dual-output stream (expected)
                - "connection_error": Network/camera connection issue
                - "codec_error": Video/audio codec problem
                - "unknown": Unknown error or unable to read log
        """
        if not stderr_log_path:
            return "unknown"

        try:
            with open(stderr_log_path, 'r') as f:
                stderr_content = f.read()

            # Check for broken pipe errors (normal for dual-output buffer management)
            if "broken pipe" in stderr_content.lower() or "epipe" in stderr_content.lower():
                # Further check: is this from MediaMTX refusing the write?
                if "rtsp://" in stderr_content and ("nvr-packager" in stderr_content or "mediamtx" in stderr_content):
                    return "buffer_management"

            # Other error classifications
            if "connection" in stderr_content.lower() or "timeout" in stderr_content.lower():
                return "connection_error"

            if "codec" in stderr_content.lower() or "invalid" in stderr_content.lower():
                return "codec_error"

            return "unknown"

        except Exception as e:
            logger.debug(f"Could not read stderr log {stderr_log_path}: {e}")
            return "unknown"

    def is_stream_healthy(self, camera_serial: str, caller: str) -> bool:

        info = self.active_streams.get(camera_serial)
        if not info:
            print(f"[{caller}:is_stream_healthy] {camera_serial} not healthy (no info)")
            return False

        # Add grace period check
        stream_age = time.time() - info.get('start_time', 0)
        if stream_age < 50:  # Don't health-check streams younger than 50 seconds
            return True  # Assume healthy during startup

        proc = info.get("process")
        if not proc or proc.poll() is not None:
            # Check if this is a buffer management exit (broken pipe) vs actual error
            if proc and proc.poll() is not None:
                stderr_log_path = info.get('stderr_log')
                exit_reason = self._classify_ffmpeg_exit(stderr_log_path, proc.returncode)
                if exit_reason == "buffer_management":
                    print(f"[{caller}:is_stream_healthy] {camera_serial} FFmpeg exit due to buffer management (normal for unconsumed dual-output streams)")
                else:
                    print(f"[{caller}:is_stream_healthy] {camera_serial} not healthy (FFmpeg exited: {exit_reason})")
            else:
                print(f"[{caller}:is_stream_healthy] {camera_serial} not healthy (no process)")
            return False
        playlist = info.get("playlist_path")
        stream_dir = info.get("stream_dir")
        if not playlist or not stream_dir:
            print(f"[{caller}:is_stream_healthy] {camera_serial} not healthy (not playlist or not stream_dir)")
            return False
        try:
            # playlist updated in last Ns and at least one .ts present
            fresh = (time.time() - playlist.stat().st_mtime) <= 50
        except Exception as e:
            print(f"Erro in is_stream_healthy (fresh): {e}")
            print(traceback.print_exc())
            return False

        try:
            has_segments = any(stream_dir.glob("segment_*.ts"))
        except Exception as e:
            print(f"Erro in is_stream_healthy (has_segments): {e}")
            print(traceback.print_exc())
            return False
        finally:
            self.printout_active_streams(caller="is_stream_healthy")

        print(f"[{caller}:is_stream_healthy] {camera_serial}: fresh:{fresh} has_segments:{has_segments}")
        return fresh and has_segments

    def restart_stream(self, camera_serial: str) -> bool:
        """
        Restart LL-HLS stream for a camera.

        Public method for StreamWatchdog and manual restart to use. Performs a
        clean stop and start cycle for the FFmpeg publisher, then waits for
        MediaMTX to confirm the publisher is ready before returning success.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if restart was successful and publisher confirmed ready,
                  False otherwise

        Note:
            Uses stop_stream() + start_stream() + wait_for_publisher_ready()
            for a complete restart with readiness verification.
        """
        logger.info(f"[RESTART] Initiating restart for {camera_serial}")

        try:
            # Step 1: Stop existing stream
            if camera_serial in self.active_streams:
                logger.info(f"[RESTART] Stopping existing stream for {camera_serial}")
                stopped = self.stop_stream(camera_serial, stop_watchdog=False)
                if not stopped:
                    logger.warning(f"[RESTART] stop_stream returned False for {camera_serial}")
                # Brief pause to allow cleanup
                time.sleep(1)

            # Step 2: Start fresh stream
            logger.info(f"[RESTART] Starting fresh stream for {camera_serial}")
            playlist_url = self.start_stream(camera_serial, resolution='sub')

            if playlist_url:
                # Step 3: Wait for publisher readiness confirmation
                # start_stream() returns URL immediately, but _start_stream() runs
                # in background thread. Wait for the publisher to actually be ready.
                from services.camera_state_tracker import camera_state_tracker
                publisher_ready = camera_state_tracker.wait_for_publisher_ready(
                    camera_serial, timeout=20
                )

                if publisher_ready:
                    logger.info(f"[RESTART] Stream restart successful for {camera_serial}: {playlist_url}")
                    return True
                else:
                    logger.warning(
                        f"[RESTART] Stream started but publisher not confirmed ready "
                        f"for {camera_serial} (FFmpeg may still be connecting)"
                    )
                    # Return True anyway - FFmpeg is running, it may just need more time
                    # The watchdog will catch it if it never comes up
                    return True
            else:
                logger.error(f"[RESTART] start_stream returned None for {camera_serial}")
                return False

        except Exception as e:
            logger.error(f"[RESTART] Stream restart failed for {camera_serial}: {e}", exc_info=True)
            return False

    # NOTE: Old watchdog methods (_start_watchdog, _watchdog_loop, _state,
    # _suppress_watchdog, _watchdog_restart_stream) removed as of Jan 4, 2026.
    # Stream health monitoring now handled by services/stream_watchdog.py
    # which uses CameraStateTracker for unified state management.

    def _wait_for_playlist(self, camera_serial: str, timeout: int = 10):
        """Wait for HLS playlist to be created

        NOTE: This method must NOT hold the lock while sleeping, otherwise it blocks
        all other threads (health checks, stream starts/stops) for up to 10 seconds.
        The fix: grab playlist_path quickly with lock, then release lock before polling.
        """
        # Quick lock acquisition to get playlist path
        with self._streams_lock:
            if camera_serial not in self.active_streams:
                return False
            playlist_path = self.active_streams[camera_serial].get('playlist_path')

        # If no playlist_path (e.g., LL-HLS streams), return early
        if not playlist_path:
            return True

        # Poll WITHOUT holding the lock - allows other threads to proceed
        for _ in range(timeout * 2):  # Check every 0.5 seconds
            if playlist_path.exists():
                return True
            time.sleep(0.5)

        return False

    def get_stream_url(self, camera_serial: str) -> Optional[str]:
        with self._streams_lock:
            stream_info = self.active_streams.get(camera_serial)
            if not stream_info :
                return None

            # NEW: honor stored URL for LL-HLS publishers
            if stream_info.get('protocol') == 'll_hls':
                return stream_info.get('stream_url')

            # existing fallback for classic HLS
            return f"/streams/{camera_serial}/playlist.m3u8"

    def is_stream_alive(self, camera_serial: str) -> bool:
        """Check if stream process is running"""
        info = self.active_streams.get(camera_serial)
        if not info:
            return False
        # If a slot was reserved but the worker hasn't attached the process yet
        if info.get('status') == 'starting':
            return False
        process = info.get('process')
        if not process:
            return False
        try:
            return process.poll() is None
        except Exception:
            return False

    def get_active_streams(self) -> Dict:
        """Get list of active streams

        Note: This method reports stream status but does NOT stop dead streams.
        Dead stream cleanup should be handled by the watchdog or explicit stop calls.
        Previously, this method would stop streams that weren't alive, but that caused
        race conditions - streams in 'starting' state would be killed before they
        had a chance to fully initialize.
        """
        with self._streams_lock:
            active = {}

            for camera_serial in list(self.active_streams.keys()):
                info = self.active_streams[camera_serial]
                status = info.get('status', 'unknown')

                # Include streams that are starting or active
                if status == 'starting':
                    # Stream is still initializing - include it but mark as starting
                    active[camera_serial] = {
                        'camera_name': info.get('camera_name', 'Unknown'),
                        'camera_type': info.get('camera_type', 'Unknown'),
                        'stream_url': None,  # Not ready yet
                        'uptime': 0,
                        'status': 'starting'
                    }
                elif self.is_stream_alive(camera_serial):
                    active[camera_serial] = {
                        'camera_name': info['camera_name'],
                        'camera_type': info['camera_type'],
                        'stream_url': self.get_stream_url(camera_serial),
                        'uptime': time.time() - info['start_time'],
                        'status': 'active'
                    }
                # Note: Dead streams are NOT stopped here to avoid race conditions.
                # The watchdog or explicit stop calls handle cleanup.

            return active

    def _get_or_create_lock(self, camera_serial: str) -> threading.Lock:
        lock = self._restart_locks.get(camera_serial)
        if lock is None:
            lock = threading.Lock()
            self._restart_locks[camera_serial] = lock
        return lock

    # NOTE: _mark_ok and _mark_fail removed - old watchdog bookkeeping
    # Now handled by CameraStateTracker.register_success/register_failure

    def stop_all_streams(self):
        """Stop all active streams"""
        logger.info("Stopping all streams...")

        with self._streams_lock:
            for camera_serial in list(self.active_streams.keys()):
                self.stop_stream(camera_serial)

        logger.info("All streams stopped")

    def cleanup_stream_files(self) -> None:
        """
            Stop proc, resiliently delete dir, and recreate it.
            ONLY TO BE CALLED at startup and app exit.
        """
        streams = Path('./streams')  # ✅ Make it a Path object

        # 2) delete directory and contents robustly (tolerate ENOENT)
        _safe_rmtree(streams)

        # 3) recreate empty dir with same perms
        try:
            streams.mkdir(parents=True, exist_ok=True)
            os.chmod(streams, 0o755)
        except Exception as e:
            logger.error(f"[cleanup] failed to recreate {streams}: {e}")

        print("✅ streamfiles cleaned up.")

##############

def _safe_rmtree(path: Path) -> None:
    """
    Robust rmtree:
    - never follows symlinks
    - ignores ENOENT (already removed)
    - fixes perms on EACCES then retries
    """
    if not isinstance(path, Path):
        path = Path(path)

    try:
        if not path.exists():
            return
    except FileNotFoundError:
        return

    if path.is_symlink():
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except PermissionError:
            os.chmod(path, stat.S_IWUSR | stat.S_IREAD)
            try:
                path.unlink()
            except FileNotFoundError:
                return
        return

    def onerror(func, p, exc_info):
        # p may be str; normalize
        try:
            err = exc_info[1]
            # If it's gone already, ignore
            if getattr(err, 'errno', None) == getattr(os, 'ENOENT', 2):
                return
            # If permission denied, chmod and retry once
            if isinstance(err, PermissionError):
                try:
                    os.chmod(p, stat.S_IWUSR | stat.S_IREAD | stat.S_IEXEC)
                    func(p)
                    return
                except Exception as e2:
                    logger.error(f"[cleanup] still cannot remove {p}: {e2}")
                    return
            # Other errors: log once, continue
            logger.error(f"[cleanup] failed to remove {p}: {err}")
        except Exception as e:
            logger.error(f"[cleanup] onerror handler exception for {p}: {e}")

    try:
        shutil.rmtree(path, ignore_errors=False, onerror=onerror)
    except FileNotFoundError:
        # root disappeared while walking — fine
        return

