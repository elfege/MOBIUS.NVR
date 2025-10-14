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
from services.credentials.eufy_credential_provider import EufyCredentialProvider
from services.camera_repository import CameraRepository
from low_level_handlers.cleanup_handler import kill_ffmpeg
from low_level_handlers.process_reaper import (
    terminate_process_gracefully,
    kill_processes_by_pattern,
    reap_child_processes
)
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
    def start_stream(self, camera_serial: str, stream_type: str = 'sub') -> Optional[str]:
        """Start stream asynchronously and return immediately"""

        with self._streams_lock:
            # Check if already running
            entry = self.active_streams.get(camera_serial)
            if entry and entry.get('status') == 'starting':
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                print(f"Stream already starting for {camera_serial}")
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                return f"/api/streams/{camera_serial}/playlist.m3u8"
            if entry and self.is_stream_alive(camera_serial):
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                print(f"Stream already active for {camera_serial}")
                print("═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-═-")
                return self.get_stream_url(camera_serial)
            
            # Reserve the slot IMMEDIATELY to prevent duplicate starts
            # Mark as "starting" before spawning thread
            if camera_serial not in self.active_streams:
                camera = self.camera_repo.get_camera(camera_serial)
                camera_name = camera.get('name', camera_serial) if camera else camera_serial
                
                self.active_streams[camera_serial] = {
                    'process': None,  # Will be set by thread
                    'playlist_path': None,  # Will be set by thread
                    'stream_dir': None,  # Will be set by thread
                    'status': 'starting',  # Mark as starting
                    'camera_name': camera_name,
                    'start_time': None  # Will be set by thread
                }
                print(f"[RESERVED] Slot for {camera_serial} - preventing duplicate starts")
            # ===== END NEW BLOCK =====

        # Start in background thread
        threading.Thread(
            target=self._start_stream,
            args=(camera_serial, stream_type),
            daemon=True
        ).start()

        # Return placeholder URL immediately
        return f"/api/streams/{camera_serial}/playlist.m3u8"

    # Private implementation
    def _start_stream(self, camera_serial: str, stream_type: str = 'sub') -> Optional[str]:
        try:
            # Step 1: Quick checks WITH lock
            with self._streams_lock:
                if camera_serial in self.active_streams:
                    entry = self.active_streams[camera_serial]
                    if entry.get('status') == 'starting':
                        print(f"[THREAD] Found reserved slot for {camera_serial}, proceeding...")
                    else:
                        print(f"[THREAD] Stream already active for {camera_serial}, aborting thread")
                        return None
                            
                # Kill lingering ffmpeg
                self._kill_all_ffmpeg_for_camera(camera_serial)
            
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
            
            # Step 3: Build URL
            print(f"════════ Building URL for {camera_name} ════════")
            source_url = handler.build_rtsp_url(camera, stream_type=stream_type)
            
            if not source_url:
                logger.error(f"Failed to build URL for {camera_name}")
                raise Exception(f"Failed to build URL for {camera_name}")
            
            print(f"✅ URL built: {source_url}")
            
            # Protocol branching (RTMP vs HLS)
            protocol = camera.get('stream_type', 'HLS').upper()
            
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
                    self.active_streams[camera_serial] = {
                        'process': process,
                        'protocol': 'rtmp',
                        'rtmp_url': source_url,
                        'stream_dir': None,
                        'camera_name': camera_name,
                        'camera_type': camera_type,
                        'start_time': time.time(),
                        'playlist_path': None,
                        'status': 'active'
                    }
                
                logger.info(f"Started RTMP stream for {camera_name}")
                return f"/api/camera/{camera_serial}/flv"
            
            else:
                # HLS path
                stream_dir = self.hls_dir / camera_serial
                stream_dir.mkdir(exist_ok=True)
                
                playlist_path = stream_dir / "playlist.m3u8"
                segment_pattern = stream_dir / "segment_%03d.ts"
                
                process = self._start_ffmpeg(
                    rtsp_url=source_url,
                    playlist_path=playlist_path,
                    segment_pattern=segment_pattern,
                    handler=handler,
                    stream_type=stream_type,
                    camera_config=camera
                )
                
                time.sleep(0.5)
                if process.poll() is not None:
                    raise Exception(f"FFmpeg died immediately")
                
                # Register WITH lock
                with self._streams_lock:
                    self.active_streams[camera_serial] = {
                        'process': process,
                        'protocol': 'hls',
                        'rtsp_url': source_url,
                        'stream_dir': stream_dir,
                        'camera_name': camera_name,
                        'camera_type': camera_type,
                        'start_time': time.time(),
                        'playlist_path': playlist_path,
                        'status': 'active' 
                        
                    }
                
                # Wait for playlist (outside lock)
                self._wait_for_playlist(camera_serial)
                
                # Start watchdog
                self._start_watchdog(camera_serial)
                
                logger.info(f"Started stream for {camera_name}")
                return self.get_stream_url(camera_serial)
        
        except Exception as e:
            # CRITICAL: Clean up the reservation slot on failure
            logger.error(f"❌ Failed to start stream for {camera_name}: {e}")
            print(traceback.print_exc())
            
            # Remove the 'starting' reservation
            with self._streams_lock:
                if camera_serial in self.active_streams:
                    entry = self.active_streams.get(camera_serial, {})
                    if entry.get('status') == 'starting':
                        logger.warning(f"Removing failed 'starting' slot for {camera_name}")
                        self.active_streams.pop(camera_serial, None)
            
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
                print("════════ FFmpeg STDERR ════════")
                print(stderr.decode('utf-8'))
                print("════════════════════════════════")
                raise Exception(f"FFmpeg died immediately with code {process.returncode}")

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
            print(f"[{caller}:is_stream_healthy] {camera_serial} not healthy (proc not None)")
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
        """Get HLS stream URL for camera"""
        with self._streams_lock:
            if camera_serial not in self.active_streams:
                return None
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
        """Get list of active streams"""
        with self._streams_lock:
            active = {}

            for camera_serial in list(self.active_streams.keys()):
                if self.is_stream_alive(camera_serial):
                    info = self.active_streams[camera_serial]
                    active[camera_serial] = {
                        'camera_name': info['camera_name'],
                        'camera_type': info['camera_type'],
                        'stream_url': self.get_stream_url(camera_serial),
                        'uptime': time.time() - info['start_time']
                    }
                else:
                    self.stop_stream(camera_serial)

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

