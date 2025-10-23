#!/bin/bash

# test_HLS_MULTIPLE_CONFIG.sh	
# Comprehensive RTSP connection tester with multiple fallback strategies

clear
cd ~/0_NVR

. ~/.env.colors

# Setup
STREAM_DIR="$HOME/0_NVR/streams/TEST_HLS_COMPREHENSIVE"
mkdir -p "${STREAM_DIR}"
set -a
get_cameras_credentials >/dev/null
set +a

CAMERA_IP=192.168.10.89
CAMERA_PASSWORD=${REOLINK_PASSWORD//#/%23}
CAMERA_USERNAME=${REOLINK_USERNAME}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Comprehensive RTSP Connection Tester${NC}"
echo -e "${BLUE}Camera: ${CAMERA_IP}${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Test counter
TEST_NUM=0
SUCCESS=0

TIMEOUT=60

# Function to run a test
run_test() {
	local test_name="$1"
	shift
	local args=("$@")

	((TEST_NUM++))
	echo -e "${YELLOW}========================================${NC}"
	echo -e "${YELLOW}Test ${TEST_NUM}: ${test_name}${NC}"
	echo -e "${YELLOW}========================================${NC}"
	
	local log_file="/tmp/ffmpeg_test_${TEST_NUM}.log"
	
	timeout $TIMEOUT ffmpeg "${args[@]}" \
		-y "${STREAM_DIR}"/playlist.m3u8 \
		> "$log_file" 2>&1 &
	
	local ffmpeg_pid=$!
	
	# Wait for segments to START being created
	local elapsed=0
	local segment_found=0
	
	while [ $elapsed -lt 30 ]; do  # 30 second warmup
		sleep 1
		((elapsed++))
		
		if ! kill -0 $ffmpeg_pid 2>/dev/null; then
			echo -e "${RED}FFmpeg crashed after ${elapsed}s${NC}"
			break
		fi
		
		# Check if ANY segment exists (even just one)
		if ls "${STREAM_DIR}"/*.m4s 1> /dev/null 2>&1; then
			segment_found=1
			break
		fi
	done
	
	if [ $segment_found -eq 1 ]; then
		echo -e "${GREEN}✓ SUCCESS! Segments detected after ${elapsed}s${NC}"
		sleep 5  # Let it run for 5 more seconds
		kill $ffmpeg_pid 2>/dev/null
		wait $ffmpeg_pid 2>/dev/null
		SUCCESS=1
		return 0
	fi
	
	kill $ffmpeg_pid 2>/dev/null
	wait $ffmpeg_pid 2>/dev/null
	
	echo -e "${RED}✗ FAILED - No segments after ${elapsed}s${NC}"
	echo -e "${YELLOW}FFmpeg output:${NC}"
	cat "$log_file"
	echo ""
	return 1
}

clean_test_dir() {
	rm -f "${STREAM_DIR}"/*
}

# =============================================================================
# TEST 0: Known working configuration (from test_LL_HLS.sh)
# =============================================================================
clean_test_dir
run_test "Known working config (UDP + standard params)" \
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
	-hls_segment_filename "${STREAM_DIR}/segment_%03d.m4s" \
	
[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 1: Force video size (skip metadata requirement)
# =============================================================================
clean_test_dir
run_test "Force video size 640x480" \
	-rtsp_transport tcp \
	-timeout 5000000 \
	-analyzeduration 10000000 \
	-probesize 10000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-video_size 640x480 \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 1: Standard method (current working config)
# =============================================================================
clean_test_dir
run_test "Standard UDP transport" \
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
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 3: TCP transport (more reliable, higher latency)
# =============================================================================
clean_test_dir
run_test "TCP transport instead of UDP" \
	-rtsp_transport tcp \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 4: Increase analyzeduration/probesize (more time to find metadata)
# =============================================================================
clean_test_dir
run_test "Increased probe time (10x)" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 10000000 \
	-probesize 10000000 \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 5: Try main stream instead of sub
# =============================================================================
clean_test_dir
run_test "Main stream (h264Preview_01_main)" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-i "rtsp://${CAMERA_USERNAME}:${CAMERA_PASSWORD}@${CAMERA_IP}:554/h264Preview_01_main" \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 6: Remove nobuffer flag (allow buffering for metadata)
# =============================================================================
clean_test_dir
run_test "Allow buffering" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 7: Force pixel format and framerate
# =============================================================================
clean_test_dir
run_test "Force pix_fmt yuv420p + framerate" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-pixel_format yuv420p \
	-framerate 15 \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 8: Ignore errors and continue (err_detect aggressive)
# =============================================================================
clean_test_dir
run_test "Ignore stream errors" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-err_detect ignore_err \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 9: Copy codec (no transcoding - fastest, but picky)
# =============================================================================
clean_test_dir
run_test "Copy codec (no transcode)" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-i "rtsp://${CAMERA_USERNAME}:${CAMERA_PASSWORD}@${CAMERA_IP}:554/h264Preview_01_sub" \
	-c:v copy \
	-map 0:v:0 \
	-f hls \
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

# =============================================================================
# TEST 10: Alternative RTSP URL format (some cameras use different paths)
# =============================================================================
clean_test_dir
run_test "Alternative URL format (/Preview_01_sub)" \
	-rtsp_transport udp \
	-timeout 5000000 \
	-analyzeduration 1000000 \
	-probesize 1000000 \
	-use_wallclock_as_timestamps 1 \
	-fflags nobuffer \
	-i "rtsp://${CAMERA_USERNAME}:${CAMERA_PASSWORD}@${CAMERA_IP}:554/Preview_01_sub" \
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
	-hls_time 0.5 \
	-hls_list_size 1 \
	-hls_segment_type fmp4 \
	-hls_fmp4_init_filename init.mp4 \
	-movflags +frag_keyframe+empty_moov+default_base_moof \
	-hls_delete_threshold 1 \
	-hls_segment_filename "${STREAM_DIR}"/segment_%03d.m4s

[ $SUCCESS -eq 1 ] && exit 0

echo -e "${RED}========================================${NC}"
echo -e "${RED}All tests failed!${NC}"
echo -e "${RED}Camera may need physical maintenance${NC}"
echo -e "${RED}========================================${NC}"
