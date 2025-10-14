#!/usr/bin/env python3
"""
UniFi Protect Service - Simplified (No Authentication Required)
For cameras adopted into Protect - just provide pre-authenticated URLs
"""

import os
import logging
import traceback
from .camera_base import CameraService

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

        print(f"Protect Host: {self.protect_host}")
        print(f"PROTECT_USERNAME: {self.username}")
        print(f"PROTECT_SERVER_PASSWORD: {self.password}")
        print(f"CAMERA_68d49398005cf203e400043f_TOKEN_ALIAS: {self.protect_alias}")
        
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
    
    def get_snapshot(self) -> bytes:
        """
        Extract snapshot from RTSPS stream using FFmpeg
        (Only needed if not using Protect's snapshot API)
        """
        if os.getenv("USE_PROTECT", "false").lower() in ["1", "true"]:
            print("ignoring mjpeg capture as USE_PROTECT=true")
            return None

        import subprocess
        
        
        rtsp_url = self.get_rtsp_url()
        if not rtsp_url:
            return None
        
        try:
            # Use FFmpeg to extract single frame
            cmd = [
                'ffmpeg',
                '-rtsp_transport', 'tcp',
                '-i', rtsp_url,
                '-frames:v', '1',
                '-f', 'image2pipe',
                '-vcodec', 'mjpeg',
                '-'
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                logger.error(f"FFmpeg snapshot failed: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Snapshot error for {self.name}: {e}")
            print(traceback.print_exc())
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
        """No cleanup needed - no persistent connections"""
        logger.info(f"Cleanup called for {self.name} (nothing to clean)")