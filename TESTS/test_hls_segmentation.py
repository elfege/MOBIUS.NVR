# Let's specifically test an Eufy camera

# set a monitoring duration - in seconds
SECONDS = 300
CAMERA_TO_TEST_FROM_NAME="Entryway"

# First, let's check which cameras have which capabilities
import sys
sys.path.append('/home/elfege/0_NVR')
from device_manager import DeviceManager

dm = DeviceManager()

# Check what cameras we have and their capabilities
all_devices = dm.get_all_devices()
print("=== ALL CAMERAS ===")
for serial, info in all_devices.items():
    print(f"{serial}: {info['name']} - Type: {info['type']} - Capabilities: {info.get('capabilities', [])}")


print("=== FINDING EUFY CAMERAS ===")
streaming_cameras = dm.get_streaming_cameras()
eufy_cameras = [(s, i) for s, i in streaming_cameras.items() if i.get('type') == 'eufy']

if not eufy_cameras:
    print("❌ No Eufy cameras found")
else:
    print("Available Eufy cameras:")
    for serial, info in eufy_cameras:
        print(f"  {serial}: {info['name']}")
        if info.get('rtsp'):
            print(f"    RTSP: {info['rtsp']['url']}")
    
    
    serial, info = next(
        (serial, info) for serial, info in eufy_cameras if info.get("name") == CAMERA_TO_TEST_FROM_NAME
    )

    
    print(f"\n=== TESTING EUFY CAMERA: {info['name']} ===")
    
    if not info.get('rtsp'):
        print("❌ No RTSP info found for this camera")
    else:
        rtsp_url = info['rtsp']['url']
        print(f"RTSP URL: {rtsp_url}")
        
        # Continue with the isolated FFmpeg test
        import time
        import os
        from pathlib import Path
        import subprocess
        import shutil
        
        # Clean slate
        print("\n=== COMPLETE CLEANUP ===")
        try:
            subprocess.run(['pkill', '-f', 'ffmpeg.*rtsp://'], capture_output=True)
            print("Killed any remaining FFmpeg processes")
        except:
            pass
        
        # Remove test directory
        test_dir = Path('test_stream')
        if test_dir.exists():
            shutil.rmtree(test_dir)
        print("Cleaned test directories")
        
        # Create stream directory
        stream_dir = Path('test_stream') / serial
        stream_dir.mkdir(parents=True, exist_ok=True)
        
        # Test FFmpeg command (same as stream_manager uses)
        ffmpeg_cmd = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-c:v', 'libx264',
            '-c:a', 'aac', 
            '-preset', 'ultrafast',
            '-g', '30',
            '-sc_threshold', '0',
            '-f', 'hls',
            '-hls_time', '2',
            '-hls_list_size', '100',
            '-hls_flags', 'delete_segments+append_list',
            '-hls_segment_filename', str(stream_dir / 'segment_%03d.ts'),
            str(stream_dir / 'playlist.m3u8')
        ]
        
        print(f"\nFFmpeg command: {' '.join(ffmpeg_cmd)}")
        print("Starting FFmpeg process...")
        
        # Start FFmpeg process
        process = subprocess.Popen(ffmpeg_cmd, 
                                 stdout=subprocess.DEVNULL, 
                                 stderr=subprocess.DEVNULL,
                                 universal_newlines=True)
        
        print(f"FFmpeg PID: {process.pid}")
        
        # Monitor for 30 seconds
        print("\n=== MONITORING SEGMENT CREATION ===")
        start_time = time.time()
        last_segment_count = 0
        
        for i in range(SECONDS):  # Monitor for N seconds
            time.sleep(1)
            
            # Check process status
            if process.poll() is not None:
                print(f"❌ FFmpeg process died! Return code: {process.returncode}")
                stdout, stderr = process.communicate()
                print(f"STDOUT: {stdout[-1000:]}")  # Last 1000 chars
                print(f"STDERR: {stderr[-1000:]}")  # Last 1000 chars
                break
            
            # Count segments
            if stream_dir.exists():
                segments = list(stream_dir.glob('*.ts'))
                segment_count = len(segments)
                
                if segment_count != last_segment_count:
                    print(f"[{i:2d}s] New segments: {segment_count} total")
                    if segments:
                        latest = max(segments, key=os.path.getmtime)
                        age = time.time() - os.path.getmtime(latest)
                        print(f"      Latest: {latest.name} ({age:.1f}s ago)")
                    last_segment_count = segment_count
                elif i % 5 == 0:  # Status update every 5 seconds
                    print(f"[{i:2d}s] Still {segment_count} segments")
        
        # Final cleanup
        if process.poll() is None:
            print("Terminating FFmpeg process...")
            process.terminate()
            time.sleep(2)
            if process.poll() is None:
                process.kill()