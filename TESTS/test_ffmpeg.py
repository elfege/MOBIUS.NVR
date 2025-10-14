# While /home/elfege/0_NVR/TESTS/test_hls_segmentation.py is running (if it is), check the process state


import subprocess
import time

# Check if FFmpeg is still running and what it's doing
result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
ffmpeg_lines = [line for line in result.stdout.split('\n') if 'ffmpeg' in line and 'rtsp://' in line]

if ffmpeg_lines:
    print("FFmpeg process still running:")
    for line in ffmpeg_lines:
        print(f"  {line}")
    
    # Check CPU usage - if 0%, it's stuck waiting
    print("\nChecking if process is consuming CPU...")
    time.sleep(5)
    result2 = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    ffmpeg_lines2 = [line for line in result2.stdout.split('\n') if 'ffmpeg' in line and 'rtsp://' in line]
    for line in ffmpeg_lines2:
        print(f"  {line}")