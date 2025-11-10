#!/usr/bin/env python3
"""
Eufy Bridge WebSocket Client

Provides Python interface to eufy-security-ws WebSocket server
for authentication operations (captcha and 2FA submission).
"""

import asyncio
import json
import websockets
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class EufyBridgeClient:
    """WebSocket client for communicating with eufy-security-ws bridge"""
    
    def __init__(self, bridge_url: str = "ws://127.0.0.1:3000", timeout: int = 10):
        """
        Initialize Eufy bridge client
        
        Args:
            bridge_url: WebSocket URL of eufy-security-ws server
            timeout: Connection/operation timeout in seconds
        """
        self.bridge_url = bridge_url
        self.timeout = timeout
        
    async def _send_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send command to bridge and get response
        
        Args:
            command: Command dictionary to send
            
        Returns:
            Response dictionary from bridge
            
        Raises:
            ConnectionError: If connection fails
            TimeoutError: If operation times out
            ValueError: If command fails
        """
        try:
            async with websockets.connect(
                self.bridge_url, 
                open_timeout=self.timeout,
                close_timeout=5
            ) as ws:
                # Send command
                await ws.send(json.dumps(command))
                
                # Get response with timeout
                response_str = await asyncio.wait_for(
                    ws.recv(), 
                    timeout=self.timeout
                )
                
                response = json.loads(response_str)
                
                # Check for success
                if response.get('type') == 'result':
                    if response.get('success'):
                        logger.info(f"Command successful: {command.get('command', 'unknown')}")
                        return response
                    else:
                        error_msg = response.get('error', 'Unknown error')
                        logger.error(f"Command failed: {error_msg}")
                        raise ValueError(f"Command failed: {error_msg}")
                        
                return response
                
        except asyncio.TimeoutError as e:
            logger.error(f"Command timeout: {command.get('command', 'unknown')}")
            raise TimeoutError(f"Operation timed out after {self.timeout}s") from e
            
        except websockets.exceptions.WebSocketException as e:
            logger.error(f"WebSocket error: {e}")
            raise ConnectionError(f"Failed to connect to bridge: {e}") from e
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            raise ValueError(f"Invalid response from bridge") from e
    
    async def connect_driver(self) -> bool:
        """
        Connect to Eufy cloud (triggers captcha/2FA if needed)
        
        Returns:
            True if connection initiated successfully
        """
        try:
            command = {
                "messageId": "connect",
                "command": "driver.connect"
            }
            
            response = await self._send_command(command)
            return response.get('success', False)
            
        except Exception as e:
            logger.error(f"Failed to connect driver: {e}")
            return False
    
    async def submit_captcha(self, captcha_code: str) -> bool:
        """
        Submit captcha code to bridge
        
        Args:
            captcha_code: 4-digit captcha code from image
            
        Returns:
            True if captcha accepted, False otherwise
        """
        if not captcha_code or len(captcha_code) != 4 or not captcha_code.isalnum():
            raise ValueError("Captcha code must be exactly 4 digits")
        
        try:
            command = {
                "messageId": "set_captcha",
                "command": "driver.set_captcha",
                "captchaCode": captcha_code 
            }
            
            response = await self._send_command(command)
            success = response.get('success', False)
            
            if success:
                logger.info(f"Captcha accepted: {captcha_code}")
            else:
                logger.warning(f"Captcha rejected: {captcha_code}")
                
            return success
            
        except Exception as e:
            logger.error(f"Failed to submit captcha: {e}")
            return False
    
    async def submit_2fa(self, verify_code: str) -> bool:
        """
        Submit 2FA verification code to bridge
        
        Args:
            verify_code: 6-digit code from email
            
        Returns:
            True if 2FA accepted, False otherwise
        """
        if not verify_code or len(verify_code) != 6 or not verify_code.isdigit():
            raise ValueError("2FA code must be exactly 6 digits")
        
        try:
            command = {
                "messageId": "set_verify_code",
                "command": "driver.set_verify_code",
                "verifyCode": verify_code
            }
            
            response = await self._send_command(command)
            success = response.get('success', False)
            
            if success:
                logger.info(f"2FA code accepted: {verify_code}")
            else:
                logger.warning(f"2FA code rejected: {verify_code}")
                
            return success
            
        except Exception as e:
            logger.error(f"Failed to submit 2FA: {e}")
            return False
    
    async def check_connection_status(self) -> Dict[str, Any]:
        """
        Check if bridge is connected to Eufy cloud
        
        Returns:
            Status dictionary with 'connected' and 'push_connected' booleans
        """
        try:
            # Check driver connection
            command = {
                "messageId": "is_connected",
                "command": "driver.is_connected"
            }
            
            response = await self._send_command(command)
            result = response.get('result', {})
            
            return {
                'connected': result.get('connected', False),
                'status': 'connected' if result.get('connected') else 'disconnected'
            }
            
        except Exception as e:
            logger.error(f"Failed to check connection status: {e}")
            return {
                'connected': False,
                'status': 'error',
                'error': str(e)
            }


def submit_captcha_sync(captcha_code: str, bridge_url: str = "ws://127.0.0.1:3000") -> bool:
    """
    Synchronous wrapper for submitting captcha code
    
    Args:
        captcha_code: 4-digit captcha code
        bridge_url: WebSocket URL of bridge
        
    Returns:
        True if successful, False otherwise
    """
    client = EufyBridgeClient(bridge_url)
    return asyncio.run(client.submit_captcha(captcha_code))


def submit_2fa_sync(verify_code: str, bridge_url: str = "ws://127.0.0.1:3000") -> bool:
    """
    Synchronous wrapper for submitting 2FA code
    
    Args:
        verify_code: 6-digit verification code
        bridge_url: WebSocket URL of bridge
        
    Returns:
        True if successful, False otherwise
    """
    client = EufyBridgeClient(bridge_url)
    return asyncio.run(client.submit_2fa(verify_code))


def check_status_sync(bridge_url: str = "ws://127.0.0.1:3000") -> Dict[str, Any]:
    """
    Synchronous wrapper for checking connection status
    
    Args:
        bridge_url: WebSocket URL of bridge
        
    Returns:
        Status dictionary
    """
    client = EufyBridgeClient(bridge_url)
    return asyncio.run(client.check_connection_status())


if __name__ == "__main__":
    # Test the client
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python eufy_bridge_client.py captcha <4-digit-code>")
        print("  python eufy_bridge_client.py 2fa <6-digit-code>")
        print("  python eufy_bridge_client.py status")
        sys.exit(1)
    
    action = sys.argv[1].lower()
    
    if action == "captcha" and len(sys.argv) == 3:
        success = submit_captcha_sync(sys.argv[2])
        sys.exit(0 if success else 1)
        
    elif action == "2fa" and len(sys.argv) == 3:
        success = submit_2fa_sync(sys.argv[2])
        sys.exit(0 if success else 1)
        
    elif action == "status":
        status = check_status_sync()
        print(json.dumps(status, indent=2))
        sys.exit(0)
        
    else:
        print("Invalid command")
        sys.exit(1)