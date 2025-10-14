# Test with working reconnection options for FFmpeg 5.1.6
import time
import os
from pathlib import Path
import subprocess
import shutil

print("=== TESTING WITH RECONNECTION OPTIONS ===")
try:
    subprocess.run(['pkill', '-f', 'ffmpeg.*rtsp://'], capture_output=True)
    print("Killed existing FFmpeg processes")
except:
    pass

test_dir = Path('test_stream_reconnect')
if test_dir.exists():
    shutil.rmtree(test_dir)

stream_dir = test_dir / serial
stream_dir.mkdir(parents=True, exist_ok=True)

# FFmpeg command with available reconnection options
ffmpeg_cmd = [
    'ffmpeg',
    '-rtsp_transport', 'tcp',
    '-timeout', '30000000',  # 30 second timeout (available)
    '-reconnect', '1',       # Available
    '-reconnect_at_eof', '1', # Available
    '-reconnect_streamed', '1', # Available
    '-i', rtsp_url,
    '-c:v', 'copy',
    '-c:a', 'copy',
    '-f', 'hls',
    '-hls_time', '2',
    '-hls_list_size', '6',
    '-hls_flags', 'delete_segments+append_list',
    '-hls_segment_filename', str(stream_dir / 'segment_%03d.ts'),
    str(stream_dir / 'playlist.m3u8')
]

print(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")
print("Starting FFmpeg with reconnection support...")

process = subprocess.Popen(ffmpeg_cmd, 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         universal_newlines=True)

print(f"FFmpeg PID: {process.pid}")

# Monitor for 120 seconds
print("\n=== MONITORING WITH RECONNECTION SUPPORT ===")
last_segment_count = 0
stall_start_time = None

for i in range(120):
    time.sleep(1)
    
    if process.poll() is not None:
        print(f"Process died! Return code: {process.returncode}")
        stdout, stderr = process.communicate()
        print(f"STDERR: {stderr[-600:]}")
        break
    
    if stream_dir.exists():
        segments = list(stream_dir.glob('*.ts'))
        segment_count = len(segments)
        
        if segment_count != last_segment_count:
            if stall_start_time:
                stall_duration = i - stall_start_time
                print(f"[{i:2d}s] RESUMED after {stall_duration}s stall! New segments: {segment_count}")
                stall_start_time = None
            else:
                print(f"[{i:2d}s] New segments: {segment_count} total")
            
            if segments:
                latest = max(segments, key=os.path.getctime)
                age = time.time() - os.path.getctime(latest)
                print(f"      Latest: {latest.name} ({age:.1f}s ago)")
            last_segment_count = segment_count
        else:
            # Track stalls
            if stall_start_time is None and i > 10:
                stall_start_time = i
            elif stall_start_time and (i - stall_start_time) % 20 == 0:
                print(f"[{i:2d}s] Still stalled at {segment_count} segments for {i - stall_start_time}s")

# Cleanup
if process.poll() is None:
    print("Terminating process...")
    process.terminate()
    time.sleep(2)
    if process.poll() is None:
        process.kill()

print("Test completed.")