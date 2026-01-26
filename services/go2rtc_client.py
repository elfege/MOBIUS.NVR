#!/usr/bin/env python3
"""
go2rtc Client - ONVIF AudioBackChannel Integration

Provides an async client for interacting with go2rtc's API to send audio
to cameras via ONVIF AudioBackChannel.

Architecture (Flow 3):
    Browser -> Flask WebSocket -> This Client -> go2rtc API -> ONVIF -> Camera

go2rtc handles the ONVIF backchannel protocol, we just need to:
1. Start a backchannel session via /api/streams/{stream}/backchannel
2. Send audio frames to the WebSocket endpoint

Documentation: https://github.com/AlexxIT/go2rtc

Author: NVR System
Date: January 25, 2026
"""

import aiohttp
import asyncio
import logging
import base64
from typing import Optional, Dict, Callable

logger = logging.getLogger(__name__)


class Go2rtcClient:
    """
    Async client for go2rtc backchannel API.

    Manages WebSocket connections to go2rtc for sending audio data
    to cameras via ONVIF AudioBackChannel.

    Usage:
        client = Go2rtcClient()
        await client.start_backchannel("sv3c_living")
        await client.send_audio("sv3c_living", audio_bytes)
        await client.stop_backchannel("sv3c_living")
    """

    def __init__(self, base_url: str = "http://nvr-go2rtc:1984"):
        """
        Initialize the go2rtc client.

        Args:
            base_url: go2rtc API base URL (default: docker internal network)
        """
        self.base_url = base_url.rstrip('/')
        self.ws_base_url = base_url.replace('http://', 'ws://').replace('https://', 'wss://').rstrip('/')

        # Active WebSocket connections per stream
        self._websockets: Dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._sessions: Dict[str, aiohttp.ClientSession] = {}

        self._log_prefix = "[Go2rtcClient]"

    async def get_streams(self) -> Optional[Dict]:
        """
        Get list of configured streams from go2rtc.

        Returns:
            Dict of stream configurations or None on error
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/streams") as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"{self._log_prefix} Failed to get streams: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"{self._log_prefix} Error getting streams: {e}")
            return None

    async def check_stream_exists(self, stream_name: str) -> bool:
        """
        Check if a stream is configured in go2rtc.

        Args:
            stream_name: Name of the stream (e.g., 'sv3c_living')

        Returns:
            True if stream exists
        """
        streams = await self.get_streams()
        if streams:
            return stream_name in streams
        return False

    async def start_backchannel(self, stream_name: str) -> bool:
        """
        Start a backchannel session for a stream.

        Opens a WebSocket connection to go2rtc's backchannel endpoint.
        Audio can then be sent via send_audio().

        Args:
            stream_name: Name of the stream (must match go2rtc.yaml)

        Returns:
            True if backchannel started successfully
        """
        if stream_name in self._websockets:
            ws = self._websockets[stream_name]
            if not ws.closed:
                logger.info(f"{self._log_prefix} Backchannel already active for {stream_name}")
                return True
            else:
                # Clean up stale connection
                await self._cleanup_stream(stream_name)

        try:
            # go2rtc backchannel WebSocket endpoint
            # Format: /api/ws?src={stream}&backchannel=1
            ws_url = f"{self.ws_base_url}/api/ws?src={stream_name}&backchannel=1"

            logger.info(f"{self._log_prefix} Starting backchannel for {stream_name}: {ws_url}")

            # Create persistent session for this stream
            session = aiohttp.ClientSession()
            self._sessions[stream_name] = session

            # Connect to WebSocket
            ws = await session.ws_connect(ws_url)
            self._websockets[stream_name] = ws

            logger.info(f"{self._log_prefix} Backchannel connected for {stream_name}")
            return True

        except Exception as e:
            logger.error(f"{self._log_prefix} Failed to start backchannel for {stream_name}: {e}")
            await self._cleanup_stream(stream_name)
            return False

    async def send_audio(self, stream_name: str, audio_data: bytes) -> bool:
        """
        Send audio data to the camera via backchannel.

        Audio should be in the format expected by go2rtc/ONVIF:
        - G.711 mu-law (PCMU) at 8kHz, mono
        - Or the format configured in go2rtc for this stream

        Args:
            stream_name: Name of the stream
            audio_data: Raw audio bytes (already transcoded to correct format)

        Returns:
            True if sent successfully
        """
        if stream_name not in self._websockets:
            logger.warning(f"{self._log_prefix} No backchannel session for {stream_name}")
            return False

        ws = self._websockets[stream_name]
        if ws.closed:
            logger.warning(f"{self._log_prefix} Backchannel closed for {stream_name}")
            await self._cleanup_stream(stream_name)
            return False

        try:
            # go2rtc expects binary audio data on the WebSocket
            await ws.send_bytes(audio_data)
            return True

        except Exception as e:
            logger.error(f"{self._log_prefix} Failed to send audio to {stream_name}: {e}")
            return False

    async def send_audio_base64(self, stream_name: str, audio_base64: str) -> bool:
        """
        Send base64-encoded audio data.

        Convenience method for when audio arrives as base64 string.

        Args:
            stream_name: Name of the stream
            audio_base64: Base64-encoded audio bytes

        Returns:
            True if sent successfully
        """
        try:
            audio_data = base64.b64decode(audio_base64)
            return await self.send_audio(stream_name, audio_data)
        except Exception as e:
            logger.error(f"{self._log_prefix} Base64 decode error: {e}")
            return False

    async def stop_backchannel(self, stream_name: str) -> bool:
        """
        Stop a backchannel session.

        Closes the WebSocket connection and cleans up resources.

        Args:
            stream_name: Name of the stream

        Returns:
            True if stopped successfully
        """
        logger.info(f"{self._log_prefix} Stopping backchannel for {stream_name}")
        await self._cleanup_stream(stream_name)
        return True

    async def _cleanup_stream(self, stream_name: str):
        """Clean up WebSocket and session for a stream."""
        # Close WebSocket
        ws = self._websockets.pop(stream_name, None)
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception as e:
                logger.debug(f"{self._log_prefix} Error closing WebSocket: {e}")

        # Close session
        session = self._sessions.pop(stream_name, None)
        if session and not session.closed:
            try:
                await session.close()
            except Exception as e:
                logger.debug(f"{self._log_prefix} Error closing session: {e}")

    async def stop_all(self):
        """Stop all active backchannel sessions."""
        stream_names = list(self._websockets.keys())
        for stream_name in stream_names:
            await self.stop_backchannel(stream_name)

    def is_backchannel_active(self, stream_name: str) -> bool:
        """Check if backchannel is active for a stream."""
        ws = self._websockets.get(stream_name)
        return ws is not None and not ws.closed

    @property
    def active_backchannels(self) -> list:
        """Get list of streams with active backchannel sessions."""
        return [
            stream_name for stream_name, ws in self._websockets.items()
            if not ws.closed
        ]


# Singleton instance for use across the application
_go2rtc_client: Optional[Go2rtcClient] = None


def get_go2rtc_client() -> Go2rtcClient:
    """
    Get the singleton go2rtc client instance.

    Returns:
        Go2rtcClient instance
    """
    global _go2rtc_client
    if _go2rtc_client is None:
        _go2rtc_client = Go2rtcClient()
    return _go2rtc_client
