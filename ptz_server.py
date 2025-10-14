#!/usr/bin/env python3
"""
UniFi G5-Flex PTZ Command Server for Blue Iris
Translates Blue Iris PTZ commands to G5-Flex digital PTZ API calls
"""

import requests
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import socket

# Auto-detect local IP
def get_local_ip():
    local_ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        local_ip.connect(('8.8.8.8', 80))
        server_ip = local_ip.getsockname()[0]
        local_ip.close()
        return server_ip
    except Exception:
        return '127.0.0.1'

server_ip = get_local_ip()

class G5FlexPTZController:
    def __init__(self, camera_ip, username="ubnt", password="ubnt"):
        self.camera_ip = camera_ip
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.login_url = f"http://{camera_ip}/api/1.1/login"
        self.settings_url = f"http://{camera_ip}/api/1.1/settings"
        self.last_login = 0
        self.login_interval = 3600  # Re-login every hour
        self.lock = threading.Lock()
        
        # PTZ state tracking
        self.current_x = 50  # Center X (0-100)
        self.current_y = 50  # Center Y (0-100)
        self.current_zoom = 0  # Zoom scale (0-100)
        
        # PTZ movement increments
        self.pan_step = 5    # How much to move per pan command
        self.tilt_step = 5   # How much to move per tilt command
        self.zoom_step = 10  # How much to zoom per zoom command
        
    def login(self):
        """Login to camera and establish session"""
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
                self._update_current_position()
                return True
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Login failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Login error: {e}")
            return False
    
    def _ensure_authenticated(self):
        """Ensure we have a valid session"""
        with self.lock:
            if time.time() - self.last_login > self.login_interval:
                return self.login()
            return True
    
    def _update_current_position(self):
        """Get current PTZ position from camera"""
        try:
            response = self.session.get(self.settings_url, timeout=10)
            if response.status_code == 200:
                settings = response.json()
                ptz_settings = settings.get('isp', {})
                self.current_x = ptz_settings.get('dZoomCenterX', 50)
                self.current_y = ptz_settings.get('dZoomCenterY', 50)
                self.current_zoom = ptz_settings.get('dZoomScale', 0)
                return True
        except Exception as e:
            print(f"Error getting current position: {e}")
        return False
    
    def _set_ptz_position(self, x=None, y=None, zoom=None):
        """Send PTZ position to camera"""
        try:
            if not self._ensure_authenticated():
                return False
            
            # Use current values if not specified
            target_x = x if x is not None else self.current_x
            target_y = y if y is not None else self.current_y
            target_zoom = zoom if zoom is not None else self.current_zoom
            
            # Clamp values to valid ranges
            target_x = max(0, min(100, target_x))
            target_y = max(0, min(100, target_y))
            target_zoom = max(0, min(100, target_zoom))
            
            settings_data = {
                "isp": {
                    "dZoomCenterX": target_x,
                    "dZoomCenterY": target_y,
                    "dZoomScale": target_zoom,
                    "dZoomStreamId": 4
                }
            }
            
            response = self.session.put(
                self.settings_url,
                data=json.dumps(settings_data),
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                # Update our tracking
                self.current_x = target_x
                self.current_y = target_y
                self.current_zoom = target_zoom
                print(f"[{time.strftime('%H:%M:%S')}] PTZ: X={target_x}, Y={target_y}, Zoom={target_zoom}")
                return True
            else:
                print(f"PTZ command failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"Error setting PTZ position: {e}")
            return False
    
    # PTZ Command Methods
    def pan_left(self, steps=None, degrees=None):
        """Pan left (decrease X)"""
        movement = self._calculate_movement(steps, degrees, self.pan_step)
        new_x = self.current_x - movement
        return self._set_ptz_position(x=new_x)
    
    def pan_right(self, steps=None, degrees=None):
        """Pan right (increase X)"""
        movement = self._calculate_movement(steps, degrees, self.pan_step)
        new_x = self.current_x + movement
        return self._set_ptz_position(x=new_x)
    
    def tilt_up(self, steps=None, degrees=None):
        """Tilt up (decrease Y)"""
        movement = self._calculate_movement(steps, degrees, self.tilt_step)
        new_y = self.current_y - movement
        return self._set_ptz_position(y=new_y)
    
    def tilt_down(self, steps=None, degrees=None):
        """Tilt down (increase Y)"""
        movement = self._calculate_movement(steps, degrees, self.tilt_step)
        new_y = self.current_y + movement
        return self._set_ptz_position(y=new_y)
    
    def zoom_in(self, steps=None):
        """Zoom in (increase scale)"""
        movement = steps if steps else self.zoom_step
        new_zoom = self.current_zoom + movement
        return self._set_ptz_position(zoom=new_zoom)
    
    def zoom_out(self, steps=None):
        """Zoom out (decrease scale)"""
        movement = steps if steps else self.zoom_step
        new_zoom = self.current_zoom - movement
        return self._set_ptz_position(zoom=new_zoom)
    
    def set_absolute_position(self, x, y, zoom):
        """Set absolute PTZ position"""
        return self._set_ptz_position(x=x, y=y, zoom=zoom)
    
    def _calculate_movement(self, steps, degrees, default_step):
        """Calculate movement amount from steps, degrees, or default"""
        if steps is not None and steps > 0:
            return steps
        elif degrees is not None and degrees > 0:
            # Convert degrees to our 0-100 scale
            # Assuming 360 degrees = 100 units, so 1 degree ≈ 0.28 units
            return int(degrees * 100 / 360)
        else:
            return default_step
    
    def go_preset(self, preset_num):
        """Go to preset position"""
        presets = {
            1: (25, 25, 0),   # Top-left
            2: (50, 25, 0),   # Top-center  
            3: (75, 25, 0),   # Top-right
            4: (25, 50, 0),   # Center-left
            5: (50, 50, 0),   # Center
            6: (75, 50, 0),   # Center-right
            7: (25, 75, 0),   # Bottom-left
            8: (50, 75, 0),   # Bottom-center
            9: (75, 75, 0),   # Bottom-right
        }
        
        if preset_num in presets:
            x, y, zoom = presets[preset_num]
            return self._set_ptz_position(x=x, y=y, zoom=zoom)
        return False
    
    def stop(self):
        """Stop PTZ movement (no-op for digital PTZ)"""
        return True

class PTZHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, ptz_controller=None, **kwargs):
        self.ptz_controller = ptz_controller
        super().__init__(*args, **kwargs)
    
    def log_message(self, format, *args):
        """Reduce verbose logging"""
        pass
    
    def do_GET(self):
        """Handle PTZ command requests"""
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        
        if path == "/ptz":
            self.handle_ptz_command(query)
        elif path == "/status":
            self.serve_status()
        elif path == "/":
            self.serve_index()
        else:
            self.send_error(404)
    
    def handle_ptz_command(self, query):
        """Process PTZ commands from Blue Iris"""
        try:
            # Blue Iris sends: /ptz?cmd=left&steps=5 or /ptz?cmd=right&degrees=10
            cmd = query.get('cmd', [''])[0].lower()
            
            # Extract movement parameters
            steps = int(query.get('steps', ['0'])[0]) if query.get('steps') else None
            degrees = int(query.get('degrees', ['0'])[0]) if query.get('degrees') else None
            speed = int(query.get('speed', ['50'])[0]) if query.get('speed') else 50
            
            success = False
            
            if cmd == 'left':
                success = self.ptz_controller.pan_left(steps=steps, degrees=degrees)
            elif cmd == 'right':
                success = self.ptz_controller.pan_right(steps=steps, degrees=degrees)
            elif cmd == 'up':
                success = self.ptz_controller.tilt_up(steps=steps, degrees=degrees)
            elif cmd == 'down':
                success = self.ptz_controller.tilt_down(steps=steps, degrees=degrees)
            elif cmd == 'zoomin':
                success = self.ptz_controller.zoom_in(steps=steps)
            elif cmd == 'zoomout':
                success = self.ptz_controller.zoom_out(steps=steps)
            elif cmd == 'preset':
                preset_num = int(query.get('preset', ['1'])[0])
                success = self.ptz_controller.go_preset(preset_num)
            elif cmd == 'stop':
                success = self.ptz_controller.stop()
            elif cmd == 'absolute':
                # Absolute positioning: /ptz?cmd=absolute&x=75&y=25&zoom=10
                x = int(query.get('x', [self.ptz_controller.current_x])[0])
                y = int(query.get('y', [self.ptz_controller.current_y])[0])
                zoom = int(query.get('zoom', [self.ptz_controller.current_zoom])[0])
                success = self.ptz_controller.set_absolute_position(x, y, zoom)
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Unknown PTZ command')
                return
            
            if success:
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'PTZ command failed')
                
        except Exception as e:
            print(f"PTZ command error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'Error: {e}'.encode())
    
    def serve_status(self):
        """Serve current PTZ status"""
        status = {
            "current_x": self.ptz_controller.current_x,
            "current_y": self.ptz_controller.current_y,
            "current_zoom": self.ptz_controller.current_zoom,
            "camera_ip": self.ptz_controller.camera_ip
        }
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(status, indent=2).encode())
    
    def serve_index(self):
        """Serve control page"""
        html = f"""
        <html>
        <head><title>G5-Flex PTZ Control</title></head>
        <body>
            <h1>UniFi G5-Flex PTZ Control Server</h1>
            <p><strong>Camera:</strong> {self.ptz_controller.camera_ip}</p>
            <p><strong>Current Position:</strong> X={self.ptz_controller.current_x}, Y={self.ptz_controller.current_y}, Zoom={self.ptz_controller.current_zoom}</p>
            
            <h2>For Blue Iris PTZ Setup:</h2>
            <ul>
                <li><strong>PTZ Type:</strong> Generic HTTP</li>
                <li><strong>PTZ Address:</strong> http://{server_ip}:8081/ptz</li>
                <li><strong>Commands:</strong></li>
                <ul>
                    <li>Left: ?cmd=left</li>
                    <li>Right: ?cmd=right</li>
                    <li>Up: ?cmd=up</li>
                    <li>Down: ?cmd=down</li>
                    <li>Zoom In: ?cmd=zoomin</li>
                    <li>Zoom Out: ?cmd=zoomout</li>
                    <li>Preset: ?cmd=preset&preset=1</li>
                    <li>Stop: ?cmd=stop</li>
                </ul>
            </ul>
            
            <h2>Test Commands:</h2>
            <p>
                <a href="/ptz?cmd=left">← Left</a> | 
                <a href="/ptz?cmd=right">Right →</a> | 
                <a href="/ptz?cmd=up">↑ Up</a> | 
                <a href="/ptz?cmd=down">↓ Down</a>
            </p>
            <p>
                <a href="/ptz?cmd=zoomin">🔍 Zoom In</a> | 
                <a href="/ptz?cmd=zoomout">🔎 Zoom Out</a> | 
                <a href="/ptz?cmd=preset&preset=5">🎯 Center</a>
            </p>
            
            <p><a href="/status">📊 Status JSON</a></p>
        </body>
        </html>
        """
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

def create_handler(ptz_controller):
    """Create handler with PTZ controller dependency injection"""
    def handler(*args, **kwargs):
        return PTZHandler(*args, ptz_controller=ptz_controller, **kwargs)
    return handler

def main():
    # Configuration
    CAMERA_IP = "192.168.10.104"
    PTZ_PORT = 8081
    
    print("=" * 60)
    print("UniFi G5-Flex PTZ Command Server")
    print("=" * 60)
    print(f"Camera IP: {CAMERA_IP}")
    print(f"PTZ Server Port: {PTZ_PORT}")
    print()
    
    # Initialize PTZ controller
    ptz_controller = G5FlexPTZController(CAMERA_IP)
    
    # Test initial login
    if not ptz_controller.login():
        print("ERROR: Could not login to camera!")
        return
    
    print("✓ Camera login successful")
    print(f"✓ Initial position: X={ptz_controller.current_x}, Y={ptz_controller.current_y}, Zoom={ptz_controller.current_zoom}")
    print()
    print("Starting PTZ command server...")
    print()
    print("Blue Iris PTZ Configuration:")
    print(f"  PTZ Type: Generic HTTP")
    print(f"  PTZ Address: http://{server_ip}:{PTZ_PORT}/ptz")
    print(f"  Commands: ?cmd=left|right|up|down|zoomin|zoomout|preset&preset=N")
    print()
    print("Test URLs:")
    print(f"  Control Page: http://{server_ip}:{PTZ_PORT}/")
    print(f"  Status JSON:  http://{server_ip}:{PTZ_PORT}/status")
    print(f"  Test Pan:     http://{server_ip}:{PTZ_PORT}/ptz?cmd=right")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)
    
    try:
        # Create and start HTTP server
        handler = create_handler(ptz_controller)
        server = HTTPServer(('0.0.0.0', PTZ_PORT), handler)
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\nShutting down PTZ server...")
        server.shutdown()
        print("Goodbye!")

if __name__ == "__main__":
    main()