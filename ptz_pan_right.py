#!/usr/bin/env python3
"""
PTZ Test Script for UniFi G5-Flex
Tests pan right command using same login method as stream_proxy.py
"""

import requests
import json
import time

# Configuration
CAMERA_IP = "192.168.10.104"
USERNAME = "ubnt"
PASSWORD = "ubnt"

class G5FlexPTZ:
    def __init__(self, camera_ip, username="ubnt", password="ubnt"):
        self.camera_ip = camera_ip
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.login_url = f"http://{camera_ip}/api/1.1/login"
        self.settings_url = f"http://{camera_ip}/api/1.1/settings"
        self.last_login = 0
        self.login_interval = 3600  # Re-login every hour
        
    def login(self):
        """Login to camera and establish session - SAME AS STREAM_PROXY.PY"""
        try:
            login_data = {
                "username": self.username,
                "password": self.password
            }
            
            headers = {
                "Content-Type": "application/json"
            }
            
            print(f"[{time.strftime('%H:%M:%S')}] Logging into camera {self.camera_ip}...")
            
            response = self.session.post(
                self.login_url, 
                data=json.dumps(login_data), 
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                self.last_login = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] Login successful!")
                return True
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Login failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Login error: {e}")
            return False
    
    def get_current_position(self):
        """Get current PTZ position using authenticated session"""
        try:
            response = self.session.get(self.settings_url, timeout=10)
            if response.status_code == 200:
                settings = response.json()
                ptz_settings = settings.get('isp', {})
                return {
                    'centerX': ptz_settings.get('dZoomCenterX', 50),
                    'centerY': ptz_settings.get('dZoomCenterY', 50), 
                    'scale': ptz_settings.get('dZoomScale', 0),
                    'streamId': ptz_settings.get('dZoomStreamId', 4)
                }
            else:
                print(f"Settings request failed: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error getting position: {e}")
            return None
    
    def set_ptz_position(self, center_x=None, center_y=None, scale=None):
        """Set PTZ position using correct API format from camera controller"""
        try:
            if center_x is not None:
                # CORRECT FORMAT: PUT /api/1.1/settings with nested JSON structure
                print(f"Trying correct PUT /settings format...")
                api_url = f"http://{self.camera_ip}/api/1.1/settings"
                
                # Build nested JSON structure like the web interface
                settings_data = {
                    "isp": {
                        "dZoomCenterX": center_x
                    }
                }
                
                response = self.session.put(
                    api_url,
                    data=json.dumps(settings_data),
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                print(f"PUT /settings response status: {response.status_code}")
                print(f"PUT /settings response text: {response.text[:200]}")
                
                if response.status_code == 200:
                    return True
                
                # Try with additional parameters that might be required
                print(f"Trying with multiple ISP parameters...")
                full_settings_data = {
                    "isp": {
                        "dZoomCenterX": center_x,
                        "dZoomCenterY": center_y or 50,  # Keep current or default
                        "dZoomScale": scale or 0,        # Keep current or default
                        "dZoomStreamId": 4               # Default stream ID
                    }
                }
                
                response = self.session.put(
                    api_url,
                    data=json.dumps(full_settings_data),
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                print(f"Full ISP settings response status: {response.status_code}")
                print(f"Full ISP settings response text: {response.text[:200]}")
                
                if response.status_code == 200:
                    return True
                    
            return False
            
        except Exception as e:
            print(f"Error setting PTZ position: {e}")
            return False

def main():
    print("=" * 50)
    print("UniFi G5-Flex PTZ Test")
    print("Testing Pan Right Command")
    print("Using same auth method as stream_proxy.py")
    print("=" * 50)
    
    # Initialize PTZ controller
    ptz = G5FlexPTZ(CAMERA_IP, USERNAME, PASSWORD)
    
    # Login using proven method
    if not ptz.login():
        print("ERROR: Could not login to camera!")
        return
    
    # Get current position
    print("\n--- Current PTZ Position ---")
    current_pos = ptz.get_current_position()
    if current_pos:
        print(f"Center X: {current_pos['centerX']}")
        print(f"Center Y: {current_pos['centerY']}")
        print(f"Scale: {current_pos['scale']}")
        print(f"Stream ID: {current_pos['streamId']}")
    else:
        print("Could not get current position - authentication may have failed")
        return
    
    # Calculate new position (pan right = increase X by 10)
    new_x = min(100, current_pos['centerX'] + 10)
    
    print(f"\n--- Testing Pan Right ---")
    print(f"Moving from X={current_pos['centerX']} to X={new_x}")
    
    # Send PTZ command using multiple formats
    success = ptz.set_ptz_position(center_x=new_x)
    
    if success:
        print("\n✓ PTZ command sent successfully!")
        
        # Wait and check new position
        print("Waiting 3 seconds for camera to process...")
        time.sleep(3)
        
        new_pos = ptz.get_current_position()
        if new_pos and new_pos['centerX'] == new_x:
            print(f"✓ Position updated successfully! New X: {new_pos['centerX']}")
        else:
            print(f"⚠ Position may not have updated. Current X: {new_pos['centerX'] if new_pos else 'unknown'}")
            print("This could mean the API call format is still incorrect")
    else:
        print("\n✗ All PTZ command formats failed!")
        print("Need to investigate the correct API format further")

if __name__ == "__main__":
    main()