#!/usr/bin/env python3
"""
Enhanced PTZ Discovery Script for UniFi G5-Flex
Attempts multiple PTZ control methods beyond digital zoom
"""

import requests
import json
import time
import socket
import struct
from urllib.parse import urlencode

class G5FlexPTZDiscovery:
    def __init__(self, camera_ip, username="ubnt", password="ubnt"):
        self.camera_ip = camera_ip
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.login_url = f"http://{camera_ip}/api/1.1/login"
        self.last_login = 0
        self.login_interval = 3600
        
    def login(self):
        """Login using proven method from stream_proxy"""
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
    
    def test_ptz_endpoints(self):
        """Test various PTZ endpoint possibilities"""
        endpoints = [
            # Standard PTZ endpoints
            "/api/1.1/ptz",
            "/api/1.1/ptz/move",
            "/api/1.1/ptz/control", 
            "/api/1.1/motor",
            "/api/1.1/motor/move",
            "/api/1.1/camera/ptz",
            "/api/1.1/camera/move",
            
            # CGI style endpoints
            "/cgi-bin/ptz.cgi",
            "/cgi-bin/motor.cgi", 
            "/rest.cgi",
            "/axis-cgi/com/ptz.cgi",
            
            # Other possible endpoints
            "/ptz",
            "/motor", 
            "/move",
            "/control/ptz",
            "/control/motor",
            "/sys/ptz",
            "/sys/motor"
        ]
        
        ptz_commands = [
            {"action": "move", "direction": "right", "speed": 50},
            {"cmd": "right", "speed": 50},
            {"pan": 1, "tilt": 0, "zoom": 0},
            {"move": "right"}
        ]
        
        print("\n=== Testing PTZ Endpoints ===")
        for endpoint in endpoints:
            url = f"http://{self.camera_ip}{endpoint}"
            
            # Test GET first
            try:
                response = self.session.get(url, timeout=5)
                if response.status_code != 404:
                    print(f"✓ {endpoint} responds: {response.status_code}")
                    if len(response.text) < 200:
                        print(f"  Response: {response.text[:100]}")
            except:
                pass
            
            # Test POST with different command formats
            for cmd_data in ptz_commands:
                try:
                    response = self.session.post(
                        url, 
                        json=cmd_data,
                        timeout=5
                    )
                    if response.status_code not in [404, 405]:
                        print(f"✓ {endpoint} POST {cmd_data}: {response.status_code}")
                        break
                except:
                    continue
    
    def test_cgi_parameters(self):
        """Test CGI-style parameter combinations"""
        print("\n=== Testing CGI Parameter Formats ===")
        
        # Common PTZ CGI formats
        cgi_formats = [
            "/rest.cgi?move=right&speed=50",
            "/rest.cgi?action=move&direction=right&speed=50", 
            "/rest.cgi?cmd=ptz&pan=right&speed=50",
            "/rest.cgi?ptz=move&dir=right&vel=50",
            "/cgi-bin/ptz.cgi?move=right&speed=50",
            "/axis-cgi/com/ptz.cgi?move=right&speed=50",
            "/ptz?move=right&speed=50",
            "/motor?cmd=right&speed=50"
        ]
        
        for cgi_url in cgi_formats:
            try:
                url = f"http://{self.camera_ip}{cgi_url}"
                response = self.session.get(url, timeout=5)
                if response.status_code not in [404, 500]:
                    print(f"✓ {cgi_url}: {response.status_code}")
                    if len(response.text) < 100:
                        print(f"  Response: {response.text}")
            except Exception as e:
                continue
    
    def test_websocket_ptz(self):
        """Check if PTZ uses WebSocket like the video stream"""
        print("\n=== Checking WebSocket PTZ ===")
        
        # Check if there are WebSocket endpoints for PTZ
        ws_paths = [
            "/ws/ptz",
            "/ws/motor", 
            "/ws/control",
            "/websocket/ptz",
            "/socket/ptz"
        ]
        
        for path in ws_paths:
            try:
                # Try to connect (will fail but may give different error codes)
                url = f"http://{self.camera_ip}{path}"
                response = self.session.get(url, timeout=2)
                if response.status_code not in [404]:
                    print(f"✓ WebSocket path exists: {path} ({response.status_code})")
            except:
                continue
    
    def test_udp_ptz(self):
        """Test if PTZ uses UDP protocol"""
        print("\n=== Testing UDP PTZ ===")
        
        # Common PTZ UDP ports
        udp_ports = [554, 8554, 1259, 52000, 52001, 52002]
        
        for port in udp_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2)
                
                # Simple PTZ command formats
                commands = [
                    b"PTZ_RIGHT",
                    b"MOVE:RIGHT:50", 
                    struct.pack("!BBB", 0x81, 0x01, 0x06),  # VISCA format
                    b"\x81\x01\x06\x01\x01\x01\x03\x01\xFF"  # VISCA pan right
                ]
                
                for cmd in commands:
                    try:
                        sock.sendto(cmd, (self.camera_ip, port))
                        data, addr = sock.recvfrom(1024)
                        print(f"✓ UDP response on port {port}: {data.hex()}")
                        break
                    except socket.timeout:
                        continue
                    except Exception as e:
                        continue
                
                sock.close()
            except Exception as e:
                continue
    
    def test_settings_commit(self):
        """Test if PTZ settings need a commit/apply action"""
        print("\n=== Testing Settings Commit ===")
        
        # Try setting digital zoom then triggering various commit actions
        ptz_data = {
            "isp": {
                "dZoomCenterX": 75,
                "dZoomCenterY": 50,
                "dZoomScale": 0,
                "dZoomStreamId": 4
            }
        }
        
        # Set the PTZ position
        response = self.session.put(
            f"http://{self.camera_ip}/api/1.1/settings",
            json=ptz_data,
            headers={"Content-Type": "application/json"}
        )
        print(f"PTZ setting response: {response.status_code}")
        
        # Try various commit/apply endpoints
        commit_endpoints = [
            "/api/1.1/settings/commit",
            "/api/1.1/settings/apply", 
            "/api/1.1/commit",
            "/api/1.1/apply",
            "/api/1.1/save",
            "/api/1.1/reload",
            "/api/1.1/restart",
            "/commit",
            "/apply"
        ]
        
        for endpoint in commit_endpoints:
            try:
                url = f"http://{self.camera_ip}{endpoint}"
                
                # Try POST
                response = self.session.post(url, timeout=5)
                if response.status_code not in [404, 405]:
                    print(f"✓ Commit endpoint {endpoint}: {response.status_code}")
                
                # Try GET
                response = self.session.get(url, timeout=5)
                if response.status_code not in [404, 405]:
                    print(f"✓ Commit endpoint {endpoint} (GET): {response.status_code}")
                    
            except Exception as e:
                continue
                
        time.sleep(2)
        
        # Check if position changed
        try:
            response = self.session.get(f"http://{self.camera_ip}/api/1.1/settings")
            if response.status_code == 200:
                settings = response.json()
                current_x = settings.get('isp', {}).get('dZoomCenterX', 50)
                print(f"Current X position after commit attempts: {current_x}")
        except:
            pass
    
    def test_alternate_auth_headers(self):
        """Test if PTZ requires different authentication headers"""
        print("\n=== Testing Alternate Auth Headers ===")
        
        ptz_data = {
            "isp": {
                "dZoomCenterX": 80,
                "dZoomCenterY": 50,
                "dZoomScale": 0,
                "dZoomStreamId": 4
            }
        }
        
        # Different header combinations
        header_sets = [
            {"Content-Type": "application/json", "X-PTZ-Control": "1"},
            {"Content-Type": "application/json", "X-Camera-Auth": "ubnt"},
            {"Content-Type": "application/json", "Authorization": "Basic dWJudDp1Ym50"},
            {"Content-Type": "application/x-www-form-urlencoded"},
            {"Content-Type": "text/plain"},
        ]
        
        for headers in header_sets:
            try:
                if headers.get("Content-Type") == "application/x-www-form-urlencoded":
                    # Try form data instead of JSON
                    form_data = "isp.dZoomCenterX=80&isp.dZoomCenterY=50&isp.dZoomScale=0"
                    response = self.session.put(
                        f"http://{self.camera_ip}/api/1.1/settings",
                        data=form_data,
                        headers=headers
                    )
                else:
                    response = self.session.put(
                        f"http://{self.camera_ip}/api/1.1/settings",
                        json=ptz_data,
                        headers=headers
                    )
                
                print(f"Headers {headers}: {response.status_code}")
                if response.status_code != 200:
                    print(f"  Response: {response.text[:100]}")
                    
            except Exception as e:
                continue

def main():
    CAMERA_IP = "192.168.10.104"
    
    print("=" * 70)
    print("UniFi G5-Flex Enhanced PTZ Discovery")
    print("=" * 70)
    print(f"Camera IP: {CAMERA_IP}")
    print()
    
    discovery = G5FlexPTZDiscovery(CAMERA_IP)
    
    if not discovery.login():
        print("ERROR: Could not login to camera!")
        return
    
    # Run all discovery tests
    discovery.test_ptz_endpoints()
    discovery.test_cgi_parameters()
    discovery.test_websocket_ptz()
    discovery.test_udp_ptz()
    discovery.test_settings_commit()
    discovery.test_alternate_auth_headers()
    
    print("\n" + "=" * 70)
    print("Discovery Complete!")
    print("Check output above for any successful PTZ endpoints or methods.")
    print("=" * 70)

if __name__ == "__main__":
    main()