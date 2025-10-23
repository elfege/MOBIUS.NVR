#!/bin/bash 

# test_SIMPLE_HLS.sh
# Test script to generate a simple HLS stream from a camera using FFmpeg

clear

cd ~/0_NVR

# Create test directory
STREAM_DIR="$HOME/0_NVR/streams/TEST_HLS"
mkdir -p "${STREAM_DIR}"

set -a
get_cameras_credentials >/dev/null
set +a 

CAMERA_IP=192.168.10.89
CAMERA_PASSWORD=${REOLINK_PASSWORD//#/%23}
CAMERA_USERNAME=${REOLINK_USERNAME}

# CAMERA_USERNAME=api_user
# CAMERA_PASSWORD=test_test154

echo "sending ffmpeg command to generate HLS stream... $(date)"

# Run FFmpeg with full output
ffmpeg \
  -rtsp_transport udp \
  -timeout 5000000 \
  -analyzeduration 1000000 \
  -probesize 1000000 \
  -use_wallclock_as_timestamps 1 \
  -fflags nobuffer \
  -i "rtsp://${CAMERA_USERNAME}:${CAMERA_PASSWORD}@${CAMERA_IP}:554/h264Preview_01_sub" \
  -c:v libx264 \
  -map 0:v:0 \
  -profile:v baseline \
  -pix_fmt yuv420p \
  -r 15 \
  -vf scale=640:480 \
  -tune zerolatency \
  -g 7 \
  -keyint_min 7 \
  -preset veryfast \
  -f hls \
  -hls_flags independent_segments+program_date_time+append_list+delete_segments+split_by_time \
  -hls_time 0.5 \
  -hls_list_size 1 \
  -hls_segment_type fmp4 \
  -hls_fmp4_init_filename init.mp4 \
  -movflags +frag_keyframe+empty_moov+default_base_moof \
  -hls_delete_threshold 1 \
  -hls_segment_filename "${STREAM_DIR}segment_%03d.m4s" \
  -y "${STREAM_DIR}/playlist.m3u8"