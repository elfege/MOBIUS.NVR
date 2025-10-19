#!/bin/bash 

# test_LL_HLS.sh 

cd ~/0_NVR

# Create test directory
mkdir -p streams/TEST_LL_HLS

# -hls_flags independent_segments+program_date_time+delete_segments \
# independent_segments+program_date_time+append_list+delete_segments+split_by_time
#   -hls_flags independent_segments+program_date_time+append_list+delete_segments+split_by_time+part_inf \
#   -map 0:a? \

set -a
get_cameras_credentials >/dev/null
set +a 

REOLINK_PASSWORD=${REOLINK_PASSWORD//#/%23}
echo "REOLINK_USERNAME: ${REOLINK_USERNAME}
echo "REOLINK_PASSWORD: ${REOLINK_PASSWORD}

# Run FFmpeg with full output
ffmpeg \
  -rtsp_transport udp \
  -timeout 5000000 \
  -analyzeduration 1000000 \
  -probesize 1000000 \
  -use_wallclock_as_timestamps 1 \
  -fflags nobuffer \
  -i "rtsp://${REOLINK_USERNAME}:${REOLINK_PASSWORD}@192.168.10.88:554/h264Preview_01_sub" \
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
  -hls_segment_filename streams/TEST_LL_HLS/segment_%03d.m4s \
  -y streams/TEST_LL_HLS/playlist.m3u8