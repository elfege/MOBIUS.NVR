#!/usr/bin/env python3
"""
Centralized FFmpeg parameter generation for HLS streaming
"""

import os
import sys
import logging
import traceback
from pprint import pprint
from typing import Dict, List, Optional
from config.ffmpeg_names_map import ffmpeg_names_map


logger = logging.getLogger(__name__)


class FFmpegHLSParamBuilder:
    """
    Builds FFmpeg HLS output parameters with four-tier configuration priority.
    Per-camera settings (camera_rtsp_config from cameras.json)
    """

    def __init__(
        self,
        camera_name: str = 'unknown',
        stream_type: str = 'sub',
        camera_rtsp_config: Optional[Dict] = None,
        vendor_prefix: str = '',
    ):
        self.stream_type = stream_type
        self.camera_rtsp_config = camera_rtsp_config or {}
        self.vendor_prefix = vendor_prefix
        self.camera_name = camera_name


    def build_rtsp_params(self) -> List[str]:
        """
        Build a flat argv list for ffmpeg based on camera_rtsp_config.
        - Translates project-only keys via ffmpeg_names_map (e.g. frame_rate_full_screen → r)
        - Applies conditional logic: only one fps/resolution per stream_type (sub vs main)
        - Maps 'resolution_*' entries to -vf scale=WxH when map value is 'scale'
        - Maps 'c:v' project tokens ('transcode','copy','smart') to valid encoders
        - Skips 'N/A' and empty values
        - Returns a clean argv-style list, e.g. ['-c:v','libx264','-r','18','-vf','scale=1280:720',...]
        """
        try:
            params: List[str] = []
            cfg = dict(self.camera_rtsp_config or {})

            # typo correction
            if "hsl_delete_threshold" in cfg and "hls_delete_threshold" not in cfg:
                cfg["hls_delete_threshold"] = cfg.pop("hsl_delete_threshold")

            # --- handle codec field explicitly
            if "c:v" in cfg:
                val = cfg.pop("c:v")
                if isinstance(val, str):
                    t = val.lower()
                    if t in ("transcode", "smart"):
                        params.extend(["-c:v", "libx264"])
                    elif t == "copy":
                        params.extend(["-c:v", "copy"])
                    else:
                        params.extend(["-c:v", t])

            # --- iterate other params
            for key, value in cfg.items():
                if not value or value == "N/A":
                    continue

                # conditional filtering for sub/main variants
                # =====================================================
                # 1. Frame rate: only include one based on stream_type
                if key in ("frame_rate_full_screen", "frame_rate_grid_mode"):
                    if self.stream_type == "sub" and key != "frame_rate_grid_mode":
                        continue
                    if self.stream_type != "sub" and key != "frame_rate_full_screen":
                        continue

                # 2. Resolution: only include one based on stream_type
                if key in ("resolution_main", "resolution_sub"):
                    if self.stream_type == "sub" and key != "resolution_sub":
                        continue
                    if self.stream_type != "sub" and key != "resolution_main":
                        continue
                # =====================================================

                # translate key if mapped
                mapped = ffmpeg_names_map.get(key)
                flag = f"-{mapped}" if mapped else f"-{key}"

                # list of values
                if isinstance(value, list):
                    for entry in value:
                        if not entry or entry == "N/A":
                            continue
                        # special case: scale mapping
                        if mapped == "scale" and isinstance(entry, str) and "x" in entry:
                            w, h = entry.split("x", 1)
                            params.extend(["-vf", f"scale={w}:{h}"])
                        else:
                            params.extend([flag, str(entry)])
                    continue

                # single value
                if mapped == "scale" and isinstance(value, str) and "x" in value:
                    w, h = value.split("x", 1)
                    params.extend(["-vf", f"scale={w}:{h}"])
                    continue

                params.extend([flag, str(value)])

            return params

        except Exception as e:
            print(traceback.print_exc())
            print(f"build_rtsp_params failed: {e}")
            raise

    def build_ll_hls_publish_output(self, ll_hls_cfg: dict) -> List[str]:
        """
            Build ONLY the ffmpeg OUTPUT args to publish a low-latency, HLS-friendly stream
            to MediaMTX. Input flags (`-rtsp_transport ... -i <rtsp_url>`) are NOT included.

            ll_hls_cfg schema (per cameras.json):
            {
                "publisher": { "protocol": "rtmp|rtsp", "host": "...", "port": 1935|554, "path": "CAM_ID" },
                "video": {
                "c:v": "libx264", "preset": "veryfast", "tune": "zerolatency",
                "profile:v": "baseline", "pix_fmt": "yuv420p",
                "r": 30, "g": 15, "keyint_min": 15,
                "b:v": "800k", "maxrate": "800k", "bufsize": "1600k",
                "x264-params": "scenecut=0:min-keyint=15:open_gop=0",
                "force_key_frames": "expr:gte(t,n_forced*1)",
                "vf": "scale=640:480"          # optional, OR use width/height below
                "width": 640, "height": 480    # optional -> auto-build vf if no explicit "vf"
                },
                "audio": { "enabled": false|true, "c:a": "aac", "b:a":"64k", "ar":44100, "ac":1 }
            }
        """
        if not ll_hls_cfg:
            raise ValueError("LL-HLS: missing ll_hls configuration")

        pub = (ll_hls_cfg.get("publisher") or {}).copy()
        vid = (ll_hls_cfg.get("video") or {}).copy()
        aud = (ll_hls_cfg.get("audio") or {}).copy()

        # ----- publisher sink (protocol, host, port, path) -----
        protocol = str(pub.get("protocol", "rtmp")).lower()
        host     = pub.get("host", "nvr-packager")
        port     = int(pub.get("port", 1935 if protocol == "rtmp" else 554))
        path     = pub.get("path")
        if not host or not path:
            raise ValueError("LL-HLS: publisher.host and publisher.path are required")

        # ----- video flags (use exact ffmpeg key names when present) -----
        # If width/height provided and "vf" not explicitly set, synthesize a scale filter.
        if "vf" not in vid and ("width" in vid and "height" in vid):
            try:
                w, h = int(vid["width"]), int(vid["height"])
                vid["vf"] = f"scale={w}:{h}"
            except Exception:
                pass  # ignore bad width/height

        # Order matters a bit for readability; keys must be ffmpeg-style to avoid remapping.
        video_key_order = [
            "c:v", "preset", "tune", "profile:v", "pix_fmt",
            "r", "g", "keyint_min",
            "b:v", "maxrate", "bufsize",
            "x264-params", "force_key_frames",
            "vf"
        ]
        out: List[str] = []
        for k in video_key_order:
            if k in vid and vid[k] is not None:
                out += [f"-{k}", str(vid[k])]

        # ----- audio flags (fully controlled by JSON; no hardcoding) -----
        if bool(aud.get("enabled", False)):
            # Allow either friendly or ffmpeg-style keys; map friendly if present
            c_a = aud.get("c:a", aud.get("codec", None))
            b_a = aud.get("b:a", aud.get("bitrate", None))
            ar  = aud.get("ar",  aud.get("rate", None))
            ac  = aud.get("ac",  aud.get("channels", None))
            if c_a: out += ["-c:a", str(c_a)]
            if b_a: out += ["-b:a", str(b_a)]
            if ar:  out += ["-ar",  str(ar)]
            if ac is not None: out += ["-ac", str(ac)]
        else:
            out = ["-an"] + out

        # ----- container + sink by protocol -----
        if protocol == "rtmp":
            sink = f"rtmp://{host}:{port}/{path}"
            out += ["-f", "flv", sink]
        elif protocol == "rtsp":
            # Use TCP and low mux latency for RTSP publishing
            # sink = f"rtsp://{host}:{port}/{path}"
            # out += ["-f", "rtsp", "-rtsp_transport", "tcp", "-muxpreload", "0", "-muxdelay", "0", sink]

            sink = f"rtsp://{host}:{port}/{path}"
            rtsp_transport = pub.get("rtsp_transport", "tcp")  # ← Read from config, default tcp
            out += ["-f", "rtsp", "-rtsp_transport", rtsp_transport, sink]  # ← Remove muxpreload/muxdelay
        else:
            raise ValueError(f"LL-HLS: unsupported publisher.protocol '{protocol}'")

        return out


# Convenience functions
def build_rtsp_output_params(
    stream_type: str = 'sub',
    camera_config: Optional[Dict] = None,
    vendor_prefix: str = 'Unknown',
) -> List[str]:
    try:
        camera_rtsp_config=camera_config.get('rtsp_output')
        camera_name=camera_config.get('name', 'unknown camera')
        if not camera_rtsp_config:
            raise Exception(f"Missing rtsp ouput config for {camera_name}")
            sys.exit(1)

        """Build FFmpeg HLS parameters (convenience wrapper)."""
        builder = FFmpegHLSParamBuilder(camera_name=camera_name, stream_type=stream_type, camera_rtsp_config=camera_rtsp_config, vendor_prefix=vendor_prefix)
        return builder.build_rtsp_params()
    except Exception as e:
        print(traceback.print_exc())
        raise Exception(f"something went wrong in build_rtsp_output_params: {e}")

# a bit redundant but best for clarity.
def build_rtsp_input_params(
    # input_params_keys: List,
    camera_config: Dict
) -> List[str]:
    try:
        camera_rtsp_config=camera_config.get('rtsp_input')
        camera_name=camera_config.get('name', 'unknown camera')
        if not camera_rtsp_config:
            raise Exception(f"Missing rtsp ouput config for {camera_name}")
            sys.exit(1)

        """params in json must match exact ffmpeg syntax"""
        builder = FFmpegHLSParamBuilder(camera_name=camera_name, camera_rtsp_config=camera_rtsp_config)
        return builder.build_rtsp_params()
    except Exception as e:
        print(traceback.print_exc())
        raise Exception(f"something went wrong in build_rtsp_input_params: {e}")


# --- LL-HLS convenience helpers (publish path) ---

from typing import Dict, List, Optional

def build_ll_hls_input_publish_params(
    camera_config: Dict
) -> List[str]:
    """
    Thin wrapper for clarity during troubleshooting.
    Returns INPUT flags for the *publisher* pipeline (RTSP camera source).
    Intentionally mirrors build_rtsp_input_params behavior.
    """
    try:
        # Reuse the same input policy as RTSP (redundant by design, per  request)
        return build_rtsp_input_params(camera_config=camera_config)
    except Exception as e:
        print(traceback.print_exc())
        raise Exception(f"something went wrong in build_ll_hls_input_publish_params: {e}")


def build_ll_hls_output_publish_params(
    camera_config: Dict,
    vendor_prefix: str = 'Unknown',
) -> List[str]:
    """
    Returns OUTPUT flags that publish to the packager (RTMP or RTSP),
    driven 100% by cameras.json -> camera_config['ll_hls'].

    Requires FFmpegHLSParamBuilder to implement:
        build_ll_hls_publish_output(ll_hls_cfg: dict) -> List[str]
    """
    try:
        camera_name = camera_config.get('name', 'unknown camera')
        ll = camera_config.get('ll_hls')
        if not ll:
            raise Exception(f"Missing ll_hls config for {camera_name}")

        builder = FFmpegHLSParamBuilder(
            camera_name=camera_name,
            stream_type='sub',                     # consistent with existing usage
            camera_rtsp_config={},                 # not used for this call
            vendor_prefix=vendor_prefix or camera_config.get('type', '')
        )
        return builder.build_ll_hls_publish_output(ll_hls_cfg=ll)
    except Exception as e:
        print(traceback.print_exc())
        raise Exception(f"something went wrong in build_ll_hls_output_publish_params: {e}")


def build_ll_hls_dual_output_publish_params(
    camera_config: Dict,
    vendor_prefix: str = 'Unknown',
) -> List[str]:
    """
    Returns OUTPUT flags for DUAL publishing to MediaMTX:
    - Sub stream: transcoded (320x240, low bitrate) → /camera_serial
    - Main stream: transcoded OR passthrough → /camera_serial_main

    This allows clients to receive low-res for grid view and high-res for fullscreen
    from a SINGLE FFmpeg process with ONE camera connection.

    PASSTHROUGH MODE (video_main.c:v = "copy"):
    - Main stream is copied directly without re-encoding
    - Significantly reduces latency (~2-3 seconds less)
    - Sub stream is still transcoded for grid thumbnails
    - filter_complex only scales sub stream, main uses raw input

    TRANSCODE MODE (video_main.c:v = "libx264" or other encoder):
    - Both streams are transcoded
    - Correct GOP alignment with MediaMTX 1-second segments
    - Format compatibility (fixes broken pipe issues)

    Args:
        camera_config: Full camera configuration from cameras.json
        vendor_prefix: Vendor type for logging

    Returns:
        List of FFmpeg output arguments for both streams
    """
    try:
        camera_name = camera_config.get('name', 'unknown camera')
        ll = camera_config.get('ll_hls')
        if not ll:
            raise Exception(f"Missing ll_hls config for {camera_name}")

        pub = (ll.get("publisher") or {}).copy()
        # Read from video_sub and video_main (new structure)
        # Fallback to old "video" key for backward compatibility
        vid_sub = (ll.get("video_sub") or ll.get("video") or {}).copy()
        vid_main = (ll.get("video_main") or {}).copy()
        aud = (ll.get("audio") or {}).copy()

        # ----- Publisher settings -----
        protocol = str(pub.get("protocol", "rtmp")).lower()
        host = pub.get("host", "nvr-packager")
        port = int(pub.get("port", 1935 if protocol == "rtmp" else 554))
        path = pub.get("path")
        if not host or not path:
            raise ValueError("LL-HLS: publisher.host and publisher.path are required")

        out: List[str] = []

        # Check if main stream should be passthrough (c:v = "copy")
        main_cv = vid_main.get("c:v", "")
        logger.debug(f"DEBUG {camera_name}: vid_main = {vid_main}, c:v = '{main_cv}'")
        main_is_passthrough = str(main_cv).lower() == "copy"
        logger.info(f"{camera_name}: main_is_passthrough = {main_is_passthrough}")

        # Video encoding params (excluding vf which is handled separately)
        video_key_order = [
            "c:v", "preset", "tune", "profile:v", "pix_fmt",
            "r", "g", "keyint_min",
            "b:v", "maxrate", "bufsize",
            "x264-params", "force_key_frames"
        ]

        if main_is_passthrough:
            # ========== PASSTHROUGH MODE: Sub transcoded, Main passthrough ==========
            # Only apply filter to sub stream, main stream copies raw input directly

            # Extract scale resolution for sub stream only
            sub_scale = vid_sub.get("vf", "scale=320:240")

            # Filter complex: only scale sub stream from input
            # Main stream will map directly from input (0:v)
            filter_complex = f"[0:v]{sub_scale}[sub]"
            out += ["-filter_complex", filter_complex]

            # ========== OUTPUT 1: SUB STREAM (transcoded, low-res) ==========
            out += ["-map", "[sub]"]  # Map filtered sub stream
            for k in video_key_order:
                if k in vid_sub and vid_sub[k] is not None:
                    out += [f"-{k}", str(vid_sub[k])]

            # Audio for sub stream
            if bool(aud.get("enabled", False)):
                out += ["-map", "0:a:0?"]  # Map audio if present (? = optional)
                c_a = aud.get("c:a", aud.get("codec", None))
                b_a = aud.get("b:a", aud.get("bitrate", None))
                ar = aud.get("ar", aud.get("rate", None))
                ac = aud.get("ac", aud.get("channels", None))
                if c_a: out += ["-c:a", str(c_a)]
                if b_a: out += ["-b:a", str(b_a)]
                if ar: out += ["-ar", str(ar)]
                if ac is not None: out += ["-ac", str(ac)]
                out += ["-async", "1"]
            else:
                out += ["-an"]

            out += ["-max_muxing_queue_size", "1024"]

            # Sub stream sink
            if protocol == "rtmp":
                sub_sink = f"rtmp://{host}:{port}/{path}"
                out += ["-f", "flv", sub_sink]
            elif protocol == "rtsp":
                sub_sink = f"rtsp://{host}:{port}/{path}"
                rtsp_transport = pub.get("rtsp_transport", "tcp")
                out += ["-f", "rtsp", "-rtsp_transport", rtsp_transport, sub_sink]
            else:
                raise ValueError(f"LL-HLS: unsupported publisher.protocol '{protocol}'")

            # ========== OUTPUT 2: MAIN STREAM (passthrough, native resolution) ==========
            out += ["-map", "0:v"]  # Map raw video input directly (no filter)
            out += ["-c:v", "copy"]  # Copy video codec (no re-encoding)

            # Audio for main stream (also passthrough if available)
            if bool(aud.get("enabled", False)):
                out += ["-map", "0:a:0?"]
                out += ["-c:a", "copy"]  # Copy audio too for lowest latency
            else:
                out += ["-an"]

            out += ["-max_muxing_queue_size", "1024"]

            # Main stream sink
            main_path = f"{path}_main"
            if protocol == "rtmp":
                main_sink = f"rtmp://{host}:{port}/{main_path}"
                out += ["-f", "flv", main_sink]
            elif protocol == "rtsp":
                main_sink = f"rtsp://{host}:{port}/{main_path}"
                rtsp_transport = pub.get("rtsp_transport", "tcp")
                out += ["-f", "rtsp", "-rtsp_transport", rtsp_transport, main_sink]

            logger.info(f"Built dual LL-HLS output for {camera_name}: sub→{path} ({sub_scale}), main→{main_path} (PASSTHROUGH)")

        else:
            # ========== TRANSCODE MODE: Both streams transcoded ==========
            # Original behavior - split and scale both streams

            # Extract scale resolutions from config
            sub_scale = vid_sub.get("vf", "scale=320:240")
            main_scale = vid_main.get("vf", "scale=1280:720")

            # Build filter_complex to split input into two scaled outputs
            filter_complex = f"[0:v]split=2[vsub][vmain];[vsub]{sub_scale}[sub];[vmain]{main_scale}[main]"
            out += ["-filter_complex", filter_complex]

            # ========== OUTPUT 1: SUB STREAM (transcoded, low-res) ==========
            out += ["-map", "[sub]"]  # Map filtered sub stream
            for k in video_key_order:
                if k in vid_sub and vid_sub[k] is not None:
                    out += [f"-{k}", str(vid_sub[k])]

            # Audio for sub stream
            if bool(aud.get("enabled", False)):
                out += ["-map", "0:a:0?"]
                c_a = aud.get("c:a", aud.get("codec", None))
                b_a = aud.get("b:a", aud.get("bitrate", None))
                ar = aud.get("ar", aud.get("rate", None))
                ac = aud.get("ac", aud.get("channels", None))
                if c_a: out += ["-c:a", str(c_a)]
                if b_a: out += ["-b:a", str(b_a)]
                if ar: out += ["-ar", str(ar)]
                if ac is not None: out += ["-ac", str(ac)]
                out += ["-async", "1"]
            else:
                out += ["-an"]

            out += ["-max_muxing_queue_size", "1024"]

            # Sub stream sink
            if protocol == "rtmp":
                sub_sink = f"rtmp://{host}:{port}/{path}"
                out += ["-f", "flv", sub_sink]
            elif protocol == "rtsp":
                sub_sink = f"rtsp://{host}:{port}/{path}"
                rtsp_transport = pub.get("rtsp_transport", "tcp")
                out += ["-f", "rtsp", "-rtsp_transport", rtsp_transport, sub_sink]
            else:
                raise ValueError(f"LL-HLS: unsupported publisher.protocol '{protocol}'")

            # ========== OUTPUT 2: MAIN STREAM (transcoded, high-res) ==========
            out += ["-map", "[main]"]  # Map filtered main stream
            for k in video_key_order:
                if k in vid_main and vid_main[k] is not None:
                    out += [f"-{k}", str(vid_main[k])]

            # Audio for main stream
            if bool(aud.get("enabled", False)):
                out += ["-map", "0:a:0?"]
                c_a = aud.get("c:a", aud.get("codec", None))
                b_a = aud.get("b:a", aud.get("bitrate", None))
                ar = aud.get("ar", aud.get("rate", None))
                ac = aud.get("ac", aud.get("channels", None))
                if c_a: out += ["-c:a", str(c_a)]
                if b_a: out += ["-b:a", str(b_a)]
                if ar: out += ["-ar", str(ar)]
                if ac is not None: out += ["-ac", str(ac)]
                out += ["-async", "1"]
            else:
                out += ["-an"]

            out += ["-max_muxing_queue_size", "1024"]

            # Main stream sink
            main_path = f"{path}_main"
            if protocol == "rtmp":
                main_sink = f"rtmp://{host}:{port}/{main_path}"
                out += ["-f", "flv", main_sink]
            elif protocol == "rtsp":
                main_sink = f"rtsp://{host}:{port}/{main_path}"
                rtsp_transport = pub.get("rtsp_transport", "tcp")
                out += ["-f", "rtsp", "-rtsp_transport", rtsp_transport, main_sink]

            logger.info(f"Built dual LL-HLS output for {camera_name}: sub→{path} ({sub_scale}), main→{main_path} ({main_scale})")

        return out

    except Exception as e:
        print(traceback.print_exc())
        raise Exception(f"something went wrong in build_ll_hls_dual_output_publish_params: {e}")
