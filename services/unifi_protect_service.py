#!/usr/bin/env python3
"""
UniFi Protect Service - Simplified (No Authentication Required)
For cameras adopted into Protect - just provide pre-authenticated URLs
"""

import os
import logging
import traceback
import requests
import urllib3
from .camera_base import CameraService

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

class UniFiProtectService(CameraService):
    """
    UniFi Protect camera service - Uses tokenized URLs from Protect
    
    Two modes:
    1. Direct LL-HLS proxy (camera provides LL-HLS URL with embedded token)
    2. RTSPS transcoding (FFmpeg converts RTSPS to our own LL-HLS)
    """
    
    def __init__(self, camera_config):
        
        super().__init__(camera_config)
        
        # Protect console info
        self.protect_host = camera_config.get('protect_host', '192.168.10.3')
        self.camera_id = camera_config.get('camera_id')
        self.username = os.getenv('PROTECT_USERNAME', "None")
        self.password = os.getenv('PROTECT_SERVER_PASSWORD', "None")
        self.protect_alias = os.getenv('CAMERA_68d49398005cf203e400043f_TOKEN_ALIAS', "None")
        self.rtsp_alias = os.getenv('CAMERA_68d49398005cf203e400043f_TOKEN_ALIAS', "None") # camera_config.get('rtsp_alias')  # From bootstrap or manual config
        self.protect_port = os.getenv('PROTECT_PORT', 7447)
        # Pre-authenticated URLs (optional - from Protect web UI)
        self.ll_hls_url = camera_config.get('ll_hls_url')  # Tokenized LL-HLS from Protect
        
        # Streaming mode
        self.stream_mode = camera_config.get('stream_mode', 'rtsps_transcode')  # or 'direct_proxy'

        # HTTP session for Protect API calls (reused for efficiency)
        self._session = None
        self._authenticated = False

        logger.info(f"Initialized {self.name} in {self.stream_mode} mode")
        
    def authenticate(self) -> bool:
        """No authentication required - URLs are pre-authenticated"""
        return True
    
    def get_rtsp_url(self) -> str:
        """
        Get RTSPS URL for FFmpeg transcoding
        Format: rtsps://PROTECT_IP:7441/RTSP_ALIAS
        """
        if not self.rtsp_alias:
            logger.error(f"No rtsp_alias configured for {self.name}")
            return None
        
        return f"rtsp://{self.protect_host}:{self.protect_port}/{self.rtsp_alias}"
    
    def get_ll_hls_url(self) -> str:
        """
        Get direct LL-HLS URL from Protect (if configured)
        This URL includes embedded authentication token
        """
        return self.ll_hls_url
    
    def get_stream_url(self) -> str:
        """
        Return appropriate stream URL based on mode
        - direct_proxy: Return Protect's LL-HLS URL
        - rtsps_transcode: Return our HLS endpoint (we transcode)
        """
        if self.stream_mode == 'direct_proxy':
            return self.get_ll_hls_url()
        else:
            # Our own HLS endpoint (stream_manager transcodes from RTSPS)
            return f"/api/streams/{self.camera_id}/playlist.m3u8"
    
    def _ensure_session(self) -> bool:
        """
        Ensure we have an authenticated session with Protect API.
        Creates session and logs in if needed.
        """
        if self._session and self._authenticated:
            return True

        try:
            self._session = requests.Session()

            # Login to Protect API
            login_url = f"https://{self.protect_host}/api/auth/login"
            login_data = {
                "username": self.username,
                "password": self.password
            }

            response = self._session.post(
                login_url,
                json=login_data,
                verify=False,  # Self-signed certs
                timeout=10
            )

            if response.status_code == 200:
                self._authenticated = True
                logger.info(f"Authenticated with Protect API for {self.name}")
                return True
            else:
                logger.error(f"Protect API login failed: {response.status_code} - {response.text}")
                self._session = None
                return False

        except Exception as e:
            logger.error(f"Protect API authentication error: {e}")
            self._session = None
            return False

    def get_snapshot(self) -> bytes:
        """
        Get snapshot from Protect API.
        Uses authenticated session to fetch JPEG from Protect console.
        """
        try:
            # Ensure we have an authenticated session
            if not self._ensure_session():
                logger.error(f"Cannot get snapshot - not authenticated with Protect")
                return None

            # Build snapshot URL using Protect API
            # Format: https://{protect_host}/proxy/protect/api/cameras/{camera_id}/snapshot
            snapshot_url = f"https://{self.protect_host}/proxy/protect/api/cameras/{self.camera_id}/snapshot"

            response = self._session.get(
                snapshot_url,
                verify=False,  # Self-signed certs
                timeout=10
            )

            if response.status_code == 200:
                return response.content
            elif response.status_code == 401:
                # Session expired, re-authenticate and retry once
                logger.warning(f"Protect session expired, re-authenticating...")
                self._authenticated = False
                if self._ensure_session():
                    response = self._session.get(snapshot_url, verify=False, timeout=10)
                    if response.status_code == 200:
                        return response.content
                logger.error(f"Snapshot failed after re-auth: {response.status_code}")
                return None
            else:
                logger.error(f"Protect snapshot failed: {response.status_code} - {response.text[:200]}")
                return None

        except Exception as e:
            logger.error(f"Snapshot error for {self.name}: {e}")
            traceback.print_exc()
            return None
    
    def get_stats(self):
        """Get camera statistics"""
        return {
            "protect_host": self.protect_host,
            "camera_id": self.camera_id,
            "camera_name": self.name,
            "stream_mode": self.stream_mode,
            "rtsp_alias": self.rtsp_alias,
            "has_ll_hls_url": self.ll_hls_url is not None,
            "rtsp_url": self.get_rtsp_url() if self.rtsp_alias else "Not configured"
        }
    
    def cleanup(self):
        """Close HTTP session if open"""
        if self._session:
            try:
                self._session.close()
                self._session = None
                self._authenticated = False
                logger.info(f"Closed Protect API session for {self.name}")
            except Exception as e:
                logger.warning(f"Error closing session for {self.name}: {e}")