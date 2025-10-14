#!/bin/bash 

# test_LL_HLS.sh 

cd ~/0_NVR

# Create test directory
mkdir -p streams/TEST_LL_HLS

# Run FFmpeg with full output
ffmpeg \
  -rtsp_transport udp \
  -timeout 5000000 \
  -analyzeduration 1000000 \
  -probesize 1000000 \
  -use_wallclock_as_timestamps 1 \
  -fflags +genpts \
  -i "rtsp://admin:TarTo56))%23FatouiiDRtu@192.168.10.88:554/h264Preview_01_sub" \
  -c:v libx264 \
  -map 0:v:0 \
  -map 0:a? \
  -profile:v baseline \
  -pix_fmt yuv420p \
  -r 15 \
  -vf scale=640:480 \
  -tune zerolatency \
  -g 15 \
  -keyint_min 15 \
  -preset veryfast \
  -f hls \
  -hls_time 1 \
  -hls_list_size 3 \
  -hls_flags independent_segments+program_date_time+delete_segments \
  -hls_segment_type fmp4 \
  -hls_fmp4_init_filename init.mp4 \
  -movflags +frag_keyframe+empty_moov+default_base_moof \
  -hls_delete_threshold 2 \
  -hls_segment_filename streams/TEST_LL_HLS/segment_%03d.m4s \
  -y streams/TEST_LL_HLS/playlist.m3u8