playlist_path=/tmp/test_eufy/playlist.m3u8

set -a
get_cameras_credentials >/dev/null
set +a 

mkdir -p "$(dirname "$playlist_path")"
cmd=(
    'ffmpeg'
    '-i' "rtsp://${EUFY_CAMERA_T8441P122428038A_USERNAME}:${EUFY_CAMERA_T8441P122428038A_PASSWORD}@192.168.10.183/live0"
    '-rtsp_transport' 'tcp'
    '-c:v' 'libx264'
    '-preset' 'ultrafast' 
    '-tune' 'zerolatency'   
    '-c:a' 'aac'   
    '-f' 'hls' 
    '-hls_time' 2 
    '-hls_list_size' 10   
    '-hls_flags' 'delete_segments+split_by_time'   
    '-hls_segment_filename' /tmp/test_eufy/segment_%03d.ts
	'-y'   
	"$playlist_path"
)

eval "${cmd[@]}"