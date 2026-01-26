#!/usr/bin/env python3
"""
Talkback Audio Transcoder Service

Converts raw PCM audio (16kHz, mono, 16-bit) to AAC ADTS format
for Eufy cameras which require AAC-encoded audio for two-way communication.

Architecture:
    Browser → PCM (16kHz, 16-bit, mono) → WebSocket → Flask
    Flask → FFmpeg (PCM→AAC) → Eufy Bridge → Camera

Audio Format Requirements (Eufy):
    - Codec: AAC
    - Container: ADTS (raw AAC frames)
    - Sample Rate: 16 kHz
    - Channels: Mono
    - Bitrate: ~20 kbps

Author: NVR System
Date: January 25, 2026
"""

import subprocess
import threading
import queue
import base64
import logging
import time
import select
import os
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)


class TalkbackTranscoder:
    """
    Transcodes PCM audio to AAC using FFmpeg for Eufy camera talkback.

    Maintains a persistent FFmpeg process for the duration of a talkback session.
    PCM audio frames are fed to FFmpeg stdin, AAC frames are read from stdout.

    Usage:
        transcoder = TalkbackTranscoder(camera_serial, on_aac_frame_callback)
        transcoder.start()
        transcoder.feed_pcm(pcm_bytes)  # Call repeatedly
        transcoder.stop()
    """

    def __init__(
        self,
        camera_serial: str,
        on_aac_frame: Callable[[str, bytes], None],
        camera_settings: Optional[Dict] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        bitrate: str = '20k'
    ):
        """
        Initialize the transcoder.

        Args:
            camera_serial: Camera identifier for logging/callbacks
            on_aac_frame: Callback(camera_serial, aac_bytes) called when AAC frame ready
            camera_settings: Camera config dict containing two_way_audio settings (optional)
            sample_rate: Input PCM sample rate (default 16000 Hz, overridden by camera_settings)
            channels: Input channel count (default 1 = mono, overridden by camera_settings)
            bitrate: AAC output bitrate (default '20k', overridden by camera_settings)
        """
        self.camera_serial = camera_serial
        self.on_aac_frame = on_aac_frame

        # Load settings from camera config if provided
        if camera_settings:
            twa = camera_settings.get('two_way_audio', {})
            audio_out = twa.get('audio_output', {})
            self.sample_rate = audio_out.get('sample_rate') or sample_rate
            self.channels = audio_out.get('channels') or channels
            self.bitrate = audio_out.get('bitrate') or bitrate
            self.codec = audio_out.get('codec', 'aac')
            self.container = audio_out.get('container', 'adts')
            self.protocol = twa.get('protocol', 'eufy_p2p')

            logger.info(
                f"[TalkbackTranscoder:{camera_serial[:8]}] Loaded settings from config: "
                f"protocol={self.protocol}, codec={self.codec}, sample_rate={self.sample_rate}, "
                f"channels={self.channels}, bitrate={self.bitrate}"
            )
        else:
            # Use defaults (backwards compatibility)
            self.sample_rate = sample_rate
            self.channels = channels
            self.bitrate = bitrate
            self.codec = 'aac'
            self.container = 'adts'
            self.protocol = 'eufy_p2p'

        # FFmpeg process and threads
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

        # Input queue for PCM frames (thread-safe)
        self._pcm_queue: queue.Queue = queue.Queue(maxsize=100)
        self._writer_thread: Optional[threading.Thread] = None

        # Stats
        self._frames_in = 0
        self._frames_out = 0
        self._start_time = 0

        self._log_prefix = f"[TalkbackTranscoder:{camera_serial[:8]}]"

    def start(self) -> bool:
        """
        Start the FFmpeg transcoding process.

        Returns:
            bool: True if started successfully
        """
        if self._running:
            logger.warning(f"{self._log_prefix} Already running")
            return True

        logger.info(f"{self._log_prefix} Starting FFmpeg PCM→AAC transcoder")

        try:
            # FFmpeg command: read PCM from stdin, output to configured format
            # Build codec-specific command based on camera settings
            cmd = [
                'ffmpeg',
                '-hide_banner',
                '-loglevel', 'warning',  # Show warnings too for debugging
                # Input format: raw PCM
                '-f', 's16le',              # Signed 16-bit little-endian
                '-ar', str(self.sample_rate),  # Sample rate from config
                '-ac', str(self.channels),     # Channels from config
                '-i', 'pipe:0',             # Read from stdin
            ]

            # Add codec-specific output settings
            if self.codec == 'aac':
                # AAC codec (for Eufy cameras)
                cmd.extend([
                    '-c:a', 'aac',              # AAC encoder
                    '-b:a', self.bitrate or '20k',  # Bitrate
                ])
            elif self.codec == 'pcmu':
                # G.711 mu-law (for ONVIF cameras)
                cmd.extend([
                    '-c:a', 'pcm_mulaw',        # G.711 mu-law encoder
                    '-ar', '8000',              # G.711 requires 8kHz
                    '-ac', '1',                 # Mono
                ])
            elif self.codec == 'pcma':
                # G.711 A-law (alternative for ONVIF)
                cmd.extend([
                    '-c:a', 'pcm_alaw',         # G.711 A-law encoder
                    '-ar', '8000',              # G.711 requires 8kHz
                    '-ac', '1',                 # Mono
                ])
            else:
                # Default to AAC
                cmd.extend([
                    '-c:a', 'aac',
                    '-b:a', self.bitrate or '20k',
                ])

            # Add output format and flush settings
            cmd.extend([
                '-flush_packets', '1',       # Flush output immediately
                '-fflags', '+flush_packets', # Additional flush flag
            ])

            # Output container format
            if self.container == 'adts' or self.codec == 'aac':
                cmd.extend(['-f', 'adts'])   # ADTS container for AAC
            elif self.codec in ('pcmu', 'pcma'):
                cmd.extend(['-f', 'mulaw' if self.codec == 'pcmu' else 'alaw'])
            else:
                cmd.extend(['-f', 'adts'])   # Default

            cmd.append('pipe:1')  # Write to stdout

            logger.info(f"{self._log_prefix} FFmpeg command: {' '.join(cmd)}")

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for low latency
            )

            self._running = True
            self._start_time = time.time()
            self._frames_in = 0
            self._frames_out = 0

            # Start reader thread (reads AAC from FFmpeg stdout)
            self._reader_thread = threading.Thread(
                target=self._read_aac_frames,
                name=f"TalkbackReader-{self.camera_serial[:8]}",
                daemon=True
            )
            self._reader_thread.start()
            print(f"{self._log_prefix} Reader thread started: {self._reader_thread.is_alive()}", flush=True)

            # Start writer thread (feeds PCM to FFmpeg stdin)
            self._writer_thread = threading.Thread(
                target=self._write_pcm_frames,
                name=f"TalkbackWriter-{self.camera_serial[:8]}",
                daemon=True
            )
            self._writer_thread.start()

            logger.info(f"{self._log_prefix} Transcoder started")
            return True

        except Exception as e:
            logger.error(f"{self._log_prefix} Failed to start: {e}")
            self._cleanup()
            return False

    def stop(self):
        """Stop the transcoding process and cleanup."""
        if not self._running:
            return

        logger.info(f"{self._log_prefix} Stopping transcoder")
        self._running = False

        # Signal writer to stop
        self._pcm_queue.put(None)

        # Give threads time to finish
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1)

        self._cleanup()

        # Log stats
        duration = time.time() - self._start_time
        logger.info(
            f"{self._log_prefix} Stopped. Duration: {duration:.1f}s, "
            f"Frames in: {self._frames_in}, Frames out: {self._frames_out}"
        )

    def feed_pcm(self, pcm_data: bytes) -> bool:
        """
        Feed PCM audio data to the transcoder.

        Args:
            pcm_data: Raw PCM bytes (16-bit signed, little-endian, mono, 16kHz)

        Returns:
            bool: True if queued successfully
        """
        if not self._running:
            return False

        try:
            self._pcm_queue.put_nowait(pcm_data)
            self._frames_in += 1
            return True
        except queue.Full:
            # Drop frame if queue is full (prevents memory buildup)
            logger.warning(f"{self._log_prefix} PCM queue full, dropping frame")
            return False

    def feed_pcm_base64(self, pcm_base64: str) -> bool:
        """
        Feed base64-encoded PCM audio data.

        Args:
            pcm_base64: Base64-encoded PCM bytes

        Returns:
            bool: True if queued successfully
        """
        try:
            pcm_data = base64.b64decode(pcm_base64)
            return self.feed_pcm(pcm_data)
        except Exception as e:
            logger.error(f"{self._log_prefix} Base64 decode error: {e}")
            return False

    def _write_pcm_frames(self):
        """Writer thread: feeds PCM from queue to FFmpeg stdin."""
        log_prefix = f"{self._log_prefix} [Writer]"

        while self._running and self._process and self._process.stdin:
            try:
                # Wait for PCM data with timeout
                pcm_data = self._pcm_queue.get(timeout=0.5)

                if pcm_data is None:
                    # Stop signal
                    break

                # Write to FFmpeg stdin
                self._process.stdin.write(pcm_data)
                self._process.stdin.flush()

            except queue.Empty:
                continue
            except BrokenPipeError:
                logger.error(f"{log_prefix} FFmpeg stdin closed")
                break
            except Exception as e:
                logger.error(f"{log_prefix} Write error: {e}")
                break

        # Close stdin to signal EOF to FFmpeg
        try:
            if self._process and self._process.stdin:
                self._process.stdin.close()
        except:
            pass

        logger.debug(f"{log_prefix} Writer thread exiting")

    def _read_aac_frames(self):
        """Reader thread: reads AAC frames from FFmpeg stdout and calls callback."""
        log_prefix = f"{self._log_prefix} [Reader]"

        print(f"{log_prefix} Reader thread function entered", flush=True)

        # AAC frames are small, typically 100-300 bytes for voice at 20kbps
        # Read in small chunks for low latency
        CHUNK_SIZE = 512

        # Make stdout non-blocking so we can poll for data
        stdout_fd = self._process.stdout.fileno()
        import fcntl
        fl = fcntl.fcntl(stdout_fd, fcntl.F_GETFL)
        fcntl.fcntl(stdout_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        print(f"{log_prefix} Set stdout to non-blocking mode", flush=True)

        read_count = 0
        while self._running and self._process:
            try:
                # Check if FFmpeg process is still alive
                if self._process.poll() is not None:
                    retcode = self._process.returncode
                    stderr_data = b''
                    try:
                        stderr_data = self._process.stderr.read()
                    except:
                        pass
                    print(f"{log_prefix} FFmpeg exited with code {retcode}, stderr: {stderr_data.decode('utf-8', errors='ignore')}", flush=True)
                    break

                # Use select to wait for data with timeout
                readable, _, _ = select.select([self._process.stdout], [], [], 0.1)

                if not readable:
                    # No data available yet, continue waiting
                    continue

                # Read AAC data from FFmpeg stdout
                try:
                    aac_chunk = self._process.stdout.read(CHUNK_SIZE)
                except BlockingIOError:
                    # No data ready despite select (race condition)
                    continue

                read_count += 1

                if not aac_chunk:
                    # FFmpeg closed stdout
                    if self._running:
                        logger.warning(f"{log_prefix} FFmpeg stdout closed (read returned empty)")
                    break

                # Log progress
                if self._frames_out == 0:
                    print(f"{log_prefix} First AAC chunk received! size={len(aac_chunk)}B", flush=True)
                elif self._frames_out % 50 == 0:
                    print(f"{log_prefix} Read AAC chunk #{self._frames_out}, size={len(aac_chunk)}B", flush=True)

                # Call the callback with AAC data
                try:
                    self.on_aac_frame(self.camera_serial, aac_chunk)
                    self._frames_out += 1
                except Exception as e:
                    logger.error(f"{log_prefix} Callback error: {e}")

            except Exception as e:
                if self._running:
                    logger.error(f"{log_prefix} Read error: {e}")
                    import traceback
                    print(f"{log_prefix} Exception: {traceback.format_exc()}", flush=True)
                break

        logger.debug(f"{log_prefix} Reader thread exiting")

    def _cleanup(self):
        """Clean up FFmpeg process and resources."""
        if self._process:
            try:
                # Close pipes
                if self._process.stdin:
                    self._process.stdin.close()
                if self._process.stdout:
                    self._process.stdout.close()
                if self._process.stderr:
                    self._process.stderr.close()

                # Terminate process
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            except Exception as e:
                logger.error(f"{self._log_prefix} Cleanup error: {e}")

            self._process = None

        # Clear queue
        try:
            while not self._pcm_queue.empty():
                self._pcm_queue.get_nowait()
        except:
            pass

    @property
    def is_running(self) -> bool:
        """Check if transcoder is running."""
        return self._running and self._process is not None


class TalkbackTranscoderManager:
    """
    Manages TalkbackTranscoder instances for multiple cameras.

    One transcoder per active talkback session.
    """

    def __init__(self, on_aac_frame: Callable[[str, bytes], None]):
        """
        Initialize the manager.

        Args:
            on_aac_frame: Callback(camera_serial, aac_bytes) for all transcoders
        """
        self.on_aac_frame = on_aac_frame
        self._transcoders: Dict[str, TalkbackTranscoder] = {}
        self._lock = threading.Lock()

    def start_transcoder(self, camera_serial: str, camera_settings: Optional[Dict] = None) -> bool:
        """
        Start a transcoder for a camera.

        Args:
            camera_serial: Camera identifier
            camera_settings: Camera config dict containing two_way_audio settings (optional)

        Returns:
            bool: True if started successfully
        """
        with self._lock:
            # Check if already exists
            if camera_serial in self._transcoders:
                if self._transcoders[camera_serial].is_running:
                    logger.info(f"[TranscoderManager] Transcoder already running for {camera_serial}")
                    return True
                else:
                    # Cleanup stale transcoder
                    self._transcoders[camera_serial].stop()
                    del self._transcoders[camera_serial]

            # Create and start new transcoder with camera settings
            transcoder = TalkbackTranscoder(
                camera_serial=camera_serial,
                on_aac_frame=self.on_aac_frame,
                camera_settings=camera_settings
            )

            if transcoder.start():
                self._transcoders[camera_serial] = transcoder
                return True
            else:
                return False

    def stop_transcoder(self, camera_serial: str):
        """Stop and remove transcoder for a camera."""
        with self._lock:
            transcoder = self._transcoders.pop(camera_serial, None)
            if transcoder:
                transcoder.stop()

    def feed_pcm(self, camera_serial: str, pcm_data: bytes) -> bool:
        """Feed PCM data to a camera's transcoder."""
        with self._lock:
            transcoder = self._transcoders.get(camera_serial)

        if not transcoder:
            logger.warning(f"[TranscoderManager] No transcoder for {camera_serial}")
            return False

        return transcoder.feed_pcm(pcm_data)

    def feed_pcm_base64(self, camera_serial: str, pcm_base64: str) -> bool:
        """Feed base64-encoded PCM data to a camera's transcoder."""
        with self._lock:
            transcoder = self._transcoders.get(camera_serial)

        if not transcoder:
            logger.warning(f"[TranscoderManager] No transcoder for {camera_serial}")
            return False

        return transcoder.feed_pcm_base64(pcm_base64)

    def stop_all(self):
        """Stop all transcoders."""
        with self._lock:
            for camera_serial, transcoder in list(self._transcoders.items()):
                transcoder.stop()
            self._transcoders.clear()
