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
                        print(f"extended: {flag} {entry} to ffmpeg params for {self.camera_name}")
                    continue

                # single value
                if mapped == "scale" and isinstance(value, str) and "x" in value:
                    w, h = value.split("x", 1)
                    params.extend(["-vf", f"scale={w}:{h}"])
                    print(f"extended: -vf scale={w}:{h} to ffmpeg params for {self.camera_name}")
                    continue

                params.extend([flag, str(value)])
                print(f"extended: {flag} {value} to ffmpeg params for {self.camera_name}")

            return params

        except Exception as e:
            print(traceback.print_exc())
            print(f"build_rtsp_params failed: {e}")
            raise



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


