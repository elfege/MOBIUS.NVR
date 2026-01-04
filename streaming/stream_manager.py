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


        self.watchdogs = {}
        self.stop_flags = {}

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
    def start_stream(self, camera_serial: str, resolution: str = 'sub') -> Optional[str]:
        """Start stream asynchronously and return immediately

        Args:
            camera_serial: Camera identifier
            resolution: 'sub' for grid view (low-res), 'main' for fullscreen (high-res)
                        Note: This is different from cameras.json 'stream_type' which is protocol (HLS, LL_HLS, etc.)

        For LL_HLS/NEOLINK cameras with dual-output FFmpeg:
            - A single FFmpeg process publishes BOTH sub and main streams
            - When requesting 'main', if sub stream is running, just return main URL
            - No need to start a second FFmpeg process
        """
        # Check camera protocol type first
        camera = self.camera_repo.get_camera(camera_serial)
        protocol = (camera or {}).get('stream_type', 'HLS').upper()

        # For LL_HLS/NEOLINK with dual-output: main stream is always available if sub is running
        # The single FFmpeg process publishes to both /camera and /camera_main
        if protocol in ('LL_HLS', 'NEOLINK') and resolution == 'main':
            # Check if sub stream (the actual FFmpeg process) is running
            sub_entry = self.active_streams.get(camera_serial)
            if sub_entry and (sub_entry.get('status') == 'active' or sub_entry.get('status') == 'starting'):
                path = camera.get('packager_path') or camera_serial
                main_url = f"/hls/{path}_main/index.m3u8"
                print(f"[DUAL-OUTPUT] Main stream already available via dual-output FFmpeg: {main_url}")
                return main_url

        # Derive stream_key from camera_serial + resolution
        # For non-LL_HLS cameras, this allows separate sub and main streams
        stream_key = f"{camera_serial}_main" if resolution == 'main' else camera_serial

        with self._streams_lock:
            # Check if already running
            entry = self.active_streams.get(stream_key)
            if entry and entry.get('status') == 'starting':
                print(f"... Stream already starting for {stream_key} ...")

                # NEOLINK uses LL_HLS path through MediaMTX
                if protocol in ('LL_HLS', 'NEOLINK'):
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
                    'start_time': None  # Will be set by thread
                }
                print(f"[RESERVED] Slot for {stream_key} - preventing duplicate starts")
            # ===== END NEW BLOCK =====

        # Start in background thread - pass stream_key for storage, camera_serial for config
        threading.Thread(
            target=self._start_stream,
            args=(camera_serial, resolution, stream_key),
            daemon=True
        ).start()

        # Return placeholder URL immediately
        if protocol in ('LL_HLS', 'NEOLINK'):
            path = camera.get('packager_path') or camera_serial
            if resolution == 'main':
                return f"/hls/{path}_main/index.m3u8"
            return f"/hls/{path}/index.m3u8"

        if resolution == 'main':
            return f"/api/streams/{camera_serial}_main/playlist.m3u8"
        return f"/api/streams/{camera_serial}/playlist.m3u8"

    # Private implementation
    def _start_stream(self, camera_serial: str, resolution: str = 'sub', stream_key: str = None) -> Optional[str]:
        """Internal stream start implementation

        Args:
            camera_serial: Camera identifier (used for config lookup)
            resolution: 'sub' for grid view, 'main' for fullscreen
            stream_key: Key for active_streams dict (camera_serial or camera_serial_main)
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

                # Kill lingering ffmpeg for this stream_key
                self._kill_all_ffmpeg_for_camera(stream_key)
            
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
            print(f"════════ Building URL for {camera_name} ({resolution}) ════════")
            source_url = handler.build_rtsp_url(camera, stream_type=resolution)
            
            if not source_url:
                logger.error(f"Failed to build URL for {camera_name}")
                raise Exception(f"Failed to build URL for {camera_name}")
            
            print(f"✅ URL built: {source_url}")
            
            # Protocol branching (RTMP vs HLS)
            protocol = camera.get('stream_type', 'HLS').upper()
            
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
            if protocol in ('LL_HLS', 'NEOLINK'):
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

                time.sleep(3) # ← Give it more time to fail and write error
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
                    
                    import os
                    os.unlink(stderr_file.name)  # Clean up temp file
                    
                    raise Exception(f"LL-HLS publisher died (code {process.returncode}): {error_output[:3000]}")
                    
                    # Process is running - close the file handle but keep the file for debugging
                    stderr_file.close()
                    # Note: temp file stays on disk for post-mortem debugging if needed
                    # Could add cleanup in stop_stream() if desired

                # Register as active
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

                # Optional: start watchdog if you want restart behavior (sub streams only)
                if resolution == 'sub':
                    self._start_watchdog(stream_key)

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

                # Start watchdog (sub streams only)
                if resolution == 'sub':
                    self._start_watchdog(stream_key)

                logger.info(f"Started stream for {camera_name} ({resolution})")
                return self.get_stream_url(stream_key)

        except Exception as e:
            # CRITICAL: Clean up the reservation slot on failure
            logger.error(f"❌ Failed to start stream for {camera_name}: {e}")
            print(traceback.print_exc())

            # Remove the 'starting' reservation
            with self._streams_lock:
                if stream_key in self.active_streams:
                    entry = self.active_streams.get(stream_key, {})
                    if entry.get('status') == 'starting':
                        logger.warning(f"Removing failed 'starting' slot for {stream_key}")
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
            stop_watchdog: If False, don't stop watchdog thread (used during restarts)
        
        Returns:
            bool: True if stopped successfully
        """
        # Signal watchdog to stop (outside lock)
        if stop_watchdog and camera_serial in self.stop_flags:
            self.stop_flags[camera_serial].set()

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
        
        # Watchdog cleanup happens outside lock
        if stop_watchdog and camera_serial in self.watchdogs:
            t = self.watchdogs.get(camera_serial)
            if t and t.is_alive() and threading.current_thread() is not t:
                t.join(timeout=3)
            self.watchdogs.pop(camera_serial, None)
            self.stop_flags.pop(camera_serial, None)
        
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

        Public method for the new StreamWatchdog to use. Performs a clean
        stop and start cycle for the FFmpeg publisher.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if restart was successful, False otherwise

        Note:
            This replaces the old _watchdog_restart_stream internal method.
            Uses stop_stream() + start_stream() for clean restart.
        """
        logger.info(f"[RESTART] Initiating restart for {camera_serial}")

        try:
            # Step 1: Stop existing stream (don't stop old watchdog - it's deprecated)
            # Use stop_watchdog=False since old watchdog is being removed
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
                logger.info(f"[RESTART] Stream restart successful for {camera_serial}: {playlist_url}")
                return True
            else:
                logger.error(f"[RESTART] start_stream returned None for {camera_serial}")
                return False

        except Exception as e:
            logger.error(f"[RESTART] Stream restart failed for {camera_serial}: {e}", exc_info=True)
            return False

    def _start_watchdog(self, camera_serial: str):
        """Start watchdog thread to monitor stream health"""
        if camera_serial in self.watchdogs:
            return

        stop_event = threading.Event()
        self.stop_flags[camera_serial] = stop_event

        watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(camera_serial, stop_event),
            daemon=True
        )
        self.watchdogs[camera_serial] = watchdog_thread
        watchdog_thread.start()

    def _watchdog_loop(self, camera_serial: str, stop_event: threading.Event) -> None:
        backoff = 5
        watchdog_enabled=os.getenv('ENABLE_WATCHDOG', 'true').lower() in ['true', '1']
        if not watchdog_enabled:
            print(f"[WATCHDOG] DISABLED")
            return
        if watchdog_enabled:
            while not stop_event.is_set():
                # SLEEP FIRST, OUTSIDE THE LOCK
                time.sleep(max(5, min(backoff, 60)))
                with self._streams_lock:
                    if stop_event.is_set() or camera_serial not in self.active_streams:
                        break

                    st = self._state(camera_serial)
                    if time.time() < st.get("suppress_until", 0.0):
                        backoff = 5
                        continue

                    if self.is_stream_healthy(camera_serial, caller="WATCHDOG"):
                        backoff = 5
                        continue

                    try:
                        print(f"[WATCHDOG] restarting {camera_serial}")
                        self._watchdog_restart_stream(camera_serial)
                        backoff = min(backoff * 2, 60)
                    except Exception as e:
                        print(traceback.print_exc())
                        print(f"Failed to execute _watchdog_restart_stream(): {e}")
                        backoff = min(backoff * 2, 60)




    def _state(self, camera_serial: str) -> dict:
        s = self._restart_state.get(camera_serial)
        if s is None:
            s = {"in_progress": False, "failures": 0, "last_ok": 0.0, "suppress_until": 0.0}
            self._restart_state[camera_serial] = s
        return s

    def _suppress_watchdog(self, camera_serial: str, seconds: float = 10.0) -> None:
        """Ignore health for this camera until now+seconds (first segments to appear)."""
        st = self._state(camera_serial)
        st["suppress_until"] = time.time() + max(0.0, seconds)

    def _watchdog_restart_stream(self, camera_serial: str) -> None:
        lock = self._get_or_create_lock(camera_serial)
        watchdog_enabled=os.getenv('ENABLE_WATCHDOG', 'true').lower() in ['true', '1']
        if not watchdog_enabled:
            print(f"[WATCHDOG] DISABLED")
            return

        if not lock.acquire(blocking=False):
            logger.info(
                f"[WATCHDOG] restart already in progress for {camera_serial}")
            return

        st = self._restart_state.setdefault(
            camera_serial, {"in_progress": False, "failures": 0, "last_ok": 0.0})
        if st["in_progress"]:
            lock.release()
            logger.info(
                f"[WATCHDOG] restart flag set; skipping duplicate for {camera_serial}")
            return
        st["in_progress"] = True

        try:

            logger.warning(f"[WATCHDOG] restarting {camera_serial}")

            # IMPORTANT: do not stop/join the watchdog from inside the watchdog thread
            self.stop_stream(camera_serial, stop_watchdog=False)

            camera = self.camera_repo.get_camera(camera_serial)
            handler = self.handlers.get(camera.get('type'))
            if not handler:
                raise RuntimeError(
                    f"No handler for camera type: {camera.get('type')}")

            # Reconstruct paths if needed
            stream_dir = (self.hls_dir / camera_serial)
            playlist_path = stream_dir / "index.m3u8"
            rtsp_url = handler.build_rtsp_url(camera, stream_type=stream_type)

            proc = self._start_ffmpeg(
                rtsp_url=rtsp_url,
                playlist_path=playlist_path,
                segment_pattern=stream_dir / "segment_%03d.ts",
                handler=handler,
                camera_config=camera
            )
            # give the new pipeline time to produce segments
            self._suppress_watchdog(camera_serial, seconds=10)
            self._mark_ok(camera_serial)

            with self._streams_lock:
                self.active_streams[camera_serial] = {
                    "process": proc,
                    "stream_dir": stream_dir,
                    "playlist_path": playlist_path,
                }
                self._mark_ok(camera_serial)

        except Exception as e:
            self._mark_fail(camera_serial)
            logger.exception(
                f"[WATCHDOG] restart failed for {camera_serial}: {e}")

        finally:
            st["in_progress"] = False
            lock.release()

    def _wait_for_playlist(self, camera_serial: str, timeout: int = 10):
        """Wait for HLS playlist to be created"""
        with self._streams_lock:
            if camera_serial not in self.active_streams:
                return False

            playlist_path = self.active_streams[camera_serial]['playlist_path']

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

    def _mark_ok(self, camera_serial: str) -> None:
        st = self._restart_state.setdefault(
            camera_serial, {"in_progress": False, "failures": 0, "last_ok": 0.0})
        st["failures"] = 0
        st["last_ok"] = time.time()

    def _mark_fail(self, camera_serial: str) -> None:
        st = self._restart_state.setdefault(
            camera_serial, {"in_progress": False, "failures": 0, "last_ok": 0.0})
        st["failures"] += 1


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

